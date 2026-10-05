#!/usr/bin/env node
// Regenerates the screenshots in this folder from the real Windows app.
//
//   node docs/screenshots/capture.js
//
// Needs, on Windows:
// - the built app image (Windows/installer/stage; run Launch Dotify.cmd once,
//   or Windows/installer/build.ps1),
// - the offline model downloaded once through Dotify's Settings (it lives in
//   %LOCALAPPDATA%\Dotify\models),
// - Microsoft Edge.
//
// What it does: starts the staged speech server, the offline decode service
// and the braille ticker on a simulated 40-cell display (capture_display.py),
// with an empty settings file, so no API key is read, and throwaway
// transcript and dictionary files. It then opens the app page in headless
// Edge with a fake microphone that plays a synthesized speech clip (Windows
// text to speech, the SPEECH text below). Nemotron transcribes it and the
// ticker reads it at its default pace, exactly as in a live session. Nothing
// on the page is edited before capture.
//
// Ports 8788, 8790 and 8791 must be free (quit Dotify first).
'use strict';

const { spawn, execFileSync } = require('node:child_process');
const fs = require('node:fs');
const net = require('node:net');
const os = require('node:os');
const path = require('node:path');

const OUT = __dirname;
const REPO = path.resolve(__dirname, '..', '..');
const STAGE = path.join(REPO, 'Windows', 'installer', 'stage');
const MODEL = path.join(process.env.LOCALAPPDATA || '', 'Dotify', 'models');
const EDGE = [
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
].find((p) => fs.existsSync(p));
const DISPLAY_NAME = 'APH Mantis Q40';
const WIDTH = 1000;
const SCALE = 2;

const SPEECH = 'Good morning, everyone, and thank you for coming. Before we '
  + 'start, a quick reminder that today\'s meeting will run about forty '
  + 'minutes. First, we\'ll review the plans for the community garden. The '
  + 'city has approved the new location next to the library, and the first '
  + 'planting day is Saturday, the twelfth. We still need volunteers to bring '
  + 'tools and water. After that, Maria will share the budget, and then we\'ll '
  + 'open the floor for questions.';

function fail(message) {
  console.error(`capture: ${message}`);
  process.exit(1);
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function portOpen(port) {
  return new Promise((resolve) => {
    const socket = net.connect(port, '127.0.0.1');
    socket.once('connect', () => { socket.destroy(); resolve(true); });
    socket.once('error', () => resolve(false));
  });
}

async function waitForPort(port, seconds) {
  for (let i = 0; i < seconds * 4; i += 1) {
    if (await portOpen(port)) return;
    await sleep(250);
  }
  throw new Error(`nothing answered on port ${port}`);
}

async function waitFor(check, seconds, what) {
  for (let i = 0; i < seconds * 2; i += 1) {
    if (await check()) return;
    await sleep(500);
  }
  throw new Error(`timed out waiting for ${what}`);
}

// Windows text to speech (the Zira voice, a little slower than default; it
// transcribes most cleanly), 16 kHz mono, with 3 s of leading silence so the
// first words aren't spoken before the decode service is listening.
function synthesizeSpeech(wavPath) {
  const script = [
    'Add-Type -AssemblyName System.Speech',
    '$s = New-Object System.Speech.Synthesis.SpeechSynthesizer',
    '$f = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, '
      + '[System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, '
      + '[System.Speech.AudioFormat.AudioChannel]::Mono)',
    "$s.SelectVoice('Microsoft Zira Desktop')",
    '$s.Rate = -1',
    '$s.SetOutputToWaveFile($env:CAPTURE_WAV, $f)',
    '$p = New-Object System.Speech.Synthesis.PromptBuilder',
    '$p.AppendBreak([TimeSpan]::FromSeconds(3))',
    '$p.AppendText($env:CAPTURE_TEXT)',
    '$s.Speak($p)',
    '$s.Dispose()',
  ].join('; ');
  execFileSync('powershell', ['-NoProfile', '-Command', script], {
    env: { ...process.env, CAPTURE_WAV: wavPath, CAPTURE_TEXT: SPEECH },
    stdio: 'inherit',
  });
}

// The decode service loads the model on its first connection. Load it before
// the page opens: a slow first load makes the page retry, which restarts the
// fake microphone's clip from the top and repeats the opening in the
// transcript.
function warmDecoder() {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket('ws://127.0.0.1:8791/transcribe',
      { headers: { Origin: 'http://127.0.0.1:8788' } });
    const timer = setTimeout(() => { ws.close(); reject(new Error('the offline model did not load')); }, 60000);
    ws.onmessage = (event) => {
      const message = JSON.parse(event.data);
      if (message.type === 'error') reject(new Error(message.message));
      if (message.type !== 'status' || message.state !== 'ready') return;
      clearTimeout(timer);
      ws.close();
      resolve();
    };
    ws.onerror = () => { clearTimeout(timer); reject(new Error('could not reach the offline decode service')); };
  });
}

// Minimal Chrome DevTools Protocol client over Node's built-in WebSocket.
async function connect(wsUrl) {
  const ws = new WebSocket(wsUrl);
  const pending = new Map();
  let nextId = 0;
  ws.onmessage = (event) => {
    const message = JSON.parse(event.data);
    const waiter = message.id && pending.get(message.id);
    if (!waiter) return;
    pending.delete(message.id);
    if (message.error) waiter.reject(new Error(message.error.message));
    else waiter.resolve(message.result);
  };
  await new Promise((resolve, reject) => {
    ws.onopen = resolve;
    ws.onerror = () => reject(new Error('could not connect to Edge'));
  });
  return {
    send(method, params = {}) {
      return new Promise((resolve, reject) => {
        nextId += 1;
        pending.set(nextId, { resolve, reject });
        ws.send(JSON.stringify({ id: nextId, method, params }));
      });
    },
    close() { ws.close(); },
  };
}

async function evaluate(page, expression) {
  const { result, exceptionDetails } = await page.send('Runtime.evaluate', {
    expression, awaitPromise: true, returnByValue: true,
  });
  if (exceptionDetails) throw new Error(exceptionDetails.text);
  return result.value;
}

async function save(page, name, clip) {
  const params = { format: 'png', captureBeyondViewport: true };
  if (clip) params.clip = { ...clip, scale: 1 };
  const { data } = await page.send('Page.captureScreenshot', params);
  fs.writeFileSync(path.join(OUT, name), Buffer.from(data, 'base64'));
  console.log(`capture: wrote docs/screenshots/${name}`);
}

// Crops a saved screenshot (System.Drawing, page coordinates times SCALE).
function crop(from, to, clip) {
  const [x, y, w, h] = [clip.x, clip.y, clip.width, clip.height]
    .map((v) => Math.round(v * SCALE));
  const script = [
    'Add-Type -AssemblyName System.Drawing',
    '$src = [System.Drawing.Bitmap]::FromFile($env:CROP_FROM)',
    `$out = $src.Clone((New-Object System.Drawing.Rectangle(${x}, ${y}, ${w}, ${h})), $src.PixelFormat)`,
    '$out.Save($env:CROP_TO, [System.Drawing.Imaging.ImageFormat]::Png)',
    '$out.Dispose(); $src.Dispose()',
  ].join('; ');
  execFileSync('powershell', ['-NoProfile', '-Command', script], {
    env: { ...process.env, CROP_FROM: path.join(OUT, from), CROP_TO: path.join(OUT, to) },
    stdio: 'inherit',
  });
  console.log(`capture: wrote docs/screenshots/${to}`);
}

// Page-space rectangle around a set of elements, with some padding.
function clipAround(selectors, pad = 16) {
  return `(() => {
    const rects = ${JSON.stringify(selectors)}
      .map((s) => document.querySelector(s).getBoundingClientRect());
    const left = Math.min(...rects.map((r) => r.left)) - ${pad};
    const top = Math.min(...rects.map((r) => r.top)) - ${pad};
    const right = Math.max(...rects.map((r) => r.right)) + ${pad};
    const bottom = Math.max(...rects.map((r) => r.bottom)) + ${pad};
    return { x: left + scrollX, y: top + scrollY,
      width: right - left, height: bottom - top };
  })()`;
}

async function fitPageHeight(page) {
  const height = await evaluate(page, 'document.documentElement.scrollHeight');
  await page.send('Emulation.setDeviceMetricsOverride', {
    width: WIDTH, height, deviceScaleFactor: SCALE, mobile: false,
  });
}

async function main() {
  if (process.platform !== 'win32') fail('run this on Windows.');
  if (!fs.existsSync(path.join(STAGE, 'launcher.ps1'))) {
    fail('build the app image first: Windows/installer/build.ps1');
  }
  if (!fs.existsSync(path.join(MODEL, 'nemotron-3.5-560ms-int8'))) {
    fail('download the offline model once in Dotify (Settings › Transcription).');
  }
  if (!EDGE) fail('Microsoft Edge was not found.');
  for (const port of [8788, 8790, 8791]) {
    if (await portOpen(port)) fail(`port ${port} is in use; quit Dotify first.`);
  }

  const work = fs.mkdtempSync(path.join(os.tmpdir(), 'dotify-capture-'));
  fs.writeFileSync(path.join(work, '.env'), '');
  const wav = path.join(work, 'speech.wav');
  synthesizeSpeech(wav);

  const env = {
    ...process.env,
    DOTIFY_LIBLOUIS_DIR: path.join(STAGE, 'vendor', 'liblouis'),
    DOTIFY_ENV_FILE: path.join(work, '.env'),
    DOTIFY_TRANSCRIPT_FILE: path.join(work, 'transcript.txt'),
    DOTIFY_DICTIONARY_FILE: path.join(work, 'dictionary.json'),
    DOTIFY_SPEAKERS_DIR: path.join(work, 'speakers'),
    DOTIFY_MODELS_DIR: MODEL,
  };
  const python = path.join(STAGE, 'runtime', 'python', 'python.exe');
  const braille = path.join(STAGE, 'Text to Braille');
  const children = [];
  const start = (file, args) => {
    const child = spawn(file, args, { cwd: STAGE, env, stdio: 'ignore' });
    children.push(child);
    return child;
  };

  try {
    start(path.join(STAGE, 'runtime', 'node', 'node.exe'),
      [path.join(STAGE, 'Speech to text', 'server.js')]);
    await waitForPort(8788, 20);
    start(python, [path.join(braille, 'local_speech_server.py'),
      '--model', path.join(MODEL, 'nemotron-3.5-560ms-int8'),
      '--manifest', path.join(STAGE, 'Speech to text', 'offline-model.json'),
      '--port', '8791']);
    await waitForPort(8791, 20);
    await warmDecoder();
    start(python, [path.join(__dirname, 'capture_display.py'), DISPLAY_NAME,
      path.join(braille, 'windows_run.py'), '--source', 'ws', '--sink', 'sim']);
    await waitForPort(8790, 20);

    const debugPort = 9333;
    start(EDGE, ['--headless=new', `--remote-debugging-port=${debugPort}`,
      `--user-data-dir=${path.join(work, 'edge')}`, '--no-first-run',
      '--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream',
      `--use-file-for-fake-audio-capture=${wav}%noloop`, 'about:blank']);
    await waitForPort(debugPort, 20);
    const targets = await (await fetch(`http://127.0.0.1:${debugPort}/json`)).json();
    const page = await connect(targets.find((t) => t.type === 'page').webSocketDebuggerUrl);
    await page.send('Emulation.setDeviceMetricsOverride', {
      width: WIDTH, height: 900, deviceScaleFactor: SCALE, mobile: false,
    });
    await page.send('Page.enable');
    await page.send('Page.navigate', { url: 'http://127.0.0.1:8788/' });

    // A session in progress: some text already read, a line on the display,
    // and more still waiting.
    await waitFor(() => evaluate(page, `
      document.querySelector('#finalized').innerText.trim().split(/\\s+/).length >= 30
      && document.querySelector('#pending-band').innerText.trim().length > 40`),
    150, 'the transcript to fill');

    // One capture, then crop: both images show the same moment.
    await fitPageHeight(page);
    const bands = await evaluate(page,
      clipAround(['.transcript-head', '#finalized', '#current-band', '#pending-band']));
    await save(page, 'dotify-window.png');
    crop('dotify-window.png', 'transcript-bands.png', bands);

    await evaluate(page, `(() => {
      document.querySelector('#settings').open = true;
      document.querySelector('[data-settings-category="braille"]').click();
    })()`);
    await sleep(500);
    await fitPageHeight(page);
    await save(page, 'braille-settings.png',
      await evaluate(page, clipAround(['#settings-category-braille'])));
    page.close();
  } finally {
    for (const child of children.reverse()) {
      try { execFileSync('taskkill', ['/PID', String(child.pid), '/T', '/F'], { stdio: 'ignore' }); } catch (_) { /* already gone */ }
    }
    await sleep(500);
    fs.rmSync(work, { recursive: true, force: true, maxRetries: 5 });
  }
}

main().catch((error) => fail(error.message));
