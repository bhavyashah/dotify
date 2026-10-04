// Transcription provider: OpenAI Realtime transcription (gpt-live-transcribe).
//
// Every provider module exposes the same createSession, which hides
// everything specific to its upstream service:
//
//   createSession({ apiKey, model, language, dictionary, onReady, onPartial,
//                   onFinal, onError, onClose, ...provider options })
//     -> { sendAudio(pcm16Buffer), close(), keepalive?() }
//
//   language     ISO-639-1 code ('en'); a provider maps it to its API's form
//   dictionary   words to bias recognition toward
//   sendAudio    one chunk of 16-bit PCM mono @ 24 kHz
//   close()      flush trailing audio and end the session; onClose follows
//   keepalive()  optional: hold an idle socket open while silence is withheld
//
//   onReady()                 the session accepts audio
//   onPartial(itemId, text)   the full current hypothesis for a segment; each
//                             call replaces the last. '' clears the segment
//                             (it finalized to nothing).
//   onFinal(itemId, text, speaker?, turnPcm?)
//                             finalized, never-revised segment; diarizing
//                             providers add the speaker label and the
//                             turn's PCM (for speaker identification)
//   onError(message)          human-readable error
//   onClose()                 the session ended
//
// This model streams transcript deltas but finalizes a segment only on an
// app-side input_audio_buffer.commit, so the shared gap gate (speech-gate.js)
// decides when: at a short pause once the segment passes its minimum, or at
// the hard cap.

const { WebSocket } = require('ws');
const { createSpeechGate } = require('./speech-gate');
const { attachWatchdog } = require('./socket-watchdog');
const { wireVendorSocket, CONNECTION_LOST } = require('./vendor-socket');

const OPENAI_URL = 'wss://api.openai.com/v1/realtime?intent=transcription';
const MIN_COMMIT_MS = 150;     // the API needs ~100 ms of audio to accept a commit

// gateOverrides: the "accurate" latency preset's gap-only commits.
// delay: the model's latency dial (minimal/low/medium/high/xhigh), mapped
//   from the latency preset in server.js.
function createSession({ apiKey, model, language, dictionary, gateOverrides,
                         delay,
                         onReady, onPartial, onFinal, onError, onClose }) {
  const upstream = new WebSocket(OPENAI_URL, { headers: { Authorization: `Bearer ${apiKey}` } });

  // Dictionary boosting: the model's `keywords` list, which may not contain
  // <, > or line breaks.
  const keywords = (dictionary || [])
    .map((w) => String(w).replace(/[<>\r\n]/g, ' ').trim())
    .filter(Boolean)
    .slice(0, 100);

  // The gate's elapsedMs doubles as "audio appended since the last commit":
  // both reset at every commit or clear.
  const gate = createSpeechGate(gateOverrides);
  // The API streams deltas; the contract wants the full hypothesis.
  const partialText = new Map(); // item_id -> accumulated hypothesis
  let ready = false;    // session.update acknowledged

  attachWatchdog(upstream, { label: 'OpenAI', onDead: () => vendor.report(CONNECTION_LOST) });
  const vendor = wireVendorSocket(upstream, {
    label: 'OpenAI',
    onError,
    deliver,
    // The configuration goes first; buffered audio follows it.
    onOpen: () => upstream.send(JSON.stringify({
      type: 'session.update',
      session: {
        type: 'transcription',
        audio: {
          input: {
            format: { type: 'audio/pcm', rate: 24000 },
            transcription: {
              model,
              ...(language ? { languages: [language] } : {}),
              ...(keywords.length ? { keywords } : {}),
              ...(delay ? { delay } : {}),
            },
            turn_detection: null,
            noise_reduction: { type: 'near_field' },
          },
        },
      },
    })),
  });

  function commit() {
    // Never commit a buffer with no detected speech: transcribing silence is
    // what makes Whisper-family models hallucinate. This also guards the
    // trailing flush in close().
    if (!gate.sawSpeech || gate.elapsedMs < MIN_COMMIT_MS) return;
    upstream.send(JSON.stringify({ type: 'input_audio_buffer.commit' }));
    gate.reset();
  }

  upstream.on('message', (data) => {
    let event;
    try {
      event = JSON.parse(data.toString());
    } catch {
      return;
    }
    switch (event.type) {
      case 'session.updated':
        // Ready only once the configuration is accepted: a rejected one must
        // fail before the page starts streaming.
        if (!ready) {
          ready = true;
          if (onReady) onReady();
        }
        break;
      case 'conversation.item.input_audio_transcription.delta': {
        const full = (partialText.get(event.item_id) || '') + event.delta;
        partialText.set(event.item_id, full);
        if (onPartial) onPartial(event.item_id, full);
        break;
      }
      case 'conversation.item.input_audio_transcription.completed': {
        partialText.delete(event.item_id);
        const text = (event.transcript || '').trim();
        if (text) {
          if (onFinal) onFinal(event.item_id, text);
        } else if (onPartial) {
          onPartial(event.item_id, ''); // finalized to nothing
        }
        break;
      }
      case 'conversation.item.input_audio_transcription.failed': {
        // A failed item never completes. Clear it, or it would hold the
        // server's oldest-item ordering gate shut for the rest of the session.
        console.error('OpenAI transcription failed:', JSON.stringify(event.error || event));
        partialText.delete(event.item_id);
        if (onPartial) onPartial(event.item_id, '');
        break;
      }
      case 'error':
        console.error('OpenAI error:', JSON.stringify(event.error || event));
        if (onError) onError((event.error && event.error.message) || 'API error');
        // Before the configuration is accepted, the session would run with
        // defaults (no language pin): end it instead.
        if (!ready) upstream.close();
        break;
    }
  });

  upstream.on('close', () => {
    if (onClose) onClose();
  });

  function deliver(pcm) {
    upstream.send(JSON.stringify({ type: 'input_audio_buffer.append', audio: pcm.toString('base64') }));

    const verdict = gate.feed(pcm);
    if (verdict === 'force') {
      commit();
    } else if (verdict === 'silence') {
      // A buffer with no speech at the discard clock is cleared, never
      // committed (see commit()).
      upstream.send(JSON.stringify({ type: 'input_audio_buffer.clear' }));
      gate.reset();
    }
  }

  function close() {
    if (upstream.readyState !== WebSocket.OPEN) {
      if (upstream.readyState === WebSocket.CONNECTING) upstream.close();
      return;
    }
    // The API transcribes a committed segment only once more audio follows
    // it, so commit what remains, append a short silent tail to push the
    // last transcription out, and close once it has had time to arrive.
    commit();
    const silence = Buffer.alloc(4800).toString('base64'); // 100ms @ 24kHz
    for (let i = 0; i < 6; i++) {
      upstream.send(JSON.stringify({ type: 'input_audio_buffer.append', audio: silence }));
    }
    setTimeout(() => upstream.close(), 3000);
  }

  return { sendAudio: vendor.send, close };
}

module.exports = { createSession };
