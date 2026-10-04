const { test } = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const { WebSocket, WebSocketServer } = require('ws');
const { wireVendorSocket } = require('../providers/vendor-socket');

function listen(server) {
  return new Promise((resolve) => server.listen(0, '127.0.0.1',
    () => resolve(server.address().port)));
}

test('audio sent while connecting is delivered on open, after onOpen, in order', async () => {
  const wss = new WebSocketServer({ port: 0 });
  await new Promise((resolve) => wss.on('listening', resolve));
  const upstream = new WebSocket(`ws://127.0.0.1:${wss.address().port}`);
  const events = [];
  const opened = new Promise((resolve) => {
    const vendor = wireVendorSocket(upstream, {
      label: 'Test',
      onOpen: () => events.push('open'),
      deliver: (pcm) => {
        events.push(pcm.toString());
        if (events.length === 3) resolve(vendor);
      },
    });
    vendor.send(Buffer.from('a'));
    vendor.send(Buffer.from('b'));
  });
  const vendor = await opened;
  assert.deepStrictEqual(events, ['open', 'a', 'b']);
  vendor.send(Buffer.from('c'));
  assert.deepStrictEqual(events, ['open', 'a', 'b', 'c']);
  upstream.close();
  await new Promise((resolve) => upstream.on('close', resolve));
  vendor.send(Buffer.from('d')); // closed: dropped
  assert.strictEqual(events.length, 4);
  wss.close();
});

test('a refused upgrade reports one specific error, not the generic one', async () => {
  const server = http.createServer();
  server.on('upgrade', (_req, socket) => {
    socket.end('HTTP/1.1 401 Unauthorized\r\nContent-Length: 0\r\n\r\n');
  });
  const port = await listen(server);
  const upstream = new WebSocket(`ws://127.0.0.1:${port}`);
  const errors = [];
  const vendor = wireVendorSocket(upstream, {
    label: 'Test', onError: (m) => errors.push(m), deliver: () => {},
  });
  await new Promise((resolve) => upstream.on('close', resolve));
  assert.deepStrictEqual(errors,
    ['Could not start Test transcription — the Test API key was rejected.']);
  assert.strictEqual(vendor.reported, true);
  server.close();
});

test('a connection failure reports the generic error', async () => {
  const server = http.createServer();
  const port = await listen(server);
  await new Promise((resolve) => server.close(resolve));
  const upstream = new WebSocket(`ws://127.0.0.1:${port}`);
  const errors = [];
  const vendor = wireVendorSocket(upstream, {
    label: 'Test', onError: (m) => errors.push(m), deliver: () => {},
  });
  await new Promise((resolve) => upstream.on('close', resolve));
  assert.deepStrictEqual(errors, ['Connection to transcription service failed.']);
  assert.strictEqual(vendor.reported, false);
});
