#!/usr/bin/env node
// Nemotron replay adapter: feed a 16 kHz WAV through the local offline
// decode service (Windows/overlay/local_speech_server.py) at real-time pace
// and log the partial/final stream in wer-replay's sidecar shapes, so
// tools/stability-analysis.js can grid it.
//
//   node tools/nemotron-replay.js --audio <16k.wav>
//        [--url ws://127.0.0.1:8791/transcribe]
//
// Start the service first (the offline model must be downloaded):
//   python Windows/overlay/local_speech_server.py
//     --model %LOCALAPPDATA%\Dotify\models\nemotron-3.5-560ms-int8
//     --manifest core/speech-to-text/offline-model.json --port 8791
//
// The service wants 16 kHz PCM16 mono (resample: ffmpeg -i in.wav -ac 1
// -ar 16000 -sample_fmt s16 out.wav) and speaks utterances, not items:
// partials belong to the current utterance, a final closes it (empty final
// = withdrawal — silence settled to nothing). This adapter assigns seg-N
// ids on that boundary. The adopted filter is SOFT_STABILITY.nemotron in
// server.js.

const fs = require('fs');
const { WebSocket } = require('ws');
const { readWav } = require('./wav');

const SAMPLE_RATE = 16000;
const CHUNK_MS = 100;

function parseArgs(argv) {
  const args = { url: 'ws://127.0.0.1:8791/transcribe' };
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === '--audio') args.audio = argv[++i];
    else if (argv[i] === '--url') args.url = argv[++i];
    else { console.error(`Unknown argument: ${argv[i]}`); process.exit(2); }
  }
  if (!args.audio) { console.error('need --audio'); process.exit(2); }
  return args;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const { sampleRate, pcm } = readWav(args.audio);
  if (sampleRate !== SAMPLE_RATE) {
    throw new Error(`${args.audio} is ${sampleRate} Hz; the service wants ${SAMPLE_RATE} Hz`);
  }
  // The service allowlists the Dotify page's origin; present it.
  const ws = new WebSocket(args.url, { origin: 'http://127.0.0.1:8788' });
  const partials = [];
  const finals = [];
  let seg = 0;
  let startedAt = null;
  let ready = null;
  const readyP = new Promise((res, rej) => { ready = res; ws.on('error', rej); });
  let closed = null;
  const closedP = new Promise((res) => { closed = res; });
  ws.on('close', () => closed());
  ws.on('message', (raw) => {
    let msg; try { msg = JSON.parse(raw.toString()); } catch { return; }
    if (msg.type === 'status' && msg.state === 'ready') ready();
    else if (msg.type === 'error') { console.error('service error:', msg.message); process.exit(1); }
    else if (msg.type === 'partial') {
      partials.push({ atMs: Date.now() - startedAt, id: `seg-${seg}`, text: msg.text });
    } else if (msg.type === 'final') {
      finals.push({ atMs: Date.now() - startedAt, id: `seg-${seg}`, text: msg.text || '' });
      seg += 1;
    }
  });
  await new Promise((res, rej) => { ws.on('open', res); ws.on('error', rej); });
  await readyP; // model loaded
  console.error('service ready, pumping at 1x...');
  const chunkBytes = (SAMPLE_RATE / 1000) * CHUNK_MS * 2;
  startedAt = Date.now();
  for (let off = 0, sentMs = 0; off < pcm.length; off += chunkBytes) {
    ws.send(pcm.subarray(off, off + chunkBytes));
    sentMs += CHUNK_MS;
    const wait = startedAt + sentMs - Date.now();
    if (wait > 0) await sleep(wait);
  }
  ws.send(JSON.stringify({ command: 'finish' }));
  await sleep(3000);
  ws.close();
  await Promise.race([closedP, sleep(2000)]);
  fs.writeFileSync(`${args.audio}.nemotron.partials.json`, JSON.stringify(partials, null, 1) + '\n');
  fs.writeFileSync(`${args.audio}.nemotron.finals.json`, JSON.stringify(finals, null, 1) + '\n');
  const at = finals.map((f) => f.atMs);
  const gaps = at.slice(1).map((t, i) => t - at[i]).sort((a, b) => a - b);
  console.log(`${partials.length} partial updates, ${finals.length} finals `
    + `(${finals.filter((f) => !f.text).length} empty/withdrawn), `
    + `final gap med/max ${gaps.length ? (gaps[Math.floor(gaps.length / 2)] / 1000).toFixed(1) : '-'}s`
    + `/${gaps.length ? (gaps[gaps.length - 1] / 1000).toFixed(1) : '-'}s`);
  process.exit(0);
}

main().catch((e) => { console.error(e.message); process.exit(1); });
