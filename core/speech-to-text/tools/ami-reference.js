#!/usr/bin/env node
// Build the word-time-ordered reference used by the AMI WER studies from the
// corpus's official manual word annotations. Punctuation and non-word events
// are intentionally excluded. Use wer-replay.js --ignore-fillers when a
// published benchmark comparison should exclude um/uh-class words too.
//
//   node tools/ami-reference.js --words-dir annotations/words \
//     --meeting ES2004a [--until 480] > ES2004a.ref.txt

const fs = require('fs');
const path = require('path');

function args(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === '--words-dir') out.wordsDir = argv[++i];
    else if (argv[i] === '--meeting') out.meeting = argv[++i];
    else if (argv[i] === '--until') out.until = Number(argv[++i]);
    else throw new Error(`Unknown argument: ${argv[i]}`);
  }
  if (!out.wordsDir || !out.meeting) {
    throw new Error('Usage: ami-reference.js --words-dir DIR --meeting ID [--until SECONDS]');
  }
  if (out.until !== undefined && (!Number.isFinite(out.until) || out.until <= 0)) {
    throw new Error('--until must be a positive number of seconds');
  }
  return out;
}

function decodeXml(text) {
  return text
    .replace(/&#(\d+);/g, (_, n) => String.fromCodePoint(Number(n)))
    .replace(/&#x([0-9a-f]+);/gi, (_, n) => String.fromCodePoint(parseInt(n, 16)))
    .replace(/&apos;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&amp;/g, '&');
}

function parseWords(xml, source) {
  const words = [];
  const wordTag = /<w\b([^>]*)>([\s\S]*?)<\/w>/g;
  for (const match of xml.matchAll(wordTag)) {
    const attributes = match[1];
    if (/\bpunc=["']true["']/.test(attributes)) continue;
    const time = /\bstarttime=["']([^"']+)["']/.exec(attributes);
    if (!time) continue;
    const text = decodeXml(match[2].replace(/<[^>]+>/g, '').trim());
    if (!text) continue;
    words.push({ time: Number(time[1]), text, source, order: words.length });
  }
  return words;
}

function main() {
  const options = args(process.argv.slice(2));
  const prefix = `${options.meeting}.`;
  const suffix = '.words.xml';
  const files = fs.readdirSync(options.wordsDir)
    .filter((name) => name.startsWith(prefix) && name.endsWith(suffix))
    .sort();
  if (!files.length) throw new Error(`No ${options.meeting} word annotations in ${options.wordsDir}`);

  const words = files.flatMap((name) => parseWords(
    fs.readFileSync(path.join(options.wordsDir, name), 'latin1'), name));
  words.sort((a, b) => a.time - b.time
    || a.source.localeCompare(b.source) || a.order - b.order);
  const kept = options.until === undefined
    ? words : words.filter((word) => word.time < options.until);
  process.stdout.write(`${kept.map((word) => word.text).join(' ')}\n`);
}

if (require.main === module) main();

module.exports = { decodeXml, parseWords };
