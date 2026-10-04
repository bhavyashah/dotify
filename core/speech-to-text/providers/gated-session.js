// Cost-gated session wrapper: puts cost-gate.js between the mic stream and a
// keyed provider session, and owns the session's lifecycle so that silence
// can also close the vendor socket.
//
//   audio-billed vendors (Deepgram, ElevenLabs): withholding chunks is the
//     saving. The socket stays open through a lull; Deepgram needs a
//     KeepAlive every few seconds to survive it.
//   session-billed vendors (AssemblyAI): the open socket is the meter, so
//     after idleCloseMs of withholding the session is closed (COLD) and
//     re-dialed at the next speech onset. The pre-open buffer and the gate's
//     pre-roll cover the dial; the first words after a long silence arrive a
//     few hundred ms later, nothing is lost.
//
// A deliberate close must not reach the server's real handlers, which tear
// the session down and make the page fall back to another engine. So every
// dial gets intercepting handlers:
//   - onReady passes through once; later re-dials are only logged.
//   - onClose after a close we asked for parks the session COLD silently.
//   - onClose initiated by the vendor during a lull is ambiguous (idle
//     timeout or outage?), so one probe session is dialed: if it reaches
//     ready the vendor is healthy and the probe is closed again; if it dies
//     first, the original error and the real teardown go through. This also
//     covers a vendor that is down when the session starts.
//   - onError during a lull is parked: a healthy re-dial discards it, a
//     failed probe reports it.
//   - onClose/onError while the gate is open is a real death and passes
//     straight through.
//
//   const session = createGatedSession(dial, { idleCloseMs, log,
//     onReady, onError, onClose });   // the real lifecycle handlers
//   dial(handlers) -> provider.createSession({...baseOpts, ...handlers})
//   session.sendAudio(pcm)
//   session.close()          // the browser left: real teardown
//   session.stats            // cost-gate stats plus dial count

const { createCostGate, CLOSE_AFTER_MS } = require('./cost-gate');

const KEEPALIVE_EVERY_MS = 4000; // Deepgram times out after 10 s without audio

function createGatedSession(dial, {
  idleCloseMs = CLOSE_AFTER_MS,
  gateOptions,
  log = () => {},
  onReady,
  onError,
  onClose,
} = {}) {
  const gate = createCostGate(gateOptions);
  let live = null;        // the current adapter session, null while COLD
  let closing = false;    // an idle-tier close we asked for is in flight
  let finalClosing = false; // browser gone: the next onClose is the real one
  let ended = false;      // real teardown ran; everything after is inert
  let readySent = false;
  let probing = false;    // the current session is a health probe
  let parkedError = null; // vendor error seen while withholding
  let keepaliveDue = KEEPALIVE_EVERY_MS;
  let dials = 0;

  function connect() {
    dials += 1;
    const mine = dial({
      onReady: () => {
        if (ended || mine !== live) return;
        if (probing && !gate.isOpen) {
          // The vendor is healthy and merely idles out: go COLD.
          log('cost gate: vendor healthy (idle timeout); going cold');
          probing = false;
          parkedError = null;
          closing = true;
          mine.close();
          return;
        }
        probing = false;
        if (readySent) { log('cost gate: re-dialed session ready'); return; }
        readySent = true;
        if (onReady) onReady();
      },
      onError: (message) => {
        if (ended || mine !== live) return;
        if (!finalClosing && !gate.isOpen && !closing) {
          // Keep the first error: it says why the session died.
          if (!parkedError) parkedError = message;
          return;
        }
        if (onError) onError(message);
      },
      onClose: () => {
        if (ended || mine !== live) return;
        if (finalClosing) {
          // The real close, after the provider flushed its finals.
          ended = true;
          live = null;
          if (onClose) onClose();
          return;
        }
        if (closing) {
          // A close we asked for: park COLD.
          live = null;
          closing = false;
          probing = false;
          return;
        }
        if (!gate.isOpen) {
          if (probing) {
            // The probe died before ready: a real outage.
            ended = true;
            live = null;
            if (parkedError && onError) onError(parkedError);
            if (onClose) onClose();
            return;
          }
          // The vendor hung up during a lull: probe once to see why.
          log('cost gate: vendor closed during silence; probing');
          probing = true;
          live = connect();
          return;
        }
        ended = true;
        if (onClose) onClose();
      },
    });
    return mine;
  }

  live = connect();

  return {
    sendAudio(pcm) {
      if (ended || finalClosing) return;
      const { forward, reopened } = gate.feed(pcm);
      if (reopened) {
        parkedError = null;
        probing = false; // a dialing probe becomes the live session
        keepaliveDue = KEEPALIVE_EVERY_MS;
        if (closing) {
          // Speech beat an idle close in flight: abandon that session and
          // dial a fresh one.
          closing = false;
          live = null;
        }
        if (!live) {
          log(`cost gate: speech onset, re-dialing (dial #${dials + 1})`);
          live = connect();
        }
      }
      if (forward.length) {
        for (const chunk of forward) live.sendAudio(chunk);
        return;
      }
      if (!live || closing || probing) return; // a probe resolves itself
      // Withholding with a session up: close it once idle long enough.
      if (gate.withheldStreakMs >= idleCloseMs) {
        log(`cost gate: ${Math.round(gate.withheldStreakMs / 1000)} s silent, `
          + 'closing provider session until speech resumes');
        closing = true;
        live.close();
        return;
      }
      if (gate.withheldStreakMs >= keepaliveDue) {
        keepaliveDue = gate.withheldStreakMs + KEEPALIVE_EVERY_MS;
        if (live.keepalive) live.keepalive();
      }
    },
    close() {
      if (ended || finalClosing) return;
      if (live) {
        finalClosing = true;
        closing = false;
        live.close();
        return;
      }
      // COLD: nothing to flush.
      ended = true;
      if (onClose) onClose();
    },
    get stats() {
      const s = gate.stats;
      return { ...s, dials, cold: !live && !ended };
    },
    get parkedError() { return parkedError; },
  };
}

module.exports = { createGatedSession };
