// The braille control panel, served by control_bridge.py and injected into
// the speech page. It talks to the bridge (token and base URL are filled in
// when served), polls the engine state, feeds the page's transcript bands,
// and hosts the typing box.
(() => {
  'use strict';
  const token = __DOTIFY_TOKEN_JSON__;
  const base = __DOTIFY_BASE_JSON__;
  if (document.getElementById('dotify-appliance-controls')) return;

  const style = document.createElement('style');
  // Uses the page's :root color tokens, with fallbacks for an unthemed page.
  // Control sizes are generous on purpose: they are accessibility.
  style.textContent = `
    #dotify-appliance-controls { margin: 0 0 1rem; color: var(--ink, #FFFFFF); }
    .dotify-row { display: flex; flex-wrap: wrap; gap: .6rem; margin: .65rem 0; align-items: center; }
    .dotify-row[hidden] { display: none; }
    .dotify-group-label { font-size: .8rem; font-weight: 700; letter-spacing: .1em; text-transform: uppercase; color: var(--ink-2, #b9b7b0); min-width: 5.2rem; }
    #dotify-connection-details { margin: .5rem 0; }
    #dotify-quit-row { margin: 1.5rem 0 .5rem; }
    #dotify-quit-hint { color: var(--ink-2, #b9b7b0); font-size: .95rem; margin: 1.5rem 0 .25rem; }
    #dotify-quit-row button { min-height: 3rem; min-width: 7rem; font-size: 1.05rem; }
    #dotify-appliance-controls [hidden], #dotify-braille-controls [hidden] { display: none; }
    #dotify-appliance-controls button, #dotify-braille-controls button { min-height: 3rem; min-width: 7rem; font-size: 1.05rem; }
    #dotify-readout { margin: 1rem 0 0; }
    #dotify-readout div { display: flex; gap: .6rem; align-items: baseline; margin: .3rem 0; }
    #dotify-readout dt { font-size: .8rem; font-weight: 700; letter-spacing: .1em; text-transform: uppercase; color: var(--ink-2, #b9b7b0); min-width: 8.5rem; }
    #dotify-readout dd { margin: 0; }
    #dotify-readout output { font-weight: 700; }
    #dotify-control-status { color: var(--ink-2, #b9b7b0); margin: 0 0 .75rem; }
    #dotify-compose { margin: .4rem 0 1.25rem; }
    #dotify-compose label { font-weight: 700; }
    #dotify-compose .dotify-row { align-items: stretch; margin: .4rem 0; }
    #dotify-compose button { min-height: 3rem; min-width: 7rem; font-size: 1.05rem; }
    #dotify-type-text { flex: 1 1 20rem; min-height: 3rem; font-size: 1.05rem; }
    #dotify-type-status { font-weight: 600; margin: .3rem 0 0; color: var(--ink-2, #b9b7b0); }
    #dotify-control-error, #dotify-settings-error {
      color: var(--alert, #ff8a80); font-weight: 600; }
    #dotify-control-error:empty, #dotify-settings-error:empty {
      display: none; }
    #dotify-demo-settings button { min-height: 3rem; min-width: 7rem; font-size: 1.05rem; }
  `;
  document.head.appendChild(style);

  // The main screen keeps only the braille status line and the two live
  // actions (Catch up, Summarize). Pace, grade, reading mode, pause and the
  // readouts move into the Settings > Braille category below.
  const panel = document.createElement('section');
  panel.id = 'dotify-appliance-controls';
  panel.setAttribute('aria-label', 'Braille');
  panel.innerHTML = `
    <p id="dotify-control-status" role="status" aria-live="polite">Connecting to braille display...</p>
    <details id="dotify-connection-details" hidden>
      <summary>Connection details</summary>
      <p id="dotify-connection-reason"></p>
    </details>
    <div class="dotify-row">
      <button type="button" data-command="live" data-shortcut="L">Catch up</button>
      <button type="button" data-command="summarize" data-shortcut="U">Summarize</button>
    </div>
    <p id="dotify-control-error" role="alert"></p>
    <div id="dotify-braille-controls">
      <div class="dotify-row" role="group" aria-labelledby="dotify-speed-group">
        <span id="dotify-speed-group" class="dotify-group-label">Speed</span>
        <button type="button" data-command="slower" data-shortcut="S">Slower</button>
        <button type="button" data-command="faster" data-shortcut="F">Faster</button>
      </div>
      <div class="dotify-row" role="group" aria-labelledby="dotify-reading-group">
        <span id="dotify-reading-group" class="dotify-group-label">Reading</span>
        <button type="button" data-command="grade" data-shortcut="G">Switch grade</button>
        <button type="button" data-command="advance" data-shortcut="R">Change reading mode</button>
        <button type="button" data-command="pause" data-shortcut="P">Pause braille</button>
      </div>
      <dl id="dotify-readout">
        <div><dt>Grade</dt><dd><output id="dotify-grade">--</output></dd></div>
        <div><dt>Mode</dt><dd><output id="dotify-mode">--</output></dd></div>
        <div><dt>Reading</dt><dd><output id="dotify-advance">--</output></dd></div>
        <div><dt>Reading speed</dt><dd><output id="dotify-speed">--</output></dd></div>
        <div><dt>Model</dt><dd><output id="dotify-model">--</output></dd></div>
      </dl>
    </div>
    <div id="dotify-quit-row">
      <p id="dotify-quit-hint">Quit stops speech and braille and closes this window.</p>
      <button type="button" data-command="quit" data-shortcut="Q">Quit Dotify</button>
    </div>
    <div id="dotify-braille-settings">
      <div class="dotify-row" id="dotify-window-row">
        <label for="dotify-window">Cells per refresh (auto mode)</label>
        <input id="dotify-window" type="number" min="1" max="40" step="1" inputmode="numeric">
      </div>
      <div class="dotify-row" id="dotify-space-time-row">
        <label for="dotify-space-time">Space time, percent of pace</label>
        <select id="dotify-space-time">
          <option value="100">100%</option>
          <option value="75">75%</option>
          <option value="50">50%</option>
          <option value="25">25%</option>
          <option value="10">10%</option>
          <option value="1">1%</option>
        </select>
      </div>
      <div class="dotify-row" id="dotify-punct-time-row">
        <label for="dotify-punct-time">Punctuation time, percent of pace</label>
        <select id="dotify-punct-time">
          <option value="100">100%</option>
          <option value="75">75%</option>
          <option value="50">50%</option>
          <option value="25">25%</option>
          <option value="10">10%</option>
          <option value="1">1%</option>
        </select>
      </div>
      <div class="dotify-row">
        <input id="dotify-lowercase" type="checkbox">
        <label for="dotify-lowercase">Lowercase braille, no capital signs</label>
      </div>
    </div>
    <div id="dotify-demo-settings">
      <div class="dotify-row">
        <label for="dotify-demo-source">Text to play</label>
        <select id="dotify-demo-source">
          <option value="sample">Short sample</option>
          <option value="article">News article</option>
          <option value="novel">Novel</option>
          <option value="file">Text file from this computer</option>
        </select>
        <input id="dotify-demo-file" type="file" accept=".txt,text/plain" aria-label="Choose text file" hidden>
      </div>
      <button type="button" data-command="demo" data-shortcut="D">Play text on braille display</button>
      <div class="dotify-row">
        <label for="dotify-caption-file">Caption file to play (SRT or WebVTT)</label>
        <input id="dotify-caption-file" type="file" accept=".srt,.vtt,text/vtt,application/x-subrip">
      </div>
      <button type="button" data-command="captions" data-shortcut="V">Play caption file</button>
    </div>
  `;
  // Right after the page's own status line, so both read as one block.
  const statusAnchor = document.getElementById('status')
    || document.querySelector('h1');
  (statusAnchor ? statusAnchor.parentNode : document.body)
    .insertBefore(panel, statusAnchor ? statusAnchor.nextSibling : null);

  // Settings has second-level categories: the braille controls move into
  // Braille, the text player into Help and tools (both stay in the panel on
  // a page without those hosts). Everything below is looked up BEFORE the
  // moves, because panel.querySelectorAll no longer finds moved controls.
  const commandButtons = Array.from(panel.querySelectorAll('button[data-command]'));
  const liveButton = panel.querySelector('button[data-command="live"]');
  const summarizeButton = panel.querySelector('button[data-command="summarize"]');
  const fasterButton = panel.querySelector('button[data-command="faster"]');
  const pauseBrailleButton = panel.querySelector('button[data-command="pause"]');
  const gradeOut = panel.querySelector('#dotify-grade');
  const modeOut = panel.querySelector('#dotify-mode');
  const advanceOut = panel.querySelector('#dotify-advance');
  const speedOut = panel.querySelector('#dotify-speed');
  const modelOut = panel.querySelector('#dotify-model');
  const brailleControls = panel.querySelector('#dotify-braille-controls');
  const windowGroup = panel.querySelector('#dotify-braille-settings');
  const windowInput = windowGroup.querySelector('#dotify-window');
  const windowRow = windowGroup.querySelector('#dotify-window-row');
  const spaceTimeRow = windowGroup.querySelector('#dotify-space-time-row');
  const punctTimeRow = windowGroup.querySelector('#dotify-punct-time-row');
  const spaceTimeSelect = windowGroup.querySelector('#dotify-space-time');
  const punctTimeSelect = windowGroup.querySelector('#dotify-punct-time');
  const lowercaseCheck = windowGroup.querySelector('#dotify-lowercase');
  // The percent selects offer presets; a value set on the command line
  // between them gets its own option so the select shows the truth.
  function syncPercentSelect(select, percent) {
    if (document.activeElement === select) return;
    const value = String(percent);
    if (!Array.from(select.options).some((o) => o.value === value)) {
      const custom = document.createElement('option');
      custom.value = value;
      custom.textContent = `${value}%`;
      select.appendChild(custom);
    }
    select.value = value;
  }
  const demoGroup = panel.querySelector('#dotify-demo-settings');
  const demoButton = demoGroup.querySelector('button[data-command="demo"]');
  const demoSource = demoGroup.querySelector('#dotify-demo-source');
  const demoFile = demoGroup.querySelector('#dotify-demo-file');
  // The file picker shows only for "Text file from this computer".
  demoSource.addEventListener('change', () => {
    demoFile.hidden = demoSource.value !== 'file';
  });
  // Caption replay has its own always-visible picker and button.
  const captionButton = demoGroup.querySelector('button[data-command="captions"]');
  const captionFile = demoGroup.querySelector('#dotify-caption-file');
  const brailleCategory = document.getElementById('settings-category-braille');
  const helpCategory = document.getElementById('settings-category-help');
  if (brailleCategory) {
    brailleCategory.appendChild(brailleControls);
    brailleCategory.appendChild(windowGroup);
    window.dotifySettings?.enableCategory('braille');
  }
  if (helpCategory) {
    helpCategory.appendChild(demoGroup);
    window.dotifySettings?.enableCategory('help');
  }
  // Quit lives at the end of the Settings categories screen, away from the
  // everyday controls where it invited accidental presses. Alt+Shift+Q
  // works from anywhere (the shortcut clicks the button, hidden or not).
  const quitRow = panel.querySelector('#dotify-quit-row');
  const quitButton = quitRow.querySelector('button[data-command="quit"]');
  const settingsCategories = document.getElementById('settings-categories');
  (settingsCategories || document.body).appendChild(quitRow);

  const status = panel.querySelector('#dotify-control-status');
  const connectionDetails = panel.querySelector('#dotify-connection-details');
  const connectionReason = panel.querySelector('#dotify-connection-reason');
  const error = panel.querySelector('#dotify-control-error');
  // The panel's error line sits in <main id="app-main">, which the page hides
  // while Settings is open, exactly when the moved controls can fail. An
  // alert in a hidden subtree is never announced, so errors are echoed into
  // a line on the Settings screen whenever the panel copy is hidden.
  const settingsRoot = document.getElementById('settings');
  let settingsErrorEcho = null;
  if (settingsRoot) {
    settingsErrorEcho = document.createElement('p');
    settingsErrorEcho.id = 'dotify-settings-error';
    settingsErrorEcho.setAttribute('role', 'alert');
    settingsRoot.appendChild(settingsErrorEcho);
  }
  const headers = { 'Content-Type': 'application/json', 'X-Dotify-Token': token };

  let lastMode = 'listen';
  // Polled state.demo: while any text plays, both play buttons stop it.
  let demoActive = false;

  // Relayed requests (the Space+dot-6 mic chord, the idle watchdog): the
  // ticker bumps a counter because the microphone lives in this page. Acting
  // on the increase means a fresh page load ignores presses from before it
  // existed; null means no baseline yet.
  let seenMicToggle = null;
  let seenIdlePause = null;
  let seenIdleResume = null;
  // Set when the idle watchdog paused the mic, so its resume never turns on
  // a mic the user had turned off.
  let idleWatchdogPaused = false;

  // Responses can arrive out of order (a threading server, unsequenced
  // fetches), and a stale one would flip readouts backwards and can reset
  // lastMode. Each request takes a sequence number at dispatch, and a
  // response renders only if nothing dispatched later has rendered.
  let requestSeq = 0;
  let renderedSeq = 0;

  // A healthy poll clears the error line, but not before this long: the
  // state poll can succeed a second after a command failed.
  const ERROR_DWELL_MS = 5000;
  let errorSetAt = 0;

  function showError(message) {
    // After quit the stopped notice is the last word.
    if (document.documentElement.dataset.dotifyStopped) return;
    error.textContent = message;
    errorSetAt = Date.now();
    // offsetParent is null anywhere under display:none.
    if (settingsErrorEcho && error.offsetParent === null) {
      settingsErrorEcho.textContent = message;
    }
  }

  // A refused transcript.txt record is reported once per page load (the
  // display already got the words). Network errors stay silent: these are
  // best-effort keepalive posts that also run at pagehide and quit.
  let ingestRecordWarned = false;
  function warnIngestRecordRefused(httpStatus) {
    if (ingestRecordWarned) return;
    ingestRecordWarned = true;
    showError('The braille display got your typed text, but the server '
      + `refused to record it in the transcript (HTTP ${httpStatus}).`);
  }

  // Records a typed line or a display reply in transcript.txt. keepalive
  // lets the post outlive the page, so pagehide and quit can use it.
  function postIngestRecord(text) {
    fetch('/ingest', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      keepalive: true,
      body: JSON.stringify({ typed: true, text }),
    }).then((response) => {
      if (!response.ok) warnIngestRecordRefused(response.status);
    }).catch(() => {});
  }

  // Screen readers repeat a rewritten live region (the status line, and the
  // <output> readouts, which are implicit role="status"), so every per-poll
  // write is change-guarded.
  function setText(el, text) {
    if (el.textContent !== text) el.textContent = text;
  }

  // --- Reading position ------------------------------------------------------
  // The page renders the transcript in three bands (history / current /
  // pending). This panel finds the reading boundary, the record offset where
  // the words on the display begin, by locating the engine's shown text in
  // the finalized record, and passes it and the band texts to
  // window.dotifyTranscript.setBands. Best effort: the braille side can
  // differ from the record (dropped segments, speaker labels), and an
  // unknown boundary just shows the whole record.
  const transcript = document.getElementById('finalized');

  // --- Compose box: type instead of speaking --------------------------------
  // Under the transcript bands, where a sighted partner is already looking.
  // The section has no aria-labelledby: naming it after the textarea's label
  // made screen readers announce the label twice on focus.
  const compose = document.createElement('section');
  compose.id = 'dotify-compose';
  compose.innerHTML = `
    <label id="dotify-compose-label" for="dotify-type-text">Type instead of speaking</label>
    <p id="dotify-type-status" role="status" aria-live="polite"></p>
    <div class="dotify-row">
      <textarea id="dotify-type-text" rows="2" aria-describedby="dotify-type-help"
        aria-keyshortcuts="Alt+Shift+T" title="Focus typing field (Alt+Shift+T)"
        placeholder="Type here"></textarea>
      <button type="button" id="dotify-type-done" aria-keyshortcuts="Escape"
        hidden>Back to AI mode</button>
    </div>
    <p id="dotify-type-help">Words stream to the braille display as you finish them. The microphone pauses while you type.</p>
  `;
  const composeAnchor = document.getElementById('pending-band') || transcript;
  if (composeAnchor) composeAnchor.insertAdjacentElement('afterend', compose);
  else panel.appendChild(compose);

  // --- The transcript bands ------------------------------------------------
  // The page owns the band elements; this panel supplies their text.
  // bandCurrent prefixes unusual frames the way the display's cell-2 marker
  // does: Recap (the frame text differs from its transcript source),
  // Announcement for a flash, Reply while the reader types.
  const bands = window.dotifyTranscript
    && typeof window.dotifyTranscript.setBands === 'function'
    ? window.dotifyTranscript : null;

  function bandCurrent(kind, text, source) {
    if (kind === 'flash' && text) return `Announcement: ${text}`;
    if (kind === 'reply') return 'Reply: typing on the display…';
    if (!text) return '';
    if (text !== source) return `Recap: ${text}`;
    return text;
  }
  const typeStatus = compose.querySelector('#dotify-type-status');
  // "Back to AI mode" shows only in Human mode. It follows the polled mode,
  // so a Human mode stuck with nothing focused still shows its way out.
  const typeDone = compose.querySelector('#dotify-type-done');
  const TYPE_STATUS_PAUSED = 'Human mode. Microphone paused.';
  const TYPE_STATUS_LIVE = 'AI mode. Microphone live.';
  const TYPE_STATUS_STILL_PAUSED =
    'AI mode. Transcription is still paused until you press Resume.';

  // Leaving Human mode does not undo a manual mic pause, so the status must
  // not claim a live microphone then: "live" has to mean the mic hears.
  function liveStatusText() {
    const pause = window.dotifyPause;
    return pause && pause.state && pause.state() === 'manual'
      ? TYPE_STATUS_STILL_PAUSED : TYPE_STATUS_LIVE;
  }
  let searchFrom = 0;      // reading position only moves forward (append-only)
  let lastFrameSeq = -1;   // /api/frame seq guard (band fast poll)
  let lastFrameAt = 0;     // when a frame last painted
  let anchorStart = -1;    // record offset where the shown words begin
  let anchorEnd = -1;      // ...and end

  function clearAnchor() {
    anchorStart = -1;
    anchorEnd = -1;
    if (bands) bands.setBands({ boundary: null });
  }

  // Lowercase and collapse every whitespace run to one space, remembering
  // each normalized character's index in the raw text so a match maps back
  // to a DOM Range (the box joins segments with newlines; the braille side
  // joins words with single spaces).
  function normalizeWithMap(raw) {
    let text = '';
    const map = [];
    let inSpace = true;                 // also trims leading whitespace
    for (let i = 0; i < raw.length; i++) {
      if (/\s/.test(raw[i])) {
        if (!inSpace) { text += ' '; map.push(i); inSpace = true; }
      } else {
        text += raw[i].toLowerCase();
        map.push(i);
        inSpace = false;
      }
    }
    return { text, map };
  }

  // The record is append-only apart from reply inserts and history clears
  // (both drop this cache), so each poll normalizes only the new tail. A
  // shorter record resets the cache.
  let boxCache = null;
  function normalizedRecord() {
    const raw = bands.record();
    if (!boxCache || raw.length < boxCache.rawLen) {
      boxCache = { rawLen: 0, text: '', map: [], inSpace: true };
    }
    let { text, inSpace } = boxCache;
    for (let i = boxCache.rawLen; i < raw.length; i++) {
      if (/\s/.test(raw[i])) {
        if (!inSpace) { text += ' '; boxCache.map.push(i); inSpace = true; }
      } else {
        text += raw[i].toLowerCase();
        boxCache.map.push(i);
        inSpace = false;
      }
    }
    boxCache.text = text;
    boxCache.inSpace = inSpace;
    boxCache.rawLen = raw.length;
    return boxCache;
  }

  function updateBoundary(shownRaw) {
    if (!bands) return;
    const shown = normalizeWithMap(String(shownRaw || '')).text;
    if (!shown || !bands.record()) { clearAnchor(); return; }
    const box = normalizedRecord();
    if (searchFrom > box.text.length) searchFrom = 0;  // record was cleared
    // Search forward from the last match (reading only advances), then the
    // whole record. The display can start with words the record never saw
    // (a speaker label, an outage gap), so retry with up to two leading
    // words dropped.
    let start = -1;
    let needle = shown;
    for (let drops = 0; drops < 3 && needle; drops++) {
      start = box.text.indexOf(needle, searchFrom);
      if (start < 0) start = box.text.indexOf(needle);
      if (start >= 0) break;
      const cut = needle.indexOf(' ');
      if (cut < 0) { needle = ''; break; }
      needle = needle.slice(cut + 1);
    }
    if (start < 0 || !needle) { clearAnchor(); return; }
    searchFrom = start;
    anchorStart = box.map[start];
    anchorEnd = box.map[start + needle.length - 1] + 1;
    bands.setBands({ boundary: anchorStart });
  }

  // A history cut invalidates every cached offset; the next poll re-finds
  // the shown words.
  if (transcript) {
    transcript.addEventListener('dotify-history-cleared', () => {
      boxCache = null;
      searchFrom = 0;
      anchorStart = -1;
      anchorEnd = -1;
      replyAnchor = -1;
    });
  }

  // --- Reply: the braille reader types on the display ----------------------
  // While state.reply is active, braille is frozen and the reader types on
  // the display's dot keys. This page pauses the microphone (the spoken
  // reply must not be transcribed back), prints the text as a "Reply: " line
  // at the reading boundary, where the sighted speaker is looking, and
  // speaks each finished sentence aloud. Cursors are keyed on
  // reply.session; the first poll only takes a baseline, so a reloaded page
  // never replays an old reply.
  let replySession = null;    // null = baseline not yet seen
  let replyInsertedLen = 0;   // chars of reply.text already in the record
  let replySpokenCount = 0;   // sentences already spoken
  let replyAnchor = -1;       // record offset where the NEXT insert goes
  let replyLine = '';         // for the transcript.txt record on reply end

  function insertReplyText(fresh) {
    if (!bands) return;   // no banded page: the spoken sentences still serve
    const record = bands.record();
    if (replyAnchor < 0 || replyAnchor > record.length) {
      // First insert: its own "Reply: " line at the reading boundary, or
      // at the record's end when the boundary is unknown.
      const at = (anchorStart >= 0 && anchorStart <= record.length)
        ? anchorStart : record.length;
      let block = 'Reply: ' + fresh.trimStart();
      if (at > 0 && record[at - 1] !== '\n') block = '\n' + block;
      let trailing = 0;
      if (at < record.length && record[at] !== '\n') {
        block += '\n';        // the cursor stays BEFORE this line break
        trailing = 1;
      }
      bands.insert(at, block);
      replyAnchor = at + block.length - trailing;
    } else {
      bands.insert(replyAnchor, fresh);
      replyAnchor += fresh.length;
    }
    // The insert shifts every cached offset after it.
    boxCache = null;
    searchFrom = 0;
  }

  function speakReplySentences(sentences) {
    if (replySpokenCount >= sentences.length) return;
    const fresh = sentences.slice(replySpokenCount);
    replySpokenCount = sentences.length;
    if (!('speechSynthesis' in window)) return;  // text is still on screen
    fresh.forEach((sentence) => {
      try {
        window.speechSynthesis.speak(new SpeechSynthesisUtterance(sentence));
      } catch (exc) { /* no voice available: the printed text stands */ }
    });
  }

  // Typed text never passes through the speech server, so the reply is
  // recorded in transcript.txt here, like Human-mode typing.
  function recordReplyLine() {
    if (!replyLine) return;
    const text = replyLine;
    replyLine = '';
    postIngestRecord(text);
  }

  let replyActive = false;
  // The summary seen while a recap streamed; it joins the record as a
  // "Recap: " line when the recap ends (see render).
  let lastCatchupState = null;
  let lastRecapText = '';

  function updateReply(state) {
    const reply = state.reply;
    if (!reply || typeof reply.session !== 'number') {
      replyActive = false;
      return;
    }
    if (replySession === null) {
      // Baseline: adopt whatever already happened without replaying it.
      replySession = reply.session;
      replyInsertedLen = String(reply.text || '').length;
      replySpokenCount = (reply.sentences || []).length;
      replyActive = !!reply.active;
      return;
    }
    if (reply.session !== replySession) {
      recordReplyLine();   // a new session before the old one ended
      replySession = reply.session;
      replyInsertedLen = 0;
      replySpokenCount = 0;
      replyAnchor = -1;
    }
    const text = String(reply.text || '');
    if (text.length > replyInsertedLen) {
      insertReplyText(text.slice(replyInsertedLen));
      replyInsertedLen = text.length;
      replyLine = text;
    }
    speakReplySentences(reply.sentences || []);
    const wasActive = replyActive;
    replyActive = !!reply.active;
    if (wasActive && !replyActive) recordReplyLine();
  }

  // The engine cycles grade 1 -> 2 -> 3 (liblouis' experimental English
  // grade 3); every grade it can report needs its own readout.
  const GRADE_LABELS = {
    1: '1 (uncontracted)',
    2: '2 (contracted)',
    3: '3 (experimental)',
  };
  function gradeLabel(grade) {
    if (grade === undefined || grade === null) return '--';
    return GRADE_LABELS[grade] || String(grade);
  }

  function render(state) {
    // After quit nothing repaints: a late poll response would overwrite the
    // focused stopped notice.
    if (document.documentElement.dataset.dotifyStopped) return;
    // A streaming recap lives in the current band; when it ends it joins the
    // record, so the speaker can still read what the reader was told.
    if (state.catchup === 'streaming') {
      if (state.summary) lastRecapText = state.summary;
    } else if (lastCatchupState === 'streaming' && lastRecapText && bands) {
      bands.append('Recap: ' + lastRecapText);
      lastRecapText = '';
    }
    lastCatchupState = state.catchup;
    // While a recap plays the reader is not reading live speech, and while a
    // reply is typed braille and the mic are stopped; the status says so.
    const replyNow = !!(state.reply && state.reply.active);
    let catchupNote = '';
    if (replyNow) {
      // Names the one action that ends it: Resume does nothing meanwhile.
      catchupNote = ' The braille reader is typing a reply; each finished '
        + 'sentence is spoken aloud. The microphone and braille stay '
        + 'paused until the reader ends the reply with Space+R.';
    } else if (state.catchup === 'fetching') {
      catchupNote = ' Summarizing what the braille reader missed.';
    } else if (state.catchup === 'streaming') {
      // Only the opening: a summary can run to 700 characters, and the
      // screen reader would speak all of it on every status change.
      const gist = state.summary && state.summary.length > 120
        ? `${state.summary.slice(0, 120)}…` : state.summary;
      catchupNote = ` Braille is streaming a summary${gist ? `: “${gist}”` : ''}.`
        + ' Press Go live now to skip.';
    }
    // While the display is unavailable this live status is the alert channel
    // that still works, and the reason carries its own fix ("set NVDA's
    // braille display to 'no braille'"). A short reason is spoken whole; a
    // long one only up to its first sentence (the action), with the full
    // text in Connection details. The details never hide while focused.
    let statusNote;
    if (state.display_wait) {
      const reason = String(state.display_wait);
      const firstSentence = (reason.match(/^[^.]*\./) || [reason])[0];
      const spoken = reason.length <= 220 ? reason : firstSentence;
      statusNote = `Waiting for the braille display. ${spoken}`;
      setText(connectionReason, reason);
      connectionDetails.hidden = false;
    } else {
      // Panned back into history: the display holds while speech queues.
      const panNote = state.pan_offset > 0
        ? ` Viewing history (${state.pan_offset} cells behind live) —`
          + ' braille holds while speech queues; thumb Next returns to live.'
        : '';
      statusNote = `Connected to ${state.display || 'braille display'}.` +
        (state.paused ? ' Braille is paused; speech keeps queueing.' : '') +
        panNote +
        catchupNote;
      if (!connectionDetails.contains(document.activeElement)) {
        connectionDetails.hidden = true;
      }
    }
    setText(status, statusNote);
    // Labels say what a press does now: during a recap Catch up becomes "Go
    // live now", and Summarize becomes "Cancel summary" or "Skip recap".
    const catchingUp =
      state.catchup === 'streaming' || state.catchup === 'fetching';
    setText(liveButton, catchingUp ? 'Go live now' : 'Catch up');
    liveButton.title = `${liveButton.textContent} (Alt+Shift+L)`;
    setText(summarizeButton,
      state.catchup === 'fetching' ? 'Cancel summary'
        : state.catchup === 'streaming' ? 'Skip recap' : 'Summarize');
    summarizeButton.title = `${summarizeButton.textContent} (Alt+Shift+U)`;
    setText(gradeOut, gradeLabel(state.grade));
    // 'listen' is shown as AI (transcription), 'type' as HUMAN (typing).
    const modeLabels = { listen: 'AI', type: 'HUMAN' };
    setText(modeOut,
      modeLabels[String(state.mode || '').toLowerCase()]
        || String(state.mode || '--').toUpperCase());
    // The wire value ('ticker' is auto). It only drives which controls and
    // labels show; the displayed word is state.advance_label.
    const advance = String(state.advance || 'ticker').toLowerCase();
    // The full-display window flips pages; smaller windows stream cells.
    const fullWindow = 'window_is_full' in state
      ? !!state.window_is_full
      : state.max_window && Number(state.window) >= Number(state.max_window);
    // The mode's word comes from the engine (advance_label); only the
    // paged/streaming note is composed here.
    const advanceWord = state.advance_label ? String(state.advance_label) : '';
    const advanceShown = advanceWord
      ? advanceWord.charAt(0).toUpperCase() + advanceWord.slice(1)
      : '--';
    setText(advanceOut,
      advanceWord && advance === 'ticker'
        ? `${advanceShown} (${fullWindow ? 'full-display pages' : 'streaming cells'})`
        : advanceShown);
    // Manual mode has no pace (and Faster becomes Advance). Auto shows the
    // reader's estimated words per minute, "about" because contraction
    // density varies.
    if (advance === 'manual') {
      setText(speedOut, advanceWord || '--');
    } else if (state.wpm) {
      setText(speedOut, `about ${state.wpm} words per minute`);
    } else {
      setText(speedOut, `${state.pace_ms} ms per cell`);
    }
    setText(fasterButton, advance === 'manual' ? 'Advance' : 'Faster');
    fasterButton.title = `${fasterButton.textContent} (Alt+Shift+F)`;
    setText(pauseBrailleButton, state.paused ? 'Resume braille' : 'Pause braille');
    pauseBrailleButton.title = `${pauseBrailleButton.textContent} (Alt+Shift+P)`;
    // Both play buttons follow the one text stream, chord-started ones
    // included; either button stops whatever is playing.
    demoActive = !!state.demo;
    setText(demoButton, demoActive
      ? 'Stop playing text' : 'Play text on braille display');
    demoButton.title = `${demoButton.textContent} (Alt+Shift+D)`;
    setText(captionButton, demoActive
      ? 'Stop playing text' : 'Play caption file');
    captionButton.title = `${captionButton.textContent} (Alt+Shift+V)`;
    // Lowercase applies in both modes, so its row never hides.
    syncPercentSelect(spaceTimeSelect, state.space_time || 100);
    syncPercentSelect(punctTimeSelect, state.punct_time || 100);
    if (document.activeElement !== lowercaseCheck) {
      lowercaseCheck.checked = !!state.lowercase;
    }
    if (advance === 'ticker') {
      windowRow.hidden = false;
      spaceTimeRow.hidden = false;
      punctTimeRow.hidden = false;
      windowInput.disabled = false;   // re-enable after a manual stint
      windowInput.max = state.max_window || 40;
      // Don't overwrite a number being typed.
      if (document.activeElement !== windowInput) windowInput.value = state.window;
    } else {
      // Manual has no cell window or pace, so those rows hide, but never
      // while focus is in the section: hiding a focused element drops
      // screen-reader focus to <body> unannounced. A later poll hides them.
      if (!windowGroup.contains(document.activeElement)) {
        windowRow.hidden = true;
        spaceTimeRow.hidden = true;
        punctTimeRow.hidden = true;
        windowInput.disabled = true;
      }
    }
    // The page's engine select follows the live engine (including the
    // connection-loss fallback), so the readout paints from it.
    const engineSelect = document.getElementById('engine');
    if (engineSelect && engineSelect.value) {
      setText(modelOut,
        ENGINE_READOUT_NAMES[engineSelect.value] || engineSelect.value);
    }
    lastMode = String(state.mode || lastMode).toLowerCase();
    // The mic status beside the compose box follows the polled mode, so a
    // failed mode command can never leave it claiming the mic is off when
    // it is not. It stays silent until the field is first really used.
    if (typeStatusArmed) {
      setText(typeStatus,
        lastMode === 'type' ? TYPE_STATUS_PAUSED : liveStatusText());
    }
    // Not gated on arming: after a reload into Human mode the way out shows.
    typeDone.hidden = lastMode !== 'type';
    // In Human mode the engine discards speech, so transcribing it only
    // spends provider quota: pause transcription, and resume on the return
    // to listening. A reply pauses it too (its spoken voice must not be
    // transcribed). Applied every render; resume({auto}) never overrides a
    // manual pause.
    if (window.dotifyPause) {
      if (lastMode === 'type' || replyNow) {
        window.dotifyPause.pause({ auto: true });
      } else {
        window.dotifyPause.resume({ auto: true });
      }
    }
    if (seenMicToggle === null) {
      seenMicToggle = state.mic_toggle || 0;
    } else if ((state.mic_toggle || 0) > seenMicToggle) {
      seenMicToggle = state.mic_toggle;
      toggleMic();
    }
    // The idle watchdog: no human input for a while pauses the mic; the
    // next input brings it back.
    if (seenIdlePause === null) {
      seenIdlePause = state.idle_pause || 0;
    } else if ((state.idle_pause || 0) > seenIdlePause) {
      seenIdlePause = state.idle_pause;
      idlePauseMic();
    }
    if (seenIdleResume === null) {
      seenIdleResume = state.idle_resume || 0;
    } else if ((state.idle_resume || 0) > seenIdleResume) {
      seenIdleResume = state.idle_resume;
      idleResumeMic();
    }
    updateBoundary(state.shown);
    // The pending band can grow while no frames are written, so this poll
    // keeps it current. The current band belongs to the frame poll; this
    // copy stands in only when frames have been quiet for 3 s.
    if (bands) {
      bands.setBands({ pending: state.pending_text || '' });
      if (Date.now() - lastFrameAt > 3000) {
        bands.setBands({
          current: bandCurrent(
            'content', state.shown_display || '', state.shown || ''),
        });
      }
    }
    // After updateBoundary, so a reply anchors on the fresh position.
    updateReply(state);
    if (Date.now() - errorSetAt >= ERROR_DWELL_MS) {
      error.textContent = '';
      if (settingsErrorEcho) settingsErrorEcho.textContent = '';
    }
  }

  function renderIfFresh(seq, state) {
    if (seq < renderedSeq) return false;  // something dispatched later already painted
    renderedSeq = seq;
    render(state);
    return true;
  }

  // A request hung behind the engine lock (a display outage) must reject, or
  // the poll's in-flight guard would stick and the outage notice never show.
  function fetchSignal(ms) {
    return (typeof AbortSignal !== 'undefined' && AbortSignal.timeout)
      ? AbortSignal.timeout(ms) : undefined;
  }

  async function state() {
    const seq = ++requestSeq;
    const response = await fetch(`${base}/api/state`,
      { headers, cache: 'no-store', signal: fetchSignal(8000) });
    if (!response.ok) throw new Error(`control service returned ${response.status}`);
    renderIfFresh(seq, await response.json());
  }

  async function command(name, value) {
    const seq = ++requestSeq;
    try {
      const response = await fetch(`${base}/api/command`, {
        method: 'POST', headers, signal: fetchSignal(10000),
        body: JSON.stringify({ command: name, value })
      });
      const body = await response.json();
      if (response.status === 400) {
        // The bridge refused this command or value (say, a cell-window edit
        // that raced a switch to manual mode). The service is fine, so name
        // the refusal instead of reporting an outage.
        showError(`Not applied: ${body.error || 'the braille engine refused it'}.`);
        return false;
      }
      if (!response.ok) throw new Error(body.error || `command failed (${response.status})`);
      if (!renderIfFresh(seq, body)) {
        // A later poll painted an older snapshot first; re-sync now.
        state().catch(() => {});
      }
      return true;
    } catch (exc) {
      showError(`Braille control unavailable: ${exc.message}`);
      return false;
    }
  }

  // Flash the outcome of a relayed display-key command, so the reader feels
  // it. Keep it to a few lowercase words: it must fit the display.
  function announce(text) { command('announce', text); }
  // For notices nobody asked for; they dwell longer.
  function alertFlash(text) { command('alert', text); }

  // The page reports the capturing microphone here; flash is set when a
  // device change swapped the live capture.
  window.dotifyMicSource = (label, flash) => {
    command('mic', label);
    if (flash && label) announce(label);
  };

  // Short names for model-switch flashes on the display.
  const ENGINE_FLASH_NAMES = {
    offline: 'nemotron',
    openai: 'openai',
    assemblyai: 'assembly',
    deepgram: 'deepgram',
    elevenlabs: 'eleven',
  };

  // The Model readout; the select's labels carry versions and key hints.
  const ENGINE_READOUT_NAMES = {
    offline: 'Nemotron',
    openai: 'OpenAI',
    assemblyai: 'AssemblyAI',
    deepgram: 'Deepgram',
    elevenlabs: 'ElevenLabs',
  };

  // Hooks for the speech overlay: the display is the one alert channel a
  // deaf-blind reader always has, and this panel owns the bridge.
  window.dotifyAnnounce = announce;
  window.dotifyEngineFlashName = (id) => ENGINE_FLASH_NAMES[id] || id;

  // The idle pause clicks the pause button (a manual pause), not
  // pause({auto}): render() resumes auto pauses in listen mode, which is
  // exactly when the watchdog fires.
  function idlePauseMic() {
    const pauseToggle = document.getElementById('pause-toggle');
    if (!pauseToggle || pauseToggle.disabled) return;   // nothing to pause
    if (window.dotifyPause && window.dotifyPause.state() !== 'off') return;
    pauseToggle.click();
    idleWatchdogPaused = true;
    alertFlash('idle - mic off, any key resumes');
  }

  function idleResumeMic() {
    if (!idleWatchdogPaused) return;  // we never paused (or user beat us)
    idleWatchdogPaused = false;
    const pauseToggle = document.getElementById('pause-toggle');
    if (!pauseToggle || pauseToggle.disabled) return;
    // The user may have resumed by hand between the relays; a second
    // click would pause again.
    if (window.dotifyPause && window.dotifyPause.state() === 'off') return;
    pauseToggle.click();
    const on = window.dotifyPause && window.dotifyPause.state() === 'off';
    announce(on ? 'mic on' : 'mic still off');
  }

  function toggleMic() {
    const pauseToggle = document.getElementById('pause-toggle');
    if (!pauseToggle || pauseToggle.disabled) {
      // Not transcribing (never started, or stopped): there is no mic
      // session to pause and none to resume into.
      announce('mic already off');
      return;
    }
    pauseToggle.click();
    // The page's pause handlers run synchronously in click(), so the state
    // read here is the settled outcome, not a race.
    const paused = window.dotifyPause && window.dotifyPause.state() !== 'off';
    announce(paused ? 'mic off' : 'mic on');
  }

  // Leave Settings for the main screen synchronously. The page unhides
  // #app-main from the details toggle event, which fires asynchronously, too
  // late for a focus() or window.close() right after.
  function revealMainScreen() {
    const settingsHost = document.getElementById('settings');
    if (settingsHost && settingsHost.open) settingsHost.open = false;
    const mainHost = document.getElementById('app-main');
    if (mainHost) mainHost.hidden = false;
  }

  // After quit: stop the mic, disable every control, and close the window.
  // The launcher opens an app-mode window, where window.close() is allowed;
  // the stopped notice is set first for browsers that refuse to close.
  function markStopped() {
    document.documentElement.dataset.dotifyStopped = '1';
    clearAnchor();
    if (bands) bands.setBands({ current: '', pending: '' });
    clearInterval(pollTimer);
    clearInterval(framePollTimer);
    if (flushTimer) clearTimeout(flushTimer);
    flushTimer = null;
    if (typeDwellTimer) clearTimeout(typeDwellTimer);
    typeDwellTimer = null;
    // Record a typed line now: Alt+Shift+Q quits without a blur, the usual
    // recorder. Likewise a reply, and stop any speech in progress.
    closeTypedLine();
    recordReplyLine();
    if ('speechSynthesis' in window) {
      try { window.speechSynthesis.cancel(); } catch (exc) { /* best effort */ }
    }
    const speechToggle = document.getElementById('toggle');
    if (speechToggle && speechToggle.textContent.trim().startsWith('Stop')) {
      speechToggle.click();
    }
    const pauseToggle = document.getElementById('pause-toggle');
    if (pauseToggle) pauseToggle.disabled = true;
    panel.querySelectorAll('button, textarea, input').forEach((el) => { el.disabled = true; });
    // The compose box and the moved controls live outside the panel.
    compose.querySelectorAll('button, textarea').forEach((el) => { el.disabled = true; });
    commandButtons.forEach((el) => { el.disabled = true; });
    demoButton.disabled = true;
    demoSource.disabled = true;
    demoFile.disabled = true;
    captionButton.disabled = true;
    captionFile.disabled = true;
    windowInput.disabled = true;
    spaceTimeSelect.disabled = true;
    punctTimeSelect.disabled = true;
    lowercaseCheck.disabled = true;
    quitButton.disabled = true;
    connectionDetails.hidden = true;
    error.textContent = '';
    if (settingsErrorEcho) settingsErrorEcho.textContent = '';
    status.textContent = 'Dotify is stopped: speech and braille are off, and '
      + 'nothing on this page streams anymore. To start again, close this '
      + 'window and double-click Launch Dotify.';
    // Quit may have been pressed in Settings, which hides the notice.
    revealMainScreen();
    status.setAttribute('tabindex', '-1');
    status.focus();
    window.close();
  }

  // The play button sends no value for the built-in sample, the fetched
  // text for the article or novel, and the file's text for an upload.
  // Stopping needs no value.
  const DEMO_TEXT_URLS = {
    article: '/texts/news-article.txt',
    novel: '/texts/novel.txt',
  };
  const DEMO_FILE_LIMIT_BYTES = 1024 * 1024;

  // The chosen file's text without a BOM, or null after showing why not.
  // The size limit matches the bridge's 1 MB cap.
  async function readChosenTextFile(input, noun) {
    const file = input.files && input.files[0];
    if (!file) {
      showError(`Choose a ${noun} first.`);
      input.focus();
      return null;
    }
    if (file.size > DEMO_FILE_LIMIT_BYTES) {
      showError(`That ${noun} is too large. The limit is 1 MB.`);
      return null;
    }
    let text;
    try {
      text = await file.text();
    } catch (exc) {
      showError(`Could not read that ${noun}: ${exc.message}`);
      return null;
    }
    if (text.charCodeAt(0) === 0xFEFF) text = text.slice(1);   // strip a BOM
    if (!text.trim()) { showError(`That ${noun} has no text in it.`); return null; }
    return text;
  }

  async function playText() {
    if (demoActive) { command('demo'); return; }
    const source = demoSource.value;
    if (source === 'sample') { command('demo'); return; }
    let text;
    if (source === 'file') {
      text = await readChosenTextFile(demoFile, 'file');
      if (text === null) return;
    } else {
      const label = source === 'novel' ? 'novel' : 'news article';
      try {
        const response = await fetch(DEMO_TEXT_URLS[source], { cache: 'no-store' });
        if (!response.ok) throw new Error(`server returned ${response.status}`);
        text = await response.text();
      } catch (exc) {
        showError(`Could not load the ${label}: ${exc.message}`);
        return;
      }
      if (text.charCodeAt(0) === 0xFEFF) text = text.slice(1);   // strip a BOM
      if (!text.trim()) { showError('That file has no text in it.'); return; }
    }
    command('demo', text);
  }

  // Sends the SRT/WebVTT content as {captions}, which the bridge replays at
  // the file's own timing. While anything plays, a press stops it.
  async function playCaptions() {
    if (demoActive) { command('demo'); return; }
    const text = await readChosenTextFile(captionFile, 'caption file');
    if (text === null) return;
    command('demo', { captions: text });
  }

  commandButtons.forEach((button) => {
    if (button.dataset.command === 'demo') {
      button.addEventListener('click', playText);
    } else if (button.dataset.command === 'captions') {
      // Not a bridge command: the handler sends "demo" itself.
      button.addEventListener('click', playCaptions);
    } else {
      button.addEventListener('click', async () => {
        const ok = await command(button.dataset.command);
        // Only a delivered quit: otherwise the window would close over a
        // ticker that is still running.
        if (button.dataset.command === 'quit' && ok) markStopped();
      });
    }
    const shortcut = `Alt+Shift+${button.dataset.shortcut}`;
    button.setAttribute('aria-keyshortcuts', shortcut);
    button.title = `${button.textContent} (${shortcut})`;
  });

  windowInput.addEventListener('change', () => {
    const requested = Math.round(Number(windowInput.value));
    if (!Number.isFinite(requested) || requested < 1) return;
    const limit = Number(windowInput.max) || 40;
    command('window', Math.min(requested, limit));
  });

  spaceTimeSelect.addEventListener('change', () => {
    command('space_time', Number(spaceTimeSelect.value));
  });
  punctTimeSelect.addEventListener('change', () => {
    command('punct_time', Number(punctTimeSelect.value));
  });
  lowercaseCheck.addEventListener('change', () => {
    command('lowercase', lowercaseCheck.checked);
  });

  // Typed text is diffed against the previous value (typing, paste and
  // selection replacement all work) and sent through one queue, so order
  // holds under fast typing. Deletions send nothing: braille already shown
  // cannot be recalled.
  const input = compose.querySelector('#dotify-type-text');
  let previousText = input.value;
  let composing = false;
  let commandQueue = Promise.resolve();

  // --- Typed-word echo -------------------------------------------------------
  // Typed words bypass the speech server, so the panel echoes them into the
  // transcript and records the line in transcript.txt when Human mode ends.
  // A word is echoed only after its type_text command was delivered, and
  // the echo splits words exactly where the engine does.
  let echoWordBuffer = '';   // partial word the engine is still holding
  let typedLine = '';        // this Human-mode stint's words (one box line)
  let echoAnchor = -1;       // box text length after OUR last write
  let typeStatusArmed = false;  // silent until the field is genuinely used

  function echoWords(words) {
    if (!words.length) return;
    const chunk = words.join(' ');
    const box = window.dotifyTranscript;
    if (box) {
      // If speech landed in the record meanwhile, our line is no longer the
      // tail: record it and start a new one. On screen the line is prefixed
      // "Human: "; transcript.txt keeps it bare.
      const recordLen = box.record ? box.record().length : -1;
      if (typedLine && recordLen !== echoAnchor) {
        recordTypedLine();
      }
      if (typedLine) box.extend(' ' + chunk);
      else box.append('Human: ' + chunk);
      echoAnchor = box.record ? box.record().length : -1;
    }
    typedLine = typedLine ? `${typedLine} ${chunk}` : chunk;
  }

  function echoInserted(inserted) {
    echoWordBuffer += inserted;
    const words = echoWordBuffer.split(/\s+/);
    echoWordBuffer = words.pop(); // trailing partial word ('' after a space)
    echoWords(words.filter(Boolean));
  }

  function echoFlush() {
    const word = echoWordBuffer.trim();
    echoWordBuffer = '';
    if (word) echoWords([word]);
  }

  function recordTypedLine() {
    if (!typedLine) return;
    const text = typedLine;
    typedLine = '';
    postIngestRecord(text);
  }

  // Leaving Human mode commits the engine's partial word, so echo it too.
  function closeTypedLine() {
    echoFlush();
    recordTypedLine();
  }

  // The engine holds a word until a space so contractions see all of it.
  // After a typing pause, flush it; long enough not to split a slow word.
  const IDLE_FLUSH_MS = 3000;
  let flushTimer = null;

  function insertedText(before, after) {
    let prefix = 0;
    const maxPrefix = Math.min(before.length, after.length);
    while (prefix < maxPrefix && before[prefix] === after[prefix]) prefix++;

    let suffix = 0;
    const maxSuffix = Math.min(before.length - prefix, after.length - prefix);
    while (suffix < maxSuffix &&
      before[before.length - 1 - suffix] === after[after.length - 1 - suffix]) suffix++;
    return after.slice(prefix, after.length - suffix);
  }

  function streamInsertedText() {
    const currentText = input.value;
    // All whitespace becomes a plain space: the bridge drops non-ASCII
    // whitespace (a pasted NBSP), which would fuse words on the display
    // while the echo split them.
    const inserted = insertedText(previousText, currentText).replace(/\s+/g, ' ');
    previousText = currentText;
    if (!inserted) return;
    commandQueue = commandQueue.then(() => command('type_text', inserted))
      .then((ok) => { if (ok) echoInserted(inserted); });
    if (flushTimer) clearTimeout(flushTimer);
    flushTimer = null;
    if (!/\s$/.test(currentText)) {
      flushTimer = setTimeout(() => {
        flushTimer = null;
        commandQueue = commandQueue.then(() => command('flush'))
          .then((ok) => { if (ok) echoFlush(); });
      }, IDLE_FLUSH_MS);
    }
  }

  // Focusing the field enters Human mode, after a short dwell so that
  // tabbing or a screen reader passing through doesn't pause the mic (typing
  // enters it at once). Mode changes queue behind pending keystrokes and
  // name the mode explicitly, so a race can't flip it the wrong way.
  const FOCUS_DWELL_MS = 250;
  let typeDwellTimer = null;

  function armTypeMode() {
    typeStatusArmed = true;
    setText(typeStatus, TYPE_STATUS_PAUSED);  // optimistic; render() corrects
    typeDone.hidden = false;
    commandQueue = commandQueue.then(() => {
      if (lastMode !== 'type') return command('mode', 'type');
    });
  }

  input.addEventListener('focus', () => {
    typeDwellTimer = setTimeout(() => {
      typeDwellTimer = null;
      armTypeMode();
    }, FOCUS_DWELL_MS);
  });

  input.addEventListener('blur', () => {
    if (typeDwellTimer) {
      // A pass-through focus: nothing was sent.
      clearTimeout(typeDwellTimer);
      typeDwellTimer = null;
      return;
    }
    // An IME composition commits on blur, but some Chromium versions fire
    // compositionend after this handler; send the text now, ahead of the
    // mode change.
    composing = false;
    streamInsertedText();
    if (flushTimer) clearTimeout(flushTimer);
    flushTimer = null;
    if (typeStatusArmed) setText(typeStatus, liveStatusText());  // optimistic
    typeDone.hidden = true;
    commandQueue = commandQueue.then(() => {
      if (lastMode === 'type') return command('mode', 'listen');
    }).then((ok) => {
      // false: the engine is still in Human mode and holds the partial
      // word, so keep the line open (pagehide records it at the latest).
      if (ok !== false) closeTypedLine();
    });
  });

  // Also recovers a Human mode left behind by a failed mode command, hence
  // its own return to listening. Focus moves to the transcript.
  typeDone.addEventListener('click', () => {
    if (transcript) transcript.focus();
    typeDone.hidden = true;
    commandQueue = commandQueue.then(() => {
      if (lastMode === 'type') return command('mode', 'listen');
    }).then((ok) => { if (ok !== false) closeTypedLine(); });
  });

  input.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    // Escape during an IME composition belongs to the IME.
    if (event.isComposing || composing) return;
    event.preventDefault();
    if (transcript) transcript.focus();
    else input.blur();
  });

  input.addEventListener('compositionstart', () => { composing = true; });
  input.addEventListener('compositionend', () => {
    composing = false;
    streamInsertedText();
  });
  input.addEventListener('input', () => {
    if (typeDwellTimer) {
      clearTimeout(typeDwellTimer);
      typeDwellTimer = null;
      armTypeMode();
    }
    if (!composing) streamInsertedText();
  });

  // Closing the window quits Dotify. The beacon tells the bridge, which
  // waits out a grace period in case this is a reload. sendBeacon survives
  // teardown and sends no custom headers, so the token is the body. Not
  // sent after a quit, or when the page is parked in the bfcache.
  window.addEventListener('pagehide', (event) => {
    closeTypedLine();
    recordReplyLine();
    if (!document.documentElement.dataset.dotifyStopped && !event.persisted
        && navigator.sendBeacon) {
      navigator.sendBeacon(`${base}/api/page-hidden`, token);
    }
  });

  const shortcutDetails = document.getElementById('dotify-keyboard-shortcuts');
  if (shortcutDetails) {
    const list = shortcutDetails.querySelector('ul');
    [
      ['S', 'Slower braille'],
      ['F', 'Faster braille'],
      ['G', 'Switch braille grade'],
      ['R', 'Change reading mode'],
      ['L', 'Catch up (snap to live)'],
      ['U', 'Summarize the backlog, press again to cancel or skip'],
      ['P', 'Pause or resume braille'],
      ['D', 'Play or stop text on the braille display'],
      ['V', 'Play or stop a caption file on the braille display'],
      ['T', 'Focus the live typing field'],
      ['Q', 'Quit Dotify'],
    ].forEach(([key, label]) => {
      const item = document.createElement('li');
      item.textContent = `Alt+Shift+${key}: ${label}`;
      list.appendChild(item);
    });
    const esc = document.createElement('li');
    esc.textContent = 'Escape (in the typing field): back to AI mode';
    list.appendChild(esc);
  }

  // Capture phase, so a focused textarea or browser default can't take the
  // shortcuts. Key repeats are ignored.
  document.addEventListener('keydown', (event) => {
    if (!event.altKey || !event.shiftKey || event.ctrlKey || event.metaKey || event.repeat) return;
    const key = event.key.toUpperCase();
    if (key === 'T') {
      event.preventDefault();
      event.stopPropagation();
      // The field is hidden while Settings is open.
      revealMainScreen();
      input.focus();
      return;
    }
    const button = commandButtons.find((b) => b.dataset.shortcut === key);
    if (!button) return;
    event.preventDefault();
    event.stopPropagation();
    button.click();
  }, true);
  state().catch((exc) => { showError(`Braille control unavailable: ${exc.message}`); });
  // The 1 s state poll. The in-flight guard keeps a stalled bridge from
  // filling the browser's per-origin connection pool, which commands share;
  // three straight failures replace a stale "Connected" with a notice.
  let pollInFlight = false;
  let pollFailures = 0;
  const pollTimer = setInterval(() => {
    if (pollInFlight) return;
    pollInFlight = true;
    state().then(() => { pollFailures = 0; }).catch(() => {
      pollFailures += 1;
      // Timeouts land here too, and can be a reconnect holding the lock.
      if (pollFailures >= 3 && !document.documentElement.dataset.dotifyStopped) {
        setText(status, 'Braille controls are not responding (the display '
          + 'may be reconnecting). If this persists, close this window and '
          + 'double-click Launch Dotify.');
      }
    }).finally(() => { pollInFlight = false; });
  }, 1000);
  // The 250 ms frame poll keeps the current band and reading position in
  // step with the display. An unchanged frame (same seq) paints nothing;
  // failures stay silent, since the state poll reports outages.
  let framePollInFlight = false;
  const framePollTimer = setInterval(() => {
    if (framePollInFlight) return;
    framePollInFlight = true;
    fetch(`${base}/api/frame`,
      { headers, cache: 'no-store', signal: fetchSignal(4000) })
      .then((response) => (response.ok ? response.json() : null))
      .then((frame) => {
        if (document.documentElement.dataset.dotifyStopped) return;
        if (!frame || !frame.seq || frame.seq === lastFrameSeq) return;
        lastFrameSeq = frame.seq;
        lastFrameAt = Date.now();
        if (bands) {
          bands.setBands({
            current: bandCurrent(
              frame.kind, frame.text || '', frame.source || ''),
            pending: frame.pending || '',
          });
        }
        if (frame.kind === 'content') {
          updateBoundary(frame.source || '');
        }
      })
      .catch(() => {})
      .finally(() => { framePollInFlight = false; });
  }, 250);
})();
