// Minimal WAV reader for the accuracy tools: PCM16 mono RIFF/WAVE at any
// sample rate, which covers the files recorder.js writes. Chunk-walks the
// container, so extra chunks (LIST/fact) from externally recorded files are
// tolerated.

const fs = require('fs');

function readWav(filePath) {
  const buf = fs.readFileSync(filePath);
  if (buf.length < 12 || buf.toString('ascii', 0, 4) !== 'RIFF'
      || buf.toString('ascii', 8, 12) !== 'WAVE') {
    throw new Error(`${filePath} is not a RIFF/WAVE file`);
  }
  let fmt = null;
  let data = null;
  let offset = 12;
  while (offset + 8 <= buf.length) {
    const id = buf.toString('ascii', offset, offset + 4);
    const size = buf.readUInt32LE(offset + 4);
    const body = offset + 8;
    if (id === 'fmt ') {
      fmt = {
        format: buf.readUInt16LE(body),
        channels: buf.readUInt16LE(body + 2),
        sampleRate: buf.readUInt32LE(body + 4),
        bitsPerSample: buf.readUInt16LE(body + 14),
      };
    } else if (id === 'data') {
      // A recorder header whose close() never ran (crash/Ctrl+C) still says
      // size 0 while the PCM bytes are on disk: fall back to everything
      // after the header, instead of "successfully" reading zero samples.
      if (size === 0 && offset + 8 < buf.length) {
        data = buf.subarray(body);
        break;
      }
      data = buf.subarray(body, Math.min(body + size, buf.length));
    }
    offset = body + size + (size % 2); // chunks are word-aligned
  }
  if (!fmt || !data) throw new Error(`${filePath}: missing fmt/data chunk`);
  if (fmt.format !== 1 || fmt.bitsPerSample !== 16 || fmt.channels !== 1) {
    throw new Error(`${filePath}: need PCM16 mono, got format ${fmt.format}, `
      + `${fmt.bitsPerSample}-bit, ${fmt.channels} channel(s)`);
  }
  return { sampleRate: fmt.sampleRate, pcm: data };
}

module.exports = { readWav };
