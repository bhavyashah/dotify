// Named speaker identification: server.js against a stub speaker-id service.
// Covers the /api/speakers proxy, the /ingest pcm path, name substitution at
// print time, the one-shot re-announce when the current speaker's name
// resolves, and per-session reset.

const { test, before, after } = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const path = require('node:path');
const os = require('node:os');
const WebSocket = require('ws');
const rig = require('./helpers');

const PORT = 8795;       // server.js under test (8799 = server.test.js, 8798 = ui.test.js)
const STUB_PORT = 8794;  // stub speaker-id service
const BASE = `http://127.0.0.1:${PORT}`;

let child;
let stub;
const stubState = {
  requests: [],          // { method, path, bodyLength }
  names: {},             // label -> name the stub should return
};

function startStub() {
  stub = http.createServer((req, res) => {
    const chunks = [];
    req.on('data', (c) => chunks.push(c));
    req.on('end', () => {
      const body = Buffer.concat(chunks);
      const url = new URL(req.url, `http://127.0.0.1:${STUB_PORT}`);
      stubState.requests.push({ method: req.method, path: url.pathname, bodyLength: body.length });
      const reply = (obj) => {
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify(obj));
      };
      if (url.pathname === '/health') {
        reply({ ok: true, model: 'stub', speakers: [{ name: 'Guest', seconds: 12 }] });
      } else if (url.pathname === '/identify') {
        const label = url.searchParams.get('label');
        const name = stubState.names[label] || null;
        reply({ label, name, assigned: Boolean(name) });
      } else if (url.pathname === '/session/reset') {
        reply({ reset: true });
      } else if (url.pathname.startsWith('/speakers/')) {
        reply(req.method === 'DELETE'
          ? { removed: true }
          : { name: decodeURIComponent(url.pathname.slice('/speakers/'.length)), seconds: 12.3 });
      } else {
        res.writeHead(404).end();
      }
    });
  });
  return new Promise((resolve) => stub.listen(STUB_PORT, '127.0.0.1', resolve));
}

function startServer() {
  child = rig.startServer({
    port: PORT,
    env: {
      DOTIFY_ENV_FILE: path.join(os.tmpdir(), `dotify-naming-${PORT}.env`),
      DOTIFY_TRANSCRIPT_FILE: path.join(os.tmpdir(), `dotify-naming-${PORT}.txt`),
      DOTIFY_SPEAKER_CONFIRM_MS: '400',
      DOTIFY_SPEAKER_ID_URL: `http://127.0.0.1:${STUB_PORT}`,
    },
    deleteKeys: ['OPENAI_API_KEY', 'ASSEMBLYAI_API_KEY', 'DEEPGRAM_API_KEY',
                 'ELEVENLABS_API_KEY'],
  });
}

// This suite's probe has always required a 200 (never the 404 leniency).
const waitForListening = () => rig.waitForListening(BASE);

const settle = (ms = 150) => new Promise((r) => setTimeout(r, ms));
const ingest = rig.makeIngest(BASE);
// 1 s of quiet-but-nonzero PCM16 @ 24 kHz, base64 (content is irrelevant to
// the stub; only the plumbing is under test).
const fakePcm = Buffer.alloc(48000, 7).toString('base64');

async function stubSaw(pathname, sinceCount = 0, timeoutMs = 2000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (stubState.requests.filter((r) => r.path === pathname).length > sinceCount) return;
    await new Promise((r) => setTimeout(r, 50));
  }
  throw new Error(`stub never saw ${pathname}`);
}

before(async () => {
  await startStub();
  startServer();
  await waitForListening();
});
after(() => {
  if (child) child.kill();
  if (stub) stub.close();
});

test('GET /api/speakers proxies the service health + roster', async () => {
  const info = await (await fetch(`${BASE}/api/speakers`)).json();
  assert.strictEqual(info.ok, true);
  assert.strictEqual(info.speakers[0].name, 'Guest');
});

test('speaker name validation rejects junk before touching the service', async () => {
  const before = stubState.requests.length;
  const r = await fetch(`${BASE}/api/speakers/${encodeURIComponent('../../etc')}`, { method: 'POST', body: 'x' });
  assert.strictEqual(r.status, 400);
  const r2 = await fetch(`${BASE}/api/speakers/${encodeURIComponent('9starts-with-digit')}`, { method: 'DELETE' });
  assert.strictEqual(r2.status, 400);
  assert.strictEqual(stubState.requests.length, before);
});

test('enrollment audio is forwarded to the service verbatim', async () => {
  const body = Buffer.alloc(24000, 1); // any raw bytes
  const r = await fetch(`${BASE}/api/speakers/Host%20Speaker`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/octet-stream' },
    body,
  });
  assert.strictEqual(r.status, 200);
  assert.strictEqual((await r.json()).name, 'Host Speaker');
  const seen = stubState.requests.find((x) => x.path === '/speakers/Host%20Speaker' && x.method === 'POST');
  assert.ok(seen, 'stub must receive the enrollment');
  assert.strictEqual(seen.bodyLength, body.length);
});

test('DELETE /api/speakers/<name> forwards to the service', async () => {
  const r = await fetch(`${BASE}/api/speakers/Guest`, { method: 'DELETE' });
  assert.strictEqual(r.status, 200);
  assert.strictEqual((await r.json()).removed, true);
});

test('names replace labels at print time, with one re-announce for the current speaker', async () => {
  const ws = new WebSocket(`ws://127.0.0.1:${PORT}/finalized`);
  await new Promise((resolve, reject) => { ws.on('open', resolve); ws.on('error', reject); });
  const received = [];
  const waiters = [];
  ws.on('message', (raw) => {
    const msg = JSON.parse(raw.toString());
    if (msg.type === 'latency') return;  // the connection welcome, not text
    const waiter = waiters.shift();
    if (waiter) waiter(msg); else received.push(msg);
  });
  const next = () => (received.length
    ? Promise.resolve(received.shift())
    : new Promise((resolve) => waiters.push(resolve)));

  try {
    await fetch(`${BASE}/reset`, { method: 'POST' });
    await settle();

    // Turn 1: label A, unidentified yet -> announces as "A:". The pcm rides
    // along and the server must submit it to the service.
    const identifyCalls = stubState.requests.filter((r) => r.path === '/identify').length;
    await ingest({ text: 'hello there', speaker: 'A', pcm: fakePcm });
    assert.strictEqual((await next()).text, 'A: hello there');
    await stubSaw('/identify', identifyCalls);

    // The service now knows A = Guest. The next A turn resolves the name and
    // re-announces once, then A continues unprefixed as usual.
    stubState.names.A = 'Guest';
    await ingest({ text: 'still talking', speaker: 'A', pcm: fakePcm });
    const printed = await next();
    // Name resolution is asynchronous: this final either already carries the
    // name (fast response) or the following one does (re-announce). Accept
    // the deterministic outcome: within two finals, "Guest:" is printed once.
    let texts = [printed.text];
    if (printed.text === 'still talking') {
      await settle(); // let the identify response land
      await ingest({ text: 'and more', speaker: 'A', pcm: fakePcm });
      texts.push((await next()).text);
    }
    assert.ok(texts.some((t) => t.startsWith('Guest: ')),
      `expected a "Guest: " prefix in ${JSON.stringify(texts)}`);

    // Continuing A: no repeated prefix now that the name has been printed.
    await ingest({ text: 'no prefix here', speaker: 'A' });
    assert.strictEqual((await next()).text, 'no prefix here');

    // Change to unenrolled B (confirmed by a second B turn): plain label.
    await ingest({ text: 'someone new', speaker: 'B' });
    await ingest({ text: 'yes new', speaker: 'B' });
    assert.strictEqual((await next()).text, 'B: someone new');
    assert.strictEqual((await next()).text, 'yes new');

    // Back to A (confirmed): the change prints the NAME, not the label.
    await ingest({ text: 'guest returns', speaker: 'A' });
    await ingest({ text: 'indeed', speaker: 'A' });
    assert.strictEqual((await next()).text, 'Guest: guest returns');
    assert.strictEqual((await next()).text, 'indeed');

    // /reset clears the name map and tells the service to reset the session.
    const resets = stubState.requests.filter((r) => r.path === '/session/reset').length;
    stubState.names.A = null;
    await fetch(`${BASE}/reset`, { method: 'POST' });
    await stubSaw('/session/reset', resets);
    await ingest({ text: 'fresh session', speaker: 'A', pcm: fakePcm });
    assert.strictEqual((await next()).text, 'A: fresh session');
  } finally {
    ws.close();
  }
});
