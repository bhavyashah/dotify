// The speech/silence gap gate behind every provider's forced-endpoint
// backstop. Vendor endpointing alone lets continuous speech run unfinalized
// indefinitely, so once a segment has run past MIN_SEGMENT_MS it is forced at
// the next short pause, or unconditionally at MAX_SEGMENT_MS.
//
// speaker-id/audio.py mirrors SPEECH_RMS at int16 scale; keep them in sync.
//
// Input: PCM16 mono at 24 kHz.
//
//   const gate = createSpeechGate();          // or override the constants
//   const verdict = gate.feed(pcmChunk);      // per chunk, in stream order
//     'force'    finalize the segment now (send the provider's
//                ForceEndpoint / Finalize / commit), then gate.reset()
//     'silence'  the segment reached the discard clock without any speech;
//                only OpenAI acts on it (it clears the buffer)
//     null       keep streaming
//   gate.reset()                              // segment finalized upstream
//   gate.sawSpeech / gate.elapsedMs           // all audio since the reset

const SPEECH_RMS = 0.008;     // normalized RMS above this counts as speech
const GAP_SILENCE_MS = 250;   // an inter-word gap long enough to force at
const MIN_SEGMENT_MS = 1500;  // don't force a segment shorter than this
const MAX_SEGMENT_MS = 2500;  // hard cap: force even with no gap (latency)

// Normalized RMS of one PCM16LE mono chunk (also used by cost-gate.js).
function chunkRms(pcm) {
  const n = Math.floor(pcm.length / 2);
  let sum = 0;
  if (pcm.byteOffset % 2 === 0) {
    // A typed view is much faster than readInt16LE in this per-chunk loop.
    // Node runs only on little-endian platforms, matching PCM16LE.
    const view = new Int16Array(pcm.buffer, pcm.byteOffset, n);
    for (let i = 0; i < n; i++) sum += (view[i] / 32768) ** 2;
  } else {
    // A view needs 2-byte alignment; odd-offset Buffer slices fall back.
    for (let i = 0; i < n; i++) sum += (pcm.readInt16LE(i * 2) / 32768) ** 2;
  }
  return Math.sqrt(sum / Math.max(1, n));
}

function createSpeechGate({
  speechRms = SPEECH_RMS,
  gapSilenceMs = GAP_SILENCE_MS,
  minSegmentMs = MIN_SEGMENT_MS,
  maxSegmentMs = MAX_SEGMENT_MS,
  // The 'silence' verdict's clock; kept separate so it can stay finite when
  // maxSegmentMs is Infinity.
  silenceDiscardMs = maxSegmentMs,
} = {}) {
  let silenceMs = 0;   // trailing silence since the last speech chunk
  let speechMs = 0;    // segment clock — runs only once speech has started
  let bufferedMs = 0;  // all audio since the last reset
  let sawSpeech = false;

  function reset() {
    silenceMs = 0;
    speechMs = 0;
    bufferedMs = 0;
    sawSpeech = false;
  }

  function feed(pcm) {
    const n = Math.floor(pcm.length / 2);
    const rms = chunkRms(pcm);
    const chunkMs = n / 24; // 24 samples per ms at 24 kHz
    bufferedMs += chunkMs;

    if (rms >= speechRms) {
      sawSpeech = true;
      silenceMs = 0;
    } else if (sawSpeech) {
      silenceMs += chunkMs;
    }
    // The segment clock starts at the first speech chunk; counting the
    // silence before it would force the next utterance's first word out as
    // a fragment.
    if (sawSpeech) speechMs += chunkMs;

    const gapForce = sawSpeech && speechMs >= minSegmentMs
      && silenceMs >= gapSilenceMs;
    if (gapForce || (sawSpeech && speechMs >= maxSegmentMs)) return 'force';
    if (!sawSpeech && bufferedMs >= silenceDiscardMs) return 'silence';
    return null;
  }

  return {
    feed,
    reset,
    get sawSpeech() { return sawSpeech; },
    get elapsedMs() { return bufferedMs; },
  };
}

module.exports = {
  createSpeechGate,
  chunkRms,
  SPEECH_RMS,
  MIN_SEGMENT_MS,
  MAX_SEGMENT_MS,
};
