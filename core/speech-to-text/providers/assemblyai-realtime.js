// Transcription provider: AssemblyAI Universal-Streaming (v3).
//
// Same createSession contract as providers/openai-realtime.js. Audio goes up
// as raw binary PCM at 24 kHz (?sample_rate=24000). "Turn" events carry the
// full current hypothesis for the turn (onPartial); end_of_turn:true marks
// the finalized, formatted text. Auth is the raw key in the Authorization
// header. On close we must send Terminate: an abandoned session keeps
// billing until AssemblyAI's 3-hour cap.
//
// AssemblyAI ends a turn only on enough silence or a semantically complete
// turn, so continuous speech would never finalize; the shared gap gate
// (speech-gate.js) sends ForceEndpoint at the next short pause past its
// minimum, or at its hard cap.
//
// Conversation mode (the opt-in for everything speaker-related) requests
// speaker_labels: finals then carry "A", "B", ... or "UNKNOWN" as onFinal's
// third argument, and the turn's own PCM as the fourth, for local speaker
// identification (speaker-names.js). The diarizer needs longer turns, so the
// gate's bounds stretch to CONV_*_SEGMENT_MS (and, on the Pro model, the
// max_accuracy preset runs).

const { WebSocket } = require('ws');
const { createSpeechGate } = require('./speech-gate');
const { attachWatchdog } = require('./socket-watchdog');
const { wireVendorSocket, CONNECTION_LOST } = require('./vendor-socket');

const ASSEMBLYAI_URL = 'wss://streaming.assemblyai.com/v3/ws';
const SAMPLE_RATE = 24000;      // matches the browser capture pipeline
const MODE = 'balanced';        // Pro-model preset (min_latency | balanced | max_accuracy)
const CLOSE_TIMEOUT_MS = 3000;  // give Termination time to arrive after Terminate

const CONV_MODE = 'max_accuracy';
const CONV_MIN_SEGMENT_MS = 4000;
const CONV_MAX_SEGMENT_MS = 7000;

// Without diarization only the cap matters: it bounds memory if AssemblyAI
// stops finalizing.
const MAX_TURN_BUFFER_BYTES = 12 * SAMPLE_RATE * 2; // 12 s of PCM16

// mode: the Pro model's preset (the app runs the English model, so only
//   tools/wer-replay.js's assemblyai-pro engine uses it). Conversation
//   mode's preset wins.
// maxSpeakers: AssemblyAI's max_speakers hint (1-10), conversation mode only.
// gateOverrides: the "accurate" latency preset's longer segments;
//   conversation mode's bounds win.
function createSession({ apiKey, model, language, conversation, maxSpeakers,
                         dictionary, gateOverrides, mode,
                         onReady, onPartial, onFinal, onError, onClose }) {
  const gate = conversation
    ? createSpeechGate({ minSegmentMs: CONV_MIN_SEGMENT_MS,
                         maxSegmentMs: CONV_MAX_SEGMENT_MS })
    : createSpeechGate(gateOverrides);
  const params = new URLSearchParams({
    sample_rate: String(SAMPLE_RATE),
    speech_model: model,
  });
  // `mode` exists only on the Pro model; Universal-Streaming English rejects it.
  if (model === 'universal-3-5-pro') {
    params.set('mode', conversation ? CONV_MODE : (mode || MODE));
  }
  // Dictionary boosting: keyterms_prompt takes a JSON array (max 100).
  if (dictionary && dictionary.length) {
    params.set('keyterms_prompt', JSON.stringify(dictionary.slice(0, 100)));
  }
  if (conversation) {
    params.set('speaker_labels', 'true');
    if (Number.isInteger(maxSpeakers) && maxSpeakers >= 1 && maxSpeakers <= 10) {
      params.set('max_speakers', String(maxSpeakers));
    }
  }
  // Pin the language: unset, the Pro model auto-detects across ~18 languages.
  if (language) params.set('language_codes', language);

  const upstream = new WebSocket(`${ASSEMBLYAI_URL}?${params}`,
    { headers: { Authorization: apiKey } });

  let terminating = false;

  attachWatchdog(upstream, { label: 'AssemblyAI', onDead: () => vendor.report(CONNECTION_LOST) });
  const vendor = wireVendorSocket(upstream, { label: 'AssemblyAI', onError, deliver });

  // The audio since the last finalized turn, handed to onFinal for speaker
  // identification (conversation mode only). Cleared on end_of_turn, not on
  // ForceEndpoint: the forced turn's final arrives a beat later and must
  // still find its audio.
  let turnChunks = [];
  let turnBytes = 0;

  function takeTurnPcm() {
    const pcm = Buffer.concat(turnChunks);
    turnChunks = [];
    turnBytes = 0;
    return pcm;
  }

  upstream.on('message', (data) => {
    let event;
    try {
      event = JSON.parse(data.toString());
    } catch {
      return;
    }
    switch (event.type) {
      case 'Begin':
        if (onReady) onReady();
        break;
      case 'Turn': {
        const itemId = `turn-${event.turn_order}`;
        if (event.end_of_turn) {
          gate.reset();
          const turnPcm = takeTurnPcm();
          const text = (event.transcript || '').trim();
          if (text) {
            if (onFinal) onFinal(itemId, text, event.speaker_label, turnPcm);
          } else if (onPartial) {
            onPartial(itemId, ''); // finalized to nothing
          }
        } else if (onPartial && (event.transcript || '').trim()) {
          // Empty interims are not forwarded: onPartial('') means "finalized
          // to nothing" and would withdraw the segment's soft text.
          onPartial(itemId, event.transcript);
        }
        break;
      }
      case 'Termination':
        upstream.close();
        break;
      case 'Error':
      case 'error':
        console.error('AssemblyAI error:', JSON.stringify(event));
        if (onError) onError(event.error || event.message || 'API error');
        break;
    }
  });

  upstream.on('close', (code, reason) => {
    // Anything but a normal close outside our own Terminate is a failure,
    // unless a more specific error was already reported.
    if (code !== 1000 && !terminating && !vendor.reported) {
      const detail = reason ? reason.toString() : `code ${code}`;
      console.error('AssemblyAI closed abnormally:', detail);
      if (onError) onError(`Transcription service closed the session (${detail}).`);
    }
    if (onClose) onClose();
  });

  function forceEndpoint() {
    if (upstream.readyState !== WebSocket.OPEN || terminating) return;
    upstream.send(JSON.stringify({ type: 'ForceEndpoint' }));
    gate.reset();
  }

  function deliver(pcm) {
    upstream.send(pcm);

    if (conversation) {
      turnChunks.push(pcm);
      turnBytes += pcm.length;
      while (turnBytes > MAX_TURN_BUFFER_BYTES && turnChunks.length > 1) {
        turnBytes -= turnChunks.shift().length;
      }
    }

    if (gate.feed(pcm) === 'force') forceEndpoint();
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
    terminating = true;
    // Terminate flushes the current turn and yields Termination, whose
    // handler closes the socket; the timeout is the backstop.
    upstream.send(JSON.stringify({ type: 'Terminate' }));
    setTimeout(() => {
      if (upstream.readyState === WebSocket.OPEN) upstream.close();
    }, CLOSE_TIMEOUT_MS);
  }

  return { sendAudio, close };
}

module.exports = { createSession };
