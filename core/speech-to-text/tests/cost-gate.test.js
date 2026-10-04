// The cost gate (providers/cost-gate.js): decides which capture chunks are
// worth paying a keyed provider for. These tests pin the two guarantees the
// design leans on: no onset is ever chopped (the pre-roll flush), and no
// live-conversation gap is ever gated (the hangover).

const { test } = require('node:test');
const assert = require('node:assert');
const { createCostGate, HANGOVER_MS, PRE_ROLL_MS } = require('../providers/cost-gate');

// One 100 ms chunk of PCM16 mono @ 24 kHz (2400 samples).
function chunk(amplitude) {
  const buf = Buffer.alloc(2400 * 2);
  for (let i = 0; i < 2400; i++) buf.writeInt16LE(amplitude, i * 2);
  return buf;
}
const LOUD = chunk(8000);
const QUIET = chunk(0);

function feedMany(gate, buf, count) {
  const results = [];
  for (let i = 0; i < count; i++) results.push(gate.feed(buf));
  return results;
}
function forwardedMs(results) {
  let ms = 0;
  for (const r of results) for (const b of r.forward) ms += b.length / 2 / 24;
  return ms;
}

test('starts gated: pre-speech room silence is never forwarded', () => {
  const gate = createCostGate();
  const results = feedMany(gate, QUIET, 100); // 10 s of quiet room
  assert.strictEqual(forwardedMs(results), 0);
  assert.strictEqual(gate.isOpen, false);
  assert.strictEqual(gate.stats.suppressedMs, 10000);
});

test('first speech flushes the pre-roll ahead of the onset chunk', () => {
  const gate = createCostGate();
  feedMany(gate, QUIET, 100);
  const r = gate.feed(LOUD);
  assert.strictEqual(r.reopened, true);
  // The flush carries up to PRE_ROLL_MS of lead-in plus the onset chunk,
  // oldest first, onset last.
  assert.strictEqual(forwardedMs([r]), PRE_ROLL_MS + 100);
  assert.strictEqual(r.forward[r.forward.length - 1], LOUD);
  assert.ok(gate.isOpen);
});

test('open gate forwards speech and short gaps untouched', () => {
  const gate = createCostGate();
  gate.feed(LOUD);
  const speech = feedMany(gate, LOUD, 20);            // 2 s speech
  const gap = feedMany(gate, QUIET, (HANGOVER_MS / 100) - 1); // just under
  const resumed = feedMany(gate, LOUD, 10);
  assert.ok([...speech, ...gap, ...resumed].every((r) => r.forward.length === 1));
  assert.ok(resumed.every((r) => r.reopened === false)); // never closed
});

test('silence past the hangover closes the gate; the hangover itself was paid', () => {
  const gate = createCostGate();
  gate.feed(LOUD);
  const results = feedMany(gate, QUIET, HANGOVER_MS / 100 + 50);
  const sent = results.filter((r) => r.forward.length > 0).length;
  // Chunks 1..(hangover/100 - 1) forwarded; the chunk that completes the
  // hangover and everything after it withheld.
  assert.strictEqual(sent, HANGOVER_MS / 100 - 1);
  assert.strictEqual(gate.isOpen, false);
});

test('speech resumption after a lull loses nothing: pre-roll + onset arrive', () => {
  const gate = createCostGate();
  gate.feed(LOUD);
  feedMany(gate, QUIET, HANGOVER_MS / 100 + 300);     // 30 s lull, gated
  const r = gate.feed(LOUD);
  assert.strictEqual(r.reopened, true);
  assert.strictEqual(forwardedMs([r]), PRE_ROLL_MS + 100);
  assert.strictEqual(r.forward[r.forward.length - 1], LOUD);
});

test('withheldStreakMs runs while gated and clears on reopen', () => {
  const gate = createCostGate();
  gate.feed(LOUD);
  feedMany(gate, QUIET, HANGOVER_MS / 100);           // gate just closed
  feedMany(gate, QUIET, 600);                          // +60 s withheld
  assert.strictEqual(gate.withheldStreakMs, 100 + 60000); // closing chunk too
  gate.feed(LOUD);
  assert.strictEqual(gate.withheldStreakMs, 0);
});

test('stats account every millisecond fed as forwarded or suppressed', () => {
  const gate = createCostGate();
  feedMany(gate, QUIET, 50);                           // 5 s pre-speech
  gate.feed(LOUD);                                     // flush (1 s + 0.1 s)
  feedMany(gate, LOUD, 9);                             // 0.9 s speech
  feedMany(gate, QUIET, HANGOVER_MS / 100 + 100);      // hangover + 10 s lull
  const { fedMs, forwardedMs: fwd, suppressedMs } = gate.stats;
  assert.strictEqual(fedMs, fwd + suppressedMs);
  // Billed: 1 s flush + 0.1 s onset + 0.9 s speech + (hangover - 0.1 s)
  assert.strictEqual(fwd, 1000 + 100 + 900 + HANGOVER_MS - 100);
});

test('a lone noise spike costs one flush plus one hangover, then re-gates', () => {
  const gate = createCostGate();
  gate.feed(LOUD);
  feedMany(gate, QUIET, HANGOVER_MS / 100 + 10);       // gated
  gate.feed(LOUD);                                     // door slam
  const after = feedMany(gate, QUIET, HANGOVER_MS / 100 + 10);
  assert.strictEqual(gate.isOpen, false);              // re-gated on its own
  const sent = after.filter((r) => r.forward.length > 0).length;
  assert.strictEqual(sent, HANGOVER_MS / 100 - 1);
});

test('overrides move the bounds', () => {
  const gate = createCostGate({ hangoverMs: 300, preRollMs: 200 });
  gate.feed(LOUD);
  feedMany(gate, QUIET, 3);                            // 300 ms: gate closes
  assert.strictEqual(gate.isOpen, false);
  feedMany(gate, QUIET, 20);
  const r = gate.feed(LOUD);
  assert.strictEqual(forwardedMs([r]), 200 + 100);     // trimmed pre-roll
});
