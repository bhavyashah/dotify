// The cost gate: don't pay for silence.
//
// Keyed providers bill the audio we stream (Deepgram, ElevenLabs) or the time
// a session is open (AssemblyAI), and much of a conversation is silence. This
// gate sits on the capture side of every keyed session and decides, chunk by
// chunk, what to forward:
//
//   OPEN   forward the chunk. Trailing silence is forwarded too until it has
//          lasted HANGOVER_MS, so pauses reach the vendor's endpointing intact.
//   GATED  withhold the chunk into a bounded pre-roll ring. The first speech
//          chunk flushes the ring (up to PRE_ROLL_MS of lead-in) ahead of
//          itself and reopens the gate, so onsets are never chopped.
//
// The gate starts GATED: a session opened into a quiet room costs nothing
// until somebody talks. withheldStreakMs lets the caller close an idle socket
// altogether (gated-session.js).
//
// Input: PCM16 mono at 24 kHz, the same chunks that feed speech-gate.js.
//
//   const gate = createCostGate();
//   const { forward, reopened } = gate.feed(pcmChunk);
//     forward   chunks to send now ([chunk], [], or [ring..., chunk] on onset)
//     reopened  this chunk flipped GATED to OPEN
//   gate.isOpen, gate.withheldStreakMs
//   gate.stats  { fedMs, forwardedMs, suppressedMs }

const { chunkRms } = require('./speech-gate');
const { createPreOpenBuffer } = require('./preopen-buffer');

// Lower than speech-gate.js's SPEECH_RMS on purpose: misreading quiet speech
// as silence there only moves a segment boundary, here it drops the words.
// On the AMI benchmark meeting, 0.008 withheld 4.2% of reference words and
// 0.003 withheld none, while a quiet room's floor sits well below 0.003.
const COST_SPEECH_RMS = 0.003;

// HANGOVER_MS must comfortably exceed the slowest vendor endpointing
// (~1.3 s), or the last utterance before a lull would not finalize until the
// next onset. Above that it trades billed seconds for gate churn; 5 s sits at
// the churn elbow measured with tools/cost-gate-sweep.js.
const HANGOVER_MS = 5000;    // trailing silence forwarded before gating
const PRE_ROLL_MS = 1000;    // onset flush: lead-in context kept while gated
const CLOSE_AFTER_MS = 120000; // default idle time before the socket closes

function createCostGate({
  hangoverMs = HANGOVER_MS,
  preRollMs = PRE_ROLL_MS,
  speechRms = COST_SPEECH_RMS,
} = {}) {
  const ring = createPreOpenBuffer(preRollMs);
  let open = false;
  let trailingSilenceMs = 0; // OPEN only: silence since the last speech chunk
  let withheldStreakMs = 0;  // GATED only: how long we've been withholding
  let fedMs = 0;
  let forwardedMs = 0;

  function feed(pcm) {
    const chunkMs = Math.floor(pcm.length / 2) / 24;
    const speech = chunkRms(pcm) >= speechRms;
    fedMs += chunkMs;

    if (open) {
      if (speech) {
        trailingSilenceMs = 0;
      } else {
        trailingSilenceMs += chunkMs;
        if (trailingSilenceMs >= hangoverMs) {
          // The hangover is spent; this chunk is the first withheld one.
          open = false;
          trailingSilenceMs = 0;
          withheldStreakMs = chunkMs;
          ring.push(pcm);
          return { forward: [], reopened: false };
        }
      }
      forwardedMs += chunkMs;
      return { forward: [pcm], reopened: false };
    }

    if (!speech) {
      withheldStreakMs += chunkMs;
      ring.push(pcm);
      return { forward: [], reopened: false };
    }

    // Onset: reopen and flush the pre-roll ahead of the speech chunk.
    open = true;
    withheldStreakMs = 0;
    const forward = [];
    ring.flush((held) => forward.push(held));
    forward.push(pcm);
    for (const buf of forward) forwardedMs += Math.floor(buf.length / 2) / 24;
    return { forward, reopened: true };
  }

  return {
    feed,
    get isOpen() { return open; },
    get withheldStreakMs() { return withheldStreakMs; },
    get stats() {
      return {
        fedMs: Math.round(fedMs),
        forwardedMs: Math.round(forwardedMs),
        suppressedMs: Math.round(fedMs - forwardedMs),
      };
    },
  };
}

module.exports = {
  createCostGate,
  COST_SPEECH_RMS,
  HANGOVER_MS,
  PRE_ROLL_MS,
  CLOSE_AFTER_MS,
};
