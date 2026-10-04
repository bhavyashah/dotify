// The /audio finals replay (?last_seq=): a reconnecting page gets the
// provider finals it missed, never /ingest finals (the Windows overlay
// already put those in the box), and nothing at all without last_seq.

const { test, before, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const os = require('node:os');
const WebSocket = require('ws');
const rig = require('./helpers');

const PORT = 8806;
const BASE = `http://127.0.0.1:${PORT}`;

let child;

before(async () => {
  child = rig.startServer({
    port: PORT,
    env: {
      DOTIFY_ENV_FILE: path.join(os.tmpdir(), `dotify-replay-${PORT}.env`),
      DOTIFY_TRANSCRIPT_FILE: path.join(os.tmpdir(), `dotify-replay-${PORT}.txt`),
      DOTIFY_DICTIONARY_FILE: path.join(os.tmpdir(), `dotify-replay-dict-${PORT}.json`),
      DOTIFY_MODELS_DIR: path.join(os.tmpdir(), `dotify-replay-models-${PORT}`),
      DOTIFY_MOCK_PROVIDER: '1',
      DOTIFY_MOCK_KEY: 'mock-key',
    },
    tag: 'replay-server',
  });
  await rig.waitForListening(BASE);
});

after(() => { if (child) child.kill(); });

// Open a mock /audio session and collect its messages up to 'ready'.
async function openSession(params) {
  const ws = new WebSocket(`ws://127.0.0.1:${PORT}/audio?provider=mock${params}`);
  const beforeReady = [];
  const later = [];
  let ready;
  const readyPromise = new Promise((resolve) => { ready = resolve; });
  let isReady = false;
  ws.on('message', (raw) => {
    const msg = JSON.parse(raw.toString());
    if (msg.type === 'ready') { isReady = true; ready(); return; }
    (isReady ? later : beforeReady).push(msg);
  });
  await readyPromise;
  return { ws, beforeReady, later };
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

test('replay carries missed provider finals only, and only when asked', async () => {
  const first = await openSession('&last_seq=0');
  assert.deepStrictEqual(first.beforeReady, []);
  first.ws.send(Buffer.from('MOCKFINAL:spoken through the provider', 'latin1'));
  await sleep(200);
  const final = first.later.find((m) => m.type === 'final');
  assert.ok(final, 'the provider final reaches the page');
  assert.strictEqual(final.text, 'spoken through the provider');
  assert.ok(Number.isInteger(final.seq));
  first.ws.close();
  await sleep(200);

  const ingested = await fetch(`${BASE}/ingest`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text: 'spoken through the offline engine' }),
  });
  assert.strictEqual(ingested.status, 200);

  const second = await openSession('&last_seq=0');
  assert.deepStrictEqual(second.beforeReady.map((m) => m.text),
    ['spoken through the provider']);
  second.ws.close();

  const caughtUp = await openSession(`&last_seq=${final.seq}`);
  assert.deepStrictEqual(caughtUp.beforeReady, []);
  caughtUp.ws.close();

  const bare = await openSession('');
  assert.deepStrictEqual(bare.beforeReady, []);
  bare.ws.close();
});
