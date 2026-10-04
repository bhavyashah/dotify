// Jump-to-live summaries ("what did I miss") for the braille ticker. When the
// reader jumps to live with a backlog, the ticker posts the missed text to
// /summarize and streams the reply at the reader's pace. summarize() asks a
// small, fast model for a summary within a hard character budget;
// localSummary() is the keyless fallback.

const { httpRequest } = require('./http-request');

// Chosen for consistent length behavior at low reasoning effort (smaller
// models were erratic about the budget). Override with DOTIFY_SUMMARY_MODEL.
const DEFAULT_MODEL = 'gpt-5.6-luna';
const DEFAULT_BASE_URL = 'https://api.openai.com';
// Must stay well under the ticker's 12 s request timeout
// (braille_engine/summary.py), so the extractive fallback can still answer
// when the model hangs.
const TIMEOUT_MS = 10000;
const MAX_INPUT_CHARS = 20000;

function buildSystemPrompt(chars, grade) {
  const gradeHint = grade === 2
    ? 'It will be rendered in contracted (grade 2) Unified English Braille, '
      + 'where common English words take fewer cells — prefer common words '
      + 'over rare ones.'
    : 'It will be rendered in uncontracted (grade 1) braille: every letter '
      + 'costs one cell.';
  // Kept short: a long instruction list diluted the length constraint.
  // Capitals cost a braille indicator cell, so only names keep theirs.
  return (
    'You compress missed live speech for a blind braille reader catching '
    + `up. Reply with ONLY the summary, max ${chars} characters — fewer `
    + 'than the speech, so keep the most important points: '
    + 'decisions, amounts, names, questions to the reader. Plan the wording '
    + 'so it ends naturally within the limit — the last word must be '
    + 'complete, never cut off. Avoid punctuation. Every capital letter '
    + 'costs an extra braille cell: write in lowercase except names and '
    + 'acronyms. Speaker labels like "A:" may appear — attribute '
    + 'when it matters who spoke. '
    + gradeHint
  );
}

function summarize({ apiKey, text, chars, grade, model, baseUrl }) {
  const chosenModel = model || DEFAULT_MODEL;
  const base = new URL(baseUrl || DEFAULT_BASE_URL);
  // gpt-4-era override models take neither reasoning_effort nor a schema;
  // they get the prompt-only path, without the length guarantee.
  const legacy = chosenModel.startsWith('gpt-4');
  const body = JSON.stringify({
    model: chosenModel,
    // At 'none' the model does not plan for the length limit and ends
    // mid-word against it.
    ...(legacy ? {} : { reasoning_effort: 'low' }),
    // The hard guarantee: constrained decoding enforces maxLength.
    ...(legacy ? {} : {
      response_format: {
        type: 'json_schema',
        json_schema: {
          name: 'summary',
          strict: true,
          schema: {
            type: 'object',
            properties: { s: { type: 'string', maxLength: chars } },
            required: ['s'],
            additionalProperties: false,
          },
        },
      },
    }),
    // Roomy: reasoning tokens count against it, and hitting the cap returns
    // empty content.
    max_completion_tokens: 2000,
    messages: [
      { role: 'system', content: buildSystemPrompt(chars, grade) },
      { role: 'user', content: text.slice(0, MAX_INPUT_CHARS) },
    ],
  });
  return httpRequest(new URL('/v1/chat/completions', base), {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${apiKey}`,
      'Content-Type': 'application/json',
    },
    body,
    timeoutMs: TIMEOUT_MS,
  }).then(({ status, body: data }) => {
    let payload;
    let content;
    try {
      payload = JSON.parse(data);
      if (status === 200) {
        content = payload.choices[0].message.content;
        if (!legacy) content = JSON.parse(content).s;
        if (typeof content !== 'string') throw new TypeError('no text');
      }
    } catch {
      throw new Error('Summary model returned an unreadable response.');
    }
    if (status !== 200) {
      const message = (payload.error && payload.error.message) || `HTTP ${status}`;
      throw new Error(`Summary model error: ${message}`);
    }
    // Normalize whitespace and shave any padding artifacts the grammar
    // wall can leave when the model runs into it ("...roof||||").
    const summary = content.replace(/\s+/g, ' ').trim()
      .replace(/[^A-Za-z0-9)"']+$/, '');
    if (!summary) throw new Error('Summary model returned empty text.');
    return summary;
  });
}

// ---- Keyless fallback: local extractive summary ----------------------------
//
// Score the backlog's content words by frequency (stopwords out), keep the
// highest-scoring ones that fit the budget, and emit them in spoken order:
// "the roof quote came in over budget" at 22 chars becomes "roof quote came
// budget". No grammar, but the load-bearing words survive. Used without an
// OpenAI key or when the model call fails.
const STOPWORDS = new Set(('a an the and or but if then so of to in on at for with from by as is are was were be been ' +
  'being am do does did done have has had having will would can could should shall may might must ' +
  'i you he she it we they me him her us them my your his its our their mine yours theirs this that ' +
  'these those there here what which who whom whose when where why how not no nor yes just very ' +
  'really quite some any all both each few more most other such only own same than too also about ' +
  'into over under again further once out off up down between through during before after above ' +
  'below because while until although though um uh like okay ok well yeah right oh hmm gonna wanna ' +
  'kinda sorta know mean guess say said says get got getting go going went come came thing things ' +
  'stuff bit lot let lets dont doesnt didnt wont cant couldnt shouldnt isnt arent wasnt werent ' +
  'im ive id youre youve weve theyre hes shes thats whats theres').split(' '));

function localSummary(text, chars) {
  const words = String(text).toLowerCase()
    .replace(/[^a-z0-9' -]+/g, ' ')
    .replace(/(^|\s)'+|'+(\s|$)/g, ' ')
    .split(/\s+/).filter(Boolean);
  if (!words.length) return '';
  const freq = new Map();
  for (const word of words) {
    if (!STOPWORDS.has(word)) freq.set(word, (freq.get(word) || 0) + 1);
  }
  if (!freq.size) return ''; // pure filler: nothing to say
  // One candidate per distinct content word, at its first position, scored by
  // frequency with a small recency tiebreak.
  const seen = new Set();
  const candidates = [];
  words.forEach((word, position) => {
    if (STOPWORDS.has(word) || seen.has(word)) return;
    seen.add(word);
    candidates.push({ word, position, score: freq.get(word) + position / words.length });
  });
  candidates.sort((a, b) => b.score - a.score);
  // Greedy pack by score, continuing past a miss so shorter words can still
  // fill the budget.
  const chosen = [];
  let length = -1; // joined length; the first word adds no separator
  for (const candidate of candidates) {
    if (length + 1 + candidate.word.length <= chars) {
      chosen.push(candidate);
      length += 1 + candidate.word.length;
    }
  }
  if (!chosen.length) {
    // The budget is below the shortest content word: truncate the last one.
    const lastContent = [...words].reverse().find((w) => !STOPWORDS.has(w));
    return lastContent.slice(0, chars);
  }
  return chosen.sort((a, b) => a.position - b.position)
    .map((c) => c.word).join(' ');
}

module.exports = { summarize, localSummary };
