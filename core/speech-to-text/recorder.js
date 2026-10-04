// Session recorder for accuracy measurement. Off by default: the page's
// "Record sessions" control (developer options) starts it for a session or
// mid-session, and DOTIFY_RECORD_DIR records every session. A recording is
// two files:
//
//   <stamp>-<session>.wav      the microphone audio as the provider heard it
//                              (PCM16 mono 24 kHz)
//   <stamp>-<session>.ref.txt  the finals the session emitted, one per line
//
// The .ref.txt is a draft: listen to the WAV, correct the text, then score
// any engine or configuration against the same audio with
// tools/wer-replay.js.
//
// The recorder only taps the pipeline: a write error disables the recording,
// never transcription.
//
// Privacy: recordings are raw room audio. They stay in the recordings folder,
// are sent nowhere except by wer-replay.js when the user runs it, and must
// never be committed.

const fs = require('fs');
const path = require('path');

const SAMPLE_RATE = 24000;
const WAV_HEADER_BYTES = 44;

function wavHeader(dataBytes) {
  const header = Buffer.alloc(WAV_HEADER_BYTES);
  header.write('RIFF', 0);
  header.writeUInt32LE(36 + dataBytes, 4);
  header.write('WAVE', 8);
  header.write('fmt ', 12);
  header.writeUInt32LE(16, 16);              // PCM fmt chunk size
  header.writeUInt16LE(1, 20);               // PCM
  header.writeUInt16LE(1, 22);               // mono
  header.writeUInt32LE(SAMPLE_RATE, 24);
  header.writeUInt32LE(SAMPLE_RATE * 2, 28); // byte rate
  header.writeUInt16LE(2, 32);               // block align
  header.writeUInt16LE(16, 34);              // bits per sample
  header.write('data', 36);
  header.writeUInt32LE(dataBytes, 40);
  return header;
}

// name distinguishes concurrent sessions; the timestamp orders a day's runs.
function createSessionRecorder(dir, name) {
  if (!dir) return null;
  const stamp = new Date().toISOString().replace(/[:.]/g, '-');
  const base = path.join(dir, `${stamp}-${name}`);
  let fd = null;
  let dataBytes = 0;
  try {
    fs.mkdirSync(dir, { recursive: true });
    fd = fs.openSync(`${base}.wav`, 'w');
    // Placeholder header; the sizes are patched in close() when they exist.
    fs.writeSync(fd, wavHeader(0));
    console.log(`Recording this session to ${base}.wav`);
  } catch (err) {
    console.error(`Session recording disabled: ${err.message}`);
    return null;
  }

  function fail(err) {
    console.error(`Session recording stopped: ${err.message}`);
    try { if (fd !== null) fs.closeSync(fd); } catch {}
    fd = null;
  }

  return {
    file: `${base}.wav`,
    audio(pcm) {
      if (fd === null) return;
      try {
        fs.writeSync(fd, pcm);
        dataBytes += pcm.length;
      } catch (err) { fail(err); }
    },
    final(text) {
      if (!text) return;
      // Appended as it arrives: a crash keeps the draft so far, and a final
      // released after close() still lands.
      try {
        fs.appendFileSync(`${base}.ref.txt`, text + '\n');
      } catch (err) {
        console.error(`Reference draft write failed: ${err.message}`);
      }
    },
    close() {
      if (fd !== null) {
        try {
          fs.writeSync(fd, wavHeader(dataBytes), 0, WAV_HEADER_BYTES, 0);
          fs.closeSync(fd);
        } catch (err) { fail(err); }
        fd = null;
      }
    },
  };
}

module.exports = { createSessionRecorder };
