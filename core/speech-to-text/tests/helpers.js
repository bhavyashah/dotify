// Shared server-boot rig for the suites that run server.js as a child
// process. Each suite keeps its OWN port — `node --test` can run files in
// parallel, so the ports are deliberately distinct — and builds its own env
// on top of these mechanics. (`node --test` only collects files matching the
// test patterns, so this module is never run as a suite itself.)

const { spawn } = require('node:child_process');
const path = require('node:path');
const fs = require('node:fs');
const WebSocket = require('ws');

const ROOT = path.join(__dirname, '..');

// Spawn server.js on the given port and return the child process.
// - `env` layers the suite's variables (temp files, feature toggles, and any
//   per-test extraEnv) on top of process.env + PORT.
// - `deleteKeys` are removed AFTER `env` is applied, so nothing leaks in
//   from the machine (e.g. a real OPENAI_API_KEY would un-keyless a suite).
// - `clean` paths are removed first so no state from a previous run leaks in.
// - Child stderr passes through tagged so parallel suites stay attributable.
function startServer({ port, env: overrides = {}, deleteKeys = [],
                       clean = [], tag = 'server' }) {
  for (const p of clean) {
    try { fs.rmSync(p, { recursive: true, force: true }); } catch {}
  }
  const env = { ...process.env, PORT: String(port), ...overrides };
  for (const key of deleteKeys) delete env[key];
  const child = spawn('node', ['server.js'], { cwd: ROOT, env });
  child.stderr.on('data', (d) => process.stderr.write(`[${tag}] ${d}`));
  return child;
}

// Poll GET / until the server serves the page, or throw after timeoutMs.
async function waitForListening(base, { timeoutMs = 5000 } = {}) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const r = await fetch(`${base}/`);
      if (r.ok) return;
    } catch {}
    await new Promise((r) => setTimeout(r, 100));
  }
  throw new Error('server did not start listening');
}

// A persistent /finalized client with an awaitable message queue. Latency
// frames (the connection welcome and mode-change fanout) are skipped unless
// `raw` — server.test.js's latency tests assert on exactly those frames.
// `next(timeoutMs)` defaults to 3 s; slow paths stretch it per call.
function openFinalized(port, { raw = false } = {}) {
  const ws = new WebSocket(`ws://127.0.0.1:${port}/finalized`);
  const received = [];
  const waiters = [];
  ws.on('message', (data) => {
    const msg = JSON.parse(data.toString());
    if (!raw && msg.type === 'latency') return;  // the connection welcome, not text
    const waiter = waiters.shift();
    if (waiter) waiter(msg); else received.push(msg);
  });
  const next = (timeoutMs = 3000) => (received.length
    ? Promise.resolve(received.shift())
    : new Promise((resolve, reject) => {
        waiters.push(resolve);
        setTimeout(() => reject(new Error('no /finalized message')), timeoutMs);
      }));
  const opened = new Promise((resolve, reject) => {
    ws.on('open', resolve);
    ws.on('error', reject);
  });
  return { ws, next, opened, pending: received };
}

// POST /ingest the way the sessionless engines do.
const makeIngest = (base) => (payload) => fetch(`${base}/ingest`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(payload),
});

module.exports = { startServer, waitForListening, openFinalized, makeIngest };
