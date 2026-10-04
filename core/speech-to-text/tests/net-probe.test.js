// /api/net-probe (connection-loss recovery) and
// the mock provider's die-file seam that stands in for a vendor outage.
// Own server boot on port 8808: the probe seam needs DOTIFY_MOCK_DIE_FILE,
// which the shared server.test.js boot deliberately does not set.

const { test, before, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const os = require('node:os');
const fs = require('node:fs');
const WebSocket = require('ws');
const rig = require('./helpers');

const PORT = 8808;
const BASE = `http://127.0.0.1:${PORT}`;
const DIE_FILE = path.join(os.tmpdir(), `dotify-mock-die-${PORT}`);

let child;

before(async () => {
  child = rig.startServer({
    port: PORT,
    clean: [DIE_FILE],
    env: {
      DOTIFY_ENV_FILE: path.join(os.tmpdir(), `dotify-probe-${PORT}.env`),
      DOTIFY_TRANSCRIPT_FILE: path.join(os.tmpdir(), `dotify-probe-${PORT}.txt`),
      DOTIFY_DICTIONARY_FILE: path.join(os.tmpdir(), `dotify-probe-dict-${PORT}.json`),
      DOTIFY_MODELS_DIR: path.join(os.tmpdir(), `dotify-probe-models-${PORT}`),
      DOTIFY_MOCK_PROVIDER: '1',
      DOTIFY_MOCK_KEY: 'mock-key',
      DOTIFY_MOCK_DIE_FILE: DIE_FILE,
    },
    tag: 'probe-server',
  });
  await rig.waitForListening(BASE);
});

after(() => {
  if (child) child.kill();
  try { fs.rmSync(DIE_FILE, { force: true }); } catch {}
});

test('an unknown engine is a 400, not a probe', async () => {
  const response = await fetch(`${BASE}/api/net-probe?engine=nonsense`);
  assert.strictEqual(response.status, 400);
});

test('the mock vendor probes reachable until the die-file exists, then unreachable', async () => {
  let response = await fetch(`${BASE}/api/net-probe?engine=mock`);
  assert.strictEqual(response.status, 200);
  assert.strictEqual((await response.json()).reachable, true);

  fs.writeFileSync(DIE_FILE, 'outage');
  response = await fetch(`${BASE}/api/net-probe?engine=mock`);
  assert.strictEqual((await response.json()).reachable, false);

  fs.rmSync(DIE_FILE);
  response = await fetch(`${BASE}/api/net-probe?engine=mock`);
  assert.strictEqual((await response.json()).reachable, true);
});

test('a running mock session dies with a provider-style error when the vendor "goes down"', async () => {
  // The death end of the ladder: the same {type:'error'} relay a real
  // provider's watchdog verdict rides to the page.
  const ws = new WebSocket(`ws://127.0.0.1:${PORT}/audio?provider=mock`);
  const messages = [];
  const errorMessage = new Promise((resolve) => {
    ws.on('message', (raw) => {
      const msg = JSON.parse(raw.toString());
      messages.push(msg);
      if (msg.type === 'error') resolve(msg);
    });
  });
  await new Promise((resolve) => ws.on('open', resolve));
  await new Promise((r) => setTimeout(r, 400)); // session ready, no die-file: alive
  assert.ok(!messages.some((m) => m.type === 'error'));

  fs.writeFileSync(DIE_FILE, 'outage');
  try {
    const msg = await Promise.race([
      errorMessage,
      new Promise((_, reject) => setTimeout(() => reject(new Error('no error within 3s')), 3000)),
    ]);
    assert.match(msg.message, /mock outage/);
  } finally {
    fs.rmSync(DIE_FILE, { force: true });
    ws.close();
  }
});
