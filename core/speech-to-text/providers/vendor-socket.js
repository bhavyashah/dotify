// The socket plumbing every keyed provider shares: audio captured while the
// vendor socket is still connecting is buffered and flushed on open (see
// preopen-buffer.js), and connection failures become one clear onError
// message each.
//
//   const vendor = wireVendorSocket(upstream, { label, onError, deliver, onOpen });
//   vendor.send(pcm)        buffer while CONNECTING, deliver(pcm) once OPEN,
//                           drop after
//   vendor.report(message)  send a specific error to onError; the generic
//                           socket error that usually follows is suppressed
//   vendor.reported         whether a specific error went out
//
// onOpen (optional) runs on 'open' before the buffered audio is flushed.

const { WebSocket } = require('ws');
const { createPreOpenBuffer } = require('./preopen-buffer');

const CONNECTION_LOST =
  'The transcription service stopped answering — the internet connection may be down.';

function wireVendorSocket(upstream, { label, onError, deliver, onOpen }) {
  const preopen = createPreOpenBuffer();
  let reported = false;

  function report(message) {
    reported = true;
    if (onError) onError(message);
  }

  upstream.on('open', () => {
    if (onOpen) onOpen();
    const { droppedMs } = preopen.flush(deliver);
    if (droppedMs) {
      console.error(`${label} connect outlasted the pre-open buffer: `
        + `${droppedMs}ms of audio dropped (oldest first)`);
    }
  });

  // A refused upgrade (401 bad key, 400 bad parameter) never fires 'open'.
  upstream.on('unexpected-response', (_req, res) => {
    const detail = res.statusCode === 401 || res.statusCode === 403
      ? `the ${label} API key was rejected`
      : `${label} rejected the request (HTTP ${res.statusCode})`;
    report(`Could not start ${label} transcription — ${detail}.`);
    res.resume();
    upstream.close();
  });

  upstream.on('error', (err) => {
    console.error(`${label} upstream error:`, err.message);
    if (!reported && onError) onError('Connection to transcription service failed.');
  });

  function send(pcm) {
    if (upstream.readyState === WebSocket.CONNECTING) preopen.push(pcm);
    else if (upstream.readyState === WebSocket.OPEN) deliver(pcm);
  }

  return {
    send,
    report,
    get reported() { return reported; },
  };
}

module.exports = { wireVendorSocket, CONNECTION_LOST };
