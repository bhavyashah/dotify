// Liveness watchdog for the vendor WebSockets. A dead internet connection
// does not error a socket for 15-60+ s while the OS retransmits, and the
// reader gets no captions and no fallback meanwhile. Every vendor answers
// WebSocket pings, silent or not, so no pong (or any other frame) for
// PONG_TIMEOUT_MS is a reliable death verdict: report it once through onDead,
// then terminate() the socket so the provider's normal close handling runs.

const PING_INTERVAL_MS = 4000;
const PONG_TIMEOUT_MS = 9000;

function attachWatchdog(upstream, { label, onDead,
                                    intervalMs = PING_INTERVAL_MS,
                                    timeoutMs = PONG_TIMEOUT_MS } = {}) {
  let lastAlive = 0;
  let timer = null;

  upstream.on('open', () => {
    lastAlive = Date.now();
    timer = setInterval(() => {
      if (Date.now() - lastAlive > timeoutMs) {
        clearInterval(timer);
        timer = null;
        console.error(`${label || 'The transcription service'} stopped answering pings — ending the dead session.`);
        if (onDead) onDead();
        upstream.terminate();
        return;
      }
      try { upstream.ping(); } catch { /* CLOSING/CLOSED: 'close' cleans up */ }
    }, intervalMs);
    if (timer.unref) timer.unref();
  });
  upstream.on('pong', () => { lastAlive = Date.now(); });
  upstream.on('message', () => { lastAlive = Date.now(); });
  upstream.on('close', () => {
    if (timer) clearInterval(timer);
    timer = null;
  });
}

module.exports = { attachWatchdog, PING_INTERVAL_MS, PONG_TIMEOUT_MS };
