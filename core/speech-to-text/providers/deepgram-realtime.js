// Transcription provider: Deepgram Nova-3 live streaming.
//
// Same createSession contract as providers/openai-realtime.js. Audio goes up
// as raw binary PCM; results come back as {type:"Results", is_final,
// channel:{alternatives:[{transcript}]}}. Interim results carry the growing
// hypothesis for the current chunk (onPartial); each is_final result is a
// finalized, never-revised chunk. On close, CloseStream flushes buffered
// audio, emits the trailing finals and closes the socket.
//
// Deepgram finalizes on ENDPOINTING_MS of silence, so continuous speech would
// never finalize; the shared gap gate (speech-gate.js) sends Finalize at the
// next short pause past its minimum, or at its hard cap.

const { WebSocket } = require('ws');
const { createSpeechGate } = require('./speech-gate');
const { attachWatchdog } = require('./socket-watchdog');
const { wireVendorSocket, CONNECTION_LOST } = require('./vendor-socket');

const DEEPGRAM_URL = 'wss://api.deepgram.com/v1/listen';
const SAMPLE_RATE = 24000;      // matches the browser capture pipeline
const ENDPOINTING_MS = 300;     // silence that finalizes a segment naturally
const CLOSE_TIMEOUT_MS = 3000;  // give CloseStream's trailing results time to arrive

// gateOverrides / endpointingMs: the "accurate" latency preset's longer
// segments (see server.js).
function createSession({ apiKey, model, language, dictionary, gateOverrides,
                         endpointingMs,
                         onReady, onPartial, onFinal, onError, onClose }) {
  const params = new URLSearchParams({
    model,
    encoding: 'linear16',
    sample_rate: String(SAMPLE_RATE),
    channels: '1',
    interim_results: 'true',
    smart_format: 'true',
    endpointing: String(endpointingMs || ENDPOINTING_MS),
  });
  if (language) params.set('language', language);
  // Dictionary boosting: Nova-3 keyterm prompting, one parameter per word.
  for (const term of (dictionary || []).slice(0, 100)) {
    params.append('keyterm', term);
  }

  const upstream = new WebSocket(`${DEEPGRAM_URL}?${params}`, {
    headers: { Authorization: `Token ${apiKey}` },
  });

  let terminating = false;
  let ready = false;
  // Interims after an is_final belong to the next chunk, so each chunk gets
  // its own item id.
  let segmentId = 0;
  const gate = createSpeechGate(gateOverrides);

  attachWatchdog(upstream, { label: 'Deepgram', onDead: () => vendor.report(CONNECTION_LOST) });
  const vendor = wireVendorSocket(upstream, { label: 'Deepgram', onError, deliver });

  upstream.on('open', () => {
    // The socket opens only once Deepgram has accepted the request.
    ready = true;
    if (onReady) onReady();
  });

  upstream.on('message', (data) => {
    let event;
    try {
      event = JSON.parse(data.toString());
    } catch {
      return;
    }
    if (event.type !== 'Results') return; // ignore Metadata, etc.

    const itemId = `seg-${segmentId}`;
    const alt = event.channel && event.channel.alternatives && event.channel.alternatives[0];
    const text = ((alt && alt.transcript) || '').trim();

    if (event.is_final) {
      gate.reset();
      if (text) {
        if (onFinal) onFinal(itemId, text);
        segmentId += 1;
      } else if (onPartial) {
        onPartial(itemId, ''); // finalized to nothing
      }
    } else if (onPartial && text) {
      // Empty interims are not forwarded: onPartial('') means "finalized to
      // nothing" and would withdraw the segment's soft text.
      onPartial(itemId, text);
    }
  });

  upstream.on('close', (code, reason) => {
    if (code !== 1000 && !terminating && !vendor.reported && ready) {
      const detail = reason && reason.length ? reason.toString() : `code ${code}`;
      console.error('Deepgram closed abnormally:', detail);
      if (onError) onError(`Transcription service closed the session (${detail}).`);
    }
    if (onClose) onClose();
  });

  function forceEndpoint() {
    if (upstream.readyState !== WebSocket.OPEN || terminating) return;
    upstream.send(JSON.stringify({ type: 'Finalize' }));
    gate.reset();
  }

  function deliver(pcm) {
    upstream.send(pcm);
    if (gate.feed(pcm) === 'force') forceEndpoint();
  }

  function sendAudio(pcm) {
    if (!terminating) vendor.send(pcm);
  }

  // Deepgram closes a stream after ~10 s without audio; KeepAlive holds it
  // open for free while the cost gate withholds silence.
  function keepalive() {
    if (upstream.readyState !== WebSocket.OPEN || terminating) return;
    upstream.send(JSON.stringify({ type: 'KeepAlive' }));
  }

  function close() {
    if (upstream.readyState === WebSocket.CONNECTING) {
      terminating = true;
      upstream.close();
      return;
    }
    if (upstream.readyState !== WebSocket.OPEN) return;
    terminating = true;
    upstream.send(JSON.stringify({ type: 'CloseStream' }));
    setTimeout(() => {
      if (upstream.readyState === WebSocket.OPEN) upstream.close();
    }, CLOSE_TIMEOUT_MS);
  }

  return { sendAudio, close, keepalive };
}

module.exports = { createSession };
