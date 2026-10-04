// Personal dictionary: names and uncommon words that engines mis-hear. Two
// mechanisms share one word list:
//
//   1. Boosting: boostTerms() hands the words to each provider's vocabulary
//      biasing at session start.
//   2. Correction: makeCorrector() builds a conservative pass the server runs
//      on all outgoing text, so engines without boosting (the offline model)
//      benefit too, and braille, the transcript box and transcript.txt agree.
//
// Storage is one JSON file beside the .env ({words:[{word, soundsLike:[]}]}),
// in the user's data folder in the installed app.
//
// Correction is deliberately timid: rewriting a real word into someone's name
// is worse than a miss.
//   - "always replace" aliases replace only on an exact normalized match
//     (aliases may be multi-word). They are unconditional, so
//     entryWarnings() warns at add time when an alias is ordinary English.
//   - fuzzy matching replaces a single token only when ALL hold: the token is
//     >= 4 letters, is not a common English word, shares the word's first
//     letter (speakers rarely lose the first sound), and is within edit
//     distance 1 (4-6 letters) or 2 (7+) of exactly ONE dictionary word.
//   - dictionary words that ARE common English words never fuzzy-match
//     (adding "May" must not rewrite "way").

const fs = require('fs');
const path = require('path');

const MAX_ENTRIES = 100;      // AssemblyAI/Deepgram boost lists cap near here
const MAX_WORD_LENGTH = 40;
const MAX_ALIASES = 5;

// Guard lexicon: tokens that are everyday English never fuzzy-correct, and
// aliases that are everyday English earn an add-time warning. Backed by
// common-words.txt (top 10,000 words of the Google Web Trillion Word Corpus,
// with counts). The 10k cutoff is calibrated: it must contain everyday
// words one edit from a dictionary entry, like "brilliant" (#6035, one
// edit from the display name "Brailliant"), but must EXCLUDE the rarer
// names and jargon users add, because a word inside the guard can never
// RECEIVE fuzzy corrections.
const CORPUS_TOKENS = 588124220187;   // full count_1w.txt total, all 333k words
const SPEECH_WORDS_PER_HOUR = 9000;   // ~150 wpm conversational speech
const WORD_FREQUENCY = new Map();     // word -> corpus count (top 10k only)
try {
  for (const line of fs.readFileSync(
    path.join(__dirname, 'common-words.txt'), 'utf8').split('\n')) {
    const m = line.match(/^([a-z']+)\t(\d+)\r?$/); // \r?: survive CRLF checkouts
    if (m) WORD_FREQUENCY.set(m[1], Number(m[2]));
  }
} catch { /* fall back to the embedded list below */ }

// Embedded fallback, so a missing data file still leaves a small guard.
const FALLBACK_COMMON = new Set((
  'a an the and or but if then so not no yes of to in on at for with from by ' +
  'as is are was were be been being am do does did done have has had having ' +
  'will would can could shall should may might must this that these those ' +
  'there here when where which what who whom whose why how all any both each ' +
  'few more most other some such only own same than too very just about into ' +
  'over under again further once out off up down before after above below ' +
  'between through during against because while until unless since your my ' +
  'his her its our their you i he she it we they them him me us mine yours ' +
  'time year day way man men woman women child people life world hand part ' +
  'place work week case point company number group problem fact home water ' +
  'room mother father friend house service thing area money story month lot ' +
  'right study book eye job word business issue side kind head far back thing ' +
  'call came come went gone going know knew think thought take took see saw ' +
  'look want give gave use find found tell told ask asked seem feel felt try ' +
  'leave left get got make made say said good new first last long great ' +
  'little old big high small large next early young important public bad ' +
  'able better best sure real still even also never now then well much many'
).split(/\s+/));

const COMMON_WORDS = WORD_FREQUENCY.size
  ? new Set(WORD_FREQUENCY.keys())
  : FALLBACK_COMMON;

function normalizeToken(token) {
  return token.toLowerCase().replace(/^[^a-z0-9']+/, '').replace(/[^a-z0-9']+$/, '');
}

function validateEntry(raw) {
  const word = raw && typeof raw.word === 'string' ? raw.word.trim() : '';
  if (!word || word.length > MAX_WORD_LENGTH) {
    throw new Error(`Dictionary words are 1-${MAX_WORD_LENGTH} characters.`);
  }
  // Printable text only; letters must appear somewhere (bare punctuation or
  // digits-only entries can't be spoken, so they could only misfire).
  if (/[\u0000-\u001f\u007f]/.test(word) || !/\p{L}/u.test(word)) {
    throw new Error('Dictionary words must be plain text containing letters.');
  }
  let soundsLike = [];
  if (raw.soundsLike !== undefined) {
    if (!Array.isArray(raw.soundsLike)) throw new Error('soundsLike must be a list.');
    soundsLike = raw.soundsLike
      .map((alias) => String(alias).trim())
      .filter(Boolean)
      .slice(0, MAX_ALIASES);
    for (const alias of soundsLike) {
      if (alias.length > MAX_WORD_LENGTH || /[\u0000-\u001f\u007f]/.test(alias)) {
        throw new Error(`"Always replace" entries are 1-${MAX_WORD_LENGTH} plain-text characters.`);
      }
    }
  }
  return { word, ...(soundsLike.length ? { soundsLike } : {}) };
}

// Best-effort: a missing or unreadable file is an empty dictionary, and an
// invalid entry (a hand edit) is skipped rather than costing the others —
// the next save rewrites the file from what loaded.
function loadDictionary(file) {
  let parsed;
  try {
    parsed = JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch {
    return [];
  }
  if (!parsed || !Array.isArray(parsed.words)) return [];
  const entries = [];
  for (const raw of parsed.words) {
    try { entries.push(validateEntry(raw)); } catch { /* skip */ }
    if (entries.length === MAX_ENTRIES) break;
  }
  return entries;
}

function saveDictionary(file, entries) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, JSON.stringify({ words: entries }, null, 2) + '\n',
    { encoding: 'utf8', mode: 0o600 });
}

// The flat word list handed to provider boosting. Aliases are NOT included:
// they describe mis-hearings, not vocabulary the engine should emit.
function boostTerms(entries) {
  return entries.map((entry) => entry.word);
}

// Add-time check: an "always replace" alias that is ordinary English will
// rewrite correct speech. Rates come from the corpus behind common-words.txt
// (web English, an order-of-magnitude guide for speech).
function aliasWarning(word, alias) {
  const parts = alias.split(/\s+/).map(normalizeToken).filter(Boolean);
  if (!parts.length) return null;
  if (parts.length === 1) {
    if (!COMMON_WORDS.has(parts[0])) return null;
    const count = WORD_FREQUENCY.get(parts[0]);
    const perHour = count ? (count / CORPUS_TOKENS) * SPEECH_WORDS_PER_HOUR : 0;
    const often = !perHour ? 'regularly'
      : perHour >= 1.5 ? `roughly ${Math.round(perHour)} times an hour`
        : perHour >= 0.75 ? 'roughly once an hour'
          : perHour >= 1 / 48 ? `roughly once every ${Math.round(1 / perHour)} hours`
            : 'occasionally';
    return `"${alias}" is an ordinary English word, spoken ${often} in `
      + `conversation — every occurrence will be rewritten to "${word}".`;
  }
  if (parts.every((p) => COMMON_WORDS.has(p))) {
    return `"${alias}" is a phrase of ordinary English words, so it can occur `
      + `in normal speech — every exact occurrence will be rewritten to "${word}".`;
  }
  return null;
}

function entryWarnings(entry) {
  return (entry.soundsLike || [])
    .map((alias) => aliasWarning(entry.word, alias))
    .filter(Boolean);
}

// Levenshtein with a cutoff — distances beyond `max` all report max+1.
function editDistance(a, b, max) {
  if (Math.abs(a.length - b.length) > max) return max + 1;
  let prev = Array.from({ length: b.length + 1 }, (_, i) => i);
  for (let i = 1; i <= a.length; i++) {
    const cur = [i];
    let rowMin = i;
    for (let j = 1; j <= b.length; j++) {
      cur[j] = Math.min(
        prev[j] + 1,
        cur[j - 1] + 1,
        prev[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1),
      );
      if (cur[j] < rowMin) rowMin = cur[j];
    }
    if (rowMin > max) return max + 1;
    prev = cur;
  }
  return prev[b.length];
}

function fuzzyLimit(length) {
  if (length < 4) return 0;
  return length < 7 ? 1 : 2;
}

// Build the correction pass for the current entries. Rebuilt whenever the
// dictionary changes; cheap enough to run on every final (<=100 entries).
function makeCorrector(entries) {
  if (!entries.length) return (text) => text;

  // Exact-match tables: canonical words and aliases, normalized. Multi-word
  // aliases/words are grouped by token count for the sliding-window pass.
  const aliasByLength = new Map(); // token count -> Map(normalized -> canonical)
  const known = new Set();         // every normalized canonical/alias token
  const fuzzyWords = [];           // single-word canonicals eligible for fuzzy
  for (const entry of entries) {
    const canonicalNorm = entry.word.split(/\s+/).map(normalizeToken).join(' ');
    for (const token of canonicalNorm.split(' ')) known.add(token);
    for (const alias of entry.soundsLike || []) {
      const parts = alias.split(/\s+/).map(normalizeToken).filter(Boolean);
      if (!parts.length) continue;
      if (!aliasByLength.has(parts.length)) aliasByLength.set(parts.length, new Map());
      aliasByLength.get(parts.length).set(parts.join(' '), entry.word);
      for (const part of parts) known.add(part);
    }
    if (!/\s/.test(entry.word)) {
      const norm = normalizeToken(entry.word);
      // A common English word may be corrected TO via its aliases, but must
      // never fuzzy-attract neighbors ("May" must not capture "way").
      if (norm.length >= 4 && !COMMON_WORDS.has(norm)) {
        fuzzyWords.push({ norm, word: entry.word });
      }
    }
  }
  const windowSizes = [...aliasByLength.keys()].sort((a, b) => b - a); // longest first

  return function correct(text) {
    const tokens = text.split(/\s+/).filter(Boolean);
    if (!tokens.length) return text;

    // Pass 1: exact alias windows (longest first so "dot if eye live" beats
    // "dot if eye"). Replacement keeps the window's outer punctuation.
    for (const size of windowSizes) {
      const table = aliasByLength.get(size);
      for (let i = 0; i + size <= tokens.length; i++) {
        const windowNorm = tokens.slice(i, i + size).map(normalizeToken).join(' ');
        const canonical = table.get(windowNorm);
        if (!canonical) continue;
        const lead = tokens[i].match(/^[^A-Za-z0-9']*/)[0];
        const trail = tokens[i + size - 1].match(/[^A-Za-z0-9']*$/)[0];
        tokens.splice(i, size, lead + canonical + trail);
      }
    }

    // Pass 2: single-token fuzzy against canonical words.
    for (let i = 0; i < tokens.length; i++) {
      const norm = normalizeToken(tokens[i]);
      const limit = fuzzyLimit(norm.length);
      if (!limit || COMMON_WORDS.has(norm) || known.has(norm)) continue;
      let best = null;
      let bestDistance = limit + 1;
      let tied = false;
      for (const candidate of fuzzyWords) {
        if (candidate.norm[0] !== norm[0]) continue;
        const distance = editDistance(norm, candidate.norm, limit);
        if (distance < bestDistance) {
          bestDistance = distance;
          best = candidate;
          tied = false;
        } else if (distance === bestDistance && best && candidate.norm !== best.norm) {
          tied = true;
        }
      }
      if (!best || tied || bestDistance > limit) continue;
      const lead = tokens[i].match(/^[^A-Za-z0-9']*/)[0];
      const trail = tokens[i].match(/[^A-Za-z0-9']*$/)[0];
      tokens[i] = lead + best.word + trail;
    }

    return tokens.join(' ');
  };
}

module.exports = {
  MAX_ENTRIES,
  validateEntry,
  loadDictionary,
  saveDictionary,
  boostTerms,
  entryWarnings,
  makeCorrector,
};
