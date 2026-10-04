#!/usr/bin/env node
// Demo feeder: replay a timed caption track (SRT) into a local Dotify
// server at the track's real cadence, so the braille display shows a
// video's captions arriving exactly when they appeared on screen, bursts and
// pauses included, with nobody speaking.
//
// Each cue lands as ONE final on POST /ingest ({text}) — SRT cues are
// settled text, so cue-per-final is the honest shape. Posts are serialized
// in cue order. A server that is down or restarting is retried with
// doubling backoff and the replay catches up rather than drifts; any other
// refusal is a misconfiguration that retrying cannot fix, so it prints what
// happened and exits non-zero.
//
// Portable Node (no OS-specific code, no dependencies). Run from anywhere:
//   node tools/demo-feeder.js --list-tracks
//   node tools/demo-feeder.js --track nasa-twan-2021-10-16
'use strict';

const fs = require('node:fs');
const path = require('node:path');

const DEFAULT_SERVER = 'http://127.0.0.1:8788';
// The braille engine's folder is "text-to-braille" in the repo and
// "Text to Braille" in the installed Windows image.
const TRACKS_DIR = ['text-to-braille', 'Text to Braille']
  .map((dir) => path.join(__dirname, '..', '..', dir, 'demo_tracks'))
  .find((dir) => fs.existsSync(dir))
  || path.join(__dirname, '..', '..', 'text-to-braille', 'demo_tracks');
const RETRY_MS = 1000;      // first retry delay; doubles to RETRY_MAX_MS
const RETRY_MAX_MS = 15000;

const USAGE = `
demo-feeder — replay a timed caption track into a local Dotify server at
real cadence (looping if asked)

Usage:
  node tools/demo-feeder.js --track <name|file.srt> [options]
  node tools/demo-feeder.js --list-tracks

Options:
  --track <t>          a bundled track name from
                       core/text-to-braille/demo_tracks (with or without
                       .srt), or a path to any .srt file
  --list-tracks        print the bundled tracks (cues, duration) and exit
  --server <url>       the Dotify server (default ${DEFAULT_SERVER})
  --loop               restart the track when it ends, until stopped
  --loop-gap-s <n>     quiet seconds between passes (default 3; scaled by
                       --rate like the cue timing)
  --rate <x>           speed multiplier for testing (default 1.0 — real
                       cadence; 10 plays ten times faster)
  --help               this text

Exit status: 0 after a finished (non-loop) replay or a clean stop; 2 for
bad usage; 1 when the server refuses a caption.
`;

function parseArgs(argv) {
  const opts = {
    track: null, listTracks: false, server: DEFAULT_SERVER,
    loop: false, loopGapS: 3, rate: 1, help: false,
  };
  const takes = {
    '--track': 'track', '--server': 'server', '--loop-gap-s': 'loopGapS',
    '--rate': 'rate',
  };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--help' || a === '-h') { opts.help = true; continue; }
    if (a === '--list-tracks') { opts.listTracks = true; continue; }
    if (a === '--loop') { opts.loop = true; continue; }
    if (!(a in takes)) throw new Error(`unknown option: ${a}`);
    const v = argv[++i];
    if (v === undefined) throw new Error(`${a} needs a value`);
    opts[takes[a]] = v;
  }
  for (const k of ['loopGapS', 'rate']) {
    if (typeof opts[k] === 'string') {
      const n = Number(opts[k]);
      if (!Number.isFinite(n)) throw new Error(`--${k} must be a number`);
      opts[k] = n;
    }
  }
  if (!(opts.rate > 0)) throw new Error('--rate must be > 0');
  if (opts.loopGapS < 0) throw new Error('--loop-gap-s must be >= 0');
  return opts;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const log = (msg) => process.stderr.write(`[demo-feeder] ${msg}\n`);

// ---------------------------------------------------------------------------
// SRT parsing (pure functions, unit-tested)

// "HH:MM:SS,mmm" -> ms. SRT uses a comma; dot is accepted too (files in the
// wild mix them, and VTT-exported SRT often keeps the dot). Hours required
// by the spec but tolerated absent.
function parseSrtClock(ts) {
  const m = /^(?:(\d+):)?(\d{1,2}):(\d{1,2})[,.](\d{1,3})$/.exec(String(ts).trim());
  if (!m) return null;
  const [, h, min, s, frac] = m;
  return ((Number(h || 0) * 60 + Number(min)) * 60 + Number(s)) * 1000
    + Number(frac.padEnd(3, '0'));
}

// Minimal entity set actually seen in caption files.
function decodeEntities(s) {
  return s.replace(/&nbsp;/g, ' ').replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>').replace(/&amp;/g, '&');
}

// Raw cue body (possibly multi-line, tagged) -> one normalized line: the
// transcript file is line-oriented, and tags are formatting, not words.
function normalizeCueText(raw) {
  return String(raw)
    .split(/\r?\n/)
    .map((l) => decodeEntities(l.replace(/<[^>]*>/g, '')).trim())
    .filter((l) => l !== '')
    .join(' ')
    .replace(/\s+/g, ' ')
    .trim();
}

// SRT text -> [{startMs, endMs, text}]. Handles CRLF, an optional BOM, the
// optional numeric index line, multi-line cue bodies, and tag stripping.
// Malformed blocks and cues that normalize to nothing are skipped, never
// fatal. Cues sort by start so a hand-edited file still replays
// monotonically.
function parseSrt(text) {
  const cues = [];
  const blocks = String(text).replace(/^﻿/, '').split(/\r?\n\s*\r?\n/);
  for (const block of blocks) {
    const lines = block.split(/\r?\n/).filter((l) => l.trim() !== '');
    if (!lines.length) continue;
    let i = 0;
    if (!lines[i].includes('-->')) i++; // the optional index line
    if (i >= lines.length || !lines[i].includes('-->')) continue;
    const [rawStart, rawRest] = lines[i].split('-->');
    const startMs = parseSrtClock(rawStart);
    const endMs = parseSrtClock((rawRest || '').trim().split(/\s+/)[0] || '');
    if (startMs === null) continue;
    const body = normalizeCueText(lines.slice(i + 1).join('\n'));
    if (!body) continue;
    cues.push({ startMs, endMs: endMs === null ? startMs : endMs, text: body });
  }
  cues.sort((a, b) => a.startMs - b.startMs);
  return cues;
}

// ---------------------------------------------------------------------------
// Track resolution

function listTracks() {
  let names;
  try {
    names = fs.readdirSync(TRACKS_DIR).filter((f) => f.endsWith('.srt')).sort();
  } catch (err) {
    throw new Error(`cannot read ${TRACKS_DIR}: ${err.message}`);
  }
  return names.map((f) => {
    const cues = parseSrt(fs.readFileSync(path.join(TRACKS_DIR, f), 'utf8'));
    const endS = Math.round((cues[cues.length - 1]?.endMs ?? 0) / 1000);
    return {
      name: f.replace(/\.srt$/, ''),
      cues: cues.length,
      duration: `${Math.floor(endS / 60)}:${String(endS % 60).padStart(2, '0')}`,
    };
  });
}

// --track value -> readable file path: a direct path wins; otherwise the
// bundled tracks directory, .srt optional. Throws with the bundled names
// listed so a typo is a one-look fix.
function resolveTrack(wanted) {
  const candidates = [
    wanted,
    path.join(TRACKS_DIR, wanted),
    path.join(TRACKS_DIR, `${wanted}.srt`),
  ];
  for (const c of candidates) {
    try { if (fs.statSync(c).isFile()) return c; } catch {}
  }
  let known = [];
  try { known = listTracks().map((t) => t.name); } catch {}
  throw new Error(`track "${wanted}" not found`
    + (known.length ? ` — bundled: ${known.join(', ')}` : ''));
}

// ---------------------------------------------------------------------------
// The sender: serialized posts to /ingest, retry-with-backoff while the
// server is unreachable, fatal on any refusal. fetch/wait are injectable for
// the tests' stub network.
function makeSender({ server, fetchImpl = fetch, wait = sleep, logImpl = log }) {
  const endpoint = `${String(server).replace(/\/+$/, '')}/ingest`;
  let posted = 0;
  let fatal = null;   // the explanation string once a refusal is terminal
  let outage = false;

  // Post one caption line. Resolves true when it landed, false when the
  // sender has gone fatal.
  async function send(text) {
    if (fatal) return false;
    let backoff = RETRY_MS;
    for (;;) {
      let r;
      try {
        r = await fetchImpl(endpoint, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text }),
          signal: AbortSignal.timeout(10000),
        });
      } catch (err) {
        if (!outage) { outage = true; logImpl(`server unreachable (${err.message}) — retrying`); }
        await wait(backoff);
        backoff = Math.min(backoff * 2, RETRY_MAX_MS);
        continue;
      }
      if (r.ok) {
        if (outage) { outage = false; logImpl('server reachable again'); }
        posted += 1;
        return true;
      }
      const body = await r.text().catch(() => '');
      fatal = r.status === 404
        ? `HTTP 404 — ${endpoint} does not exist: check --server`
        : `HTTP ${r.status} — the server refused the caption (${body.slice(0, 200)})`;
      logImpl(fatal);
      return false;
    }
  }
  return { send, get fatal() { return fatal; }, get posted() { return posted; } };
}

// ---------------------------------------------------------------------------
// The replay clock: feed cues at startMs/rate against the pass's absolute
// origin, so an overdue cue (a retry stalled the line) feeds immediately
// and the replay catches up instead of drifting. onCue is awaited: posts
// stay serialized in cue order. clock/wait are injectable for the tests'
// fake time.
async function replayCues(cues, onCue, { rate = 1, clock = Date.now,
                                         wait = sleep,
                                         state = { running: true } } = {}) {
  const origin = clock();
  for (const cue of cues) {
    if (!state.running) return;
    const delay = origin + cue.startMs / rate - clock();
    if (delay > 0) await wait(delay);
    if (!state.running) return;
    if (await onCue(cue) === false) return;
  }
}

async function run(opts, state) {
  const file = resolveTrack(opts.track);
  const cues = parseSrt(fs.readFileSync(file, 'utf8'));
  if (!cues.length) throw new Error(`${file}: no usable cues`);
  const durationMs = cues[cues.length - 1].endMs;
  const sender = makeSender({ server: opts.server });
  log(`track ${path.basename(file)}: ${cues.length} cues, `
    + `${Math.round(durationMs / 1000)} s at rate ${opts.rate} -> ${opts.server}`);
  let pass = 0;
  do {
    pass++;
    if (opts.loop) log(`pass ${pass}`);
    const passStart = Date.now();
    await replayCues(cues, (cue) => sender.send(cue.text),
      { rate: opts.rate, state });
    if (sender.fatal || !state.running) break;
    if (opts.loop) {
      // Let the tail cue's on-screen time elapse, then the quiet gap —
      // both at --rate, like the cue timing itself.
      const tailMs = Math.max(0, durationMs / opts.rate - (Date.now() - passStart))
        + (opts.loopGapS * 1000) / opts.rate;
      await sleep(tailMs);
    }
  } while (opts.loop && state.running && !sender.fatal);
  if (sender.fatal) process.exitCode = 1;
  else log(`done (${sender.posted} caption${sender.posted === 1 ? '' : 's'} posted)`);
}

async function main() {
  let opts;
  try { opts = parseArgs(process.argv.slice(2)); }
  catch (err) {
    process.stderr.write(`${err.message}\n${USAGE}`);
    process.exit(2);
  }
  if (opts.help) { process.stdout.write(USAGE); return; }
  if (opts.listTracks) {
    for (const t of listTracks()) {
      process.stdout.write(`${t.name}  ${t.cues} cues  ${t.duration}\n`);
    }
    return;
  }
  if (!opts.track) {
    process.stderr.write(`--track is required (see --list-tracks)\n${USAGE}`);
    process.exit(2);
  }
  const state = { running: true };
  const stop = () => {
    if (!state.running) return;
    state.running = false;
    log('stopping');
  };
  process.on('SIGINT', stop);
  process.on('SIGTERM', stop);
  await run(opts, state);
}

if (require.main === module) {
  main().catch((err) => { log(`fatal: ${err.message}`); process.exit(1); });
}

module.exports = {
  parseSrtClock, parseSrt, listTracks, resolveTrack,
  makeSender, replayCues, TRACKS_DIR,
};
