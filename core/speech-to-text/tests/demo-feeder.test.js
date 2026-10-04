// Demo feeder (tools/demo-feeder.js): replay a timed SRT track into a local
// Dotify server at real cadence. Pins the things the tool exists for:
// honest SRT parsing (CRLF, multi-line cues, tag stripping), pacing math at
// --rate, retry-while-unreachable vs. fatal refusals, and a true end-to-end
// replay against a locally spawned server.js observed on /finalized.
const { test, before, after } = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const os = require('node:os');
const fs = require('node:fs');
const { spawn } = require('node:child_process');
const rig = require('./helpers');
const feeder = require('../tools/demo-feeder');

const PORT = 8811;
const BASE = `http://127.0.0.1:${PORT}`;
const ROOT = path.join(__dirname, '..');

// ---------------------------------------------------------------------------
// SRT parsing

test('parseSrtClock: comma and dot millis, optional hours, junk is null', () => {
  assert.strictEqual(feeder.parseSrtClock('00:00:01,500'), 1500);
  assert.strictEqual(feeder.parseSrtClock('01:02:03.004'), 3723004);
  assert.strictEqual(feeder.parseSrtClock('02:03,4'), 123400); // no hours, short frac
  assert.strictEqual(feeder.parseSrtClock('not a clock'), null);
  assert.strictEqual(feeder.parseSrtClock(''), null);
});

test('parseSrt: CRLF, BOM, index lines, multi-line cues, tag stripping', () => {
  const srt = '﻿1\r\n00:00:00,500 --> 00:00:02,000\r\n'
    + '<i>Good morning,</i>\r\nand <b>welcome</b>.\r\n\r\n'
    + '2\r\n00:00:03,000 --> 00:00:04,000\r\n'
    + 'The launch &amp; the landing.\r\n';
  const cues = feeder.parseSrt(srt);
  assert.strictEqual(cues.length, 2);
  assert.deepStrictEqual(cues[0],
    { startMs: 500, endMs: 2000, text: 'Good morning, and welcome.' });
  assert.deepStrictEqual(cues[1],
    { startMs: 3000, endMs: 4000, text: 'The launch & the landing.' });
});

test('parseSrt: malformed blocks and empty-after-strip cues are skipped, order sorts', () => {
  const srt = [
    'garbage block with no timing line', '',
    '7', '00:00:05,000 --> 00:00:06,000', 'Second by start time.', '',
    'nonsense --> also nonsense', 'never lands', '',
    '00:00:01,000 --> 00:00:02,000', 'First by start time.', '',
    '9', '00:00:08,000 --> 00:00:09,000', '<i></i>', '', // strips to nothing
  ].join('\n');
  const cues = feeder.parseSrt(srt);
  assert.deepStrictEqual(cues.map((c) => c.text),
    ['First by start time.', 'Second by start time.']);
});

test('parseSrt: every bundled demo track parses to usable cues', () => {
  const tracks = feeder.listTracks();
  assert.ok(tracks.length > 0, 'no bundled demo tracks found');
  for (const { name } of tracks) {
    const cues = feeder.parseSrt(
      fs.readFileSync(path.join(feeder.TRACKS_DIR, `${name}.srt`), 'utf8'));
    assert.ok(cues.length > 0, name);
    assert.ok(cues.every((c) => c.text.length > 0), name);
  }
});

// ---------------------------------------------------------------------------
// Pacing math

// Fake time: wait() advances the clock by exactly what was asked.
function fakeTime() {
  let now = 0;
  const waits = [];
  return {
    clock: () => now,
    wait: async (ms) => { waits.push(ms); now += ms; },
    advance: (ms) => { now += ms; },
    waits,
  };
}

test('replayCues: cues feed at startMs scaled by rate', async () => {
  const t = fakeTime();
  const fed = [];
  const cues = [
    { startMs: 0, endMs: 400, text: 'a' },
    { startMs: 1000, endMs: 1400, text: 'b' },
    { startMs: 4000, endMs: 4400, text: 'c' },
  ];
  await feeder.replayCues(cues, (c) => { fed.push([c.text, t.clock()]); },
    { rate: 2, clock: t.clock, wait: t.wait });
  // rate 2 halves every offset: due at 0, 500, 2000 on the fake clock.
  assert.deepStrictEqual(fed, [['a', 0], ['b', 500], ['c', 2000]]);
  assert.deepStrictEqual(t.waits, [500, 1500]);
});

test('replayCues: an overdue cue feeds immediately and the replay catches up', async () => {
  const t = fakeTime();
  const fed = [];
  const cues = [
    { startMs: 0, endMs: 0, text: 'a' },
    { startMs: 100, endMs: 100, text: 'b' },
    { startMs: 2000, endMs: 2000, text: 'c' },
  ];
  await feeder.replayCues(cues, (c) => {
    fed.push([c.text, t.clock()]);
    if (c.text === 'a') t.advance(700); // a slow post stalls the line
  }, { rate: 1, clock: t.clock, wait: t.wait });
  // b was due at 100, fed at 700 with no wait; c is still on schedule.
  assert.deepStrictEqual(fed, [['a', 0], ['b', 700], ['c', 2000]]);
  assert.deepStrictEqual(t.waits, [1300]);
});

// ---------------------------------------------------------------------------
// Sender: request shape, retries, refusals

function stubFetch(responder) {
  const calls = [];
  const impl = async (url, init) => {
    calls.push({ url, init });
    const r = responder(url, init, calls.length);
    if (r instanceof Error) throw r;
    return {
      status: r.status,
      ok: r.status >= 200 && r.status < 300,
      text: async () => r.body ?? '',
    };
  };
  return { impl, calls };
}

test('sender: each caption is one JSON {text} post to /ingest', async () => {
  const net = stubFetch(() => ({ status: 200, body: '{"text":"ok"}' }));
  const s = feeder.makeSender({
    server: 'http://x/', fetchImpl: net.impl, logImpl: () => {},
  });
  for (const text of ['one', 'two']) assert.ok(await s.send(text));
  assert.deepStrictEqual(net.calls.map((c) => c.url),
    ['http://x/ingest', 'http://x/ingest']);
  assert.deepStrictEqual(net.calls.map((c) => JSON.parse(c.init.body)),
    [{ text: 'one' }, { text: 'two' }]);
  assert.strictEqual(s.posted, 2);
});

test('sender: an unreachable server retries with doubling backoff, then lands', async () => {
  const waits = [];
  const net = stubFetch((url, init, n) => (n < 3
    ? new Error('ECONNREFUSED')
    : { status: 200, body: '{"text":"landed"}' }));
  const s = feeder.makeSender({
    server: 'http://x', fetchImpl: net.impl,
    wait: async (ms) => waits.push(ms), logImpl: () => {},
  });
  assert.ok(await s.send('retried line'));
  assert.strictEqual(net.calls.length, 3);
  assert.deepStrictEqual(waits, [1000, 2000]);
  assert.strictEqual(s.fatal, null);
});

test('sender: a refusal goes fatal on the spot and later sends refuse', async () => {
  const net = stubFetch(() => ({ status: 404, body: 'Not found' }));
  const s = feeder.makeSender({
    server: 'http://x', fetchImpl: net.impl, logImpl: () => {},
  });
  assert.strictEqual(await s.send('refused'), false);
  assert.match(s.fatal, /404.*check --server/);
  assert.strictEqual(await s.send('never sent'), false);
  assert.strictEqual(net.calls.length, 1);
});

// ---------------------------------------------------------------------------
// Track resolution / --list-tracks

test('listTracks: every .srt in the tracks folder is listed, sorted', () => {
  const expected = fs.readdirSync(feeder.TRACKS_DIR)
    .filter((f) => f.endsWith('.srt')).sort()
    .map((f) => f.replace(/\.srt$/, ''));
  assert.deepStrictEqual(feeder.listTracks().map((t) => t.name), expected);
});

test('resolveTrack: bundled name (with or without .srt), direct path, honest miss', () => {
  const [first] = feeder.listTracks();
  const byName = feeder.resolveTrack(first.name);
  assert.strictEqual(byName, feeder.resolveTrack(`${first.name}.srt`));
  assert.ok(fs.existsSync(byName));
  const direct = path.join(os.tmpdir(), `demo-feeder-direct-${PORT}.srt`);
  fs.writeFileSync(direct, '1\n00:00:00,000 --> 00:00:01,000\nhi\n');
  assert.strictEqual(feeder.resolveTrack(direct), direct);
  assert.throws(() => feeder.resolveTrack('no-such-track'),
    new RegExp(`not found.*${first.name}`, 's'));
});

// ---------------------------------------------------------------------------
// End to end: a locally spawned server.js, the feeder as a child process,
// finalized output observed on ws://127.0.0.1:<port>/finalized.

let child;

function writeTrack(name, body) {
  const file = path.join(os.tmpdir(), `demo-feeder-${PORT}-${name}.srt`);
  fs.writeFileSync(file, body);
  return file;
}

function runFeeder(args) {
  const proc = spawn('node', ['tools/demo-feeder.js', ...args], { cwd: ROOT });
  let stderr = '';
  proc.stderr.on('data', (d) => { stderr += d; });
  const exited = new Promise((resolve) => proc.on('exit', resolve));
  return { proc, exited, stderr: () => stderr };
}

before(async () => {
  child = rig.startServer({
    port: PORT,
    tag: 'demo-feeder',
    env: {
      DOTIFY_ENV_FILE: path.join(os.tmpdir(), `dotify-provider-config-${PORT}.env`),
      DOTIFY_TRANSCRIPT_FILE: path.join(os.tmpdir(), `dotify-transcript-${PORT}.txt`),
      DOTIFY_DICTIONARY_FILE: path.join(os.tmpdir(), `dotify-dictionary-${PORT}.json`),
      DOTIFY_MODELS_DIR: path.join(os.tmpdir(), `dotify-models-${PORT}`),
    },
    clean: [path.join(os.tmpdir(), `dotify-transcript-${PORT}.txt`)],
  });
  await rig.waitForListening(BASE);
});
after(() => { if (child) child.kill(); });

test('e2e: a short track replays through server.js — finals in order, complete', async () => {
  const track = writeTrack('e2e', [
    '1\r\n00:00:00,100 --> 00:00:00,900\r\n<i>First demo</i>\r\ncue line.\r\n',
    '\r\n2\r\n00:00:01,000 --> 00:00:01,900\r\nSecond cue.\r\n',
    '\r\n3\r\n00:00:02,000 --> 00:00:02,900\r\nThird &amp; last cue.\r\n',
  ].join(''));
  const client = rig.openFinalized(PORT);
  await client.opened;
  try {
    const f = runFeeder(['--track', track, '--server', BASE, '--rate', '50']);
    const texts = [];
    for (let i = 0; i < 3; i++) {
      const msg = await client.next(5000);
      assert.strictEqual(msg.type, 'final');
      texts.push(msg.text);
    }
    assert.deepStrictEqual(texts,
      ['First demo cue line.', 'Second cue.', 'Third & last cue.']);
    assert.strictEqual(await f.exited, 0);
    assert.match(f.stderr(), /done \(3 captions posted\)/);
  } finally {
    client.ws.close();
  }
});

test('e2e: --loop replays the track again', async () => {
  const track = writeTrack('loop', [
    '1\n00:00:00,100 --> 00:00:00,400\nLoop cue one.\n',
    '\n2\n00:00:00,500 --> 00:00:01,000\nLoop cue two.\n',
  ].join(''));
  const client = rig.openFinalized(PORT);
  await client.opened;
  try {
    const f = runFeeder(['--track', track, '--server', BASE,
      '--rate', '20', '--loop', '--loop-gap-s', '1']);
    const texts = [];
    for (let i = 0; i < 4; i++) texts.push((await client.next(5000)).text);
    // Two full passes arrived.
    assert.deepStrictEqual(texts, [
      'Loop cue one.', 'Loop cue two.', 'Loop cue one.', 'Loop cue two.',
    ]);
    f.proc.kill();
    await f.exited;
  } finally {
    client.ws.close();
  }
});

test('e2e: a wrong --server path is an honest non-zero exit', async () => {
  const track = writeTrack('badpath', '1\n00:00:00,100 --> 00:00:01,000\nNever lands.\n');
  const f = runFeeder(['--track', track, '--server', `${BASE}/not-dotify`, '--rate', '50']);
  assert.strictEqual(await f.exited, 1);
  assert.match(f.stderr(), /404.*check --server/);
});

test('cli: --list-tracks prints the bundled names', async () => {
  const proc = spawn('node', ['tools/demo-feeder.js', '--list-tracks'], { cwd: ROOT });
  let out = '';
  proc.stdout.on('data', (d) => { out += d; });
  const code = await new Promise((resolve) => proc.on('exit', resolve));
  assert.strictEqual(code, 0);
  for (const t of feeder.listTracks()) {
    assert.match(out, new RegExp(`${t.name}\\s+${t.cues} cues`));
  }
});

test('cli: usage errors exit 2', async () => {
  const proc = spawn('node', ['tools/demo-feeder.js', '--rate', '0'], { cwd: ROOT });
  const code = await new Promise((resolve) => proc.on('exit', resolve));
  assert.strictEqual(code, 2);
});
