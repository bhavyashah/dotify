// The WER harness's offline pieces: scoring (tools/wer.js), the WAV
// round-trip between recorder.js and tools/wav.js.

const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { normalizeWords, werStats } = require('../tools/wer');
const { readWav } = require('../tools/wav');
const { createSessionRecorder } = require('../recorder');

test('normalizeWords strips punctuation but keeps intra-word apostrophes', () => {
  assert.deepStrictEqual(
    normalizeWords("Don't stop -- it's FINE, ok?"),
    ["don't", 'stop', "it's", 'fine', 'ok']);
  assert.deepStrictEqual(normalizeWords('  '), []);
  assert.deepStrictEqual(normalizeWords('e-mail, no.'), ['e-mail', 'no']);
});

test('filler-insensitive scoring removes um/uh-class words only when requested', () => {
  assert.deepEqual(normalizeWords('Um, keep hmm this.', { ignoreFillers: true }),
    ['keep', 'this']);
  assert.equal(werStats('keep this', 'um keep hmm this', { ignoreFillers: true }).wer, 0);
  assert.equal(werStats('keep this', 'um keep hmm this').insertions, 2);
});

test('werStats scores a perfect hypothesis at zero', () => {
  const s = werStats('the quick brown fox', 'The quick, brown FOX.');
  assert.strictEqual(s.wer, 0);
  assert.strictEqual(s.errors, 0);
  assert.strictEqual(s.hits, 4);
});

test('werStats attributes substitutions, deletions, and insertions', () => {
  // ref: a b c d   hyp: a x c   -> b→x substituted, d deleted
  let s = werStats('a b c d', 'a x c');
  assert.strictEqual(s.substitutions, 1);
  assert.strictEqual(s.deletions, 1);
  assert.strictEqual(s.insertions, 0);
  assert.strictEqual(s.wer, 0.5);

  // ref: a b   hyp: a x b   -> x inserted
  s = werStats('a b', 'a x b');
  assert.strictEqual(s.insertions, 1);
  assert.strictEqual(s.errors, 1);
  assert.strictEqual(s.wer, 0.5);
});

test('werStats survives empty texts', () => {
  assert.strictEqual(werStats('', '').wer, 0);
  assert.strictEqual(werStats('', 'ghost words').wer, 1);
  assert.strictEqual(werStats('missing all', '').wer, 1);
});

test('recorder WAV round-trips through the reader', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'dotify-wer-'));
  try {
    const recorder = createSessionRecorder(dir, 'test');
    assert.ok(recorder);
    const a = Buffer.alloc(4800);
    for (let i = 0; i < 2400; i++) a.writeInt16LE(1000 + i, i * 2);
    const b = Buffer.alloc(2400); // odd-length tail: half a chunk
    recorder.audio(a);
    recorder.audio(b);
    recorder.final('hello there');
    recorder.final('second line');
    recorder.close();

    const wavFile = fs.readdirSync(dir).find((f) => f.endsWith('.wav'));
    const refFile = fs.readdirSync(dir).find((f) => f.endsWith('.ref.txt'));
    assert.ok(wavFile && refFile);
    const wav = readWav(path.join(dir, wavFile));
    assert.strictEqual(wav.sampleRate, 24000);
    assert.strictEqual(wav.pcm.length, a.length + b.length);
    assert.ok(wav.pcm.subarray(0, a.length).equals(a));
    assert.strictEqual(fs.readFileSync(path.join(dir, refFile), 'utf8'),
      'hello there\nsecond line\n');
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('recorder is off without a directory', () => {
  assert.strictEqual(createSessionRecorder('', 's1'), null);
  assert.strictEqual(createSessionRecorder(undefined, 's1'), null);
});
