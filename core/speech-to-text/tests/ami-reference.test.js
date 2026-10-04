const test = require('node:test');
const assert = require('node:assert/strict');

const { decodeXml, parseWords } = require('../tools/ami-reference');

test('decodeXml handles named, decimal, and hexadecimal entities', () => {
  assert.equal(decodeXml('A&amp;B &apos;x&apos; &#33; &#x3f;'), "A&B 'x' ! ?");
});

test('parseWords retains timed words and excludes punctuation and untimed events', () => {
  const xml = [
    '<root>',
    '<w starttime="2.5">later</w>',
    '<w starttime="1.0">Tom &amp; Jerry</w>',
    '<w starttime="1.5" punc="true">.</w>',
    '<w>noise</w>',
    '</root>',
  ].join('');

  assert.deepEqual(parseWords(xml, 'speaker.words.xml'), [
    { time: 2.5, text: 'later', source: 'speaker.words.xml', order: 0 },
    { time: 1, text: 'Tom & Jerry', source: 'speaker.words.xml', order: 1 },
  ]);
});
