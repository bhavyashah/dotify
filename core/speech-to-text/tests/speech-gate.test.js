// The shared gap gate (providers/speech-gate.js): the one state machine that
// bounds finalized latency for every provider. These tests pin the behavior
// the three adapters relied on when their pasted copies were consolidated.

const { test } = require('node:test');
const assert = require('node:assert');
const { createSpeechGate, MIN_SEGMENT_MS, MAX_SEGMENT_MS } = require('../providers/speech-gate');

// One 100 ms chunk of PCM16 mono @ 24 kHz (2400 samples).
function chunk(amplitude) {
  const buf = Buffer.alloc(2400 * 2);
  for (let i = 0; i < 2400; i++) buf.writeInt16LE(amplitude, i * 2);
  return buf;
}
const LOUD = chunk(8000);   // rms ~0.24, far above the speech threshold
const QUIET = chunk(0);

function feedMany(gate, buf, count) {
  const verdicts = [];
  for (let i = 0; i < count; i++) verdicts.push(gate.feed(buf));
  return verdicts;
}

test('unbroken speech forces exactly at the hard cap', () => {
  const gate = createSpeechGate();
  const verdicts = feedMany(gate, LOUD, MAX_SEGMENT_MS / 100);
  assert.strictEqual(verdicts[verdicts.length - 1], 'force');
  assert.ok(verdicts.slice(0, -1).every((v) => v === null));
});

test('an inter-word gap forces once past the minimum', () => {
  const gate = createSpeechGate();
  assert.ok(feedMany(gate, LOUD, 14).every((v) => v === null)); // 1400 ms speech
  assert.strictEqual(gate.feed(QUIET), null);   // 1500 ms, 100 ms silence
  assert.strictEqual(gate.feed(QUIET), null);   // 200 ms silence: below gap
  assert.strictEqual(gate.feed(QUIET), 'force'); // 300 ms silence past min
});

test('a gap below the minimum segment length never forces', () => {
  const gate = createSpeechGate();
  feedMany(gate, LOUD, 5);                       // 500 ms speech
  const verdicts = feedMany(gate, QUIET, 9);     // long gap, but segment short
  assert.ok(verdicts.every((v) => v === null));
  assert.ok(gate.elapsedMs < MIN_SEGMENT_MS);
});

test('pure silence reports silence at the cap, never force', () => {
  const gate = createSpeechGate();
  const verdicts = feedMany(gate, QUIET, MAX_SEGMENT_MS / 100 + 5);
  assert.ok(!verdicts.includes('force'));
  assert.strictEqual(verdicts[MAX_SEGMENT_MS / 100 - 1], 'silence');
  assert.strictEqual(gate.sawSpeech, false);
});

test('vendor-turns gate never forces; a finite silence clock still discards', () => {
  // The accurate preset's gates: Infinity bounds mean the vendor owns the
  // turn boundaries, and OpenAI's variant keeps the silence discard (its
  // hallucination guard) on its own finite clock.
  const gate = createSpeechGate({ minSegmentMs: Infinity,
                                  maxSegmentMs: Infinity,
                                  silenceDiscardMs: 2500 });
  assert.ok(feedMany(gate, LOUD, 100).every((v) => v === null)); // 10 s speech
  assert.ok(gate.sawSpeech);
  gate.reset();
  const verdicts = feedMany(gate, QUIET, 30);
  assert.ok(!verdicts.includes('force'));
  assert.strictEqual(verdicts[24], 'silence'); // 2500 ms of pure silence
});

test('vendor-turns gate without a silence clock never reports silence', () => {
  const gate = createSpeechGate({ minSegmentMs: Infinity,
                                  maxSegmentMs: Infinity });
  const verdicts = feedMany(gate, QUIET, 60);
  assert.ok(verdicts.every((v) => v === null));
});

test('reset starts a fresh segment', () => {
  const gate = createSpeechGate();
  feedMany(gate, LOUD, MAX_SEGMENT_MS / 100);    // forced
  gate.reset();
  assert.strictEqual(gate.elapsedMs, 0);
  assert.strictEqual(gate.sawSpeech, false);
  assert.ok(feedMany(gate, LOUD, 14).every((v) => v === null));
});

test('speech after silence within a segment clears the gap counter', () => {
  const gate = createSpeechGate();
  feedMany(gate, LOUD, 14);
  feedMany(gate, QUIET, 2);      // 200 ms gap — almost enough
  gate.feed(LOUD);               // speech resumes: gap counter clears
  assert.strictEqual(gate.feed(QUIET), null); // a new gap starts from zero
});

test('a long quiet stretch never force-cuts the next utterance’s first word', () => {
  // The segment clock starts at the first speech chunk: counting the
  // silence before it would force the next word out the moment it began.
  const gate = createSpeechGate();
  feedMany(gate, QUIET, 40);                     // 4 s of room silence
  assert.strictEqual(gate.feed(LOUD), null);     // first word: no force
  const verdicts = feedMany(gate, LOUD, MAX_SEGMENT_MS / 100 - 1);
  assert.strictEqual(verdicts[verdicts.length - 1], 'force'); // full cap later
  assert.ok(verdicts.slice(0, -1).every((v) => v === null));
});

test('conversation-style overrides move the bounds', () => {
  const gate = createSpeechGate({ minSegmentMs: 400, maxSegmentMs: 600 });
  assert.strictEqual(gate.feed(LOUD), null);
  feedMany(gate, LOUD, 2);                     // 300 ms
  assert.strictEqual(gate.feed(QUIET), null);  // 400 ms but no gap yet
  assert.strictEqual(gate.feed(QUIET), null);  // 200 ms silence < 250 gap
  assert.strictEqual(gate.feed(QUIET), 'force'); // gap satisfied past min
});
