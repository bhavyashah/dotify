// Test-only provider: the createSession(...) interface with no upstream
// service. Registered only when DOTIFY_MOCK_PROVIDER=1 (see server.js), so the
// /audio session machinery can be exercised offline and deterministically.
// Audio is accepted and dropped, except that a frame whose bytes read
// "MOCKFINAL:<text>" finalizes <text>. close() completes the session like a
// real provider's drained shutdown.
//
// Simulated vendor outage: when DOTIFY_MOCK_DIE_FILE names a path, a session
// dies with a provider-style error whenever that file exists (polled every
// DIE_POLL_MS), and /api/net-probe reports the mock vendor unreachable while
// it is there. This drives the connection-loss recovery end to end with no
// network.

const fs = require('fs');

const DIE_POLL_MS = 250;

function vendorDown() {
  const dieFile = process.env.DOTIFY_MOCK_DIE_FILE;
  if (!dieFile) return false;
  try { fs.accessSync(dieFile); return true; } catch { return false; }
}

function createSession({ onReady, onFinal, onError, onClose }) {
  let closed = false;
  let dieTimer = null;
  let finals = 0;

  function finish() {
    if (closed) return;
    closed = true;
    if (dieTimer) clearInterval(dieTimer);
    dieTimer = null;
    setImmediate(onClose);
  }

  setImmediate(() => {
    if (closed) return;
    if (vendorDown()) {
      // A start attempt during the outage fails like a dead connect.
      if (onError) onError('Connection to transcription service failed.');
      finish();
      return;
    }
    onReady();
  });
  if (process.env.DOTIFY_MOCK_DIE_FILE) {
    dieTimer = setInterval(() => {
      if (closed || !vendorDown()) return;
      if (onError) onError('Transcription service closed the session (mock outage).');
      finish();
    }, DIE_POLL_MS);
    if (dieTimer.unref) dieTimer.unref();
  }

  return {
    sendAudio(pcm) {
      const frame = pcm.toString('latin1');
      if (!closed && frame.startsWith('MOCKFINAL:')) {
        finals += 1;
        onFinal(`mock-${finals}`, frame.slice('MOCKFINAL:'.length));
      }
    },
    close: finish,
  };
}

module.exports = { createSession, vendorDown };
