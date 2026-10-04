const { test, before, after } = require('node:test');
const assert = require('node:assert');
const { spawn } = require('node:child_process');
const path = require('node:path');
const os = require('node:os');
const { waitForListening } = require('./helpers');
// Playwright is a devDependency: the installed/staged image ships runtime
// deps only, so `node --test` there must not die at require(). Without it the
// browser suite skips cleanly — the repo run (devDependencies installed)
// keeps full coverage of the shipping page, and the staged image's smoke
// coverage is the server tests plus verify.ps1's launcher simulation.
let chromium = null;
try { ({ chromium } = require('playwright')); } catch {}
const skip = chromium ? false : 'playwright not installed (dev-only browser tests)';

const ROOT = path.join(__dirname, '..');
const PORT = 8798;
const BASE = `http://127.0.0.1:${PORT}`;
let child, browser;

before(async () => {
  if (!chromium) return;
  const dictFile = path.join(os.tmpdir(), `dotify-ui-dictionary-${PORT}.json`);
  try { require('node:fs').rmSync(dictFile, { force: true }); } catch {}
  const env = { ...process.env, PORT: String(PORT),
    DOTIFY_ENV_FILE: path.join(os.tmpdir(), 'dotify-nonexistent-env'),
    DOTIFY_TRANSCRIPT_FILE: path.join(os.tmpdir(), `dotify-ui-${PORT}.txt`),
    DOTIFY_DICTIONARY_FILE: dictFile,
    // The env-gated mock provider, a keyed engine that never dials out,
    // lets the tests hold a running session headless.
    DOTIFY_MOCK_PROVIDER: '1',
    DOTIFY_MOCK_KEY: 'mock-key' };
  // No provider key may leak in from the machine: a configured key would
  // flip the page's default engine and break the default-pick tests.
  delete env.OPENAI_API_KEY;
  delete env.ASSEMBLYAI_API_KEY;
  delete env.DEEPGRAM_API_KEY;
  delete env.ELEVENLABS_API_KEY;
  child = spawn('node', ['server.js'], { cwd: ROOT, env });
  await waitForListening(BASE);
  // Fake mic: the keyed /audio path starts with
  // getUserMedia, which headless Chromium only grants with these flags.
  browser = await chromium.launch({
    args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream'],
  });
});
after(async () => { if (browser) await browser.close(); if (child) child.kill(); });

test('engine selector defaults to ElevenLabs and lists the keyed engines', { skip }, async () => {
  // Every core engine needs a key; the untouched default is ElevenLabs.
  const page = await browser.newPage();
  await page.goto(BASE);
  const value = await page.locator('#engine').inputValue();
  assert.strictEqual(value, 'elevenlabs');
  const labels = await page.locator('#engine option').allTextContents();
  assert.ok(labels.some((l) => /ElevenLabs/i.test(l)));
  assert.ok(labels.some((l) => /OpenAI/i.test(l)));
  assert.ok(labels.some((l) => /Deepgram/i.test(l)));
  await page.close();
});

test('posting via /ingest reaches /finalized', { skip }, async () => {
  // The browser→server contract sessionless engines use (the Windows
  // overlay's offline Nemotron path, the demo feeder, typed text).
  const WebSocket = require('ws');
  const page = await browser.newPage();
  await page.goto(BASE);
  const finalized = new Promise((resolve) => {
    const ws = new WebSocket(`ws://127.0.0.1:${PORT}/finalized`);
    ws.on('message', (raw) => {
      const msg = JSON.parse(raw.toString());
      if (msg.type === 'latency') return;  // the connection welcome, not text
      resolve(msg);
      ws.close();
    });
  });
  await new Promise((r) => setTimeout(r, 200));
  await page.evaluate(() => fetch('/ingest', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text: 'browser contract check' }),
  }));
  const msg = await finalized;
  assert.strictEqual(msg.type, 'final');
  assert.strictEqual(msg.text, 'browser contract check');
  await page.close();
});

test('mic picker prefers usb, then bluetooth, and skips synthetic entries', { skip }, async () => {
  // dotifyPickMic is the pure device picker behind
  // getUserMedia's deviceId pin — usb > bluetooth > OS default. Chrome's synthetic "default" and
  // "communications" rows duplicate a physical device and must never be
  // pinned by exact id; outputs must never look like mics.
  const page = await browser.newPage();
  await page.goto(BASE);
  const picks = await page.evaluate(() => {
    const dev = (kind, deviceId, label) => ({ kind, deviceId, label });
    const builtin = dev('audioinput', 'id-builtin', 'Microphone Array (Realtek Audio)');
    const usb = dev('audioinput', 'id-usb', 'Microphone (USB PnP Audio Device)');
    const bt = dev('audioinput', 'id-bt', 'Headset (WH-1000XM4 Hands-Free AG Audio)');
    const synthetic = dev('audioinput', 'default', 'Default - Microphone (USB PnP Audio Device)');
    const speaker = dev('audiooutput', 'id-out', 'Speakers (USB Audio DAC)');
    const pick = (list) => {
      const p = window.dotifyPickMic(list);
      return p ? { id: p.device.deviceId, label: p.label } : null;
    };
    return {
      usbWins: pick([synthetic, builtin, bt, usb, speaker]),
      btNext: pick([synthetic, builtin, bt]),
      defaultOtherwise: pick([builtin]),
      outputsIgnored: pick([speaker]),
    };
  });
  assert.deepStrictEqual(picks.usbWins, { id: 'id-usb', label: 'usb mic' });
  assert.deepStrictEqual(picks.btNext, { id: 'id-bt', label: 'bluetooth mic' });
  assert.strictEqual(picks.defaultOtherwise, null);
  assert.strictEqual(picks.outputsIgnored, null);
  await page.close();
});

test('a pinned mic that fails to open falls back to the OS default at start', { skip }, async () => {
  // The exact-id pin must never cost the session: a winner that vanished
  // between the pick and the open (or is held exclusively) makes the
  // pinned getUserMedia throw — the page retries unpinned, captures the
  // OS default, and publishes the honest 'default mic' noun. The failed
  // pin is not retried by the post-wire re-verify, so no surface ever
  // claims 'usb mic'.
  const page = await browser.newPage();
  await page.goto(BASE);
  await page.evaluate(() => {
    window.__micCalls = [];
    window.dotifyMicSource = (label, announce) =>
      window.__micCalls.push({ label, announce });
    // A USB-labeled winner no real device backs: the exact pin rejects.
    navigator.mediaDevices.enumerateDevices = async () => [
      { kind: 'audioinput', deviceId: 'id-usb-ghost',
        label: 'Microphone (USB PnP Audio Device)' },
    ];
    const engine = document.getElementById('engine');
    const option = document.createElement('option');
    option.value = 'mock';
    option.textContent = 'Mock provider (test only)';
    engine.appendChild(option);
    engine.value = 'mock';
  });
  await page.click('#toggle');
  await page.waitForFunction(toggleSays('Stop'));
  await new Promise((r) => setTimeout(r, 300)); // let the re-verify settle
  const calls = await page.evaluate(() => window.__micCalls);
  assert.ok(calls.length >= 1);
  assert.strictEqual(calls[0].label, 'default mic');
  assert.ok(calls.every((c) => c.label !== 'usb mic'));
  await page.close();
});

test('a dying capture track re-picks and announces the replacement', { skip }, async () => {
  // track.onended is the surest detach signal (devicechange never says
  // which device died). The handler re-picks and swaps with announce
  // forced — even onto the SAME published label, because a same-noun swap
  // (usb mic A dies, usb mic B takes over) is still a physical change the
  // reader must feel.
  const page = await browser.newPage();
  await page.goto(BASE);
  await page.evaluate(() => {
    window.__micCalls = [];
    window.dotifyMicSource = (label, announce) =>
      window.__micCalls.push({ label, announce });
    window.__streams = [];
    const real = navigator.mediaDevices.getUserMedia
      .bind(navigator.mediaDevices);
    navigator.mediaDevices.getUserMedia = async (c) => {
      const s = await real(c);
      window.__streams.push(s);
      return s;
    };
    const engine = document.getElementById('engine');
    const option = document.createElement('option');
    option.value = 'mock';
    option.textContent = 'Mock provider (test only)';
    engine.appendChild(option);
    engine.value = 'mock';
  });
  await page.click('#toggle');
  await page.waitForFunction(toggleSays('Stop'));
  const first = await page.evaluate(() => window.__micCalls[0]);
  assert.deepStrictEqual(first, { label: 'default mic', announce: false });
  // Simulate the OS ending the live track (stop() from page code does NOT
  // fire onended, so invoking the wired handler is the honest simulation).
  await page.evaluate(() => {
    window.__streams[0].getAudioTracks()[0].onended();
  });
  await page.waitForFunction(
    () => window.__micCalls.some((c) => c.announce));
  const out = await page.evaluate(() => ({
    calls: window.__micCalls,
    oldTrack: window.__streams[0].getAudioTracks()[0].readyState,
    status: document.getElementById('status').textContent,
  }));
  // The forced announce pushed the SAME label past the dedupe...
  assert.deepStrictEqual(out.calls[out.calls.length - 1],
    { label: 'default mic', announce: true });
  assert.match(out.status, /Microphone: default mic\./);
  // ...and the dead capture's stream was actually released.
  assert.strictEqual(out.oldTrack, 'ended');
  await page.close();
});

test('stopping the session discards an in-flight mic swap', { skip }, async () => {
  // Swaps are token-serialized: a swap that resolves after stop() must
  // not publish a mic noun for a session that no longer exists, and must
  // release the stream it opened.
  const page = await browser.newPage();
  await page.goto(BASE);
  await page.evaluate(() => {
    window.__micCalls = [];
    window.dotifyMicSource = (label, announce) =>
      window.__micCalls.push({ label, announce });
    window.__realGum = navigator.mediaDevices.getUserMedia
      .bind(navigator.mediaDevices);
    const engine = document.getElementById('engine');
    const option = document.createElement('option');
    option.value = 'mock';
    option.textContent = 'Mock provider (test only)';
    engine.appendChild(option);
    engine.value = 'mock';
  });
  await page.click('#toggle');
  await page.waitForFunction(toggleSays('Stop'));
  await page.evaluate(() => {
    // A new USB winner appears; its swap's getUserMedia hangs until the
    // test resolves it — after the session is gone.
    navigator.mediaDevices.enumerateDevices = async () => [
      { kind: 'audioinput', deviceId: 'id-usb-late',
        label: 'Microphone (USB PnP Audio Device)' },
    ];
    navigator.mediaDevices.getUserMedia = () => new Promise((res) => {
      window.__resolveGum = () => window.__realGum({ audio: true })
        .then((s) => { window.__lateStream = s; res(s); });
    });
    navigator.mediaDevices.dispatchEvent(new Event('devicechange'));
  });
  await page.click('#toggle'); // Stop while the swap is still in flight
  await page.waitForFunction(toggleSays('Start'));
  await page.evaluate(() => window.__resolveGum());
  await page.waitForFunction(() => window.__lateStream);
  await new Promise((r) => setTimeout(r, 200)); // let the resolver run out
  const out = await page.evaluate(() => ({
    calls: window.__micCalls,
    lateTrack: window.__lateStream.getAudioTracks()[0].readyState,
    status: document.getElementById('status').textContent,
  }));
  // The stale swap said nothing and named nothing...
  assert.ok(out.calls.every((c) => c.label !== 'usb mic'));
  assert.ok(!/Microphone: usb mic/.test(out.status));
  // ...and closed the stream it had opened for the dead session.
  assert.strictEqual(out.lateTrack, 'ended');
  await page.close();
});

test('dotifyTranscript.extend grows the current line without a newline', { skip }, async () => {
  // The Windows overlay echoes typed-to-display words with extend() so a
  // typed utterance reads as ONE line; append() starts lines as before.
  const page = await browser.newPage();
  await page.goto(BASE);
  const text = await page.evaluate(() => {
    window.dotifyTranscript.extend('typed'); // works on an empty box too
    window.dotifyTranscript.extend(' words');
    window.dotifyTranscript.append('spoken line');
    return document.getElementById('finalized').textContent;
  });
  assert.strictEqual(text, 'typed words\nspoken line');
  await page.close();
});

test('clear history with no reading position empties the whole box', { skip }, async () => {
  // Without a boundary provider (plain page, or highlight dark in the
  // Windows app) the documented fallback is a whole-field clear, announced
  // on the status live region.
  const page = await browser.newPage();
  await page.goto(BASE);
  const out = await page.evaluate(() => {
    window.dotifyTranscript.append('first line');
    window.dotifyTranscript.append('second line');
    document.getElementById('clear-history').click();
    return { text: document.getElementById('finalized').textContent,
             status: document.getElementById('status').textContent };
  });
  assert.strictEqual(out.text, '');
  assert.strictEqual(out.status, 'History cleared.');
  await page.close();
});

test('clear history keeps everything at or past the reading position', { skip }, async () => {
  // The Windows braille panel feeds the reading boundary through
  // dotifyTranscript.setBands; the cut removes strictly BEFORE it and
  // fires the resync event for consumers caching offsets into the old
  // record. After the cut the boundary is 0 and the kept text renders.
  const page = await browser.newPage();
  await page.goto(BASE);
  const out = await page.evaluate(() => {
    const t = window.dotifyTranscript;
    t.append('already read words');
    t.append('still being read');
    t.setBands({ boundary: t.record().indexOf('still') });
    let resynced = false;
    document.getElementById('finalized')
      .addEventListener('dotify-history-cleared', () => { resynced = true; });
    document.getElementById('clear-history').click();
    // Boundary 0 after the cut: the whole (kept) record is unread, and
    // history — the slice before the boundary — renders empty until the
    // panel re-anchors. The RECORD is what must keep the text.
    return { record: t.record(), resynced };
  });
  assert.strictEqual(out.record, 'still being read');
  assert.strictEqual(out.resynced, true);
  await page.close();
});

test('clear history at reading position zero removes nothing', { skip }, async () => {
  // Boundary 0 = the reader is at the very start: nothing is history yet.
  // The whole-field fallback must NOT fire — that would delete text the
  // display still owes the reader.
  const page = await browser.newPage();
  await page.goto(BASE);
  const record = await page.evaluate(() => {
    const t = window.dotifyTranscript;
    t.append('unread from the very start');
    t.setBands({ boundary: 0 });
    document.getElementById('clear-history').click();
    return t.record();
  });
  assert.strictEqual(record, 'unread from the very start');
  await page.close();
});

test('the three bands split the record and color source lines', { skip }, async () => {
  // The screen-braille contract: history is the record before the
  // boundary; the current and pending bands are their own elements below
  // it; a "Human: " line (like Recap:/Reply:) is colored so a sighted
  // user sees the source the prefix tells a screen reader.
  const page = await browser.newPage();
  await page.goto(BASE);
  const out = await page.evaluate(() => {
    const t = window.dotifyTranscript;
    t.append('already read words');
    t.append('Human: typed by the partner');
    t.append('still being read');
    t.setBands({
      boundary: t.record().indexOf('still'),
      current: 'still being',
      pending: 'read and more behind it',
    });
    const spans = Array.from(
      document.querySelectorAll('#finalized span'));
    const human = spans.find(
      (s) => s.textContent.startsWith('Human:'));
    return {
      history: document.getElementById('finalized').textContent,
      current: document.getElementById('current-band').textContent,
      pending: document.getElementById('pending-band').textContent,
      humanColor: human ? human.style.color : null,
      plainColor: spans.length ? spans[0].style.color : null,
    };
  });
  assert.strictEqual(
    out.history, 'already read words\nHuman: typed by the partner\n');
  assert.strictEqual(out.current, 'still being');
  assert.strictEqual(out.pending, 'read and more behind it');
  assert.strictEqual(out.humanColor, 'rgb(165, 214, 167)');
  assert.strictEqual(out.plainColor, '');
  await page.close();
});

// --- Pause/resume transcription ---------------------------------------------
// The env-gated mock provider (a keyed engine that never dials out) plus the
// fake-mic browser flags let a session genuinely RUN headless, so these tests
// exercise the real page wiring end to end: the pause button drives the
// Start/Stop toggle, and window.dotifyPause is the platform-shell hook the
// Windows app uses for Human-mode auto-pause. The page never offers
// the mock provider itself, so each test injects its <option>.

const toggleSays = (prefix) =>
  `document.getElementById('toggle').textContent.trim().startsWith('${prefix}')`;

async function startFakeSession(page) {
  await page.goto(BASE);
  await page.evaluate(() => {
    const engine = document.getElementById('engine');
    const option = document.createElement('option');
    option.value = 'mock';
    option.textContent = 'Mock provider (test only)';
    engine.appendChild(option);
    engine.value = 'mock';
  });
  await page.click('#toggle');
  await page.waitForFunction(toggleSays('Stop'));
  await page.waitForFunction(() => !document.getElementById('pause-toggle').disabled);
}

test('pause button is present but disabled while idle', { skip }, async () => {
  const page = await browser.newPage();
  await page.goto(BASE);
  const pause = page.locator('#pause-toggle');
  assert.strictEqual((await pause.textContent()).trim(), 'Pause microphone');
  assert.strictEqual(await pause.isDisabled(), true);
  await page.close();
});

test('pause stops the running session and resume restarts it', { skip }, async () => {
  const page = await browser.newPage();
  await startFakeSession(page);
  await page.click('#pause-toggle');
  await page.waitForFunction(toggleSays('Start'));
  assert.strictEqual(
    (await page.locator('#pause-toggle').textContent()).trim(),
    'Resume microphone');
  assert.match(await page.locator('#status').textContent(), /paused/i);
  await page.click('#pause-toggle');
  await page.waitForFunction(toggleSays('Stop'));
  assert.strictEqual(
    (await page.locator('#pause-toggle').textContent()).trim(),
    'Pause microphone');
  await page.close();
});

test('auto pause auto-resumes but a manual pause is sticky', { skip }, async () => {
  // The exact contract the Windows app's Human-mode hook depends on.
  const page = await browser.newPage();
  await startFakeSession(page);
  await page.evaluate(() => window.dotifyPause.pause({ auto: true }));
  await page.waitForFunction(toggleSays('Start'));
  await page.evaluate(() => window.dotifyPause.resume({ auto: true }));
  await page.waitForFunction(toggleSays('Stop'));
  await page.click('#pause-toggle'); // the user's own pause
  await page.waitForFunction(toggleSays('Start'));
  await page.evaluate(() => window.dotifyPause.resume({ auto: true }));
  await new Promise((r) => setTimeout(r, 200)); // must NOT have resumed
  assert.ok((await page.locator('#toggle').textContent()).trim().startsWith('Start'));
  assert.strictEqual(await page.evaluate(() => window.dotifyPause.state()), 'manual');
  await page.click('#pause-toggle'); // manual resume still works
  await page.waitForFunction(toggleSays('Stop'));
  await page.close();
});

// --- Settings category navigation -------------------------------------------
// Settings opens to category buttons only. Each category is a second-level
// panel, while its controls remain mounted so state survives navigation.
const openSettings = (page) => page.locator('#settings > summary').click();
const openSettingsCategory = (page, name) =>
  page.locator(`[data-settings-category="${name}"]`).click();

test('Settings landing page shows categories, then one category at a time', { skip }, async () => {
  const page = await browser.newPage();
  await page.goto(BASE);
  const settings = page.locator('#settings');
  assert.strictEqual(await settings.evaluate((el) => el.open), false);
  assert.strictEqual(await page.locator('#engine').isVisible(), false);
  assert.strictEqual(await page.locator('#provider-config').isVisible(), false);
  assert.strictEqual(await page.locator('#personal-dictionary').isVisible(), false);
  // The every-session controls stay on the main page.
  assert.strictEqual(await page.locator('#toggle').isVisible(), true);
  assert.strictEqual(await page.locator('#finalized').isVisible(), true);
  await openSettings(page);
  assert.strictEqual(await settings.evaluate((el) => el.open), true);
  assert.strictEqual(await page.locator('#settings-categories').isVisible(), true);
  assert.strictEqual(await page.locator('#engine').isVisible(), false);
  await openSettingsCategory(page, 'transcription');
  assert.strictEqual(await page.locator('#engine').isVisible(), true);
  assert.strictEqual(await page.locator('#record-session').isVisible(), false);
  await page.locator('#settings-transcription .settings-category-back').click();
  await openSettingsCategory(page, 'keys');
  assert.strictEqual(await page.locator('#provider-config').isVisible(), true);
  assert.strictEqual(await page.locator('#engine').isVisible(), false);
  await page.locator('#settings-category-keys .settings-category-back').click();
  assert.strictEqual(await page.locator('#settings-categories').isVisible(), true);
  assert.strictEqual(await page.locator('#engine').isVisible(), false);
  await page.close();
});

test('session recording is absent from the normal UI', { skip }, async () => {
  // Recording is the WER-measurement harness, a developer/eval tool — not a
  // user feature. Without the dev gate the Developer options category does
  // not exist for any user, and no visible text or accessibility-tree entry
  // anywhere mentions recording.
  const page = await browser.newPage();
  await page.goto(BASE);
  await openSettings(page);
  assert.strictEqual(
    await page.locator('[data-settings-category="developer"]').isVisible(), false);
  assert.doesNotMatch(await page.locator('body').innerText(), /record/i);
  // hidden removes the control from the accessibility tree, not just sight.
  assert.strictEqual(await page.evaluate(
    () => document.getElementById('settings-category-developer').hidden), true);
  await page.close();
});

test('?dev=1 reveals the recording control with unchanged behavior', { skip }, async () => {
  // The explicit developer gate: the same page opened with ?dev=1 shows the
  // Developer options category and its checkbox (tools/wer-replay.js
  // depends on the recorder this drives).
  const page = await browser.newPage();
  await page.goto(`${BASE}/?dev=1`);
  await openSettings(page);
  await openSettingsCategory(page, 'developer');
  assert.strictEqual(await page.locator('#record-session').isVisible(), true);
  await page.locator('#record-session').check();
  assert.strictEqual(await page.locator('#record-session').isChecked(), true);
  await page.close();
});

test('settings fields and disclosures keep state across category navigation', { skip }, async () => {
  const page = await browser.newPage();
  await page.goto(BASE);
  await openSettings(page);
  await openSettingsCategory(page, 'transcription');
  await page.locator('#personal-dictionary summary').click();
  await page.fill('#dictionary-word', 'Draft name');
  await page.locator('#settings-transcription .settings-category-back').click();
  await openSettingsCategory(page, 'transcription');
  assert.strictEqual(await page.locator('#personal-dictionary').evaluate((el) => el.open), true);
  assert.strictEqual(await page.locator('#dictionary-word').inputValue(), 'Draft name');
  await page.keyboard.press('Escape');
  assert.strictEqual(await page.locator('#settings-categories').isVisible(), true);
  await page.close();
});

// --- Conversation-mode disclosure ---------------------------------------------
// All experimental speaker features live under one collapsed <details>; the
// controls only become visible (and tabbable) after the user expands it.
test('conversation-mode features sit behind one collapsed disclosure', { skip }, async () => {
  const page = await browser.newPage();
  await page.goto(BASE);
  await openSettings(page);
  await openSettingsCategory(page, 'transcription');
  const details = page.locator('#conversation-features');
  assert.strictEqual(await details.evaluate((el) => el.open), false);
  assert.strictEqual(await page.locator('#conversation-mode').isVisible(), false);
  assert.strictEqual(await page.locator('#enroll-button').isVisible(), false);
  await page.locator('#conversation-features summary').click();
  assert.strictEqual(await details.evaluate((el) => el.open), true);
  // The wiring inside still works: checking conversation mode unlocks the
  // speaker-count hint.
  await page.locator('#conversation-mode').check();
  assert.strictEqual(await page.locator('#max-speakers').isDisabled(), false);
  await page.close();
});

// --- Personal dictionary -----------------------------------------------------
test('personal dictionary panel adds and removes words', { skip }, async () => {
  const page = await browser.newPage();
  await page.goto(BASE);
  await openSettings(page);
  await openSettingsCategory(page, 'transcription');
  // Collapsed disclosure, like the provider config next to it.
  const details = page.locator('#personal-dictionary');
  assert.strictEqual(await details.evaluate((el) => el.open), false);
  await page.locator('#personal-dictionary summary').click();
  await page.waitForFunction( // token must be loaded before writes can work
    () => typeof providerConfigToken === 'string' && providerConfigToken.length > 0);
  await page.fill('#dictionary-word', 'Dotify');
  await page.fill('#dictionary-sounds-like', 'dotafy, dot if eye');
  await page.click('#dictionary-add');
  await page.waitForFunction(() =>
    document.getElementById('dictionary-list').textContent.includes('Dotify'));
  const item = await page.locator('#dictionary-list li').first().textContent();
  assert.match(item, /Dotify \(always replaces: dotafy, dot if eye\)/);
  // "dot if eye" is a phrase of ordinary English words — the add must announce
  // the always-replace risk on the status line.
  const status = await page.locator('#dictionary-status').textContent();
  assert.match(status, /Caution:.*"dot if eye".*ordinary English/);
  await page.click('#dictionary-list li button'); // Remove Dotify
  await page.waitForFunction(() =>
    document.getElementById('dictionary-list').textContent.includes('No words yet.'));
  await page.close();
});

// --- Keyed default engine -----------------------------------------------------
// A separate server with a configured AssemblyAI key only: the untouched
// keyless default flips to AssemblyAI (the fallback preference) on load, but
// never over an explicit user choice.
test('a configured AssemblyAI key becomes the default engine', { skip }, async () => {
  // Unique across ALL test files — node --test runs them in parallel, and a
  // shared port means this page hits another suite's server (whose /reset
  // call on page load then corrupts that suite's transcript assertions).
  // 8788-8792 belong to a live Dotify install.
  const port = 8781;
  const env = { ...process.env, PORT: String(port),
    DOTIFY_ENV_FILE: path.join(os.tmpdir(), 'dotify-nonexistent-env'),
    DOTIFY_TRANSCRIPT_FILE: path.join(os.tmpdir(), `dotify-ui-${port}.txt`),
    ASSEMBLYAI_API_KEY: 'test-key-not-real',
    // A second configured engine so the user-pick assertion below has a
    // legal non-default choice (unconfigured options are disabled).
    OPENAI_API_KEY: 'test-key-not-real' };
  delete env.DEEPGRAM_API_KEY;
  delete env.ELEVENLABS_API_KEY;
  const keyed = spawn('node', ['server.js'], { cwd: ROOT, env });
  try {
    const base = `http://127.0.0.1:${port}`;
    await waitForListening(base);
    const page = await browser.newPage();
    await page.goto(base);
    await page.waitForFunction(
      () => document.getElementById('engine').value === 'assemblyai');
    const labels = await page.locator('#engine option').allTextContents();
    assert.ok(labels.some((l) => /AssemblyAI.*configured/i.test(l)));
    // An explicit user pick must survive a provider-config re-render.
    await openSettings(page);
    await openSettingsCategory(page, 'transcription'); // selectOption needs the control visible
    await page.selectOption('#engine', 'openai');
    await page.evaluate(() => refreshProviderConfig());
    await new Promise((r) => setTimeout(r, 200));
    assert.strictEqual(await page.locator('#engine').inputValue(), 'openai');
    await page.close();
  } finally { keyed.kill(); }
});

// With BOTH keys configured the default is ElevenLabs — the AMI A/B winner.
test('ElevenLabs outranks AssemblyAI as the keyed default', { skip }, async () => {
  const port = 8780; // see the port note in the previous test
  const env = { ...process.env, PORT: String(port),
    DOTIFY_ENV_FILE: path.join(os.tmpdir(), 'dotify-nonexistent-env'),
    DOTIFY_TRANSCRIPT_FILE: path.join(os.tmpdir(), `dotify-ui-${port}.txt`),
    ASSEMBLYAI_API_KEY: 'test-key-not-real',
    ELEVENLABS_API_KEY: 'test-key-not-real' };
  delete env.OPENAI_API_KEY;
  delete env.DEEPGRAM_API_KEY;
  const keyed = spawn('node', ['server.js'], { cwd: ROOT, env });
  try {
    const base = `http://127.0.0.1:${port}`;
    await waitForListening(base);
    const page = await browser.newPage();
    await page.goto(base);
    await page.waitForFunction(
      () => document.getElementById('engine').value === 'elevenlabs');
    await page.close();
  } finally { keyed.kill(); }
});

test('starting fresh while paused ends the pause', { skip }, async () => {
  const page = await browser.newPage();
  await startFakeSession(page);
  await page.click('#pause-toggle');
  await page.waitForFunction(toggleSays('Start'));
  await page.click('#toggle'); // an explicit Start overrides the pause
  await page.waitForFunction(toggleSays('Stop'));
  await page.waitForFunction(() => window.dotifyPause.state() === 'off');
  const pause = page.locator('#pause-toggle');
  assert.strictEqual((await pause.textContent()).trim(), 'Pause microphone');
  assert.strictEqual(await pause.isDisabled(), false);
  await page.close();
});

// --- Graceful connection loss -------------------------------------------------

test('an unexpected keyed-session death fires dotifySessionLost exactly once', { skip }, async () => {
  // The hook is the contract platform shells build their offline fallback on
  // (the Windows overlay's auto-fallback + auto-return). A keyed engine with
  // no key on file dies with a server-reported error AFTER the /audio socket
  // opened — the same page path a mid-run provider socket death takes — so
  // this exercises the detection end to end without a provider key or a
  // network. Needs a fake mic: the keyed path calls getUserMedia first.
  const fakeMic = await chromium.launch({
    args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream'],
  });
  try {
    const page = await fakeMic.newPage();
    await page.goto(BASE);
    await page.evaluate(() => {
      window.__losses = [];
      window.dotifySessionLost = (loss) => window.__losses.push(loss);
      document.getElementById('engine').value = 'elevenlabs';
    });
    await page.click('#toggle');
    await page.waitForFunction(() => window.__losses.length > 0, null, { timeout: 15000 });
    await new Promise((r) => setTimeout(r, 300)); // let any error->close double-fire land
    const losses = await page.evaluate(() => window.__losses);
    assert.strictEqual(losses.length, 1);
    assert.strictEqual(losses[0].engine, 'elevenlabs');
    assert.strictEqual(losses[0].reason, 'error');
    assert.match(losses[0].message, /missing/);
    // The page recovered to a startable state with an honest status — the
    // hook must fire only after the page has fully stopped, so a subscriber
    // can start another engine immediately.
    assert.match(await page.locator('#status').textContent(), /Error:/);
    assert.ok((await page.locator('#toggle').textContent()).trim().startsWith('Start'));
    await page.close();
  } finally {
    await fakeMic.close();
  }
});

test("the OS 'offline' event ends a running keyed session as a session loss", { skip }, async () => {
  // Interface-level network loss fires 'offline' immediately; waiting for
  // the socket to notice wastes the seconds this event hands us. The page
  // must stop and fire the loss hook exactly once (reason 'offline'), even
  // though its own /audio socket also closes moments later.
  const page = await browser.newPage();
  await startFakeSession(page);
  await page.evaluate(() => {
    window.__losses = [];
    window.dotifySessionLost = (loss) => window.__losses.push(loss);
    window.dispatchEvent(new Event('offline'));
  });
  await page.waitForFunction(() => window.__losses.length > 0);
  await new Promise((r) => setTimeout(r, 300)); // let the socket-close land
  const losses = await page.evaluate(() => window.__losses);
  assert.strictEqual(losses.length, 1);
  assert.strictEqual(losses[0].reason, 'offline');
  assert.strictEqual(losses[0].engine, 'mock');
  assert.match(await page.locator('#status').textContent(), /internet connection was lost/i);
  assert.ok((await page.locator('#toggle').textContent()).trim().startsWith('Start'));
  await page.close();
});

test('forgetting a speaker reports a failed delete instead of success', { skip }, async () => {
  const page = await browser.newPage();
  await page.route('**/api/speakers', (route) => route.fulfill({
    json: { ok: true, speakers: [{ name: 'Guest' }] } }));
  await page.route('**/api/speakers/Guest', (route) => route.fulfill({
    status: 502, json: { error: 'speaker service unavailable' } }));
  await page.goto(BASE);
  await openSettings(page);
  await openSettingsCategory(page, 'transcription');
  await page.locator('#conversation-features summary').click();
  await page.locator('#speakers-list button', { hasText: 'Forget Guest' }).click();
  await page.waitForFunction(() =>
    document.getElementById('speakers-status').textContent.includes('Guest'));
  assert.match(await page.locator('#speakers-status').textContent(), /Could not forget Guest/);
  await page.close();
});
