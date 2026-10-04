const { test, before, after } = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const { httpRequest } = require('../http-request');

let server;
let base;

before(async () => {
  server = http.createServer((req, res) => {
    if (req.url === '/echo') {
      let body = '';
      req.on('data', (chunk) => { body += chunk; });
      req.on('end', () => {
        res.writeHead(201, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ method: req.method, body,
                                 length: req.headers['content-length'] }));
      });
    } else if (req.url === '/stall-body') {
      res.writeHead(200);
      res.write('{"partial":');   // ...and never finishes
    } else if (req.url === '/stall-headers') {
      // never answers at all
    }
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  base = `http://127.0.0.1:${server.address().port}`;
});

after(() => {
  server.closeAllConnections();
  server.close();
});

test('resolves any HTTP answer with its status and body', async () => {
  const { status, body } = await httpRequest(`${base}/echo`, {
    method: 'POST', body: 'héllo', timeoutMs: 2000,
  });
  assert.strictEqual(status, 201);
  assert.deepStrictEqual(JSON.parse(body), { method: 'POST', body: 'héllo', length: '6' });
});

test('a response that stalls mid-body rejects at the deadline', async () => {
  const started = Date.now();
  await assert.rejects(
    httpRequest(`${base}/stall-body`, { timeoutMs: 300 }), /timed out/);
  assert.ok(Date.now() - started < 2000);
});

test('a server that never answers rejects at the deadline', async () => {
  await assert.rejects(
    httpRequest(`${base}/stall-headers`, { timeoutMs: 300 }), /timed out/);
});

test('a refused connection rejects', async () => {
  const probe = http.createServer();
  await new Promise((resolve) => probe.listen(0, '127.0.0.1', resolve));
  const { port } = probe.address();
  await new Promise((resolve) => probe.close(resolve));
  await assert.rejects(httpRequest(`http://127.0.0.1:${port}/`, { timeoutMs: 2000 }));
});
