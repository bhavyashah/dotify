// Offline billed-fraction sweep for the cost gate (providers/cost-gate.js).
//
// Replays a 24 kHz PCM16 WAV through createCostGate at a grid of hangover
// values and reports, for each: the fraction of audio that would have been
// billed, how often the gate closed and reopened (churn), and the silence
// structure that drives both. Pure DSP — no provider is dialed, nothing is
// billed (the same offline-grid pattern as tools/stability-analysis.js).
//
//   node tools/cost-gate-sweep.js --audio meeting-24k.wav
//        [--hangovers 2000,3000,5000,8000,10000,15000,30000]
//
// The point of the sweep: HANGOVER_MS is chosen at the knee of the billed-
// fraction curve. Below the knee, shaving the hangover further buys little
// (conversational gaps are shorter than any plausible hangover) but eats
// into the trailing silence vendors use to endpoint (ElevenLabs finalizes
// on >= vad_silence_threshold_secs, AssemblyAI's acoustic fallback ~1.3 s
// — the hangover must comfortably exceed those, or the last utterance
// before a lull only finalizes at the next onset). Above the knee, every
// extra second is paid on every lull and buys nothing.

const { readWav } = require('./wav');
const { createCostGate, COST_SPEECH_RMS } = require('../providers/cost-gate');
const { chunkRms } = require('../providers/speech-gate');

const CHUNK_MS = 100; // the browser's batch size — what the gate sees live
const SAMPLE_RATE = 24000;

function parseArgs(argv) {
  const args = { hangovers: [2000, 3000, 5000, 8000, 10000, 15000, 30000] };
  for (let i = 2; i < argv.length; i++) {
    const flag = argv[i];
    if (flag === '--audio') args.audio = argv[++i];
    else if (flag === '--hangovers') {
      args.hangovers = argv[++i].split(',').map(Number);
    } else throw new Error(`unknown flag ${flag}`);
  }
  if (!args.audio) {
    console.error('Usage: node tools/cost-gate-sweep.js --audio <24k.wav> '
      + '[--hangovers ms,ms,...]');
    process.exit(1);
  }
  return args;
}

function main() {
  const args = parseArgs(process.argv);
  const wav = readWav(args.audio);
  if (wav.sampleRate !== SAMPLE_RATE) {
    throw new Error(`${args.audio} is ${wav.sampleRate} Hz; expected 24 kHz `
      + '(resample with ffmpeg -ar 24000 -ac 1 -c:a pcm_s16le)');
  }
  const chunkBytes = (SAMPLE_RATE / 1000) * CHUNK_MS * 2;
  const totalMs = (wav.pcm.length / 2 / SAMPLE_RATE) * 1000;

  // Silence structure first: per-chunk speech verdicts and the length
  // distribution of silence runs — the ground truth the hangover choice
  // rests on.
  const speechChunks = [];
  for (let off = 0; off < wav.pcm.length; off += chunkBytes) {
    speechChunks.push(chunkRms(wav.pcm.subarray(off, off + chunkBytes)) >= COST_SPEECH_RMS);
  }
  const runs = []; // silence run lengths, ms
  let run = 0;
  for (const speech of speechChunks) {
    if (speech) { if (run) runs.push(run * CHUNK_MS); run = 0; }
    else run++;
  }
  if (run) runs.push(run * CHUNK_MS);
  const speechMs = speechChunks.filter(Boolean).length * CHUNK_MS;
  const sorted = [...runs].sort((a, b) => a - b);
  const pct = (p) => sorted[Math.min(sorted.length - 1,
    Math.floor(sorted.length * p))] || 0;

  console.log(`audio: ${(totalMs / 60000).toFixed(1)} min, `
    + `speech (RMS gate): ${(100 * speechMs / totalMs).toFixed(1)}%`);
  console.log(`silence runs: ${runs.length}  `
    + `p50 ${pct(0.5)} ms  p90 ${pct(0.9)} ms  p99 ${pct(0.99)} ms  `
    + `max ${sorted[sorted.length - 1] || 0} ms`);
  console.log('\nhangover_ms  billed%  saved%  closes  reopens');
  for (const h of args.hangovers) {
    const gate = createCostGate({ hangoverMs: h });
    let closes = 0;
    let reopens = 0;
    let wasOpen = gate.isOpen;
    for (let off = 0; off < wav.pcm.length; off += chunkBytes) {
      const { reopened } = gate.feed(wav.pcm.subarray(off, off + chunkBytes));
      if (reopened) reopens++;
      if (wasOpen && !gate.isOpen) closes++;
      wasOpen = gate.isOpen;
    }
    const { fedMs, forwardedMs } = gate.stats;
    console.log(`${String(h).padStart(11)}  ${(100 * forwardedMs / fedMs)
      .toFixed(1).padStart(6)}  ${(100 * (fedMs - forwardedMs) / fedMs)
      .toFixed(1).padStart(5)}  ${String(closes).padStart(6)}  ${String(reopens)
      .padStart(7)}`);
  }
}

main();
