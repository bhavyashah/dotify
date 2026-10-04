#!/usr/bin/env node
// Partial-stream stability analysis: find each engine's (depth, persist)
// soft filter for balanced mode's eager rendering (server.js SOFT_STABILITY).
//
// Replays a recorded partial stream (wer-replay.js's .partials.json /
// .finals.json sidecars — one recorded run, any number of analyses) through
// the exact freeze rule the server uses: a word freezes once it has sat
// deeper than `depth` words from the hypothesis tail with the same
// normalized value across `persist` consecutive hypotheses. Frozen words
// are compared positionally against the item's FINAL text — a mismatch is a
// REGRET: a word a caught-up reader could have read that the engine went on
// to take back. This is the worst case (every freezable word renders the
// instant it freezes); real readers trail and see fewer.
//
//   node tools/stability-analysis.js --partials f.partials.json --finals f.finals.json
//        [--depths 2,3,4,5,6,8]   candidate depths (default shown)
//        [--persists 1,2,3]       candidate persist counts (default shown)
//
// Output: one grid row per (depth, persist): words frozen, regrets, regret
// rate, coverage (frozen words / final words — how much of the text the
// filter ever lets render early), and the median lag from a word's first
// appearance at its position to its freeze (added display latency vs raw).
//
// The bar for adopting a cell (the ElevenLabs precedent, backed by a WER
// A/B on the AMI meeting benchmark): regret rate at or under ~0.55%
// (8 in 1475) with the smallest added lag, preferring high coverage.
// Caveat: the norm compare ignores case/punctuation but not formatting
// rewrites (e.g. "twenty five" -> "25"); those count as regrets here, which
// only makes the bar stricter.

const fs = require('fs');

const wordNorm = (w) => w.toLowerCase().replace(/[^a-z0-9']/g, '');

function parseArgs(argv) {
  const args = {
    depths: [2, 3, 4, 5, 6, 8],
    persists: [1, 2, 3],
  };
  for (let i = 0; i < argv.length; i++) {
    const flag = argv[i];
    if (flag === '--partials') args.partials = argv[++i];
    else if (flag === '--finals') args.finals = argv[++i];
    else if (flag === '--depths') args.depths = argv[++i].split(',').map(Number).filter(Number.isFinite);
    else if (flag === '--persists') args.persists = argv[++i].split(',').map(Number).filter(Number.isFinite);
    else { console.error(`Unknown argument: ${flag}`); process.exit(2); }
  }
  if (!args.partials || !args.finals) {
    console.error('Usage: node tools/stability-analysis.js --partials <f.partials.json> --finals <f.finals.json>');
    process.exit(2);
  }
  return args;
}

// One simulation pass: the server's stablePrefix freeze rule, worst-case
// rendering (a word freezes the moment the rule admits it, and frozen words
// never change — the display's frozen rule).
function simulate(byItem, finalsById, depth, persist) {
  let frozenTotal = 0;
  let regrets = 0;
  let finalWordsTotal = 0;
  const lags = [];
  for (const [id, updates] of byItem) {
    const finalText = finalsById.get(id);
    if (finalText === undefined) continue; // never finalized (close straggler)
    const finalWords = finalText.trim().split(/\s+/).filter(Boolean);
    finalWordsTotal += finalWords.length;
    const hist = [];
    const frozen = [];       // word values, frozen in order, never revised
    const firstSeenAt = [];  // atMs a word (any value) first held position i
    for (const { atMs, text } of updates) {
      const cur = text.trim().split(/\s+/).filter(Boolean);
      for (let i = firstSeenAt.length; i < cur.length; i++) firstSeenAt[i] = atMs;
      hist.push(cur);
      while (hist.length > persist) hist.shift();
      if (hist.length < persist) continue;
      const limit = Math.min(...hist.map((h) => h.length)) - depth;
      let n = 0;
      while (n < limit && hist.every((h) => wordNorm(h[n]) === wordNorm(cur[n]))) n++;
      for (let i = frozen.length; i < n; i++) {
        frozen.push(cur[i]);
        lags.push(atMs - (firstSeenAt[i] || atMs));
      }
    }
    frozenTotal += frozen.length;
    for (let i = 0; i < frozen.length; i++) {
      if (i >= finalWords.length
          || wordNorm(frozen[i]) !== wordNorm(finalWords[i])) regrets++;
    }
  }
  lags.sort((a, b) => a - b);
  return {
    frozenTotal,
    regrets,
    finalWordsTotal,
    medianLagMs: lags.length ? lags[Math.floor(lags.length / 2)] : null,
  };
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  const partials = JSON.parse(fs.readFileSync(args.partials, 'utf8'));
  const finals = JSON.parse(fs.readFileSync(args.finals, 'utf8'));

  const byItem = new Map(); // id -> [{atMs, text}] in stream order
  for (const p of partials) {
    if (!p || typeof p.text !== 'string') continue;
    if (!byItem.has(p.id)) byItem.set(p.id, []);
    byItem.get(p.id).push({ atMs: p.atMs, text: p.text });
  }
  const finalsById = new Map();
  for (const f of finals) finalsById.set(f.id, f.text || '');

  console.log(`${byItem.size} items with partials, ${finalsById.size} finals, `
    + `${partials.length} partial updates\n`);
  console.log('depth  persist   frozen   regrets   regret%   coverage%   median lag');
  for (const persist of args.persists) {
    for (const depth of args.depths) {
      const r = simulate(byItem, finalsById, depth, persist);
      const rate = r.frozenTotal ? (r.regrets / r.frozenTotal * 100) : 0;
      const coverage = r.finalWordsTotal
        ? (r.frozenTotal / r.finalWordsTotal * 100) : 0;
      console.log(`${String(depth).padStart(5)}  ${String(persist).padStart(7)}`
        + `${String(r.frozenTotal).padStart(9)}${String(r.regrets).padStart(10)}`
        + `${rate.toFixed(2).padStart(9)}%${coverage.toFixed(1).padStart(11)}%`
        + `${r.medianLagMs === null ? '        —' : String(r.medianLagMs).padStart(9) + 'ms'}`);
    }
  }
}

main();
