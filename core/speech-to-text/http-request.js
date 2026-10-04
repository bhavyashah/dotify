// The server's one outbound HTTP client (the summary model, the local
// speaker-id service): a promise over http(s).request with a wall-clock
// deadline. The request `timeout` option alone is a socket-idle timer, which
// a trickling response resets forever, and a bare destroy() after the
// response has started settles nothing at all.

const http = require('http');
const https = require('https');

// Resolves { status, body } (body as a UTF-8 string) for any HTTP answer;
// rejects on a network error or when the exchange outlasts timeoutMs.
function httpRequest(url, { method = 'GET', headers = {}, body, timeoutMs }) {
  const target = new URL(url);
  const transport = target.protocol === 'https:' ? https : http;
  return new Promise((resolve, reject) => {
    const req = transport.request(target, {
      method,
      headers: body === undefined
        ? headers
        : { ...headers, 'Content-Length': Buffer.byteLength(body) },
    }, (res) => {
      let data = '';
      res.setEncoding('utf8');
      res.on('data', (chunk) => { data += chunk; });
      res.on('end', () => resolve({ status: res.statusCode, body: data }));
      res.on('error', reject);
    });
    const deadline = setTimeout(
      () => req.destroy(new Error(`Request timed out after ${timeoutMs} ms.`)),
      timeoutMs);
    req.on('close', () => clearTimeout(deadline));
    req.on('error', reject);
    req.end(body);
  });
}

module.exports = { httpRequest };
