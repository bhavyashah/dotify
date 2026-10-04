const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {
  validateEntry, loadDictionary, saveDictionary, boostTerms, entryWarnings,
  makeCorrector,
} = require('../dictionary');

test('validateEntry normalizes and rejects junk', () => {
  assert.deepStrictEqual(validateEntry({ word: '  Dotify  ' }), { word: 'Dotify' });
  assert.deepStrictEqual(
    validateEntry({ word: 'Dotify Live', soundsLike: [' dot if eye live ', ''] }),
    { word: 'Dotify Live', soundsLike: ['dot if eye live'] });
  assert.throws(() => validateEntry({ word: '' }));
  assert.throws(() => validateEntry({ word: 'x'.repeat(41) }));
  assert.throws(() => validateEntry({ word: '12345' }));      // no letters
  assert.throws(() => validateEntry({ word: 'bad\nword' }));  // control char
  assert.throws(() => validateEntry({ word: 'ok', soundsLike: 'not-a-list' }));
});

test('save/load round-trips and survives a corrupt file', () => {
  const file = path.join(os.tmpdir(), `dotify-dict-${process.pid}.json`);
  const entries = [{ word: 'Dotify', soundsLike: ['dotafy'] }, { word: 'Dotify' }];
  saveDictionary(file, entries);
  assert.deepStrictEqual(loadDictionary(file), entries);
  fs.writeFileSync(file, '{not json');
  assert.deepStrictEqual(loadDictionary(file), []);
  fs.rmSync(file, { force: true });
  assert.deepStrictEqual(loadDictionary(file), []); // missing file
});

test('one invalid entry does not cost the rest of the dictionary', () => {
  const file = path.join(os.tmpdir(), `dotify-dict-partial-${process.pid}.json`);
  fs.writeFileSync(file, JSON.stringify({ words: [
    { word: 'Brailliant' }, { word: 'x'.repeat(41) }, null, { word: 'Dotify' },
  ] }));
  assert.deepStrictEqual(loadDictionary(file), [{ word: 'Brailliant' }, { word: 'Dotify' }]);
  fs.rmSync(file, { force: true });
});

test('boostTerms lists words, never aliases', () => {
  assert.deepStrictEqual(
    boostTerms([{ word: 'Dotify', soundsLike: ['dotafy'] }, { word: 'NLS eReader' }]),
    ['Dotify', 'NLS eReader']);
});

test('sounds-like aliases replace on exact match, keeping outer punctuation', () => {
  const correct = makeCorrector([
    { word: 'Dotify', soundsLike: ['dotafy', 'dot if eye'] },
  ]);
  assert.strictEqual(correct('I met Dotafy today'), 'I met Dotify today');
  assert.strictEqual(correct('that was dot if eye, right?'), 'that was Dotify, right?');
  assert.strictEqual(correct('plain text stays put'), 'plain text stays put');
});

test('multi-word aliases prefer the longest match', () => {
  const correct = makeCorrector([
    { word: 'Dotify Live', soundsLike: ['dot if eye live'] },
    { word: 'Dotify', soundsLike: ['dot if eye'] },
  ]);
  assert.strictEqual(correct('ask dot if eye live about it'), 'ask Dotify Live about it');
});

test('fuzzy correction fixes near-misses conservatively', () => {
  const correct = makeCorrector([{ word: 'Deepgram' }, { word: 'Dotify' }]);
  assert.strictEqual(correct('we use deepgran daily'), 'we use Deepgram daily');
  assert.strictEqual(correct('dotifi said so'), 'Dotify said so');
  // First letter must match: no correction despite small edit distance.
  assert.strictEqual(correct('we use jeepgram daily'), 'we use jeepgram daily');
  // Common English words never fuzzy-correct.
  const terms = makeCorrector([{ word: 'Ticker' }]);
  assert.strictEqual(terms('buy a ticket now'), 'buy a ticket now');
  // Short tokens (<4 letters) never fuzzy-correct.
  const abbr = makeCorrector([{ word: 'Vosk' }]);
  assert.strictEqual(abbr('vos went'), 'vos went');
});

test('the frequency lexicon guards near-name English words from fuzzy rewrites', () => {
  // "brilliant" is one edit from "Brailliant" and absent from the small
  // embedded fallback list; the top-10k lexicon must protect it.
  const correct = makeCorrector([{ word: 'Brailliant' }]);
  assert.strictEqual(correct('a brilliant idea'), 'a brilliant idea');
  // ...while genuine non-word garble still corrects.
  assert.strictEqual(correct('my brailiant display'), 'my Brailliant display');
});

test('entryWarnings flags always-replace aliases that are plausible English', () => {
  // Single ordinary word: warned, with a spoken-rate estimate.
  const single = entryWarnings({ word: 'Brailliant', soundsLike: ['brilliant'] });
  assert.strictEqual(single.length, 1);
  assert.match(single[0], /"brilliant" is an ordinary English word/);
  assert.match(single[0], /rewritten to "Brailliant"/);
  // Phrase of ordinary words: warned qualitatively.
  const phrase = entryWarnings({ word: 'Dotify', soundsLike: ['dot if eye'] });
  assert.strictEqual(phrase.length, 1);
  assert.match(phrase[0], /"dot if eye" is a phrase of ordinary English words/);
  // Rare garble: silent.
  assert.deepStrictEqual(
    entryWarnings({ word: 'Dotify', soundsLike: ['dotafy'] }), []);
  assert.deepStrictEqual(entryWarnings({ word: 'Dotify' }), []);
});

test('a dictionary word that is a common English word never attracts neighbors', () => {
  const correct = makeCorrector([{ word: 'Point', soundsLike: ['poynt'] }]);
  assert.strictEqual(correct('paint the wall'), 'paint the wall'); // no fuzzy pull
  assert.strictEqual(correct('the poynt is this'), 'the Point is this'); // alias still works
});

test('ambiguous fuzzy matches are skipped', () => {
  const correct = makeCorrector([{ word: 'Sherpo' }, { word: 'Sherpa' }]);
  // "sherpi" is distance 1 from both — too ambiguous to touch.
  assert.strictEqual(correct('ask sherpi about it'), 'ask sherpi about it');
});

test('tokens already in the dictionary are left alone', () => {
  const correct = makeCorrector([{ word: 'Nemotron' }, { word: 'Neotron' }]);
  // Both are dictionary words at distance 1 of each other; each must survive.
  assert.strictEqual(correct('Nemotron beat Neotron'), 'Nemotron beat Neotron');
});

test('empty dictionary is the identity function', () => {
  const correct = makeCorrector([]);
  const text = 'anything at all, unchanged.';
  assert.strictEqual(correct(text), text);
});
