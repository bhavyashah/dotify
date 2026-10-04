// /ingest robustness: a proper 413 for oversized bodies, and the
// abandonment safety net — a conservative clock that hardens a soft segment
// whose sender died mid-utterance, without ever touching a live revision
// flow.
const { test, before, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const os = require('node:os');
const rig = require('./helpers');

const PORT = 8804;
const BASE = `http://127.0.0.1:${PORT}`;

let child;

// The abandonment default is a deliberate 30 s; tests run it at 0.8 s via
// the env knob (which is itself under test), and the last test at 0
// (disabled).
const ABANDON_S = '0.8';

function startServer(extraEnv = {}) {
  const tmpTranscript = path.join(os.tmpdir(), `dotify-transcript-${PORT}.txt`);
  const tmpEnv = path.join(os.tmpdir(), `dotify-provider-config-${PORT}.env`);
  child = rig.startServer({
    port: PORT,
    clean: [tmpTranscript, tmpEnv],
    env: {
      DOTIFY_ENV_FILE: tmpEnv,
      DOTIFY_TRANSCRIPT_FILE: tmpTranscript,
      DOTIFY_DICTIONARY_FILE: path.join(os.tmpdir(), `dotify-dictionary-${PORT}.json`),
      DOTIFY_MODELS_DIR: path.join(os.tmpdir(), `dotify-models-${PORT}`),
      DOTIFY_SOFT_REVISE_MS: '0',
      DOTIFY_INGEST_ABANDON_S: ABANDON_S,
      ...extraEnv,
    },
  });
}

const waitForListening = () => rig.waitForListening(BASE);
const openFinalized = () => rig.openFinalized(PORT);
const ingest = rig.makeIngest(BASE);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

before(async () => {
  startServer();
  await waitForListening();
});
after(() => { if (child) child.kill(); });

// --- Oversized bodies: 413, not a socket reset ------------------------------

test('413: an oversized body gets the JSON error shape and the server lives on', async () => {
  const r = await ingest({ text: 'a'.repeat(1_100_000) });
  assert.strictEqual(r.status, 413);
  assert.match((await r.json()).error, /1000000 bytes/);
  const ok = await ingest({ text: 'alive after the 413' });
  assert.strictEqual(ok.status, 200);
  assert.deepStrictEqual(await ok.json(), { text: 'alive after the 413' });
});

// --- Abandonment safety net -------------------------------------------------

test('abandonment: an untouched soft segment hardens by itself', async () => {
  const client = openFinalized();
  await client.opened;
  try {
    await ingest({ interim: true, client: 'ab1', segment: '0',
                   text: 'these words were abandoned mid line' });
    const soft = await client.next();
    assert.strictEqual(soft.type, 'soft');
    assert.strictEqual(soft.text, 'these words were abandoned'); // 2-word holdback

    // No final ever comes. The net hardens the FULL last hypothesis (not
    // just the emitted prefix) in place — same id, so consumers splice.
    const hardened = await client.next(2500);
    assert.strictEqual(hardened.type, 'final');
    assert.strictEqual(hardened.id, soft.id);
    assert.strictEqual(hardened.text, 'these words were abandoned mid line');
    await sleep(200);
    assert.strictEqual(client.pending.length, 0);
  } finally {
    client.ws.close();
  }
});

test('abandonment: a hypothesis too short to stream soft still hardens', async () => {
  const client = openFinalized();
  await client.opened;
  try {
    // Two words sit entirely inside the holdback: nothing streams soft,
    // but the words were spoken and must not vanish with their sender.
    await ingest({ interim: true, client: 'ab-short', segment: '0', text: 'lone words' });
    const hardened = await client.next(2500);
    assert.strictEqual(hardened.type, 'final');
    assert.strictEqual(hardened.text, 'lone words');
  } finally {
    client.ws.close();
  }
});

test('abandonment: active revision re-arms the clock — a live flow is never cut off', async () => {
  const client = openFinalized();
  await client.opened;
  try {
    // Revise every ~200 ms for well past the 0.8 s window: the clock
    // re-arms on every touch, so nothing may harden early.
    for (let i = 0; i < 6; i++) {
      await ingest({ interim: true, client: 'ab2', segment: '0',
                     text: `steadily growing hypothesis word${i} tail tip` });
      await sleep(200);
    }
    let sawFinal = false;
    const frames = [];
    while (client.pending.length) {
      const msg = client.pending.shift();
      frames.push(msg);
      if (msg.type === 'final') sawFinal = true;
    }
    assert.strictEqual(sawFinal, false,
      `net fired during active revision: ${JSON.stringify(frames)}`);

    // The armed-but-unfired net is invisible on the wire.
    const final = await ingest({ text: 'steadily growing hypothesis word5 tail tip',
                                 client: 'ab2', segment: '0' });
    assert.strictEqual(final.status, 200);
    assert.deepStrictEqual(await final.json(),
                           { text: 'steadily growing hypothesis word5 tail tip' });
    const finalMsg = await client.next();
    assert.strictEqual(finalMsg.type, 'final');

    // And the disarm is real: nothing fires after the final.
    await sleep(1100);
    assert.strictEqual(client.pending.length, 0);
  } finally {
    client.ws.close();
  }
});

test('abandonment: withdrawal disarms the net with the segment', async () => {
  const client = openFinalized();
  await client.opened;
  try {
    await ingest({ interim: true, client: 'ab4', segment: '0',
                   text: 'tentative words about to vanish' });
    const soft = await client.next();
    await ingest({ interim: true, client: 'ab4', segment: '0', text: '' });
    const gone = await client.next();
    assert.strictEqual(gone.type, 'revise');
    assert.strictEqual(gone.revise, soft.id);
    assert.strictEqual(gone.text, '');
    // Past the abandonment window: the withdrawn segment stays withdrawn.
    await sleep(1100);
    assert.strictEqual(client.pending.length, 0);
  } finally {
    client.ws.close();
  }
});

test('abandonment: /reset hardens the open segment once and disarms its clock', async () => {
  const client = openFinalized();
  await client.opened;
  try {
    await ingest({ interim: true, client: 'ab6', segment: '0',
                   text: 'words cut short by a page reset' });
    const soft = await client.next();
    const r = await fetch(`${BASE}/reset`, { method: 'POST' });
    assert.strictEqual(r.status, 204);
    let msg = await client.next();
    while (msg.type !== 'final') msg = await client.next();
    assert.strictEqual(msg.id, soft.id);
    await sleep(1100);
    assert.ok(!client.pending.some((m) => m.type === 'final'),
      'the abandonment clock fired after /reset already hardened the segment');
  } finally {
    client.ws.close();
  }
});

// Keep LAST: restart with the net disabled to pin the knob's off position.
test('abandonment: DOTIFY_INGEST_ABANDON_S=0 disables the net entirely', async () => {
  const gone = new Promise((resolve) => { child.once('exit', resolve); child.kill(); });
  await gone;
  startServer({ DOTIFY_INGEST_ABANDON_S: '0' });
  await waitForListening();

  const client = openFinalized();
  await client.opened;
  try {
    await ingest({ interim: true, client: 'ab5', segment: '0',
                   text: 'soft words with no timeout at all' });
    const soft = await client.next();
    assert.strictEqual(soft.type, 'soft');
    await sleep(1200); // far past the (disabled) 0.8 s window
    assert.strictEqual(client.pending.length, 0); // nothing ever hardened
    // The segment is still open: its real final hardens it as always.
    const final = await ingest({ text: 'soft words with no timeout at all',
                                 client: 'ab5', segment: '0' });
    assert.strictEqual(final.status, 200);
    const finalMsg = await client.next();
    assert.strictEqual(finalMsg.type, 'final');
    assert.strictEqual(finalMsg.id, soft.id);
  } finally {
    client.ws.close();
  }
});
