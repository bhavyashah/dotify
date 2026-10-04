// Transcription provider: ElevenLabs Scribe v2 Realtime.
//
// Same createSession contract as providers/openai-realtime.js. Audio goes up
// as JSON {message_type:'input_audio_chunk', audio_base_64} frames; the server
// answers with partial_transcript (the full growing hypothesis, onPartial)
// and committed_transcript when a segment finalizes, after which the next
// partial starts fresh. A committed text may extend past the last partial.
// Auth is the raw key in an `xi-api-key` header.
//
// Segmentation is the vendor's own VAD (commit_strategy=vad). Unlike the other
// engines there is no forced-commit backstop: it cost +6 WER on this engine on
// the AMI meeting benchmark. The server instead streams the partials
// as soft text through a stability filter, so continuous speech still reaches
// the reader promptly. The gap gate is fed only to know whether there is
// speech to flush on close().

const { WebSocket } = require('ws');
const { createSpeechGate } = require('./speech-gate');
const { attachWatchdog } = require('./socket-watchdog');
const { wireVendorSocket, CONNECTION_LOST } = require('./vendor-socket');

const ELEVENLABS_URL = 'wss://api.elevenlabs.io/v1/speech-to-text/realtime';
const SAMPLE_RATE = 24000;      // matches the browser capture pipeline
const VAD_SILENCE_SECS = 0.5;   // natural-pause finalization (API minimum)
const CLOSE_TIMEOUT_MS = 3000;  // give the trailing committed_transcript time

// Server messages that report an error condition.
const ERROR_TYPES = new Set([
  'auth_error', 'quota_exceeded', 'rate_limited', 'commit_throttled',
  'queue_overflow', 'resource_exhausted', 'session_time_limit_exceeded',
  'input_error', 'chunk_size_exceeded', 'transcriber_error',
]);

// endpointingMs: the "accurate" latency preset's VAD silence (floored at the
// API minimum). gateOverrides is accepted for symmetry and has no effect.
function createSession({ apiKey, model, language, dictionary, gateOverrides,
                         endpointingMs,
                         onReady, onPartial, onFinal, onError, onClose }) {
  const vadSecs = Math.max(VAD_SILENCE_SECS, (endpointingMs || 0) / 1000);
  const params = new URLSearchParams({
    model_id: model,
    audio_format: `pcm_${SAMPLE_RATE}`,
    commit_strategy: 'vad',
    vad_silence_threshold_secs: String(vadSecs),
  });
  if (language) params.set('language_code', language);
  // Dictionary boosting: one repeated `keyterms` parameter per word (a JSON
  // array arrives as one literal term). The realtime endpoint takes at most 50.
  for (const term of (dictionary || []).slice(0, 50)) {
    params.append('keyterms', term);
  }

  const upstream = new WebSocket(`${ELEVENLABS_URL}?${params}`,
    { headers: { 'xi-api-key': apiKey } });

  let terminating = false;
  let ready = false;
  // Partials after a commit belong to the next segment, so each segment gets
  // its own item id.
  let segmentId = 0;
  let openPartial = false; // a partial for the current segment has been sent
  const gate = createSpeechGate(gateOverrides);

  attachWatchdog(upstream, { label: 'ElevenLabs', onDead: () => vendor.report(CONNECTION_LOST) });
  const vendor = wireVendorSocket(upstream, { label: 'ElevenLabs', onError, deliver });

  // A rejected key and an empty balance are what a new account actually
  // hits; name them so the user debugs the right thing.
  function describeError(event) {
    if (event.message_type === 'auth_error') return 'the ElevenLabs API key was rejected';
    if (event.message_type === 'quota_exceeded') return 'the ElevenLabs account is out of credits';
    return (typeof event.error === 'string' ? event.error : event.message_type) || 'API error';
  }

  upstream.on('message', (data) => {
    let event;
    try {
      event = JSON.parse(data.toString());
    } catch {
      return;
    }
    const itemId = `seg-${segmentId}`;
    switch (event.message_type) {
      case 'session_started':
        ready = true;
        if (onReady) onReady();
        break;
      case 'partial_transcript': {
        const text = (event.text || '').trim();
        // Empty partials are not forwarded: onPartial('') means "finalized
        // to nothing" and would withdraw the segment's soft text.
        if (text && onPartial) {
          openPartial = true;
          onPartial(itemId, text);
        }
        break;
      }
      case 'committed_transcript': {
        gate.reset();
        const text = (event.text || '').trim();
        if (text) {
          if (onFinal) onFinal(itemId, text);
          segmentId += 1;
          openPartial = false;
        } else if (openPartial) {
          openPartial = false;
          if (onPartial) onPartial(itemId, ''); // committed to nothing
        }
        break;
      }
      default:
        if (ERROR_TYPES.has(event.message_type)) {
          console.error('ElevenLabs error:', JSON.stringify(event));
          vendor.report(`ElevenLabs transcription failed — ${describeError(event)}.`);
          // Before session_started an error means the session never starts.
          if (!ready) upstream.close();
        }
        break;
    }
  });

  upstream.on('close', (code, reason) => {
    // The API also ends sessions with 1005/1006 after our own close(), which
    // `terminating` covers.
    if (code !== 1000 && !terminating && !vendor.reported && ready) {
      const detail = reason && reason.length ? reason.toString() : `code ${code}`;
      console.error('ElevenLabs closed abnormally:', detail);
      if (onError) onError(`Transcription service closed the session (${detail}).`);
    }
    if (onClose) onClose();
  });

  function sendChunk(pcm, commit) {
    upstream.send(JSON.stringify({
      message_type: 'input_audio_chunk',
      audio_base_64: pcm.toString('base64'),
      ...(commit ? { commit: true } : {}),
    }));
  }

  function deliver(pcm) {
    gate.feed(pcm);
    sendChunk(pcm, false);
  }

  function sendAudio(pcm) {
    if (!terminating) vendor.send(pcm);
  }

  function close() {
    if (upstream.readyState === WebSocket.CONNECTING) {
      terminating = true;
      upstream.close();
      return;
    }
    if (upstream.readyState !== WebSocket.OPEN) return;
    // A short silent chunk with commit:true forces the open segment out;
    // close once its committed_transcript has had time to arrive.
    if (gate.sawSpeech) {
      sendChunk(Buffer.alloc(SAMPLE_RATE / 10 * 2), true); // 100ms of silence
    }
    terminating = true;
    setTimeout(() => {
      if (upstream.readyState === WebSocket.OPEN) upstream.close();
    }, CLOSE_TIMEOUT_MS);
  }

  return { sendAudio, close };
}

module.exports = { createSession };
