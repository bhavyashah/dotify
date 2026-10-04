// Word-error-rate scoring for the accuracy tools.
//
// WER = (substitutions + deletions + insertions) / reference words, the
// standard Levenshtein word alignment. Both texts are normalized first:
// lowercased, punctuation stripped (apostrophes and hyphens survive inside
// words), whitespace collapsed — so "Don't stop." and "dont stop" differ by
// one substitution, not three. Speaker prefixes ("A:", "Maya:") are NOT
// stripped; remove them from both texts before scoring if the configs under
// comparison differ on diarization.

const FILLERS = new Set(['um', 'uh', 'uhm', 'umm', 'uhh', 'hm', 'hmm', 'mm', 'mhm', 'erm']);

function normalizeWords(text, { ignoreFillers = false } = {}) {
  const words = String(text || '')
    .toLowerCase()
    .replace(/[^a-z0-9'’-]+/g, ' ')
    .split(' ')
    .map((word) => word.replace(/’/g, "'").replace(/^['-]+|['-]+$/g, ''))
    .filter(Boolean);
  return ignoreFillers ? words.filter((word) => !FILLERS.has(word)) : words;
}

// Full-matrix DP so the backtrack can attribute errors to S/D/I. Memory is
// (ref+1)*(hyp+1) int32s and QUADRATIC in transcript length: speech runs
// ~150 wpm, so a real hour is ~9k words ≈ 324 MB (momentarily) and a
// two-hour meeting ~1.3 GB — fine for session-length replays, but score
// multi-hour recordings in chunks.
function werStats(refText, hypText, options) {
  const ref = normalizeWords(refText, options);
  const hyp = normalizeWords(hypText, options);
  const n = ref.length;
  const m = hyp.length;
  const cols = m + 1;
  const d = new Int32Array((n + 1) * cols);
  for (let j = 0; j <= m; j++) d[j] = j;
  for (let i = 1; i <= n; i++) d[i * cols] = i;
  for (let i = 1; i <= n; i++) {
    for (let j = 1; j <= m; j++) {
      const sub = d[(i - 1) * cols + j - 1] + (ref[i - 1] === hyp[j - 1] ? 0 : 1);
      const del = d[(i - 1) * cols + j] + 1;
      const ins = d[i * cols + j - 1] + 1;
      d[i * cols + j] = Math.min(sub, del, ins);
    }
  }
  let i = n;
  let j = m;
  let hits = 0;
  let substitutions = 0;
  let deletions = 0;
  let insertions = 0;
  while (i > 0 || j > 0) {
    const here = d[i * cols + j];
    if (i > 0 && j > 0
        && here === d[(i - 1) * cols + j - 1] + (ref[i - 1] === hyp[j - 1] ? 0 : 1)) {
      if (ref[i - 1] === hyp[j - 1]) hits += 1; else substitutions += 1;
      i -= 1;
      j -= 1;
    } else if (i > 0 && here === d[(i - 1) * cols + j] + 1) {
      deletions += 1;
      i -= 1;
    } else {
      insertions += 1;
      j -= 1;
    }
  }
  const errors = substitutions + deletions + insertions;
  return {
    refWords: n,
    hypWords: m,
    hits,
    substitutions,
    deletions,
    insertions,
    errors,
    // An empty reference against a non-empty hypothesis is all insertions:
    // report 100% rather than divide by zero.
    wer: n ? errors / n : (m ? 1 : 0),
  };
}

module.exports = { normalizeWords, werStats };
