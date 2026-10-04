// Bridge to the local speaker-identification service (speaker-id/server.py).
//
// Each finalized diarized turn's PCM goes to the service, which accumulates
// per-label voice evidence and eventually answers with an enrolled name.
// This module owns the label -> name map for the session. Identification is
// fire-and-forget and never delays text; a learned name applies to the next
// printed prefix. Without the service, labels stay "A:" / "B:".

const { httpRequest } = require('./http-request');

// The service binds DOTIFY_SPEAKER_ID_PORT, so the client follows it.
const DEFAULT_URL = process.env.DOTIFY_SPEAKER_ID_PORT
  ? `http://127.0.0.1:${process.env.DOTIFY_SPEAKER_ID_PORT}`
  : 'http://127.0.0.1:8792';
const IDENTIFY_TIMEOUT_MS = 5000;

class SpeakerNames {
  constructor({ url, onNamed } = {}) {
    this.base = new URL(url || process.env.DOTIFY_SPEAKER_ID_URL || DEFAULT_URL);
    this.onNamed = onNamed || (() => {});
    this.names = new Map();       // diarization label -> enrolled name
    this.reportedOffline = false; // one log line per session, not per turn
  }

  // New transcription session: labels are per-session, so both our map and
  // the service's per-label evidence start over.
  reset() {
    this.names.clear();
    this.reportedOffline = false;
    this.request('POST', '/session/reset');
  }

  displayName(label) {
    return this.names.get(label) || label;
  }

  submitTurn(label, pcm) {
    if (!label || label === 'UNKNOWN' || !pcm || pcm.length === 0) return;
    this.request('POST', `/identify?label=${encodeURIComponent(label)}`, pcm, (result) => {
      if (result && result.name && this.names.get(label) !== result.name) {
        this.names.set(label, result.name);
        this.onNamed(label, result.name);
      }
    });
  }

  request(method, path, body, onResult) {
    httpRequest(new URL(path, this.base), {
      method,
      headers: body ? { 'Content-Type': 'application/octet-stream' } : {},
      body,
      timeoutMs: IDENTIFY_TIMEOUT_MS,
    }).then(({ status, body: data }) => {
      if (!onResult || status !== 200) return;
      try { onResult(JSON.parse(data)); } catch { /* malformed: ignore */ }
    }).catch(() => {
      if (!this.reportedOffline) {
        this.reportedOffline = true;
        console.log(`Speaker-ID service not reachable at ${this.base} — `
          + 'speaker names disabled for this session (labels still work).');
      }
    });
  }
}

module.exports = { SpeakerNames };
