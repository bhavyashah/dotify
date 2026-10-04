// The cost-gated session wrapper (providers/gated-session.js): lifecycle
// interception around the cost gate. What must hold: withheld silence never
// reaches the vendor, deliberate/idle closes never reach the server's real
// teardown, mid-speech deaths always do, and a cold session re-dials on the
// next onset with the pre-roll intact.

const { test } = require('node:test');
const assert = require('node:assert');
const { createGatedSession } = require('../providers/gated-session');

function chunk(amplitude) {
  const buf = Buffer.alloc(2400 * 2); // 100 ms @ 24 kHz PCM16
  for (let i = 0; i < 2400; i++) buf.writeInt16LE(amplitude, i * 2);
  return buf;
}
const LOUD = chunk(8000);
const QUIET = chunk(0);

// A rig with fast constants: hangover 300 ms, pre-roll 200 ms, idle close
// after 1 s of withholding.
function makeRig(overrides = {}) {
  const rig = {
    sessions: [],        // every fake session ever dialed
    events: [],          // real-handler invocations
    parked: null,
  };
  const dial = (handlers) => {
    const s = {
      handlers,
      sent: [],
      closed: false,
      keepalives: 0,
      sendAudio(pcm) { this.sent.push(pcm); },
      close() { this.closed = true; },
      keepalive() { this.keepalives += 1; },
    };
    rig.sessions.push(s);
    return s;
  };
  rig.gated = createGatedSession(dial, {
    gateOptions: { hangoverMs: 300, preRollMs: 200 },
    idleCloseMs: 1000,
    onReady: () => rig.events.push('ready'),
    onError: (m) => rig.events.push(`error:${m}`),
    onClose: () => rig.events.push('close'),
    ...overrides,
  });
  return rig;
}
function feed(rig, buf, count) {
  for (let i = 0; i < count; i++) rig.gated.sendAudio(buf);
}
function sentMs(session) {
  return session.sent.reduce((ms, b) => ms + b.length / 2 / 24, 0);
}

test('speech flows to the vendor; ready passes through once', () => {
  const rig = makeRig();
  rig.sessions[0].handlers.onReady();
  feed(rig, LOUD, 10);
  assert.deepStrictEqual(rig.events, ['ready']);
  assert.strictEqual(sentMs(rig.sessions[0]), 1000);
});

test('withheld silence never reaches the vendor', () => {
  const rig = makeRig();
  feed(rig, LOUD, 5);
  feed(rig, QUIET, 50); // hangover (300 ms) forwarded, rest withheld…
  // …but the idle tier closes at 1 s withheld, so cap what we assert on:
  assert.strictEqual(sentMs(rig.sessions[0]), 500 + 200); // speech + hangover-1
});

test('keepalive nudges the vendor while withholding, not while speaking', () => {
  const rig = makeRig({ idleCloseMs: 60000 });
  feed(rig, LOUD, 10);
  assert.strictEqual(rig.sessions[0].keepalives, 0);
  feed(rig, QUIET, 100); // 10 s: gated at 0.3 s, ~9.7 s withheld
  assert.ok(rig.sessions[0].keepalives >= 2, `got ${rig.sessions[0].keepalives}`);
  assert.ok(!rig.sessions[0].closed);
});

test('idle tier closes the vendor session; the real teardown never runs', () => {
  const rig = makeRig();
  feed(rig, LOUD, 5);
  feed(rig, QUIET, 20); // 2 s silence: withheld passes idleCloseMs = 1 s
  assert.ok(rig.sessions[0].closed);
  rig.sessions[0].handlers.onClose(); // adapter finishes its close handshake
  assert.deepStrictEqual(rig.events, []);
  assert.strictEqual(rig.gated.stats.cold, true);
});

test('onset after a cold lull re-dials and delivers pre-roll + onset', () => {
  const rig = makeRig();
  rig.sessions[0].handlers.onReady();
  feed(rig, LOUD, 5);
  feed(rig, QUIET, 30);
  rig.sessions[0].handlers.onClose();
  feed(rig, QUIET, 100);                 // long cold lull, no dial
  assert.strictEqual(rig.sessions.length, 1);
  rig.gated.sendAudio(LOUD);             // speech!
  assert.strictEqual(rig.sessions.length, 2);
  assert.strictEqual(sentMs(rig.sessions[1]), 200 + 100); // pre-roll + onset
  rig.sessions[1].handlers.onReady();    // second ready is suppressed
  assert.deepStrictEqual(rig.events, ['ready']);
});

test('vendor hang-up mid-lull probes; a healthy probe parks cold quietly', () => {
  const rig = makeRig({ idleCloseMs: 60000 });
  feed(rig, LOUD, 5);
  feed(rig, QUIET, 10);                  // gated, well before idle close
  rig.sessions[0].handlers.onClose();    // vendor idle timeout
  assert.strictEqual(rig.sessions.length, 2); // the probe dial
  rig.sessions[1].handlers.onReady();    // vendor answers: healthy
  assert.ok(rig.sessions[1].closed);     // probe wrapped up
  rig.sessions[1].handlers.onClose();
  assert.deepStrictEqual(rig.events, []);
  rig.gated.sendAudio(LOUD);             // onset re-dials as usual
  assert.strictEqual(rig.sessions.length, 3);
});

test('vendor death mid-lull with a failing probe reports the outage', () => {
  const rig = makeRig({ idleCloseMs: 60000 });
  feed(rig, LOUD, 5);
  feed(rig, QUIET, 10);
  rig.sessions[0].handlers.onError('mock outage');
  rig.sessions[0].handlers.onClose();    // death → probe dial
  assert.deepStrictEqual(rig.events, []); // nothing reported yet
  rig.sessions[1].handlers.onError('connect failed'); // probe dies too
  rig.sessions[1].handlers.onClose();
  // The ORIGINAL death message propagates, then the real teardown.
  assert.deepStrictEqual(rig.events, ['error:mock outage', 'close']);
});

test('speech onset promotes a still-dialing probe to the live session', () => {
  const rig = makeRig({ idleCloseMs: 60000 });
  feed(rig, LOUD, 5);
  feed(rig, QUIET, 10);
  rig.sessions[0].handlers.onClose();    // probe dialed, not yet ready
  rig.gated.sendAudio(LOUD);             // speech during the probe dial
  assert.strictEqual(rig.sessions.length, 2); // no third dial
  assert.strictEqual(sentMs(rig.sessions[1]), 200 + 100); // pre-roll + onset
  assert.strictEqual(rig.gated.parkedError, null);
});

test('mid-speech death passes straight through to the real handlers', () => {
  const rig = makeRig();
  feed(rig, LOUD, 5);
  rig.sessions[0].handlers.onError('boom');
  rig.sessions[0].handlers.onClose();
  assert.deepStrictEqual(rig.events, ['error:boom', 'close']);
  rig.gated.sendAudio(LOUD);             // inert after teardown
  assert.strictEqual(rig.sessions.length, 1);
});

test('browser close with a live session waits for the adapter flush', () => {
  const rig = makeRig();
  feed(rig, LOUD, 5);
  rig.gated.close();
  assert.ok(rig.sessions[0].closed);
  assert.deepStrictEqual(rig.events, []); // teardown not yet
  rig.sessions[0].handlers.onClose();     // trailing finals flushed
  assert.deepStrictEqual(rig.events, ['close']);
});

test('browser close while cold runs the real teardown directly', () => {
  const rig = makeRig();
  feed(rig, LOUD, 5);
  feed(rig, QUIET, 20);
  rig.sessions[0].handlers.onClose();     // idle-tier close finished
  rig.gated.close();
  assert.deepStrictEqual(rig.events, ['close']);
});

test('speech beating an in-flight idle close dials a fresh session', () => {
  const rig = makeRig();
  feed(rig, LOUD, 5);
  feed(rig, QUIET, 20);                   // idle close requested…
  assert.ok(rig.sessions[0].closed);      // …but onClose has NOT fired yet
  rig.gated.sendAudio(LOUD);              // speech during the handshake
  assert.strictEqual(rig.sessions.length, 2);
  assert.strictEqual(sentMs(rig.sessions[1]), 200 + 100);
  rig.sessions[0].handlers.onClose();     // old handshake completes: ignored
  assert.deepStrictEqual(rig.events, []);
  feed(rig, LOUD, 3);                     // new session keeps receiving
  assert.strictEqual(sentMs(rig.sessions[1]), 300 + 300);
});

test('stats expose billed vs suppressed and the dial count', () => {
  const rig = makeRig();
  feed(rig, LOUD, 5);
  feed(rig, QUIET, 30);
  rig.sessions[0].handlers.onClose();
  rig.gated.sendAudio(LOUD);
  const s = rig.gated.stats;
  assert.strictEqual(s.dials, 2);
  assert.strictEqual(s.fedMs, 3600);
  assert.strictEqual(s.fedMs, s.forwardedMs + s.suppressedMs);
});
