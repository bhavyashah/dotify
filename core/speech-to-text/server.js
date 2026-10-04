// Dotify live-transcription server.
// Serves the UI, streams browser mic audio to a transcription provider (see
// providers/), and exposes finalized text on ws://localhost:8788/finalized and
// in transcript.txt (append-only).
//
// The /finalized stream speaks the revisable-buffer protocol. Three message
// types, all carrying segment ids unique per server boot:
//   {type:"final",  id, text, speaker?, ts}  a hardened segment — never
//       revised again. Consumers that only understand finals can ignore the
//       rest of the protocol and get the classic behavior.
//   {type:"soft",   id, text, ts}   first emission of a REVISABLE segment:
//       the stable prefix of the engine's current hypothesis, sent early so
//       downstream queues get text sooner than finalization.
//   {type:"revise", revise: id, text, ts}   replaces the text of a
//       still-soft segment (text:"" withdraws one). The final with the same
//       id is the segment's last revision.
// transcript.txt records hardened finals only.

const http = require('http');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { WebSocketServer, WebSocket } = require('ws');
const { SpeakerNames } = require('./speaker-names');
const { httpRequest } = require('./http-request');
const { summarize, localSummary } = require('./summarize');
const dictionary = require('./dictionary');
const { createSessionRecorder } = require('./recorder');
const { createOfflineModel } = require('./offline-model');
const { parseEnv } = require('./env-file');

const PORT = process.env.PORT || 8788;
const PUBLIC_DIR = path.join(__dirname, 'public');
const TRANSCRIPT_FILE = process.env.DOTIFY_TRANSCRIPT_FILE || path.join(__dirname, 'transcript.txt');
const ENV_FILE = process.env.DOTIFY_ENV_FILE || path.join(__dirname, '.env');
// User data (dictionary, recordings, the offline model) lives beside the
// .env, which in the installed app is the user's data folder.
const DICTIONARY_FILE = process.env.DOTIFY_DICTIONARY_FILE
  || path.join(path.dirname(ENV_FILE), 'dictionary.json');
// Session recording for accuracy measurement (see recorder.js): on request
// from the page, or for every session when DOTIFY_RECORD_DIR is set.
const RECORD_DIR = process.env.DOTIFY_RECORD_DIR
  || path.join(path.dirname(ENV_FILE), 'recordings');
const RECORD_ALL = Boolean(process.env.DOTIFY_RECORD_DIR);
// The platform shell's local decode server reads the offline model from the
// same directory; this server only manages the download.
const MODELS_DIR = process.env.DOTIFY_MODELS_DIR
  || path.join(path.dirname(ENV_FILE), 'models');
const offlineModel = createOfflineModel({ modelsRoot: MODELS_DIR });
// English only. Without a pinned language some engines auto-detect per
// segment and occasionally emit Japanese/Chinese characters for English.
const LANGUAGE = 'en';

// The transcription providers, selected per session by the browser
// (?provider=<name> on /audio). Each module under providers/ exports the same
// createSession(...) interface. To add one, add a module and an entry here,
// plus an <option> in public/index.html.
//   probeHost    the host /api/net-probe sends HEAD / to
//   idleCloseMs  how long the cost gate holds a silent session open (see
//                providers/gated-session.js)
//   scriptGuard  drop CJK/Hangul text (see NON_ENGLISH_SCRIPT)
const PROVIDERS = {
  openai: {
    createSession: require('./providers/openai-realtime').createSession,
    model: 'gpt-live-transcribe',
    apiKeyName: 'OPENAI_API_KEY',
    probeHost: 'api.openai.com',
    // Whisper-family models hallucinate CJK fragments on silence. Kept as
    // insurance on this model, where it is unverified; with the language
    // pinned to English it can only drop junk.
    scriptGuard: true,
  },
  assemblyai: {
    createSession: require('./providers/assemblyai-realtime').createSession,
    model: 'universal-streaming-english',
    apiKeyName: 'ASSEMBLYAI_API_KEY',
    probeHost: 'streaming.assemblyai.com',
    // AssemblyAI bills the open socket, silent or not, so close it after a
    // short lull (about 20 s of silence with the 5 s hangover).
    idleCloseMs: 15000,
  },
  deepgram: {
    createSession: require('./providers/deepgram-realtime').createSession,
    model: 'nova-3',
    apiKeyName: 'DEEPGRAM_API_KEY',
    probeHost: 'api.deepgram.com',
  },
  elevenlabs: {
    createSession: require('./providers/elevenlabs-realtime').createSession,
    model: 'scribe_v2_realtime',
    apiKeyName: 'ELEVENLABS_API_KEY',
    probeHost: 'api.elevenlabs.io',
    // Scribe closes a stream ~15 s after audio stops and pings do not hold
    // it, so close first: a deliberate close needs no health-probe re-dial.
    idleCloseMs: 12000,
  },
};
// Test seam: a keyed provider that never dials out (providers/mock-realtime.js).
if (process.env.DOTIFY_MOCK_PROVIDER === '1') {
  PROVIDERS.mock = {
    createSession: require('./providers/mock-realtime').createSession,
    model: 'mock',
    apiKeyName: 'DOTIFY_MOCK_KEY',
    // No probeHost: /api/net-probe answers from the die-file seam instead.
    vendorDown: require('./providers/mock-realtime').vendorDown,
  };
}
// For /audio clients that name no provider (the page always does). ElevenLabs
// had the lowest WER on the AMI meeting benchmark.
const DEFAULT_PROVIDER = 'elevenlabs';
const PROVIDER_CONFIG_TOKEN = crypto.randomBytes(32).toString('base64url');
const MAX_CONFIG_BODY_BYTES = 4096;
const INGEST_BODY_CAP_BYTES = 1e6;
const MAX_ENROLLMENT_BYTES = 4 * 1024 * 1024; // 30 s of PCM16 @ 24 kHz is ~1.4 MB
// Short: the caller retries in a few seconds anyway.
const NET_PROBE_TIMEOUT_MS = 2500;

// The cost gate (providers/gated-session.js) withholds silence from the
// vendors and closes long-idle sessions. DOTIFY_COST_GATE=0 turns it off for
// measurement.
const COST_GATE_OFF = process.env.DOTIFY_COST_GATE === '0';
const { createGatedSession } = require('./providers/gated-session');

// Keys are optional at boot: the sessionless engines (POST /ingest) need
// none, and a provider's key is checked only when an /audio session asks for
// that provider.
function loadEnv() {
  let text = '';
  try { text = fs.readFileSync(ENV_FILE, 'utf8'); } catch { /* no .env → keyless */ }
  return parseEnv(text);
}

// Rewrite .env from the whole in-memory map, so settings the user keeps there
// (DOTIFY_SUMMARY_MODEL and friends) survive. Comments are not preserved.
function writeEnvFile() {
  fs.mkdirSync(path.dirname(ENV_FILE), { recursive: true });
  const contents = Object.entries(ENV)
    .map(([key, value]) => `${key}=${value}`)
    .join('\n');
  fs.writeFileSync(ENV_FILE, contents ? `${contents}\n` : '', { encoding: 'utf8', mode: 0o600 });
}

const ENV = loadEnv();
function apiKeyFor(provider) {
  return ENV[provider.apiKeyName] || process.env[provider.apiKeyName] || null;
}

// Personal dictionary: the entries and the correction pass built from them.
// correctText runs on all outgoing text (soft and final), so braille, the
// transcript box and transcript.txt see the same words. Engine boosting reads
// dictionaryEntries at session start.
let dictionaryEntries = dictionary.loadDictionary(DICTIONARY_FILE);
let correctText = dictionary.makeCorrector(dictionaryEntries);

function updateDictionary(mutate) {
  const next = mutate(dictionaryEntries.slice());
  dictionary.saveDictionary(DICTIONARY_FILE, next);
  dictionaryEntries = next;
  correctText = dictionary.makeCorrector(next);
}

function addDictionaryWord(raw) {
  const entry = dictionary.validateEntry(raw);
  updateDictionary((entries) => {
    const existing = entries.findIndex(
      (e) => e.word.toLowerCase() === entry.word.toLowerCase());
    if (existing >= 0) entries.splice(existing, 1); // re-adding updates aliases
    else if (entries.length >= dictionary.MAX_ENTRIES) {
      throw new Error(`The dictionary holds at most ${dictionary.MAX_ENTRIES} words.`);
    }
    entries.push(entry);
    return entries;
  });
  // Warn when an always-replace alias is ordinary English: it will rewrite
  // correct speech too.
  return dictionary.entryWarnings(entry);
}

function removeDictionaryWord(word) {
  updateDictionary((entries) => {
    const idx = entries.findIndex(
      (e) => e.word.toLowerCase() === String(word).toLowerCase());
    if (idx < 0) throw new Error('That word is not in the dictionary.');
    entries.splice(idx, 1);
    return entries;
  });
}

function providerConfig() {
  return {
    providers: Object.fromEntries(Object.entries(PROVIDERS).map(([name, provider]) => [
      name, { configured: Boolean(apiKeyFor(provider)) },
    ])),
    token: PROVIDER_CONFIG_TOKEN,
  };
}

function saveProviderKey(providerName, key) {
  const provider = PROVIDERS[providerName];
  if (!provider) throw new Error(`Unknown transcription provider "${providerName}".`);
  if (typeof key !== 'string' || key.length < 8 || key.length > 512 || /[\r\n]/.test(key)) {
    throw new Error('API keys must be between 8 and 512 characters with no line breaks.');
  }
  ENV[provider.apiKeyName] = key;
  writeEnvFile();
}

function removeProviderKey(providerName) {
  const provider = PROVIDERS[providerName];
  if (!provider) throw new Error(`Unknown transcription provider "${providerName}".`);
  delete ENV[provider.apiKeyName];
  writeEnvFile();
}

// Browsers send an Origin header; local non-browser clients (the braille
// engine, tests, curl) send none. Only loopback pages may use the WebSocket
// feeds and the state-changing endpoints: WebSockets have no same-origin
// policy, so without this any open web page could read /finalized or post to
// /ingest.
function originAllowed(req) {
  const origin = req.headers.origin;
  if (!origin) return true;
  try {
    const { hostname } = new URL(origin);
    return hostname === '127.0.0.1' || hostname === 'localhost'
      || hostname === '::1' || hostname === '[::1]';
  } catch {
    return false;
  }
}

function sendJson(res, status, payload) {
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Cache-Control': 'no-store',
  });
  res.end(JSON.stringify(payload));
}

// Read a request body of at most maxBytes and hand it to onBody (a UTF-8
// string, or the raw Buffer). Over the cap the 413 goes out the moment the
// tally crosses it; the rest of the upload is drained unread so the sender
// reliably gets to read the refusal, and a sender still pouring long past
// it is cut off.
function readBody(req, res, maxBytes, onBody, { raw = false } = {}) {
  const chunks = [];
  let bytes = 0;
  let refused = false;
  req.on('data', (chunk) => {
    bytes += chunk.length;
    if (refused) {
      if (bytes > 8 * maxBytes) req.destroy();
      return;
    }
    if (bytes > maxBytes) {
      refused = true;
      chunks.length = 0;
      sendJson(res, 413, { error: `Request body over ${maxBytes} bytes.` });
      return;
    }
    chunks.push(chunk);
  });
  req.on('end', () => {
    if (refused) return;
    const body = Buffer.concat(chunks);
    onBody(raw ? body : body.toString('utf8'));
  });
}

// A settings write (provider keys, dictionary, offline model): JSON carrying
// the per-boot token, which only a page that could read GET /api/providers
// has. apply(payload) returns the 200 body or throws a user-facing message
// for the 400.
function handleSettingsWrite(req, res, apply) {
  if (req.headers['x-dotify-token'] !== PROVIDER_CONFIG_TOKEN
      || req.headers['content-type'] !== 'application/json') {
    sendJson(res, 403, { error: 'Configuration request was not authorized.' });
    return;
  }
  readBody(req, res, MAX_CONFIG_BODY_BYTES, (body) => {
    let result;
    try {
      const payload = JSON.parse(body);
      if (!payload || typeof payload !== 'object') throw new Error('Expected a JSON object.');
      result = apply(payload);
    } catch (error) {
      sendJson(res, 400, { error: error.message });
      return;
    }
    sendJson(res, 200, result);
  });
}

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.ttf': 'font/ttf',
};

// Split a request target into its path and query. Deliberately not
// `new URL(req.url, 'http://' + host)`: the Host header is client-controlled
// and an unparseable one would throw out of the request handler.
function splitUrl(rawUrl) {
  const q = rawUrl.indexOf('?');
  return q < 0
    ? { urlPath: rawUrl, query: new URLSearchParams() }
    : { urlPath: rawUrl.slice(0, q), query: new URLSearchParams(rawUrl.slice(q + 1)) };
}

function safeDecode(component) {
  try { return decodeURIComponent(component); } catch { return null; }
}

const server = http.createServer((req, res) => {
  const { urlPath, query } = splitUrl(req.url);

  // GET responses are unreadable cross-origin without CORS headers; every
  // other method is checked here.
  if (req.method !== 'GET' && !originAllowed(req)) {
    sendJson(res, 403, { error: 'Cross-origin requests are not allowed.' });
    return;
  }

  if (req.method === 'GET' && urlPath === '/api/providers') {
    sendJson(res, 200, providerConfig());
    return;
  }

  if (req.method === 'POST' && urlPath === '/api/providers') {
    handleSettingsWrite(req, res, (payload) => {
      if (payload.remove === true) removeProviderKey(payload.provider);
      else saveProviderKey(payload.provider, payload.key);
      return providerConfig();
    });
    return;
  }

  // Personal dictionary: POST adds ({add: {word, soundsLike?}}) or removes
  // ({remove: word}) one word. Boosting applies at the next session start;
  // the correction pass applies immediately.
  if (req.method === 'GET' && urlPath === '/api/dictionary') {
    sendJson(res, 200, { words: dictionaryEntries });
    return;
  }

  if (req.method === 'POST' && urlPath === '/api/dictionary') {
    handleSettingsWrite(req, res, (payload) => {
      let warnings = [];
      if (payload.remove !== undefined) removeDictionaryWord(payload.remove);
      else if (payload.add) warnings = addDictionaryWord(payload.add);
      else throw new Error('The dictionary request needs {add} or {remove}.');
      return {
        words: dictionaryEntries,
        ...(warnings.length ? { warning: warnings.join(' ') } : {}),
      };
    });
    return;
  }

  // "Is this vendor reachable again?" Asked every few seconds by the platform
  // shell while it has fallen back to the offline engine, so it switches back
  // only once a switch can succeed. Probing from here sidesteps the browser's
  // cross-origin rules; any HTTP answer (401 included) proves the network
  // path.
  if (req.method === 'GET' && urlPath === '/api/net-probe') {
    const provider = PROVIDERS[query.get('engine')];
    if (!provider) {
      sendJson(res, 400, { error: 'Unknown transcription provider.' });
      return;
    }
    if (!provider.probeHost) {
      // The mock test seam: reachability mirrors the die-file.
      sendJson(res, 200, { reachable: !(provider.vendorDown && provider.vendorDown()) });
      return;
    }
    httpRequest(`https://${provider.probeHost}/`, {
      method: 'HEAD',
      timeoutMs: NET_PROBE_TIMEOUT_MS,
    }).then(() => true, () => false)
      .then((reachable) => sendJson(res, 200, { reachable }));
    return;
  }

  // Optional offline model: GET reports readiness and download progress;
  // POST ({download} | {cancel} | {remove}) drives the downloader. The
  // platform shell's local decode server loads the model, not this process.
  if (req.method === 'GET' && urlPath === '/api/offline-model') {
    sendJson(res, 200, offlineModel.status());
    return;
  }

  if (req.method === 'POST' && urlPath === '/api/offline-model') {
    handleSettingsWrite(req, res, (payload) => {
      if (payload.download === true) return offlineModel.startDownload();
      if (payload.cancel === true) return offlineModel.cancel();
      if (payload.remove === true) return offlineModel.remove();
      throw new Error('The offline-model request needs {download}, {cancel}, or {remove}.');
    });
    return;
  }

  // Latency preset. The shipping page only ever posts balanced; the others
  // stay reachable for tests and tuning (see LATENCY_MODES).
  if (req.method === 'POST' && urlPath === '/latency') {
    readBody(req, res, 1024, (body) => {
      let mode = null;
      try { mode = JSON.parse(body).mode; } catch { /* rejected below */ }
      if (!LATENCY_MODES.has(mode)) {
        res.writeHead(400).end('unknown latency mode');
        return;
      }
      setLatencyMode(mode);
      res.writeHead(204).end();
    });
    return;
  }

  // Fresh start: the page calls this on load so each run begins with an
  // empty transcript file.
  if (req.method === 'POST' && urlPath === '/reset') {
    resetSpeakerState();
    // Harden only the sessionless (/ingest) soft segments: nothing else will
    // ever finalize them. A live /audio session hardens its own on close;
    // hardening them here would render its words twice.
    softHardenAbandoned('ingest:');
    // Settle every final still waiting on a display ack (the hold and the
    // orphans above included) into the OLD file before the truncate.
    flushShown();
    // A fresh page brings a fresh client token, so the sessionless engines'
    // stability history, abandonment clocks and stable-source flag go too.
    ingestHypothesis.clear();
    for (const timer of abandonTimers.values()) clearTimeout(timer);
    abandonTimers.clear();
    setLatencyMode(currentLatency, false);
    // The truncate rides the same queue as every append, so the finals
    // settled above land (and are wiped) strictly before it.
    transcriptWrites = transcriptWrites.then(() => new Promise((resolve) => {
      fs.writeFile(TRANSCRIPT_FILE, '', (err) => {
        if (err) {
          console.error('Failed to reset transcript.txt:', err.message);
          res.writeHead(500).end();
        } else {
          console.log('Transcript reset for a fresh session.');
          res.writeHead(204).end();
        }
        resolve();
      });
    }));
    return;
  }

  // Sessionless engines (the Windows overlay's offline model, the demo
  // feeder, typed text) post here; see handleIngestBody for the payload.
  if (req.method === 'POST' && urlPath === '/ingest') {
    readBody(req, res, INGEST_BODY_CAP_BYTES, (body) => handleIngestBody(body, res));
    return;
  }

  // Jump-to-live summary for the braille ticker: compress missed speech into
  // a character budget that scales with the backlog.
  if (req.method === 'POST' && urlPath === '/summarize') {
    readBody(req, res, 64 * 1024, (body) => {
      let payload = null;
      try { payload = JSON.parse(body); } catch { /* handled below */ }
      const text = payload && typeof payload.text === 'string' ? payload.text.trim() : '';
      const chars = payload && Number.isInteger(payload.chars) ? payload.chars : 0;
      const grade = payload && payload.grade === 1 ? 1 : 2;
      if (!text || chars < 4 || chars > 2000) {
        sendJson(res, 400, { error: 'summarize needs {text, chars (4-2000), grade}' });
        return;
      }
      const respond = (summary, source) => {
        // Sizes only: the summary is conversation data and the desktop
        // shell keeps logs.
        console.log(`Jump-to-live summary [${source}] `
          + `(${text.length} chars -> ${summary.length}, budget ${chars})`);
        sendJson(res, 200, { summary, source });
      };
      // Without an OpenAI key, or when the model call fails, the built-in
      // extractive summary answers instead. Only an empty extraction is an
      // error, which the ticker treats as "no summary".
      const fallback = (error) => {
        if (error) console.error('Summary model failed:', error.message);
        const summary = localSummary(text, chars);
        if (summary) respond(summary, 'local');
        else sendJson(res, 502, { error: 'No summary could be built.' });
      };
      const apiKey = apiKeyFor(PROVIDERS.openai);
      if (!apiKey) { fallback(null); return; }
      summarize({
        apiKey, text, chars, grade,
        model: ENV.DOTIFY_SUMMARY_MODEL || process.env.DOTIFY_SUMMARY_MODEL,
        baseUrl: process.env.DOTIFY_OPENAI_BASE_URL,
      }).then((summary) => respond(summary, 'openai')).catch(fallback);
    });
    return;
  }

  // Named-speaker management, proxied to the local speaker-id service so the
  // browser talks to one origin. GET lists enrolled speakers (and whether the
  // service is up at all); POST /api/speakers/<name> enrolls a raw PCM16
  // 24 kHz clip; DELETE forgets a voiceprint.
  if (urlPath === '/api/speakers' && req.method === 'GET') {
    proxySpeakerService('GET', '/health', null, res);
    return;
  }
  const speakerRoute = urlPath.match(/^\/api\/speakers\/([^/]+)$/);
  if (speakerRoute && (req.method === 'POST' || req.method === 'DELETE')) {
    const name = safeDecode(speakerRoute[1]);
    if (!name || !/^[A-Za-z][A-Za-z0-9 _'-]{0,39}$/.test(name)) {
      sendJson(res, 400, { error: 'Speaker names are 1-40 letters, digits, spaces, or _\'-.' });
      return;
    }
    const servicePath = `/speakers/${encodeURIComponent(name)}`;
    if (req.method === 'DELETE') {
      proxySpeakerService('DELETE', servicePath, null, res);
      return;
    }
    readBody(req, res, MAX_ENROLLMENT_BYTES,
      (clip) => proxySpeakerService('POST', servicePath, clip, res), { raw: true });
    return;
  }

  const rel = urlPath === '/' ? 'index.html' : urlPath.slice(1);
  const file = path.join(PUBLIC_DIR, path.normalize(rel));
  // The separator matters: a bare prefix check would admit a sibling
  // directory named e.g. public-backup.
  if (!file.startsWith(PUBLIC_DIR + path.sep)) {
    res.writeHead(403).end();
    return;
  }
  fs.readFile(file, (err, data) => {
    if (err) {
      res.writeHead(404).end('Not found');
      return;
    }
    // Revalidate on every load: an upgrade replaces these files under the
    // same URLs, and a cached page would miss whatever the upgrade added.
    res.writeHead(200, {
      'Content-Type': MIME[path.extname(file)] || 'application/octet-stream',
      'Cache-Control': 'no-cache',
    });
    res.end(data);
  });
});

// Two WebSocket endpoints on the same port: /audio for the browser page,
// /finalized for downstream consumers (the braille engine).
const audioWss = new WebSocketServer({ noServer: true });
const finalWss = new WebSocketServer({ noServer: true });

server.on('upgrade', (req, socket, head) => {
  const { urlPath } = splitUrl(req.url);
  if (!originAllowed(req)) {
    socket.destroy();
    return;
  }
  if (urlPath === '/audio') {
    audioWss.handleUpgrade(req, socket, head, (ws) => audioWss.emit('connection', ws, req));
  } else if (urlPath === '/finalized') {
    finalWss.handleUpgrade(req, socket, head, (ws) => finalWss.emit('connection', ws, req));
  } else {
    socket.destroy();
  }
});

finalWss.on('connection', (ws) => {
  // A consumer joining mid-session needs the render mode before any text.
  ws.send(JSON.stringify({ type: 'latency', mode: currentLatency,
                           render: currentRender,
                           ts: new Date().toISOString() }));
  // Upstream: the display's "shown" acks (see settleFinal).
  ws.on('message', (data) => {
    let msg = null;
    try { msg = JSON.parse(data); } catch { return; }
    if (msg && msg.type === 'shown' && typeof msg.id === 'string'
        && typeof msg.text === 'string' && msg.text.length <= 8192) {
      resolveShown(msg.id, msg.text);
    }
  });
});

// Hiragana, Katakana, CJK ideographs, Hangul: what Whisper-family models
// hallucinate on silence even with the language hint. Dropped only for
// providers with scriptGuard; ElevenLabs genuinely transcribes non-English
// speech past the English hint, and the other engines cannot emit these
// scripts at all.
const NON_ENGLISH_SCRIPT = /[぀-ヿ㐀-䶿一-鿿豈-﫿가-힯]/;
const isScriptJunk = (text, scriptGuard) =>
  scriptGuard && LANGUAGE === 'en' && NON_ENGLISH_SCRIPT.test(text);

// Speaker labels (AssemblyAI conversation mode only; otherwise finals are
// unlabeled and pass straight through). A label is folded into the text as
// an "A: " prefix, and only when the speaker changes: on a 20-40 cell line
// every repeated label costs reading time. "UNKNOWN" (a turn too short to
// attribute) counts as the current speaker continuing.
//
// The streaming diarizer's labels flicker (one voice can come back A, A, B,
// A), and braille output is append-only, so a label must be decided before
// its text is emitted. A final whose label differs from the current speaker
// is held until the next labeled final confirms the change (same new label)
// or denies it (old label again: the flicker never reaches the display).
// Unlabeled finals arriving mid-hold queue behind it to keep order. After
// SPEAKER_CONFIRM_MS of nothing, the lone label is trusted. Same-speaker
// finals never wait. An explicit 0 disables the hold.
const SPEAKER_CONFIRM_MS = process.env.DOTIFY_SPEAKER_CONFIRM_MS !== undefined
  ? Math.max(0, Number(process.env.DOTIFY_SPEAKER_CONFIRM_MS) || 0)
  : 3000;

let lastSpeaker = null;  // last speaker whose label was printed
let hold = null;         // { speaker, entries: [{text, notify}], timer }

// Named speakers (speaker-id/): holds and lastSpeaker track the diarization
// labels; names are resolved only when a prefix is printed, so a name
// learned mid-session upgrades future prefixes. When the current speaker's
// name arrives, reAnnounce prints one extra prefix on their next final.
let reAnnounce = null;   // label whose freshly learned name still needs printing
const speakerNames = new SpeakerNames({
  onNamed: (label, name) => {
    console.log(`Speaker ${label} identified as ${name}.`);
    if (label === lastSpeaker) reAnnounce = label;
  },
});

// Forward one request to the speaker-id service and pass back its JSON reply.
// The service is optional, so its absence is an ordinary JSON answer.
function proxySpeakerService(method, servicePath, body, res) {
  httpRequest(new URL(servicePath, speakerNames.base), {
    method,
    headers: body ? { 'Content-Type': 'application/octet-stream' } : {},
    body: body || undefined,
    timeoutMs: 30000,
  }).then(({ status, body: data }) => {
    res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8' });
    res.end(data);
  }, () => {
    // GET = the UI's availability poll (answer normally, marked unavailable);
    // POST/DELETE = an action the user asked for, so failure is an error.
    res.writeHead(method === 'GET' ? 200 : 503,
                  { 'Content-Type': 'application/json; charset=utf-8' });
    res.end(JSON.stringify({
      ok: false,
      error: 'Speaker naming service is not running.',
      speakers: [],
    }));
  });
}

// ---- Revisable segments -----------------------------------------------------
//
// Most text waits in the braille queue before it is read, so the engines'
// interim hypotheses go into that queue early as soft segments and are
// corrected in place until they harden. Every provider reports
// onPartial(itemId, fullHypothesis) with replace semantics, and the
// sessionless engines post {interim:true} to /ingest; one emitter serves all.
//
// Whatever reaches a caught-up reader's display is frozen there, so the
// volatile tail of a hypothesis is held back: the last SOFT_HOLDBACK_WORDS
// words, or whatever a SOFT_STABILITY filter has not yet confirmed.
// Revisions are throttled per segment; the final always carries the rest.
//
// Conversation mode never emits soft text: speaker labels are decided at
// final time, and soft text ahead of that decision would bypass the hold.
const SOFT_HOLDBACK_WORDS = 2;
// Per-engine stability filters: a word goes soft only once it has sat deeper
// than `depth` words from the hypothesis tail, unchanged, across `persist`
// consecutive hypotheses. Each pair was measured on recorded meeting audio
// as the loosest setting that froze no word the engine later revised; measured
// with tools/stability-analysis.js and tools/nemotron-replay.js. The values
// hold only at the segment lengths the forced-endpoint backstop produces;
// they leak under whole vendor turns. Engines without
// an entry (the demo feeder, typed text) render confirmed-only in balanced.
const SOFT_STABILITY = {
  elevenlabs: { depth: 5, persist: 2 },
  assemblyai: { depth: 5, persist: 2 },
  deepgram: { depth: 5, persist: 3 },
  openai: { depth: 2, persist: 1 },
  nemotron: { depth: 2, persist: 1 },
};

// Case and punctuation are formatting, not recognition.
const wordNorm = (w) => w.toLowerCase().replace(/[^a-z0-9']/g, '');

// Apply a SOFT_STABILITY filter: record this hypothesis in history (a Map
// of segment key -> its last `persist` word arrays) and return the prefix
// that has sat deeper than `depth` words from the tail, unchanged, across
// all of them. History updates on every partial, even when the caller then
// emits something else, so a mid-session preset change never sees a gap.
function stablePrefix(history, key, text, { depth, persist }) {
  const cur = text.trim().split(/\s+/).filter(Boolean);
  const hist = history.get(key) || [];
  hist.push(cur);
  while (hist.length > persist) hist.shift();
  history.set(key, hist);
  if (hist.length < persist) return '';
  const limit = Math.min(...hist.map((h) => h.length)) - depth;
  let n = 0;
  while (n < limit && hist.every((h) => wordNorm(h[n]) === wordNorm(cur[n]))) n++;
  return cur.slice(0, n).join(' ');
}

// Stability history for the sessionless engines, keyed by softKey
// (ingest:client:segment); cleaned on withdraw, final, and /reset.
const ingestHypothesis = new Map();

// Abandonment net for /ingest soft segments: a sender that dies
// mid-utterance never posts the final, and confirmed-render consumers would
// wait on that segment forever. Each interim re-arms a clock holding the
// latest hypothesis; the final or a withdrawal disarms it, and if it fires,
// the hypothesis hardens as the final. A live engine updates many times a
// second, so 30 s of stillness means a dead sender.
// DOTIFY_INGEST_ABANDON_S sets the delay in seconds; 0 disables the net.
const INGEST_ABANDON_MS = (() => {
  const raw = process.env.DOTIFY_INGEST_ABANDON_S;
  if (raw === undefined || raw === '') return 30000;
  const s = Number(raw);
  return Number.isFinite(s) && s > 0 ? Math.round(s * 1000) : 0;
})();
const abandonTimers = new Map(); // softKey -> timeout

function armAbandonClock(softKey, hypothesis) {
  if (!INGEST_ABANDON_MS) return;
  clearTimeout(abandonTimers.get(softKey));
  abandonTimers.set(softKey, setTimeout(() => {
    abandonTimers.delete(softKey);
    ingestHypothesis.delete(softKey);
    emitFinal(hypothesis, undefined, null, softTake(softKey));
  }, INGEST_ABANDON_MS));
}

function disarmAbandonClock(softKey) {
  clearTimeout(abandonTimers.get(softKey));
  abandonTimers.delete(softKey);
}

const SOFT_REVISE_MS = process.env.DOTIFY_SOFT_REVISE_MS !== undefined
  ? Math.max(0, Number(process.env.DOTIFY_SOFT_REVISE_MS) || 0)
  : 300;
const SOFT_DISABLED = process.env.DOTIFY_SOFT_DISABLE === '1';

// ---- Latency presets ---------------------------------------------------------
//
// The page always runs balanced; the other presets remain for tests and
// tuning (POST /latency, ?latency= on /audio).
//   fastest   render the raw hypothesis minus SOFT_HOLDBACK_WORDS as soon as
//             the queue reaches it; a word can freeze wrong.
//   balanced  render the engine's SOFT_STABILITY prefix eagerly, or
//             confirmed text only for engines without a filter.
//   accurate  confirmed text only, and turn boundaries belong to the vendor:
//             no forced-endpoint backstop (it cost AssemblyAI +1.5 WER and
//             ElevenLabs +6.4 on the AMI benchmark). OpenAI, which has no
//             vendor turn detection, commits only at pauses.
// Consumers learn the rendering (eager or confirmed) from the `render`
// field of the {type:'latency'} message.
const LATENCY_MODES = new Set(['fastest', 'balanced', 'accurate']);
// Vendor-governed turns: the gap gate still tracks speech (close-flush
// guards need it) but never forces a boundary or a silence discard.
const VENDOR_TURNS_GATE = { minSegmentMs: Infinity, maxSegmentMs: Infinity };
// OpenAI accurate: commit only at real pauses, but keep the silence discard.
const OPENAI_ACCURATE_GATE = { minSegmentMs: 2500, maxSegmentMs: Infinity,
                               gapSilenceMs: 350, silenceDiscardMs: 2500 };
const ACCURATE_ENDPOINTING_MS = 500;   // Deepgram's own endpointer stretch
// gpt-live-transcribe's own latency dial per preset.
const OPENAI_DELAY = { fastest: 'low', balanced: 'medium', accurate: 'xhigh' };
let currentLatency = 'balanced';
// Whether the current soft source has a SOFT_STABILITY filter; decides
// balanced-mode rendering.
let softSourceStable = false;
let currentRender = 'confirmed';

function renderFor(mode) {
  if (mode === 'accurate') return 'confirmed';
  if (mode === 'fastest') return 'eager';
  return softSourceStable ? 'eager' : 'confirmed';
}

function setLatencyMode(mode, sourceStable) {
  if (!LATENCY_MODES.has(mode)) return;
  if (sourceStable !== undefined) softSourceStable = sourceStable;
  const render = renderFor(mode);
  if (mode === currentLatency && render === currentRender) return;
  currentLatency = mode;
  currentRender = render;
  fanoutFinalized({ type: 'latency', mode, render });
}

// Segment ids are unique per server boot, so after a restart a stale
// revision can never hit a consumer's old segment.
const SEGMENT_BOOT = crypto.randomBytes(3).toString('base64url');
let segmentSeq = 0;
function newSegmentId() {
  return `${SEGMENT_BOOT}-${++segmentSeq}`;
}

const softSegments = new Map(); // segment key -> { id, text, lastAt }
let audioSessionSeq = 0;        // namespaces provider itemIds across sessions

function fanoutFinalized(payload) {
  const line = JSON.stringify({ ...payload, ts: new Date().toISOString() });
  for (const client of finalWss.clients) {
    if (client.readyState === WebSocket.OPEN) client.send(line);
  }
}

// A (possibly revised) hypothesis for a still-open segment: emit all but
// its last holdbackWords words, as {soft} the first time, {revise} after.
function softUpdate(key, hypothesis, holdbackWords = SOFT_HOLDBACK_WORDS,
                    scriptGuard = false) {
  if (SOFT_DISABLED) return;
  if (isScriptJunk(hypothesis, scriptGuard)) return;
  const words = hypothesis.trim().split(/\s+/).filter(Boolean);
  if (words.length <= holdbackWords) return;
  const stable = correctText(
    words.slice(0, words.length - holdbackWords).join(' '));
  const entry = softSegments.get(key);
  if (!entry) {
    const id = newSegmentId();
    softSegments.set(key, { id, text: stable, lastAt: Date.now() });
    fanoutFinalized({ type: 'soft', id, text: stable });
    return;
  }
  if (stable === entry.text) return;
  const now = Date.now();
  if (now - entry.lastAt < SOFT_REVISE_MS) return; // the final flushes the rest
  entry.text = stable;
  entry.lastAt = now;
  fanoutFinalized({ type: 'revise', revise: entry.id, text: stable });
}

// The segment finalized to nothing (silence/noise): take back its soft text.
function softWithdraw(key) {
  const entry = softSegments.get(key);
  if (!entry) return;
  softSegments.delete(key);
  fanoutFinalized({ type: 'revise', revise: entry.id, text: '' });
}

// A final is about to emit for this key: claim the soft segment's id, so the
// final hardens it, or null when none is open.
function softTake(key) {
  const entry = softSegments.get(key);
  if (!entry) return null;
  softSegments.delete(key);
  return entry.id;
}

// A closing session (or /reset) abandons its open soft segments; no final
// will ever come for them, and a confirmed-render consumer would wait behind
// them forever. The words were spoken, so each one hardens on its last soft
// text. Through emitFinal, so dictionary correction and a pending speaker
// hold apply as for any final.
function softHardenAbandoned(keyPrefix, notify) {
  for (const [key, entry] of softSegments) {
    if (!key.startsWith(keyPrefix)) continue;
    softSegments.delete(key);
    emitFinal(entry.text, undefined, notify, entry.id);
  }
}

// The one writer of transcript.txt. Appends are queued: back-to-back
// appendFile calls each open the file independently and may land out of
// order.
let transcriptWrites = Promise.resolve();
function appendTranscript(text) {
  transcriptWrites = transcriptWrites.then(() => new Promise((resolve) => {
    fs.appendFile(TRANSCRIPT_FILE, text + '\n', (err) => {
      if (err) console.error('Failed to append to transcript.txt:', err.message);
      resolve();
    });
  }));
}

// Every final an /audio session settles is remembered here (bounded) with a
// monotonic seq. A reconnecting page reports the last seq it rendered
// (?last_seq=) and the gap replays into its transcript box, so finals that
// settle while the page has no socket (a session's closing flush lands after
// the socket is gone) still reach the screen. /ingest finals are never
// logged: the Windows overlay appends those to the box from the /ingest
// response, so replaying them would double the text.
const FINALS_LOG_MAX = 500;
const finalsLog = [];
let finalsSeq = 0;
function recordFinal(text) {
  const seq = ++finalsSeq;
  finalsLog.push({ seq, text });
  if (finalsLog.length > FINALS_LOG_MAX) finalsLog.shift();
  return seq;
}

function broadcastFinal(text, speaker, id) {
  const segId = id || newSegmentId();
  fanoutFinalized({
    type: 'final',
    id: segId,
    text,
    ...(speaker ? { speaker } : {}),
  });
  return segId;
}

// Display acks (fastest preset only). Under eager rendering the display may
// already have streamed soft words that a later correction cannot take back,
// so the provider's final is not necessarily what the reader got. The
// braille engine acks each final over /finalized with the text it actually
// showed, and transcript.txt, the page and the recorder wait for that ack.
// Finals settle in order; a missing ack falls back to the provider's text
// after SHOWN_ACK_MS.
const SHOWN_ACK_MS = process.env.DOTIFY_SHOWN_ACK_MS !== undefined
  ? Math.max(0, Number(process.env.DOTIFY_SHOWN_ACK_MS) || 0)
  : 700;
const pendingShown = [];   // FIFO: { id, text, notify, settled, timer }

function settleFinal(text, notify, id) {
  if (currentLatency !== 'fastest' || finalWss.clients.size === 0) {
    appendTranscript(text);
    if (notify) notify(text);
    return;
  }
  const entry = { id, text, notify, settled: false, timer: null };
  entry.timer = setTimeout(() => {
    entry.settled = true;
    drainShown();
  }, SHOWN_ACK_MS);
  pendingShown.push(entry);
}

function resolveShown(id, shownText) {
  const entry = pendingShown.find((e) => e.id === id && !e.settled);
  if (!entry) return;
  // A segment the display dropped entirely keeps the provider's words.
  if (shownText.trim()) entry.text = shownText.trim();
  entry.settled = true;
  clearTimeout(entry.timer);
  drainShown();
}

function drainShown() {
  while (pendingShown.length && pendingShown[0].settled) {
    const entry = pendingShown.shift();
    clearTimeout(entry.timer);
    appendTranscript(entry.text);
    if (entry.notify) entry.notify(entry.text);
  }
}

// Settle everything now, with the provider's text.
function flushShown() {
  for (const entry of pendingShown) {
    entry.settled = true;
    clearTimeout(entry.timer);
  }
  drainShown();
}

// Emit one decided final, prefixed when the speaker differs from the last
// one printed. notify (optional) receives the text exactly as displayed, once
// it settles. id (optional) hardens an already-emitted soft segment.
function releaseFinal(text, speaker, notify, id) {
  if (speaker && (speaker !== lastSpeaker || reAnnounce === speaker)) {
    lastSpeaker = speaker;
    if (reAnnounce === speaker) reAnnounce = null;
    text = `${speakerNames.displayName(speaker)}: ${text}`;
  }
  const segId = broadcastFinal(text, speaker, id);
  settleFinal(text, notify, segId);
}

function resolveHold(asSpeaker) {
  if (!hold) return;
  const { entries, timer } = hold;
  clearTimeout(timer);
  hold = null;
  for (const entry of entries) {
    releaseFinal(entry.text, asSpeaker, entry.notify, entry.id);
  }
}

// Release a pending hold under its own label, so no text is ever dropped.
function flushHold() {
  if (hold) resolveHold(hold.speaker);
}

// Diarization labels are per transcription session ("A" may be someone new
// next time): start over, flushing any held final first.
function resetSpeakerState() {
  flushHold();
  lastSpeaker = null;
  reAnnounce = null;
  speakerNames.reset();
}

// Every final passes through here: dictionary correction, the
// hallucination guard, then the speaker-label hold.
function emitFinal(text, speaker, notify, id, scriptGuard = false) {
  text = correctText(text);
  if (isScriptJunk(text, scriptGuard)) {
    console.log(`Dropped non-English final (hallucination guard): ${text}`);
    // Withdraw any soft text it was hardening, and let the page clear its
    // in-progress entry.
    if (id) fanoutFinalized({ type: 'revise', revise: id, text: '' });
    if (notify) notify('');
    return;
  }
  const labeled = speaker && speaker !== 'UNKNOWN' ? speaker : null;

  if (hold) {
    if (!labeled) {
      // UNKNOWN / undiarized mid-hold: queue behind it to keep text in order.
      hold.entries.push({ text, notify, id });
    } else if (labeled === hold.speaker) {
      // Second consecutive turn with the new label: the change is real.
      hold.entries.push({ text, notify, id });
      resolveHold(labeled);
    } else if (labeled === lastSpeaker) {
      // The old speaker is back: the held label was a one-turn flicker.
      resolveHold(lastSpeaker);
      releaseFinal(text, labeled, notify, id);
    } else {
      // A third label while holding: single-turn evidence is all we'll get
      // for the held turn, so trust its label, then vet the new one.
      resolveHold(hold.speaker);
      startHold(labeled, text, notify, id);
    }
    return;
  }

  // A session's first labeled final announces itself at once: there is no
  // established speaker to flicker from.
  if (!labeled || labeled === lastSpeaker || lastSpeaker === null) {
    releaseFinal(text, labeled, notify, id);
  } else {
    startHold(labeled, text, notify, id);
  }
}

function startHold(speaker, text, notify, id) {
  hold = {
    speaker,
    entries: [{ text, notify, id }],
    timer: setTimeout(() => resolveHold(speaker), SPEAKER_CONFIRM_MS),
  };
}

audioWss.on('connection', (browser, req) => {
  const sendToBrowser = (obj) => {
    if (browser.readyState === WebSocket.OPEN) browser.send(JSON.stringify(obj));
  };

  const { query } = splitUrl(req.url);
  const providerName = query.get('provider') || DEFAULT_PROVIDER;
  const provider = PROVIDERS[providerName];
  if (!provider) {
    sendToBrowser({ type: 'error', message: `Unknown transcription provider "${providerName}".` });
    browser.close();
    return;
  }
  const apiKey = apiKeyFor(provider);
  if (!apiKey) {
    sendToBrowser({ type: 'error', message: `${provider.apiKeyName} is missing. Add it under Settings or pick another model.` });
    browser.close();
    return;
  }
  console.log(`Browser connected; opening ${providerName} transcription session...`);

  // A hold left over from the previous session flushes here, into the
  // finals log, so the replay below includes it.
  resetSpeakerState();

  // Replay the finals this page has not rendered (see finalsLog). Without
  // last_seq nothing is replayed.
  const lastSeq = query.has('last_seq') ? Number(query.get('last_seq')) : NaN;
  if (Number.isFinite(lastSeq)) {
    for (const entry of finalsLog) {
      if (entry.seq > lastSeq) {
        sendToBrowser({
          type: 'final', item_id: `replay-${entry.seq}`,
          text: entry.text, seq: entry.seq,
        });
      }
    }
  }
  // Namespaces this session's itemIds in the soft-segment map.
  const sessionKey = `s${++audioSessionSeq}`;
  // Session recording, switchable mid-session ({type:'record', on}); each
  // start writes a new file pair. The page announces what it hears back, so
  // a failed start it asked for must be reported.
  let recorder = null;
  let recordTake = 0;
  const startRecording = (announceFailure) => {
    if (recorder) return;
    recordTake += 1;
    const takeName = recordTake === 1 ? sessionKey : `${sessionKey}-take${recordTake}`;
    recorder = createSessionRecorder(RECORD_DIR, takeName);
    if (recorder) {
      sendToBrowser({ type: 'recording', file: recorder.file });
    } else if (announceFailure) {
      sendToBrowser({ type: 'recording-failed' });
    }
  };
  const stopRecording = () => {
    if (!recorder) return;
    const file = recorder.file;
    recorder.close();
    recorder = null;
    sendToBrowser({ type: 'recording-stopped', file });
  };
  if (RECORD_ALL || query.get('record') === '1') {
    startRecording(query.get('record') === '1');
  }
  // Items with partials that have not finalized yet, oldest first. Soft text
  // streams only for the oldest: OpenAI can stream the next item while the
  // previous one's final is pending, and that final must not land after the
  // next item's soft text. The other engines are strictly sequential.
  const openItems = [];

  const scriptGuard = provider.scriptGuard === true;

  // Conversation mode (speaker labels) exists only on AssemblyAI. It also
  // turns soft text off, so it must not stick to the other engines.
  const conversation = query.get('conversation') === '1' && providerName === 'assemblyai';
  const maxSpeakersRaw = Number(query.get('max_speakers'));
  const maxSpeakers = Number.isInteger(maxSpeakersRaw) && maxSpeakersRaw >= 1 && maxSpeakersRaw <= 10
    ? maxSpeakersRaw
    : undefined;

  const latencyRaw = query.get('latency');
  const latency = LATENCY_MODES.has(latencyRaw) ? latencyRaw : 'balanced';
  // ElevenLabs always renders its stable prefix eagerly (it has no backstop
  // for confirmed-only rendering to wait on, and its raw tail carries deep
  // rewrites); consumers see that announced as 'fastest'.
  const stability = SOFT_STABILITY[providerName];
  setLatencyMode(providerName === 'elevenlabs' ? 'fastest' : latency,
                 Boolean(stability));

  const prevHypothesis = new Map(); // stability history, keyed by itemId

  // Lifecycle handlers, held by the cost gate across its silent-lull
  // re-dials: only a real death or the browser leaving tears down.
  const onReady = () => {
    console.log('Transcription session open.');
    sendToBrowser({ type: 'ready' });
  };
  const onError = (message) => sendToBrowser({ type: 'error', message });
  const onClose = () => {
    // The provider has flushed its trailing finals. Release a pending
    // speaker hold while this session's notify and recorder still work,
    // then harden soft segments that never got a final.
    flushHold();
    softHardenAbandoned(`${sessionKey}:`, (displayText) => {
      if (recorder) recorder.final(displayText);
      recordFinal(displayText);
    });
    if (recorder) recorder.close();
    // The stable soft source is gone; drop ElevenLabs' 'fastest' too.
    setLatencyMode(latency, false);
    if (session && session.stats) {
      const { fedMs, forwardedMs, dials } = session.stats;
      console.log(`Cost gate: streamed ${Math.round(forwardedMs / 1000)} s of `
        + `${Math.round(fedMs / 1000)} s mic audio (${dials} dial${dials === 1 ? '' : 's'}).`);
    }
    sendToBrowser({ type: 'closed' });
    if (browser.readyState === WebSocket.OPEN) browser.close();
  };

  const baseOpts = {
    apiKey,
    model: provider.model,
    language: LANGUAGE,
    conversation,
    maxSpeakers,
    gateOverrides: latency === 'accurate'
      ? (providerName === 'openai' ? OPENAI_ACCURATE_GATE : VENDOR_TURNS_GATE)
      : undefined,
    endpointingMs: latency === 'accurate' ? ACCURATE_ENDPOINTING_MS : undefined,
    delay: providerName === 'openai' ? OPENAI_DELAY[latency] : undefined,
    // Dictionary words (not their aliases) for the vendor's vocabulary
    // biasing, read once per session.
    dictionary: dictionary.boostTerms(dictionaryEntries),
    onPartial: (itemId, text) => {
      sendToBrowser({ type: 'partial', item_id: itemId, text });
      // An empty partial means the segment finalized to nothing.
      if (!conversation) {
        const key = `${sessionKey}:${itemId}`;
        if (text) {
          if (!openItems.includes(itemId)) openItems.push(itemId);
          if (openItems[0] === itemId) {
            const stable = stability
              ? stablePrefix(prevHypothesis, itemId, text, stability) : null;
            if (stability && (providerName === 'elevenlabs'
                              || currentLatency !== 'fastest')) {
              if (stable) softUpdate(key, stable, 0, scriptGuard);
            } else {
              softUpdate(key, text, SOFT_HOLDBACK_WORDS, scriptGuard);
            }
          }
        } else {
          const idx = openItems.indexOf(itemId);
          if (idx >= 0) openItems.splice(idx, 1);
          prevHypothesis.delete(itemId);
          softWithdraw(key);
        }
      }
    },
    onFinal: (itemId, text, speaker, turnPcm) => {
      // Fire-and-forget: a resolved name applies to later prefixes.
      if (speaker && speaker !== 'UNKNOWN' && turnPcm) {
        speakerNames.submitTurn(speaker, turnPcm);
      }
      const idx = openItems.indexOf(itemId);
      if (idx >= 0) openItems.splice(idx, 1);
      prevHypothesis.delete(itemId);
      const segId = conversation ? null : softTake(`${sessionKey}:${itemId}`);
      emitFinal(text, speaker, (displayText) => {
        if (recorder) recorder.final(displayText);
        // Empty text is the hallucination guard clearing the page's entry.
        const seq = displayText ? recordFinal(displayText) : undefined;
        sendToBrowser({ type: 'final', item_id: itemId, text: displayText, seq });
      }, segId, scriptGuard);
    },
  };

  const session = COST_GATE_OFF
    ? provider.createSession({ ...baseOpts, onReady, onError, onClose })
    : createGatedSession(
      (handlers) => provider.createSession({ ...baseOpts, ...handlers }),
      {
        idleCloseMs: provider.idleCloseMs,
        log: (line) => console.log(line),
        onReady,
        onError,
        onClose,
      },
    );

  browser.on('message', (data, isBinary) => {
    if (isBinary) {
      const pcm = Buffer.from(data);
      if (recorder) recorder.audio(pcm);
      session.sendAudio(pcm);
      return;
    }
    // Text frames are the control channel.
    let msg;
    try { msg = JSON.parse(data); } catch { return; }
    if (msg && msg.type === 'record') {
      if (msg.on) startRecording(true);
      else stopRecording();
    }
  });

  browser.on('close', () => {
    console.log('Browser disconnected.');
    session.close();
  });
});

// POST /ingest, from the sessionless engines:
//   {text}                          a final; answers {text} (as displayed),
//                                   {dropped:true} or {deferred:true}
//   {interim:true, client, segment, text, engine?}
//                                   the live hypothesis for one utterance
//                                   (client: a per-page-load token; segment:
//                                   the utterance counter). A later final
//                                   with the same {client, segment} hardens
//                                   it. engine names a SOFT_STABILITY filter.
//   {typed:true, text}              typed-to-display text: recorded only
//   speaker, pcm                    optional label and base64 turn audio for
//                                   speaker identification
function handleIngestBody(body, res) {
  // Unparseable JSON or a bare scalar has no text: a 400 below.
  let payload = null;
  try { payload = JSON.parse(body); } catch { payload = null; }
  if (!payload || typeof payload !== 'object') payload = {};
  const text = String(payload.text || '').trim();
  const interim = payload.interim === true;
  const typed = payload.typed === true;
  const ingestStability = typeof payload.engine === 'string'
    ? SOFT_STABILITY[payload.engine] || null : null;
  const speaker = typeof payload.speaker === 'string'
    && /^[A-Za-z0-9_]{1,16}$/.test(payload.speaker) ? payload.speaker : undefined;
  const idPart = /^[A-Za-z0-9_-]{1,32}$/;
  const softKey = idPart.test(String(payload.client || ''))
    && idPart.test(String(payload.segment ?? ''))
    ? `ingest:${payload.client}:${payload.segment}` : null;
  const pcm = typeof payload.pcm === 'string' && payload.pcm
    ? Buffer.from(payload.pcm, 'base64') : null;

  if (interim) {
    // Labeled text never streams soft (labels are decided at final).
    if (softKey && !speaker) {
      if (text) {
        if (ingestStability) {
          // Switch balanced consumers to eager before this first soft.
          setLatencyMode(currentLatency, true);
          const stable = stablePrefix(ingestHypothesis, softKey, text, ingestStability);
          if (stable) softUpdate(softKey, stable, 0);
        } else {
          softUpdate(softKey, text);
        }
        armAbandonClock(softKey, text);
      } else {
        ingestHypothesis.delete(softKey);
        disarmAbandonClock(softKey);
        softWithdraw(softKey);
      }
    }
    res.writeHead(204).end();
    return;
  }
  if (!text) { res.writeHead(400).end('missing text'); return; }
  // Typed text already reached the display through the braille engine's
  // control bridge, so it is only recorded: broadcasting it would braille
  // it twice. No dictionary pass either; the user typed what they meant.
  if (typed) {
    // Speech said before this line may sit in the speaker hold.
    flushHold();
    appendTranscript(text);
    res.writeHead(204).end();
    return;
  }
  if (speaker && pcm) speakerNames.submitTurn(speaker, pcm);
  if (softKey) {
    ingestHypothesis.delete(softKey); // utterance closed
    disarmAbandonClock(softKey);
  }
  // The client appends its transcript box from this response, so it shows
  // the text as displayed. A final parked in the speaker hold or waiting on
  // a display ack answers deferred:true instead: the response must not wait,
  // or the overlay's next POST would queue behind it.
  let ruled = null;
  emitFinal(text, speaker, (displayText) => {
    ruled = displayText ? { text: displayText } : { dropped: true };
  }, softKey ? softTake(softKey) : null);
  res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
  res.end(JSON.stringify(ruled || { deferred: true }));
}

server.listen(PORT, '127.0.0.1', () => {
  console.log(`Dotify live transcription: http://127.0.0.1:${PORT}`);
  console.log(`Finalized text stream:  ws://127.0.0.1:${PORT}/finalized`);
  console.log(`Finalized text file:    ${TRANSCRIPT_FILE}`);
});
