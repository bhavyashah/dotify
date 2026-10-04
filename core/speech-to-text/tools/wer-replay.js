#!/usr/bin/env node
// WER replay harness.
//
// Replays a recorded session's audio through one or more streaming engines —
// the SAME provider adapters the live app uses, gap gate and all — and scores
// each engine's finalized transcript against a hand-corrected reference. Same
// speech, same room, every config: an engine choice, a gate retune, or a mic
// swap becomes a number instead of a feel.
//
//   node tools/wer-replay.js --audio session.wav --ref reference.txt
//        [--engines assemblyai,deepgram,openai]  default: every engine with a key
//                           (assemblyai-pro is AssemblyAI's Pro model, for A/B
//                           runs against the shipped English model)
//        [--speed N]        pump audio at N x real time (default 1; the
//                           faithful setting — vendor endpointing behaves
//                           differently when audio arrives faster)
//        [--dictionary f]   also send this personal dictionary's boost terms
//                           (dictionary.json shape), to measure boosting
//        [--language x]     language hint sent to the engine (default: en,
//                           matching server.js). "auto" sends none, leaving
//                           the vendor to detect the language per session.
//        [--ignore-fillers] exclude um/uh-class words from WER, matching the
//                           published AMI benchmark tables
//        [--no-backstop]    disable the shared gap gate's forced-endpoint
//                           backstop (segment bounds pushed to 1 hour), so
//                           the engine runs on its own vendor endpointing /
//                           turn detection alone — the provider-recommended
//                           configuration. For judging whether OUR backstop
//                           earns its keep: compare WER *and* the cadence
//                           column (the backstop exists to bound latency).
//        [--accurate]       the app's "accurate" latency preset, as server.js
//                           builds it
//        [--mode m]         AssemblyAI Pro preset (min_latency | balanced |
//                           max_accuracy)
//        [--endpointing-ms n]  Deepgram endpointing / ElevenLabs VAD silence
//        [--delay d]        OpenAI latency dial (minimal ... xhigh)
//        [--cost-gate]      send through the cost gate as the app does, and
//                           report streamed vs fed audio
//
// Record sessions by launching the server with DOTIFY_RECORD_DIR set (see
// recorder.js), or with the page's "Record sessions" control (open the page
// with ?dev=1 to see it). Either way it saves the WAV plus a .ref.txt draft
// of what the live engine heard. Hand-correct that draft into the reference before scoring —
// scoring an engine against its own uncorrected output reports 0% by
// definition. A replay streams the audio's full duration to every selected
// engine, on that engine's account.

const fs = require('fs');
const path = require('path');
const { readWav } = require('./wav');
const { werStats } = require('./wer');
const { loadDictionary, boostTerms } = require('../dictionary');
const { parseEnv } = require('../env-file');

const SAMPLE_RATE = 24000;      // the app's capture format; recorder.js writes it
const CHUNK_MS = 100;           // browser-sized chunks
const LANGUAGE = 'en';          // match server.js
const CLOSE_WAIT_MS = 15000;    // trailing finals flush after close()

// Engine registry: mirrors server.js's PROVIDERS (models and idleCloseMs,
// which --cost-gate uses); keep in step when server.js changes.
const ENGINES = {
  assemblyai: { module: '../providers/assemblyai-realtime', model: 'universal-streaming-english', keyName: 'ASSEMBLYAI_API_KEY', idleCloseMs: 15000 },
  // Not in the app: AssemblyAI's Pro model, for same-audio comparisons.
  'assemblyai-pro': { module: '../providers/assemblyai-realtime', model: 'universal-3-5-pro', keyName: 'ASSEMBLYAI_API_KEY', idleCloseMs: 15000 },
  deepgram: { module: '../providers/deepgram-realtime', model: 'nova-3', keyName: 'DEEPGRAM_API_KEY' },
  openai: { module: '../providers/openai-realtime', model: 'gpt-live-transcribe', keyName: 'OPENAI_API_KEY' },
  elevenlabs: { module: '../providers/elevenlabs-realtime', model: 'scribe_v2_realtime', keyName: 'ELEVENLABS_API_KEY', idleCloseMs: 12000 },
};

// Same .env discipline as server.js: DOTIFY_ENV_FILE or the checkout's .env,
// with real environment variables taking precedence.
function loadKeys() {
  const envFile = process.env.DOTIFY_ENV_FILE || path.join(__dirname, '..', '.env');
  let text = '';
  try { text = fs.readFileSync(envFile, 'utf8'); } catch { /* keyless */ }
  const keys = parseEnv(text);
  return (name) => process.env[name] || keys[name] || '';
}

function parseArgs(argv) {
  const args = { speed: 1 };
  for (let i = 0; i < argv.length; i++) {
    const flag = argv[i];
    if (flag === '--audio') args.audio = argv[++i];
    else if (flag === '--ref') args.ref = argv[++i];
    else if (flag === '--engines') args.engines = argv[++i].split(',').map((s) => s.trim()).filter(Boolean);
    else if (flag === '--speed') args.speed = Number(argv[++i]) || 1;
    else if (flag === '--dictionary') args.dictionary = argv[++i];
    else if (flag === '--no-backstop') args.noBackstop = true;
    else if (flag === '--accurate') args.accurate = true;
    else if (flag === '--mode') args.mode = argv[++i];
    else if (flag === '--endpointing-ms') args.endpointingMs = Number(argv[++i]) || undefined;
    else if (flag === '--delay') args.delay = argv[++i];
    else if (flag === '--language') args.language = argv[++i];
    else if (flag === '--cost-gate') args.costGate = true;
    else if (flag === '--ignore-fillers') args.ignoreFillers = true;
    else { console.error(`Unknown argument: ${flag}`); process.exit(2); }
  }
  return args;
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function runEngine(name, engine, apiKey, pcm,
                         { speed, dictionaryTerms, noBackstop, accurate,
                           mode, endpointingMs, delay, language, costGate }) {
  const { createSession } = require(engine.module);
  const { createGatedSession } = require('../providers/gated-session');
  // --accurate: the PRODUCTION accurate-preset session config, exactly as
  // server.js builds it (vendor-governed turns; OpenAI = gap-only commits
  // with no hard cap + delay xhigh; Deepgram endpointer 500ms) — so the
  // preset is measurable per engine without hand-assembling the overrides.
  if (accurate) {
    noBackstop = false;
    engine.accurateGate = name === 'openai'
      ? { minSegmentMs: 2500, maxSegmentMs: Infinity,
          gapSilenceMs: 350, silenceDiscardMs: 2500 }
      : { minSegmentMs: Infinity, maxSegmentMs: Infinity };
    endpointingMs = endpointingMs || (name === 'deepgram' ? 500 : undefined);
    if (name === 'openai') delay = delay || 'xhigh';
  }
  const finals = [];
  const finalAtMs = [];   // arrival time of each final, for cadence stats
  const finalIds = [];    // provider item id per final (correlates partials)
  // Full timestamped partial stream: what the display's soft path would see.
  // Lets offline analysis simulate any render-delay policy from one run
  // (e.g. "freeze words older than N seconds" regret curves).
  const partials = [];
  let firstFinalMs = null;
  let errorMessage = null;
  let startedAt = null; // set when the pump starts; finals only follow audio
  let readyResolve;
  let closeResolve;
  const ready = new Promise((resolve) => { readyResolve = resolve; });
  const closed = new Promise((resolve) => { closeResolve = resolve; });

  const baseOpts = {
    apiKey,
    model: engine.model,
    language,
    dictionary: dictionaryTerms,
    // 1 hour >> any utterance: the gate can never fire, so segmentation is
    // whatever the vendor's own endpointing/turn detection decides.
    gateOverrides: engine.accurateGate || (noBackstop
      ? { minSegmentMs: 3600000, maxSegmentMs: 3600000 }
      : undefined),
    mode,           // AssemblyAI latency/accuracy preset (that provider only)
    endpointingMs,  // Deepgram endpointing / ElevenLabs VAD silence threshold
    delay,          // gpt-live-transcribe context dial (that provider only)
    onPartial: (id, text) => {
      if (startedAt !== null) partials.push({ atMs: Date.now() - startedAt, id, text });
    },
    onFinal: (id, text) => {
      if (firstFinalMs === null && startedAt !== null) {
        firstFinalMs = Date.now() - startedAt;
      }
      if (startedAt !== null) finalAtMs.push(Date.now() - startedAt);
      finalIds.push(id);
      finals.push(text);
    },
  };
  const handlers = {
    onReady: () => readyResolve(),
    onError: (message) => { errorMessage = errorMessage || message; },
    onClose: () => closeResolve(),
  };
  // --cost-gate: the production send path — the cost gate withholds
  // silence, the wrapper idles/closes/re-dials exactly as server.js does, and
  // the run reports streamed vs fed audio. Measures both what gating costs in
  // WER and how much audio it withholds, on the same take.
  const session = costGate
    ? createGatedSession((h) => createSession({ ...baseOpts, ...h }), {
      idleCloseMs: engine.idleCloseMs,
      log: (line) => console.error(`  [${name}] ${line}`),
      ...handlers,
    })
    : createSession({ ...baseOpts, ...handlers });

  const readyOrDead = await Promise.race([
    ready.then(() => 'ready'),
    closed.then(() => 'closed'),
    sleep(10000).then(() => 'timeout'),
  ]);
  if (readyOrDead !== 'ready') {
    session.close();
    throw new Error(errorMessage || `${name} session never became ready (${readyOrDead})`);
  }

  const chunkBytes = (SAMPLE_RATE / 1000) * CHUNK_MS * 2;
  startedAt = Date.now();
  // Pace by the audio clock, not per-chunk sleeps: drift never accumulates.
  for (let offset = 0, sentMs = 0; offset < pcm.length; offset += chunkBytes) {
    session.sendAudio(pcm.subarray(offset, offset + chunkBytes));
    sentMs += CHUNK_MS;
    const due = startedAt + sentMs / speed;
    const wait = due - Date.now();
    if (wait > 0) await sleep(wait);
  }
  session.close();
  await Promise.race([closed, sleep(CLOSE_WAIT_MS)]);
  if (!finals.length && errorMessage) throw new Error(errorMessage);
  // Cadence: the longest and median wait between consecutive finals. The
  // reader's experience of "frozen display" is the MAX gap — one silent
  // minute mid-meeting is invisible in a median.
  let maxGapMs = null;
  let medianGapMs = null;
  if (finalAtMs.length >= 2) {
    const gaps = finalAtMs.slice(1).map((t, i) => t - finalAtMs[i]);
    gaps.sort((a, b) => a - b);
    maxGapMs = gaps[gaps.length - 1];
    medianGapMs = gaps[Math.floor(gaps.length / 2)];
  }
  return { hypothesis: finals.join(' '), firstFinalMs, errorMessage,
           finals, finalAtMs, finalIds, partials,
           finalCount: finals.length, maxGapMs, medianGapMs,
           costGateStats: session.stats || null };
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (!args.audio || !args.ref) {
    console.error('Usage: node tools/wer-replay.js --audio <session.wav> --ref <reference.txt> '
      + '[--engines a,b,c] [--speed N] [--dictionary dictionary.json]');
    process.exit(2);
  }
  const wav = readWav(args.audio);
  if (wav.sampleRate !== SAMPLE_RATE) {
    throw new Error(`${args.audio} is ${wav.sampleRate} Hz; the engines expect `
      + `${SAMPLE_RATE} Hz (recorder.js output). Resample it first.`);
  }
  const refText = fs.readFileSync(args.ref, 'utf8');
  const keyFor = loadKeys();
  const dictionaryTerms = args.dictionary
    ? boostTerms(loadDictionary(args.dictionary))
    : undefined;

  const wanted = args.engines || Object.keys(ENGINES);
  const results = [];
  for (const name of wanted) {
    const engine = ENGINES[name];
    if (!engine) {
      console.error(`Skipping unknown engine "${name}" (know: ${Object.keys(ENGINES).join(', ')})`);
      continue;
    }
    const apiKey = keyFor(engine.keyName);
    if (!apiKey) {
      console.error(`Skipping ${name}: ${engine.keyName} is not set`);
      continue;
    }
    const durationS = (wav.pcm.length / 2 / SAMPLE_RATE).toFixed(1);
    console.error(`\nReplaying ${durationS}s of audio through ${name} `
      + `(${engine.model}) at ${args.speed}x...`);
    try {
      const run = await runEngine(name, engine, apiKey, wav.pcm,
        { speed: args.speed, dictionaryTerms, noBackstop: args.noBackstop,
          accurate: args.accurate,
          mode: args.mode, endpointingMs: args.endpointingMs, delay: args.delay,
          // --language auto: send NO language hint, so the vendor detects it
          // per session (measures what the live app's 'en' pin is worth).
          language: args.language === 'auto' ? undefined
            : (args.language || LANGUAGE),
          costGate: args.costGate });
      const stats = werStats(refText, run.hypothesis,
        { ignoreFillers: args.ignoreFillers });
      const hypFile = `${args.audio}.${name}.hyp.txt`;
      fs.writeFileSync(hypFile, run.hypothesis + '\n');
      // Per-final arrival times (ms into the replay) alongside the text:
      // lets a later analysis window the hypothesis by audio time (e.g.
      // score only the first N minutes) without another run.
      fs.writeFileSync(`${args.audio}.${name}.finals.json`, JSON.stringify(
        run.finals.map((text, i) => ({ atMs: run.finalAtMs[i], id: run.finalIds[i], text })),
        null, 1) + '\n');
      if (run.partials.length) {
        fs.writeFileSync(`${args.audio}.${name}.partials.json`,
          JSON.stringify(run.partials, null, 1) + '\n');
      }
      results.push({ name, stats, run, hypFile });
    } catch (err) {
      console.error(`${name} failed: ${err.message}`);
      results.push({ name, failed: err.message });
    }
  }

  console.log('\nengine       WER     errors (sub/del/ins)   ref words   first final'
    + '   finals   gap med/max');
  for (const r of results) {
    if (r.failed) {
      console.log(`${r.name.padEnd(12)} FAILED: ${r.failed}`);
      continue;
    }
    const s = r.stats;
    const gapCol = r.run.maxGapMs === null
      ? '        —'
      : `${(r.run.medianGapMs / 1000).toFixed(1)}s/${(r.run.maxGapMs / 1000).toFixed(1)}s`;
    console.log(`${r.name.padEnd(12)} ${(s.wer * 100).toFixed(1).padStart(5)}%  `
      + `${String(s.errors).padStart(4)} (${s.substitutions}/${s.deletions}/${s.insertions})`
      + `${String(s.refWords).padStart(12)}`
      + `${r.run.firstFinalMs === null ? '        —' : String(r.run.firstFinalMs).padStart(8) + 'ms'}`
      + `${String(r.run.finalCount).padStart(9)}   ${gapCol}`);
    if (r.run.costGateStats) {
      const g = r.run.costGateStats;
      console.log(`             cost gate: streamed ${(g.forwardedMs / 1000).toFixed(0)}s `
        + `of ${(g.fedMs / 1000).toFixed(0)}s (${(100 * g.forwardedMs / g.fedMs).toFixed(1)}%), `
        + `${g.dials} dial${g.dials === 1 ? '' : 's'}`);
    }
    if (r.run.errorMessage) {
      // A mid-run provider error with finals already collected means the
      // hypothesis may be TRUNCATED — an unmarked number here would let a
      // rate-limited engine "lose" an A/B on deletions it never made.
      console.log(`             WARNING: session error mid-run — hypothesis may be truncated: ${r.run.errorMessage}`);
    }
    console.log(`             hypothesis saved to ${r.hypFile}`);
  }
}

main().then(
  () => process.exit(0),   // straggler sockets/timers must not hold the exit
  (err) => { console.error(err.message); process.exit(1); },
);
