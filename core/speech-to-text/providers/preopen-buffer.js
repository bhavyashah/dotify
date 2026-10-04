// Pre-open audio retention. A keyed provider's socket spends ~300-1000 ms
// connecting after the page has started streaming; chunks from that window
// are parked here and, on 'open', flushed through the provider's normal send
// path ahead of live audio, so the speech gate sees one continuous stream
// (its clocks count samples, not wall time). The cost gate reuses it as its
// pre-roll ring.
//
// Bounded, dropping the oldest audio first; flush() reports what was dropped
// so the provider can log it.

const MAX_BUFFERED_MS = 1500; // PCM16 @ 24 kHz mono ⇒ 48 bytes/ms ≈ 72 KB

function createPreOpenBuffer(maxMs = MAX_BUFFERED_MS) {
  const maxBytes = maxMs * 48;
  let chunks = [];
  let bytes = 0;
  let droppedBytes = 0;

  function push(pcm) {
    chunks.push(pcm);
    bytes += pcm.length;
    while (bytes > maxBytes && chunks.length > 1) {
      const evicted = chunks.shift();
      bytes -= evicted.length;
      droppedBytes += evicted.length;
    }
  }

  // Hand every retained chunk, in order, to deliver() and empty the buffer.
  // Returns { bufferedMs, droppedMs } for the adapter's log line.
  function flush(deliver) {
    const held = chunks;
    const stats = {
      bufferedMs: Math.round(bytes / 48),
      droppedMs: Math.round(droppedBytes / 48),
    };
    chunks = [];
    bytes = 0;
    droppedBytes = 0;
    for (const pcm of held) deliver(pcm);
    return stats;
  }

  return { push, flush };
}

module.exports = { createPreOpenBuffer, MAX_BUFFERED_MS };
