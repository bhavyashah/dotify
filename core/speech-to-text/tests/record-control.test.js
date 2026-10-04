// Live recording control: the record toggle works mid-session, not just for
// the next one. Exercised end-to-end against server.js with the env-gated mock
// provider (DOTIFY_MOCK_PROVIDER=1) — a keyed engine that never dials out —
// so the /audio machinery runs offline and deterministically.

const { test, before, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const os = require('node:os');
const fs = require('node:fs');
const WebSocket = require('ws');
const rig = require('./helpers');

const { readWav } = require('../tools/wav');

// node --test runs files in parallel: 8799 = server.test.js, 8798 = ui.test.js,
// 8795/8794 = speaker-naming.test.js.
const PORT = 8793;

let child;
let dataDir;      // holds the tmp .env; recordings land in <dataDir>/recordings
const recordingsDir = () => path.join(dataDir, 'recordings');

before(async () => {
  dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'dotify-record-'));
  child = rig.startServer({
    port: PORT,
    env: {
      DOTIFY_ENV_FILE: path.join(dataDir, 'provider-config.env'),
      DOTIFY_TRANSCRIPT_FILE: path.join(dataDir, 'transcript.txt'),
      DOTIFY_DICTIONARY_FILE: path.join(dataDir, 'dictionary.json'),
      DOTIFY_MOCK_PROVIDER: '1',
      DOTIFY_MOCK_KEY: 'mock-key',
    },
    deleteKeys: ['DOTIFY_RECORD_DIR'], // RECORD_ALL must stay off for these tests
  });
  await rig.waitForListening(`http://127.0.0.1:${PORT}`);
});

after(() => {
  if (child) child.kill();
  try { fs.rmSync(dataDir, { recursive: true, force: true }); } catch {}
});

// A mock-provider /audio session with a message queue the tests await on.
function openSession(params = '') {
  const ws = new WebSocket(
    `ws://127.0.0.1:${PORT}/audio?provider=mock${params}`);
  const queue = [];
  const waiters = [];
  ws.on('message', (data, isBinary) => {
    if (isBinary) return;
    const msg = JSON.parse(data.toString());
    const w = waiters.shift();
    if (w) w(msg);
    else queue.push(msg);
  });
  return {
    ws,
    next(timeoutMs = 3000) {
      if (queue.length) return Promise.resolve(queue.shift());
      return new Promise((resolve, reject) => {
        const timer = setTimeout(
          () => reject(new Error('timed out waiting for a server message')),
          timeoutMs);
        waiters.push((msg) => { clearTimeout(timer); resolve(msg); });
      });
    },
    async nextOfType(type) {
      // Skips unrelated traffic (e.g. 'ready') to the awaited message.
      for (let i = 0; i < 10; i++) {
        const msg = await this.next();
        if (msg.type === type) return msg;
      }
      throw new Error(`no ${type} message arrived`);
    },
    open() {
      return new Promise((resolve, reject) => {
        ws.on('open', resolve);
        ws.on('error', reject);
      });
    },
    close() {
      return new Promise((resolve) => {
        ws.on('close', resolve);
        ws.close();
      });
    },
  };
}

function pcmOfBytes(bytes) {
  const buf = Buffer.alloc(bytes);
  for (let i = 0; i + 1 < bytes; i += 2) buf.writeInt16LE(i, i);
  return buf;
}

async function waitForWav(file, wantBytes, timeoutMs = 3000) {
  // The header's sizes are patched at recorder.close(); poll until the file
  // says it holds the audio we sent.
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const wav = readWav(file);
      if (wav.pcm.length === wantBytes) return wav;
    } catch {}
    await new Promise((r) => setTimeout(r, 50));
  }
  throw new Error(`${file} never reached ${wantBytes} audio bytes`);
}

test('record=1 records the whole session from its first audio frame', async () => {
  const s = openSession('&record=1');
  await s.open();
  const recording = await s.nextOfType('recording');
  assert.ok(recording.file.endsWith('.wav'));
  await s.nextOfType('ready');
  const pcm = pcmOfBytes(4800);
  s.ws.send(pcm);
  await new Promise((r) => setTimeout(r, 200)); // let the frame land
  await s.close();
  const wav = await waitForWav(recording.file, pcm.length);
  assert.ok(wav.pcm.equals(pcm));
});

test('the record toggle starts and stops the recorder mid-session', async () => {
  const s = openSession();
  await s.open();
  await s.nextOfType('ready');

  // Audio before the toggle is NOT recorded anywhere.
  s.ws.send(pcmOfBytes(9600));
  await new Promise((r) => setTimeout(r, 100));

  // Start mid-session: a fresh file, fed from now on.
  s.ws.send(JSON.stringify({ type: 'record', on: true }));
  const first = await s.nextOfType('recording');
  const firstPcm = pcmOfBytes(2400);
  s.ws.send(firstPcm);
  await new Promise((r) => setTimeout(r, 200));

  // A second 'on' while recording is a no-op — no second file, no message.
  s.ws.send(JSON.stringify({ type: 'record', on: true }));

  // Stop mid-session: the file is finalized while the session keeps going.
  s.ws.send(JSON.stringify({ type: 'record', on: false }));
  const stopped = await s.nextOfType('recording-stopped');
  assert.strictEqual(stopped.file, first.file);
  const firstWav = await waitForWav(first.file, firstPcm.length);
  assert.ok(firstWav.pcm.equals(firstPcm));

  // Audio between takes is not recorded.
  s.ws.send(pcmOfBytes(9600));
  await new Promise((r) => setTimeout(r, 100));

  // Start again: a NEW take in a new file.
  s.ws.send(JSON.stringify({ type: 'record', on: true }));
  const second = await s.nextOfType('recording');
  assert.notStrictEqual(second.file, first.file);
  assert.match(second.file, /-take2\.wav$/);
  const secondPcm = pcmOfBytes(7200);
  s.ws.send(secondPcm);
  await new Promise((r) => setTimeout(r, 200));

  // Session close finalizes the take still in flight.
  await s.close();
  const secondWav = await waitForWav(second.file, secondPcm.length);
  assert.ok(secondWav.pcm.equals(secondPcm));

  // Exactly the two takes exist — the pre-toggle audio produced no file.
  const wavs = fs.readdirSync(recordingsDir()).filter((f) => f.endsWith('.wav'));
  const mine = [first.file, second.file].map((f) => path.basename(f));
  assert.ok(mine.every((f) => wavs.includes(f)));
});

test('a plain session without the toggle records nothing', async () => {
  const beforeCount = fs.existsSync(recordingsDir())
    ? fs.readdirSync(recordingsDir()).length
    : 0;
  const s = openSession();
  await s.open();
  await s.nextOfType('ready');
  s.ws.send(pcmOfBytes(4800));
  await new Promise((r) => setTimeout(r, 200));
  await s.close();
  await new Promise((r) => setTimeout(r, 200));
  const afterCount = fs.existsSync(recordingsDir())
    ? fs.readdirSync(recordingsDir()).length
    : 0;
  assert.strictEqual(afterCount, beforeCount);
});
