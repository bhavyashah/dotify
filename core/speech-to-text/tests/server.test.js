const { test, before, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const os = require('node:os');
const fs = require('node:fs');
const WebSocket = require('ws');
const rig = require('./helpers');

const PORT = 8799;
const BASE = `http://127.0.0.1:${PORT}`;

let child;
let tmpTranscript;
let tmpEnv;
let tmpDict;
let tmpModels;

// Start server.js with NO api key and temp files, so the real .env /
// transcript.txt / dictionary.json are never touched.
function startServer(extraEnv = {}) {
  tmpTranscript = path.join(os.tmpdir(), `dotify-transcript-${PORT}.txt`);
  tmpEnv = path.join(os.tmpdir(), `dotify-provider-config-${PORT}.env`);
  tmpDict = path.join(os.tmpdir(), `dotify-dictionary-${PORT}.json`);
  tmpModels = path.join(os.tmpdir(), `dotify-models-${PORT}`);
  child = rig.startServer({
    port: PORT,
    clean: [tmpTranscript, tmpEnv, tmpDict, tmpModels],
    env: {
      DOTIFY_ENV_FILE: tmpEnv,
      DOTIFY_TRANSCRIPT_FILE: tmpTranscript,
      DOTIFY_DICTIONARY_FILE: tmpDict,
      DOTIFY_MODELS_DIR: tmpModels,
      DOTIFY_SPEAKER_CONFIRM_MS: '400', // fast smoothing timeout for tests
      DOTIFY_SOFT_REVISE_MS: '0',       // no soft-revision throttle in tests
      DOTIFY_SHOWN_ACK_MS: '2000',      // roomy shown-ack window (no ack races)
      ...extraEnv,
    },
    // Ensure keyless unless a test opts in (via a DOTIFY_ENV_FILE, never env).
    deleteKeys: ['OPENAI_API_KEY', 'ASSEMBLYAI_API_KEY', 'DEEPGRAM_API_KEY',
                 'ELEVENLABS_API_KEY'],
  });
}

const waitForListening = () => rig.waitForListening(BASE);

before(async () => { startServer(); await waitForListening(); });
after(() => { if (child) child.kill(); });

test('server boots with no OPENAI_API_KEY and serves the page', async () => {
  const r = await fetch(`${BASE}/`);
  assert.strictEqual(r.status, 200);
  const html = await r.text();
  assert.match(html, /Dotify/);
  assert.match(html, /API keys/);
  assert.match(html, /saved only on this computer and never displayed/);
});

test('provider configuration reports readiness without returning keys', async () => {
  const response = await fetch(`${BASE}/api/providers`);
  assert.strictEqual(response.status, 200);
  const config = await response.json();
  assert.strictEqual(config.providers.openai.configured, false);
  assert.strictEqual(config.providers.assemblyai.configured, false);
  assert.strictEqual(config.providers.deepgram.configured, false);
  assert.strictEqual(config.providers.elevenlabs.configured, false);
  assert.ok(typeof config.token === 'string' && config.token.length >= 32);
  assert.doesNotMatch(JSON.stringify(config), /api[_-]?key/i);
});

test('provider configuration rejects cross-site-style writes without its token', async () => {
  const response = await fetch(`${BASE}/api/providers`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ provider: 'openai', key: 'secret-test-key' }),
  });
  assert.strictEqual(response.status, 403);
  assert.strictEqual(fs.existsSync(tmpEnv), false);
});

test('provider key can be saved and removed through the local interface', async () => {
  const initial = await (await fetch(`${BASE}/api/providers`)).json();
  const secret = 'secret-test-key-that-must-not-be-returned';
  const saveResponse = await fetch(`${BASE}/api/providers`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Dotify-Token': initial.token,
    },
    body: JSON.stringify({ provider: 'openai', key: secret }),
  });
  assert.strictEqual(saveResponse.status, 200);
  const saved = await saveResponse.json();
  assert.strictEqual(saved.providers.openai.configured, true);
  assert.doesNotMatch(JSON.stringify(saved), new RegExp(secret));
  assert.match(fs.readFileSync(tmpEnv, 'utf8'), /^OPENAI_API_KEY=/m);

  const removeResponse = await fetch(`${BASE}/api/providers`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Dotify-Token': initial.token,
    },
    body: JSON.stringify({ provider: 'openai', remove: true }),
  });
  assert.strictEqual(removeResponse.status, 200);
  const removed = await removeResponse.json();
  assert.strictEqual(removed.providers.openai.configured, false);
  assert.doesNotMatch(fs.readFileSync(tmpEnv, 'utf8'), /OPENAI_API_KEY/);
});

// --- Personal dictionary -----------------------------------------------------

test('an oversized settings write is a 413, not a dropped connection', async () => {
  const { token } = await (await fetch(`${BASE}/api/providers`)).json();
  const response = await fetch(`${BASE}/api/dictionary`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Dotify-Token': token },
    body: JSON.stringify({ add: { word: 'x'.repeat(5000) } }),
  });
  assert.strictEqual(response.status, 413);
  assert.match((await response.json()).error, /4096 bytes/);
  const after = await fetch(`${BASE}/api/dictionary`);
  assert.deepStrictEqual((await after.json()).words, []);
});

test('dictionary rejects writes without the token and starts empty', async () => {
  const empty = await (await fetch(`${BASE}/api/dictionary`)).json();
  assert.deepStrictEqual(empty.words, []);
  const response = await fetch(`${BASE}/api/dictionary`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ add: { word: 'Dotify' } }),
  });
  assert.strictEqual(response.status, 403);
});

test('dictionary add/remove round-trips and persists to its file', async () => {
  const { token } = await (await fetch(`${BASE}/api/providers`)).json();
  const post = (payload) => fetch(`${BASE}/api/dictionary`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Dotify-Token': token },
    body: JSON.stringify(payload),
  });

  let response = await post({ add: { word: 'Dotify', soundsLike: ['dotafy'] } });
  assert.strictEqual(response.status, 200);
  assert.deepStrictEqual((await response.json()).words,
    [{ word: 'Dotify', soundsLike: ['dotafy'] }]);
  assert.match(fs.readFileSync(tmpDict, 'utf8'), /"Dotify"/);

  // Re-adding the same word updates it instead of duplicating.
  response = await post({ add: { word: 'dotify', soundsLike: ['dot if eye'] } });
  const updated = (await response.json()).words;
  assert.strictEqual(updated.length, 1);
  assert.deepStrictEqual(updated[0].soundsLike, ['dot if eye']);

  response = await post({ add: { word: '12345' } });
  assert.strictEqual(response.status, 400); // letters required

  response = await post({ remove: 'DOTIFY' });
  assert.strictEqual(response.status, 200);
  assert.deepStrictEqual((await response.json()).words, []);

  response = await post({ remove: 'never-added' });
  assert.strictEqual(response.status, 400);
});

test('dictionary corrections reach /finalized and the transcript file', async () => {
  const { token } = await (await fetch(`${BASE}/api/providers`)).json();
  await fetch(`${BASE}/api/dictionary`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Dotify-Token': token },
    body: JSON.stringify({ add: { word: 'Dotify', soundsLike: ['dotafy'] } }),
  });
  const got = nextFinalized();
  // Give the WS a moment to connect before ingesting.
  await new Promise((r) => setTimeout(r, 200));
  await fetch(`${BASE}/ingest`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text: 'i spoke with dotafy and with dotifi today' }),
  });
  const msg = await got;
  // "dotafy" fixed by its alias; "dotifi" by the fuzzy pass.
  assert.strictEqual(msg.text, 'i spoke with Dotify and with Dotify today');
  await new Promise((r) => setTimeout(r, 200));
  assert.match(fs.readFileSync(tmpTranscript, 'utf8'),
    /i spoke with Dotify and with Dotify today/);
  // Clean up for later tests.
  await fetch(`${BASE}/api/dictionary`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Dotify-Token': token },
    body: JSON.stringify({ remove: 'Dotify' }),
  });
});

test('offline-model status reports the manifest with nothing downloaded', async () => {
  const response = await fetch(`${BASE}/api/offline-model`);
  assert.strictEqual(response.status, 200);
  const status = await response.json();
  assert.strictEqual(status.ready, false);
  assert.strictEqual(status.downloading, false);
  assert.strictEqual(status.percent, 0);
  assert.ok(status.totalBytes > 600_000_000); // the ~650 MB Nemotron manifest
  assert.match(status.label, /Nemotron/);
});

test('offline-model writes need the token and a known action', async () => {
  const bare = await fetch(`${BASE}/api/offline-model`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ download: true }),
  });
  assert.strictEqual(bare.status, 403);
  const { token } = await (await fetch(`${BASE}/api/providers`)).json();
  const unknown = await fetch(`${BASE}/api/offline-model`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Dotify-Token': token },
    body: JSON.stringify({ install: true }),
  });
  assert.strictEqual(unknown.status, 400);
  // remove is safe with nothing on disk — and proves the authorized path.
  const remove = await fetch(`${BASE}/api/offline-model`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Dotify-Token': token },
    body: JSON.stringify({ remove: true }),
  });
  assert.strictEqual(remove.status, 200);
  assert.strictEqual((await remove.json()).ready, false);
});

// Open a /finalized client and resolve with the first message it receives.
function nextFinalized() {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(`ws://127.0.0.1:${PORT}/finalized`);
    const timer = setTimeout(() => { ws.close(); reject(new Error('no /finalized message')); }, 3000);
    ws.on('message', (raw) => {
      const msg = JSON.parse(raw.toString());
      if (msg.type === 'latency') return;  // the connection welcome, not text
      clearTimeout(timer);
      ws.close();
      resolve(msg);
    });
    ws.on('error', reject);
  });
}

test('POST /ingest emits on /finalized with the braille contract shape', async () => {
  const got = nextFinalized();
  // Give the WS a moment to connect before ingesting.
  await new Promise((r) => setTimeout(r, 200));
  const r = await fetch(`${BASE}/ingest`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text: 'hello braille' }),
  });
  // The response carries the ruled display text (the screen-mirror
  // contract: the overlay appends its box from this body, so box and
  // braille can never disagree on a final the server rewrote or refused).
  assert.strictEqual(r.status, 200);
  assert.deepStrictEqual(await r.json(), { text: 'hello braille' });
  const msg = await got;
  assert.strictEqual(msg.type, 'final');       // ws_source.py checks this
  assert.strictEqual(msg.text, 'hello braille'); // ...and this
  assert.ok(typeof msg.ts === 'string');
});

test('POST /ingest appends to the transcript file', async () => {
  await fetch(`${BASE}/ingest`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text: 'written line' }),
  });
  await new Promise((r) => setTimeout(r, 100));
  const contents = fs.readFileSync(tmpTranscript, 'utf8');
  assert.match(contents, /written line/);
});

test('POST /ingest {typed:true} records to the file but never broadcasts', async () => {
  // Typed-to-display text already reached the braille display through the
  // control bridge — a /finalized broadcast would braille it twice. The next
  // thing on /finalized must be the LATER spoken line, while the file holds
  // both in order.
  const got = nextFinalized();
  await new Promise((r) => setTimeout(r, 200));
  let r = await fetch(`${BASE}/ingest`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ typed: true, text: 'typed to the display' }),
  });
  assert.strictEqual(r.status, 204);
  await fetch(`${BASE}/ingest`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text: 'spoken after typing' }),
  });
  const msg = await got;
  assert.strictEqual(msg.text, 'spoken after typing');
  await new Promise((r) => setTimeout(r, 100)); // let the async appends land
  const contents = fs.readFileSync(tmpTranscript, 'utf8');
  assert.match(contents, /typed to the display\n[\s\S]*spoken after typing/);
  // Empty typed text is still a 400, same as the spoken path.
  r = await fetch(`${BASE}/ingest`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ typed: true, text: '  ' }),
  });
  assert.strictEqual(r.status, 400);
});

test('a CJK final from /ingest passes through — the guard is per-provider', async () => {
  // The NON_ENGLISH_SCRIPT drop is scoped to sessions whose provider
  // declares the Whisper silence-hallucination failure mode
  // (PROVIDERS[].scriptGuard — OpenAI today; unreachable from these tests
  // without a real key). The /ingest engines (the offline Nemotron model)
  // are English-only pins that cannot hallucinate these scripts, and a
  // genuinely multilingual final must not be censored — it reaches
  // /finalized and the transcript like any other text.
  const got = nextFinalized();
  await new Promise((r) => setTimeout(r, 200)); // let the listener connect
  await fetch(`${BASE}/ingest`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text: 'って感じ' }),
  });
  const msg = await got;
  assert.strictEqual(msg.text, 'って感じ');
  // The transcript append rides the serialized write queue — give it a beat.
  await new Promise((r) => setTimeout(r, 200));
  const contents = fs.readFileSync(tmpTranscript, 'utf8');
  assert.ok(contents.includes('って感じ'));
});

test('POST /ingest with empty text returns 400 and emits nothing', async () => {
  const r = await fetch(`${BASE}/ingest`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text: '   ' }),
  });
  assert.strictEqual(r.status, 400);
});

test('speaker labels: smoothing decides the label BEFORE text is emitted', async () => {
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
  const ingest = (payload) => fetch(`${BASE}/ingest`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const settle = (ms = 100) => new Promise((r) => setTimeout(r, ms));

  try {
    // First labeled final announces its speaker immediately (no hold: there is
    // no established speaker to flicker from).
    await ingest({ text: 'hello there', speaker: 'A' });
    const first = await next();
    assert.strictEqual(first.text, 'A: hello there');
    assert.strictEqual(first.speaker, 'A');

    // Same speaker continuing: no repeated prefix, zero added latency.
    await ingest({ text: 'still me talking', speaker: 'A' });
    assert.strictEqual((await next()).text, 'still me talking');

    // A label flip is HELD until confirmed — nothing emits on the first B turn.
    await ingest({ text: 'hi back', speaker: 'B' });
    await settle();
    assert.strictEqual(received.length, 0, 'flip turn must not emit before confirmation');

    // Second consecutive B confirms: held text emits WITH the prefix already
    // decided (braille output is append-only — no retroactive labels).
    await ingest({ text: 'more from b', speaker: 'B' });
    assert.strictEqual((await next()).text, 'B: hi back');
    assert.strictEqual((await next()).text, 'more from b');

    // UNKNOWN (turn too short to attribute) reads as the current speaker.
    await ingest({ text: 'yeah', speaker: 'UNKNOWN' });
    const unknown = await next();
    assert.strictEqual(unknown.text, 'yeah');
    assert.strictEqual(unknown.speaker, undefined);

    // One-turn flicker: A intrudes for a single turn, then B is back. The
    // flicker is attributed to B (the current speaker) — no spurious "A:"
    // ever reaches the display.
    await ingest({ text: 'flicker turn', speaker: 'A' });
    await settle();
    assert.strictEqual(received.length, 0, 'flicker turn must be held');
    await ingest({ text: 'b continues', speaker: 'B' });
    const flick = await next();
    assert.strictEqual(flick.text, 'flicker turn');   // no "A: " prefix
    assert.strictEqual(flick.speaker, 'B');           // decided attribution
    assert.strictEqual((await next()).text, 'b continues');

    // Unconfirmed flip resolves by TIMEOUT: a lone C turn followed by silence
    // is trusted as a real speaker change.
    await ingest({ text: 'now c speaks', speaker: 'C' });
    await settle();
    assert.strictEqual(received.length, 0);
    const timedOut = await next(); // arrives when the 400ms confirm timer fires
    assert.strictEqual(timedOut.text, 'C: now c speaks');

    // Undiarized finals (plain /ingest, OpenAI) pass straight through unprefixed.
    await ingest({ text: 'keyless line' });
    assert.strictEqual((await next()).text, 'keyless line');

    // UNKNOWN arriving mid-hold queues behind it so text order is preserved.
    await ingest({ text: 'back to a', speaker: 'A' });
    await ingest({ text: 'hmm', speaker: 'UNKNOWN' });
    await settle();
    assert.strictEqual(received.length, 0, 'hold must also queue UNKNOWN turns');
    await ingest({ text: 'confirmed a', speaker: 'A' });
    assert.strictEqual((await next()).text, 'A: back to a');
    assert.strictEqual((await next()).text, 'hmm');
    assert.strictEqual((await next()).text, 'confirmed a');

    // /reset flushes any pending hold (text is never dropped) and starts a
    // fresh session, so the next speaker re-announces.
    await ingest({ text: 'tail turn', speaker: 'B' });
    await fetch(`${BASE}/reset`, { method: 'POST' });
    assert.strictEqual((await next()).text, 'B: tail turn');
    await ingest({ text: 'fresh session', speaker: 'B' });
    assert.strictEqual((await next()).text, 'B: fresh session');
  } finally {
    ws.close();
  }
});

test('a typed record flushes a pending diarization hold first', async () => {
  // Words SAID before the typed line was posted can be sitting in the
  // speaker-smoothing hold; the typed append must not jump the queue or
  // transcript.txt inverts true chronology. (Runs after the smoothing test:
  // the two settling turns below make the starting speaker irrelevant.)
  const post = (body) => fetch(`${BASE}/ingest`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  await post({ text: 'holda settles in', speaker: 'HOLDA' });
  await post({ text: 'holda keeps talking', speaker: 'HOLDA' });
  await post({ text: 'holdb speaks first', speaker: 'HOLDB' }); // enters the hold
  await post({ typed: true, text: 'typed while held' });
  await new Promise((r) => setTimeout(r, 150));
  const contents = fs.readFileSync(tmpTranscript, 'utf8');
  const held = contents.indexOf('holdb speaks first');
  const typedAt = contents.indexOf('typed while held');
  assert.ok(held >= 0, 'held spoken line must be recorded');
  assert.ok(typedAt >= 0, 'typed line must be recorded');
  assert.ok(held < typedAt, `held speech must precede the typed line:\n${contents}`);
});

test('opening /audio with no key sends an error and server stays up', async () => {
  const ws = new WebSocket(`ws://127.0.0.1:${PORT}/audio`);
  const msg = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('no error message')), 3000);
    ws.on('message', (raw) => { clearTimeout(timer); resolve(JSON.parse(raw.toString())); });
    ws.on('error', reject);
  });
  assert.strictEqual(msg.type, 'error');
  assert.match(msg.message, /_API_KEY is missing/i);
  ws.close();
  // Server still alive:
  const r = await fetch(`${BASE}/`);
  assert.strictEqual(r.status, 200);
});

test('malformed URLs and Host headers are refused without killing the server', async () => {
  const http = require('node:http');
  const rawRequest = (options) => new Promise((resolve, reject) => {
    const req = http.request({ host: '127.0.0.1', port: PORT, ...options }, (res) => {
      res.resume();
      res.on('end', () => resolve(res.statusCode));
    });
    req.on('error', reject);
    req.end();
  });
  // A broken percent-escape in the speaker name.
  assert.strictEqual(
    await rawRequest({ method: 'POST', path: '/api/speakers/%E0%A4' }), 400);
  // An unparseable Host header on routes that read the query string.
  assert.strictEqual(await rawRequest({
    method: 'GET', path: '/api/net-probe?engine=nonsense', headers: { Host: 'a b' },
  }), 400);
  const ws = new WebSocket(`ws://127.0.0.1:${PORT}/audio`, { headers: { Host: 'a b' } });
  const msg = await new Promise((resolve, reject) => {
    ws.on('message', (raw) => resolve(JSON.parse(raw.toString())));
    ws.on('error', reject);
  });
  assert.strictEqual(msg.type, 'error');
  ws.close();
  const r = await fetch(`${BASE}/`);
  assert.strictEqual(r.status, 200);
});

// --- revisable buffer --------------------------------------------------------

// Open a persistent /finalized client with an awaitable message queue.
const openFinalized = () => rig.openFinalized(PORT);

const ingest = rig.makeIngest(BASE);

test('revisable buffer: interims emit soft text, revisions, then a hardening final', async () => {
  const client = openFinalized();
  await client.opened;
  try {
    // The stable prefix (hypothesis minus the 2-word holdback) goes out soft.
    await ingest({ interim: true, client: 'rb1', segment: '0',
                   text: 'alpha beta gamma delta' });
    const soft = await client.next();
    assert.strictEqual(soft.type, 'soft');
    assert.strictEqual(soft.text, 'alpha beta');
    assert.ok(typeof soft.id === 'string' && soft.id.length > 0);

    // A grown hypothesis revises the same segment with the new stable prefix.
    await ingest({ interim: true, client: 'rb1', segment: '0',
                   text: 'alpha bravo gamma delta epsilon' });
    const revise = await client.next();
    assert.strictEqual(revise.type, 'revise');
    assert.strictEqual(revise.revise, soft.id);
    assert.strictEqual(revise.text, 'alpha bravo gamma');

    // The final for the same {client, segment} hardens it: same id, full text.
    await ingest({ text: 'alpha bravo gamma delta epsilon zeta',
                   client: 'rb1', segment: '0' });
    const final = await client.next();
    assert.strictEqual(final.type, 'final');
    assert.strictEqual(final.id, soft.id);
    assert.strictEqual(final.text, 'alpha bravo gamma delta epsilon zeta');

    // transcript.txt records the hardened final only, never soft text.
    await new Promise((r) => setTimeout(r, 100));
    const contents = fs.readFileSync(tmpTranscript, 'utf8');
    assert.ok(contents.includes('alpha bravo gamma delta epsilon zeta\n'));
    assert.ok(!contents.includes('alpha beta\n'));
  } finally {
    client.ws.close();
  }
});

test('revisable buffer: short interims are held back entirely', async () => {
  const client = openFinalized();
  await client.opened;
  try {
    // Two words = all inside the holdback: nothing may emit.
    await ingest({ interim: true, client: 'rb2', segment: '0', text: 'one two' });
    await new Promise((r) => setTimeout(r, 200));
    assert.strictEqual(client.pending.length, 0);
    // The next message is the plain final, with a fresh id (no soft existed).
    await ingest({ text: 'one two', client: 'rb2', segment: '0' });
    const final = await client.next();
    assert.strictEqual(final.type, 'final');
    assert.strictEqual(final.text, 'one two');
    assert.ok(typeof final.id === 'string' && final.id.length > 0);
  } finally {
    client.ws.close();
  }
});

test('revisable buffer: an interim cleared to empty withdraws the soft segment', async () => {
  const client = openFinalized();
  await client.opened;
  try {
    await ingest({ interim: true, client: 'rb3', segment: '0',
                   text: 'ghost words that vanish now' });
    const soft = await client.next();
    assert.strictEqual(soft.type, 'soft');
    await ingest({ interim: true, client: 'rb3', segment: '0', text: '' });
    const withdraw = await client.next();
    assert.strictEqual(withdraw.type, 'revise');
    assert.strictEqual(withdraw.revise, soft.id);
    assert.strictEqual(withdraw.text, '');
  } finally {
    client.ws.close();
  }
});

test('revisable buffer: CJK interims stream soft — /ingest is unguarded', async () => {
  // Same per-provider scoping as the finals test above: soft text from the
  // English-only /ingest engines is never script-censored. The segment is
  // finalized before the test ends so no orphan soft segment leaks into the
  // later tests' /reset hardening (which would shift their expected ids).
  const client = openFinalized();
  await client.opened;
  try {
    await ingest({ interim: true, client: 'rb4', segment: '0',
                   text: 'って感じ です ね まあ' });
    const soft = await client.next();
    assert.strictEqual(soft.type, 'soft');
    assert.strictEqual(soft.text, 'って感じ です'); // holdback drops the last 2
    await ingest({ text: 'って感じ です ね まあ', client: 'rb4', segment: '0' });
    const final = await client.next();
    assert.strictEqual(final.type, 'final');
    assert.strictEqual(final.id, soft.id); // hardened, not re-appended
  } finally {
    client.ws.close();
  }
});

test('revisable buffer: every final carries a unique per-boot id', async () => {
  const client = openFinalized();
  await client.opened;
  try {
    await ingest({ text: 'first plain line' });
    const a = await client.next();
    await ingest({ text: 'second plain line' });
    const b = await client.next();
    assert.ok(a.id && b.id && a.id !== b.id);
    assert.strictEqual(a.id.split('-')[0], b.id.split('-')[0]); // same boot nonce
  } finally {
    client.ws.close();
  }
});

test('revisable buffer: /reset hardens abandoned soft segments (no wedge)', async () => {
  // Soft text never renders downstream: a segment abandoned while soft would
  // block the braille queue behind it forever. Forgetting one must harden it.
  const client = openFinalized();
  await client.opened;
  try {
    await ingest({ interim: true, client: 'rb6', segment: '0',
                   text: 'stranded words about to orphan' });
    const soft = await client.next();
    assert.strictEqual(soft.type, 'soft');
    assert.strictEqual(soft.text, 'stranded words about');

    // Fresh-session reset with the segment still open: no final is coming.
    await fetch(`${BASE}/reset`, { method: 'POST' });
    const final = await client.next();
    assert.strictEqual(final.type, 'final');
    assert.strictEqual(final.id, soft.id);
    assert.strictEqual(final.text, soft.text);

    // The abandoned key is gone: the same {client, segment} later is a
    // fresh segment, never a second hardening of the old id.
    await ingest({ text: 'brand new line', client: 'rb6', segment: '0' });
    const next = await client.next();
    assert.strictEqual(next.type, 'final');
    assert.strictEqual(next.text, 'brand new line');
    assert.notStrictEqual(next.id, soft.id);
  } finally {
    client.ws.close();
  }
});

// --- jump-to-live summary endpoint -------------------------------------------

const http = require('node:http');

test('POST /summarize validates its payload', async () => {
  const r = await fetch(`${BASE}/summarize`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ chars: 20 }), // no text
  });
  assert.strictEqual(r.status, 400);
  // The budget scales with the backlog but stays bounded.
  const over = await fetch(`${BASE}/summarize`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text: 'hello there', chars: 2001, grade: 2 }),
  });
  assert.strictEqual(over.status, 400);
  const wide = await fetch(`${BASE}/summarize`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text: 'the roof quote came in over budget', chars: 700, grade: 2 }),
  });
  assert.strictEqual(wide.status, 200);
});

test('POST /summarize without a key falls back to the local extractive summary', async () => {
  const r = await fetch(`${BASE}/summarize`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text: 'the roof quote came in over budget', chars: 22, grade: 2 }),
  });
  assert.strictEqual(r.status, 200);
  const { summary, source } = await r.json();
  assert.strictEqual(source, 'local');
  assert.ok(summary.length <= 22, `summary over budget: "${summary}"`);
  assert.match(summary, /roof/);
  assert.match(summary, /budget/);
  assert.doesNotMatch(summary, /\bthe\b/); // stopwords dropped
  assert.strictEqual((await fetch(`${BASE}/`)).status, 200);
});

test('localSummary keeps spoken order, drops stopwords, respects the budget', () => {
  const { localSummary } = require('../summarize');
  const text = 'so um the meeting moved to thursday because the meeting '
    + 'room was double booked';
  const summary = localSummary(text, 30);
  assert.ok(summary.length <= 30, `over budget: "${summary}"`);
  assert.doesNotMatch(summary, /\b(so|um|the|to|was|because)\b/);
  assert.match(summary, /meeting/);      // repeated content word scores top
  // Whatever was chosen appears in original spoken order.
  const positions = summary.split(' ').map((w) => text.indexOf(w));
  assert.deepStrictEqual(positions, [...positions].sort((a, b) => a - b));
  // A budget smaller than any content word still returns something.
  assert.ok(localSummary('extraordinarily', 6).length <= 6);
  assert.ok(localSummary('extraordinarily', 6).length > 0);
  // No content words at all -> empty, which the server maps to an error
  // and the ticker maps to the plain snap.
  assert.strictEqual(localSummary('um uh the of and', 20), '');
});

// Keep LAST in the file: restarts the shared server with a key + a stubbed
// OpenAI upstream, so the LLM call shape is pinned without network access.
test('POST /summarize compresses through the model (stubbed upstream)', async () => {
  let seen;
  const stub = http.createServer((req, res) => {
    let body = '';
    req.on('data', (c) => { body += c; });
    req.on('end', () => {
      seen = { path: req.url, payload: JSON.parse(body) };
      res.writeHead(200, { 'Content-Type': 'application/json' });
      // Structured-output reply shape, with grammar-wall padding artifacts
      // the sanitizer must shave. Casing survives.
      res.end(JSON.stringify({
        choices: [{ message: { content: JSON.stringify({ s: '  Ana said roof quote over\n budget||| ' }) } }],
      }));
    });
  });
  await new Promise((resolve) => stub.listen(0, '127.0.0.1', resolve));
  const envFile = path.join(os.tmpdir(), 'dotify-summary-test.env');
  fs.writeFileSync(envFile, 'OPENAI_API_KEY=test-key\n');
  await new Promise((resolve) => { child.once('exit', resolve); child.kill(); });
  startServer({
    DOTIFY_ENV_FILE: envFile,
    DOTIFY_OPENAI_BASE_URL: `http://127.0.0.1:${stub.address().port}`,
  });
  await waitForListening();
  try {
    const r = await fetch(`${BASE}/summarize`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: 'the roof quote came in over budget', chars: 22, grade: 2 }),
    });
    assert.strictEqual(r.status, 200);
    const { summary, source } = await r.json();
    assert.strictEqual(source, 'openai');
    assert.strictEqual(summary, 'Ana said roof quote over budget'); // whitespace + padding shaved; casing kept
    assert.strictEqual(seen.path, '/v1/chat/completions');
    assert.strictEqual(seen.payload.model, 'gpt-5.6-luna');
    assert.strictEqual(seen.payload.reasoning_effort, 'low'); // 'none' doesn't plan for the wall
    // The hard guarantee: the character budget is a decoding-level constraint.
    const schema = seen.payload.response_format.json_schema.schema;
    assert.strictEqual(schema.properties.s.maxLength, 22);
    assert.strictEqual(seen.payload.response_format.json_schema.strict, true);
    const system = seen.payload.messages[0].content;
    assert.match(system, /max 22 characters/);
    assert.match(system, /contracted \(grade 2\)/);
  } finally {
    stub.close();
    fs.rmSync(envFile, { force: true });
  }
});

// --- Latency presets & the shown-ack settlement (internal hook) ---------------

// A raw /finalized socket that KEEPS latency messages (the helpers above
// skip them), for asserting the welcome and the change fanout.
const openFinalizedRaw = () => rig.openFinalized(PORT, { raw: true });

const postLatency = (mode) => fetch(`${BASE}/latency`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ mode }),
});

// The transcript file is created by its first append — while a settle is
// deferred (the point of these tests) it may not exist at all yet.
function transcriptText() {
  try { return fs.readFileSync(tmpTranscript, 'utf8'); } catch { return ''; }
}

test('latency: consumers get the mode on connect and on change; junk is rejected', async () => {
  const client = openFinalizedRaw();
  await client.opened;
  try {
    const welcome = await client.next();
    assert.strictEqual(welcome.type, 'latency');
    assert.strictEqual(welcome.mode, 'balanced');
    // The render gate rides every latency frame: with no stable-engine
    // session live, balanced renders confirmed-only.
    assert.strictEqual(welcome.render, 'confirmed');

    assert.strictEqual((await postLatency('warp')).status, 400);

    assert.strictEqual((await postLatency('fastest')).status, 204);
    const change = await client.next();
    assert.strictEqual(change.type, 'latency');
    assert.strictEqual(change.mode, 'fastest');
    assert.strictEqual(change.render, 'eager');

    // Setting the SAME mode again is a no-op (no duplicate fanout): the
    // next latency frame a consumer sees is the NEXT real change.
    assert.strictEqual((await postLatency('fastest')).status, 204);
    assert.strictEqual((await postLatency('accurate')).status, 204);
    const change2 = await client.next();
    assert.strictEqual(change2.type, 'latency');
    assert.strictEqual(change2.mode, 'accurate');
    assert.strictEqual(change2.render, 'confirmed');
  } finally {
    client.ws.close();
    await postLatency('balanced');
  }
});

test('a stable ingest engine (nemotron) flips balanced to eager and streams its prefix', async () => {
  // The /ingest twin of the /audio session-start stability assertion: an
  // interim tagged engine:'nemotron' applies the measured (2,1) filter —
  // the hypothesis minus its last 2 words emits as soft — and flips
  // balanced consumers to eager rendering while the stable source feeds.
  // /reset is the sessionless abandonment point: back to confirmed.
  const client = openFinalizedRaw();
  await client.opened;
  try {
    const welcome = await client.next();
    assert.strictEqual(welcome.mode, 'balanced');
    assert.strictEqual(welcome.render, 'confirmed');

    await ingest({ interim: true, client: 'nemcli', segment: '1',
                   text: 'one two three four five', engine: 'nemotron' });
    const change = await client.next();
    assert.strictEqual(change.type, 'latency');
    assert.strictEqual(change.mode, 'balanced');
    assert.strictEqual(change.render, 'eager');
    const soft = await client.next();
    assert.strictEqual(soft.type, 'soft');
    assert.strictEqual(soft.text, 'one two three');

    // An untagged interim (no engine field) keeps the generic holdback
    // path and never re-fanouts latency: the next frame the consumer sees
    // is this segment's hardening final.
    await ingest({ text: 'one two three four five',
                   client: 'nemcli', segment: '1' });
    const final = await client.next();
    assert.strictEqual(final.type, 'final');
    assert.strictEqual(final.text, 'one two three four five');

    await fetch(`${BASE}/reset`, { method: 'POST' });
    const back = await client.next();
    assert.strictEqual(back.type, 'latency');
    assert.strictEqual(back.render, 'confirmed');
  } finally {
    client.ws.close();
    await postLatency('balanced');
  }
});

test('fastest: transcript waits for the display ack and prints the shown text', async () => {
  const client = openFinalized();   // skips latency frames; acks ride the same socket
  await client.opened;
  try {
    await postLatency('fastest');
    await ingest({ text: 'provider version of line' });
    const final = await client.next();
    assert.strictEqual(final.type, 'final');
    assert.strictEqual(final.text, 'provider version of line');

    // Not yet in transcript.txt: the settle is waiting on the shown ack.
    await new Promise((r) => setTimeout(r, 150));
    let contents = transcriptText();
    assert.ok(!contents.includes('provider version of line'));

    // The display acks with its effective text (a frozen word differed) —
    // THAT is what the transcript prints.
    client.ws.send(JSON.stringify({ type: 'shown', id: final.id,
                                    text: 'display version of line' }));
    await new Promise((r) => setTimeout(r, 200));
    contents = transcriptText();
    assert.ok(contents.includes('display version of line'));
    assert.ok(!contents.includes('provider version of line'));
  } finally {
    client.ws.close();
    await postLatency('balanced');
  }
});

test('fastest: out-of-order acks still release finals in spoken order', async () => {
  const client = openFinalized();
  await client.opened;
  try {
    await postLatency('fastest');
    await ingest({ text: 'first spoken line' });
    const first = await client.next();
    await ingest({ text: 'second spoken line' });
    const second = await client.next();

    // Ack the SECOND final first: nothing may release past the pending first.
    client.ws.send(JSON.stringify({ type: 'shown', id: second.id,
                                    text: 'second shown line' }));
    await new Promise((r) => setTimeout(r, 150));
    assert.ok(!transcriptText().includes('second shown line'));

    client.ws.send(JSON.stringify({ type: 'shown', id: first.id,
                                    text: 'first shown line' }));
    await new Promise((r) => setTimeout(r, 200));
    const contents = transcriptText();
    assert.match(contents, /first shown line\n[\s\S]*second shown line/);
  } finally {
    client.ws.close();
    await postLatency('balanced');
  }
});

test('fastest: a missing ack falls back to the provider text after the timeout', async () => {
  const client = openFinalized();
  await client.opened;
  try {
    await postLatency('fastest');
    await ingest({ text: 'unacked provider line' });
    const final = await client.next();
    assert.strictEqual(final.type, 'final');
    // No ack: after DOTIFY_SHOWN_ACK_MS (2s in tests) the provider text lands.
    await new Promise((r) => setTimeout(r, 2400));
    assert.ok(transcriptText().includes('unacked provider line'));
  } finally {
    client.ws.close();
    await postLatency('balanced');
  }
});

test('balanced: finals settle immediately, acks are ignored', async () => {
  const client = openFinalized();
  await client.opened;
  try {
    await ingest({ text: 'immediate balanced line' });
    const final = await client.next();
    await new Promise((r) => setTimeout(r, 150));
    assert.ok(transcriptText().includes('immediate balanced line'));
    // A stray ack for an already-settled final changes nothing and crashes nothing.
    client.ws.send(JSON.stringify({ type: 'shown', id: final.id,
                                    text: 'should never print' }));
    await new Promise((r) => setTimeout(r, 150));
    assert.ok(!transcriptText().includes('should never print'));
  } finally {
    client.ws.close();
  }
});

test('fastest: /reset settles the soft segments it hardens before truncating', async () => {
  const client = openFinalized();
  await client.opened;
  try {
    await postLatency('fastest');
    await ingest({ interim: true, client: 'resetfast', segment: '1',
                   text: 'orphaned words left soft before the reset' });
    assert.strictEqual((await client.next()).type, 'soft');
    const reset = await fetch(`${BASE}/reset`, { method: 'POST' });
    assert.strictEqual(reset.status, 204);
    assert.strictEqual((await client.next()).type, 'final');
    // Unacked, the orphan would settle after the ack timeout into the new file.
    await new Promise((r) => setTimeout(r, 2400));
    assert.ok(!transcriptText().includes('orphaned words'),
      `the fresh transcript must not inherit pre-reset text:\n${transcriptText()}`);
  } finally {
    client.ws.close();
    await postLatency('balanced');
  }
});
