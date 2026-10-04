// Pre-open audio retention (providers/preopen-buffer.js): the bounded parking
// buffer every adapter flushes through its normal send path on socket 'open',
// so speech during the connect window is not discarded. These tests pin
// the ordering, the drop-oldest bound, and the honest drop reporting.

const { test } = require('node:test');
const assert = require('node:assert');
const { createPreOpenBuffer, MAX_BUFFERED_MS } = require('../providers/preopen-buffer');

// One 100 ms chunk of PCM16 mono @ 24 kHz (2400 samples), tagged by value so
// ordering is observable.
function chunk(tag) {
  const buf = Buffer.alloc(2400 * 2);
  buf.writeInt16LE(tag, 0);
  return buf;
}

function tagsOf(delivered) {
  return delivered.map((pcm) => pcm.readInt16LE(0));
}

test('flush delivers every buffered chunk in arrival order', () => {
  const buffer = createPreOpenBuffer();
  for (const tag of [1, 2, 3]) buffer.push(chunk(tag));
  const delivered = [];
  const { bufferedMs, droppedMs } = buffer.flush((pcm) => delivered.push(pcm));
  assert.deepStrictEqual(tagsOf(delivered), [1, 2, 3]);
  assert.strictEqual(bufferedMs, 300);
  assert.strictEqual(droppedMs, 0);
});

test('flush empties the buffer — a second flush delivers nothing', () => {
  const buffer = createPreOpenBuffer();
  buffer.push(chunk(1));
  buffer.flush(() => {});
  const delivered = [];
  const { bufferedMs } = buffer.flush((pcm) => delivered.push(pcm));
  assert.strictEqual(delivered.length, 0);
  assert.strictEqual(bufferedMs, 0);
});

test('over the cap the OLDEST audio is dropped, newest kept', () => {
  const buffer = createPreOpenBuffer(300); // 3 chunks' worth
  for (const tag of [1, 2, 3, 4, 5]) buffer.push(chunk(tag));
  const delivered = [];
  const { bufferedMs, droppedMs } = buffer.flush((pcm) => delivered.push(pcm));
  assert.deepStrictEqual(tagsOf(delivered), [3, 4, 5]);
  assert.strictEqual(bufferedMs, 300);
  assert.strictEqual(droppedMs, 200); // chunks 1 and 2, reported not silent
});

test('a single chunk larger than the cap is still delivered', () => {
  // The bound evicts down to at least one chunk — it must never wedge into
  // delivering nothing because one chunk alone exceeds the cap.
  const buffer = createPreOpenBuffer(50); // cap below one 100 ms chunk
  buffer.push(chunk(1));
  buffer.push(chunk(2));
  const delivered = [];
  const { droppedMs } = buffer.flush((pcm) => delivered.push(pcm));
  assert.deepStrictEqual(tagsOf(delivered), [2]);
  assert.strictEqual(droppedMs, 100);
});

test('the default cap holds well over a typical connect window', () => {
  // Connects measured at ~300–1000 ms; the default must retain at least that.
  assert.ok(MAX_BUFFERED_MS >= 1000);
  const buffer = createPreOpenBuffer();
  for (let i = 0; i < 10; i++) buffer.push(chunk(i)); // a 1 s connect
  const { droppedMs } = buffer.flush(() => {});
  assert.strictEqual(droppedMs, 0);
});

test('drop accounting resets with the flush', () => {
  const buffer = createPreOpenBuffer(100);
  buffer.push(chunk(1));
  buffer.push(chunk(2)); // evicts 1
  buffer.flush(() => {});
  buffer.push(chunk(3));
  const { droppedMs } = buffer.flush(() => {});
  assert.strictEqual(droppedMs, 0); // the earlier drop was already reported
});
