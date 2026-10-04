// The vendor-socket liveness watchdog (providers/socket-watchdog.js): a peer
// that stops answering pings must be terminated within the timeout — that is
// the whole point (TCP alone takes 30-60+ s) — and a healthy peer must never
// be touched. Real sockets on an ephemeral port; the silent peer is a ws
// server with autoPong disabled.

const { test } = require('node:test');
const assert = require('node:assert');
const { WebSocketServer, WebSocket } = require('ws');
const { attachWatchdog, PING_INTERVAL_MS, PONG_TIMEOUT_MS } = require('../providers/socket-watchdog');

function listen(options) {
  return new Promise((resolve) => {
    const wss = new WebSocketServer({ port: 0, ...options });
    wss.on('listening', () => resolve(wss));
  });
}

test('a peer that stops answering pings is terminated within the timeout', async () => {
  const wss = await listen({ autoPong: false }); // the "dead network" peer
  try {
    const upstream = new WebSocket(`ws://127.0.0.1:${wss.address().port}`);
    let dead = false;
    attachWatchdog(upstream, {
      label: 'test', intervalMs: 50, timeoutMs: 200,
      onDead: () => { dead = true; },
    });
    const started = Date.now();
    const code = await new Promise((resolve) => {
      upstream.on('close', (c) => resolve(c));
    });
    const elapsed = Date.now() - started;
    assert.ok(dead, 'onDead must fire before the terminate');
    assert.strictEqual(code, 1006); // terminate(), not a clean close
    assert.ok(elapsed < 2000, `verdict took ${elapsed}ms — the watchdog did not fire`);
  } finally {
    wss.close();
  }
});

test('a peer that answers pings is left alone', async () => {
  const wss = await listen({}); // default autoPong: a healthy peer
  try {
    const upstream = new WebSocket(`ws://127.0.0.1:${wss.address().port}`);
    let dead = false;
    attachWatchdog(upstream, {
      label: 'test', intervalMs: 40, timeoutMs: 120,
      onDead: () => { dead = true; },
    });
    await new Promise((resolve) => upstream.on('open', resolve));
    // Several full timeout windows of healthy pong traffic.
    await new Promise((resolve) => setTimeout(resolve, 600));
    assert.strictEqual(dead, false);
    assert.strictEqual(upstream.readyState, WebSocket.OPEN);
    upstream.close();
  } finally {
    wss.close();
  }
});

test('inbound messages keep a pong-less connection alive (belt and suspenders)', async () => {
  const wss = await listen({ autoPong: false });
  try {
    wss.on('connection', (peer) => {
      const feed = setInterval(() => peer.send('data'), 30);
      peer.on('close', () => clearInterval(feed));
    });
    const upstream = new WebSocket(`ws://127.0.0.1:${wss.address().port}`);
    let dead = false;
    attachWatchdog(upstream, {
      label: 'test', intervalMs: 40, timeoutMs: 120,
      onDead: () => { dead = true; },
    });
    await new Promise((resolve) => upstream.on('open', resolve));
    await new Promise((resolve) => setTimeout(resolve, 500));
    assert.strictEqual(dead, false);
    assert.strictEqual(upstream.readyState, WebSocket.OPEN);
    upstream.close();
  } finally {
    wss.close();
  }
});

test('shipping intervals detect a dead connection inside the fallback budget', () => {
  // The product promise this branch exists for: suspicion within ~9 s, well
  // under the old 30-60+ s TCP wait. Guard the constants against drift.
  assert.ok(PING_INTERVAL_MS <= 5000);
  assert.ok(PONG_TIMEOUT_MS <= 10000);
  assert.ok(PONG_TIMEOUT_MS > PING_INTERVAL_MS, 'timeout must outlast one ping cycle');
});
