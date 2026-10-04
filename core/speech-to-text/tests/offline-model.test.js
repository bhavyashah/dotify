// offline-model.js download manager: resumable Range downloads, the
// exact-byte-size ready contract, cancel, and delete — against a local HTTP
// server, never the real Hugging Face mirror.

const { test, before, after, beforeEach } = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const path = require('node:path');
const os = require('node:os');
const fs = require('node:fs');

const { createOfflineModel } = require('../offline-model');

const FILES = [
  { name: 'encoder.int8.onnx', bytes: 40_000 },
  { name: 'tokens.txt', bytes: 500 },
];
const CONTENT = Object.fromEntries(
  FILES.map((f, i) => [f.name, Buffer.alloc(f.bytes, 65 + i)]));

let server;
let baseUrl;
let requests;       // [{name, range}] every download request the server saw
let stallNext;      // when set, the next response sends one byte then stalls
let stalled;        // resolves once a stalled response has started

function startServer() {
  return new Promise((resolve) => {
    server = http.createServer((req, res) => {
      const name = req.url.split('/').pop();
      requests.push({ name, range: req.headers.range || null });
      const body = CONTENT[name];
      if (!body) { res.writeHead(404).end(); return; }
      let from = 0;
      if (req.headers.range) {
        from = Number(req.headers.range.match(/bytes=(\d+)-/)[1]);
        res.writeHead(206, { 'Content-Length': body.length - from });
      } else {
        res.writeHead(200, { 'Content-Length': body.length });
      }
      if (stallNext) {
        stallNext = false;
        res.write(body.subarray(from, from + 1));
        stalled.resolve();
        return; // never ends — only an abort tears it down
      }
      res.end(body.subarray(from));
    });
    server.listen(0, '127.0.0.1', () => {
      baseUrl = `http://127.0.0.1:${server.address().port}/`;
      resolve();
    });
  });
}

let root;
function makeModel() {
  return createOfflineModel({
    modelsRoot: root,
    manifest: { dirName: 'model-under-test', label: 'test model', baseUrl, files: FILES },
  });
}
const modelDir = () => path.join(root, 'model-under-test');

async function until(check, timeoutMs = 5000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (check()) return;
    await new Promise((r) => setTimeout(r, 20));
  }
  throw new Error('condition never became true');
}

before(startServer);
after(() => server.close());
beforeEach(() => {
  requests = [];
  stallNext = false;
  root = fs.mkdtempSync(path.join(os.tmpdir(), 'dotify-offline-model-'));
});

test('fresh download fetches every file and flips ready', async () => {
  const model = makeModel();
  assert.deepStrictEqual(
    { ready: false, downloading: false, percent: 0 },
    (({ ready, downloading, percent }) => ({ ready, downloading, percent }))(model.status()));
  model.startDownload();
  await until(() => !model.status().downloading);
  const status = model.status();
  assert.strictEqual(status.ready, true);
  assert.strictEqual(status.percent, 100);
  assert.strictEqual(status.error, undefined);
  for (const f of FILES) {
    assert.strictEqual(fs.statSync(path.join(modelDir(), f.name)).size, f.bytes);
  }
  assert.deepStrictEqual(requests.map((r) => r.range), [null, null]);
});

test('download resumes a partial file with an HTTP Range', async () => {
  fs.mkdirSync(modelDir(), { recursive: true });
  fs.writeFileSync(
    path.join(modelDir(), 'encoder.int8.onnx'),
    CONTENT['encoder.int8.onnx'].subarray(0, 15_000));
  const model = makeModel();
  assert.strictEqual(model.status().downloadedBytes, 15_000);
  model.startDownload();
  await until(() => !model.status().downloading);
  assert.strictEqual(model.status().ready, true);
  assert.strictEqual(requests.find((r) => r.name === 'encoder.int8.onnx').range,
    'bytes=15000-');
  // The resumed file is byte-identical, not just size-identical.
  assert.ok(CONTENT['encoder.int8.onnx'].equals(
    fs.readFileSync(path.join(modelDir(), 'encoder.int8.onnx'))));
});

test('a stale oversized partial is discarded, not resumed', async () => {
  fs.mkdirSync(modelDir(), { recursive: true });
  fs.writeFileSync(path.join(modelDir(), 'tokens.txt'), Buffer.alloc(9_999, 90));
  const model = makeModel();
  assert.strictEqual(model.status().downloadedBytes, 0); // capped: counts as zero
  model.startDownload();
  await until(() => !model.status().downloading);
  assert.strictEqual(model.status().ready, true);
  assert.strictEqual(requests.find((r) => r.name === 'tokens.txt').range, null);
});

test('cancel aborts mid-download without recording an error', async () => {
  stallNext = true;
  let startedResolve;
  stalled = { resolve: () => startedResolve() };
  const started = new Promise((r) => { startedResolve = r; });
  const model = makeModel();
  model.startDownload();
  await started;
  model.cancel();
  await until(() => !model.status().downloading);
  const status = model.status();
  assert.strictEqual(status.ready, false);
  assert.strictEqual(status.error, undefined);
  // A later download resumes from whatever landed.
  model.startDownload();
  await until(() => !model.status().downloading);
  assert.strictEqual(model.status().ready, true);
});

test('remove refuses while downloading, then deletes the model dir', async () => {
  stallNext = true;
  let startedResolve;
  stalled = { resolve: () => startedResolve() };
  const started = new Promise((r) => { startedResolve = r; });
  const model = makeModel();
  model.startDownload();
  await started;
  assert.throws(() => model.remove(), /Cancel the download/);
  model.cancel();
  await until(() => !model.status().downloading);
  model.remove();
  assert.strictEqual(fs.existsSync(modelDir()), false);
  assert.strictEqual(model.status().ready, false);
});

test('a short server response leaves an error and no ready flag', async () => {
  // Serve tokens.txt truncated by lying about its length upstream: point a
  // manifest at a file the server sends fully but the manifest says is
  // bigger — the size check must fail the download.
  const model = createOfflineModel({
    modelsRoot: root,
    manifest: {
      dirName: 'model-under-test',
      label: 'test model',
      baseUrl,
      files: [{ name: 'tokens.txt', bytes: 501 }],
    },
  });
  model.startDownload();
  await until(() => !model.status().downloading);
  const status = model.status();
  assert.strictEqual(status.ready, false);
  assert.match(status.error, /tokens\.txt ended at 500 bytes, expected 501/);
});
