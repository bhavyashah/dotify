// The Windows speech overlay, injected into the core speech page by the
// installer build. It adds the offline Nemotron engine (audio to the local
// decode service on 8791, text to the page's /ingest), the offline-model
// download controls, keyboard shortcuts, automatic start at boot, and the
// fallback to offline captions when a keyed engine loses its connection.
(() => {
  'use strict';

  // Guard against a second injection (two mic captures, every utterance
  // posted twice).
  if (window.__dotifyWindowsSpeechOverlay) return;
  window.__dotifyWindowsSpeechOverlay = true;

  // Capture-phase handlers take over the toggle for the offline engine and
  // leave the page's keyed /audio engines alone.
  const engine = document.getElementById('engine');
  const toggle = document.getElementById('toggle');
  const status = document.getElementById('status');
  const meter = document.getElementById('miclevel');
  const partial = document.getElementById('partial');
  const finalized = document.getElementById('finalized');
  if (!engine || !toggle || !status || !meter || !partial || !finalized) return;
  // Always listed, even before the model is downloaded: picking it then
  // says how to download it.
  const offlineOption = document.createElement('option');
  offlineOption.value = 'offline';
  offlineOption.textContent = 'Nemotron (offline, not downloaded)';
  engine.appendChild(offlineOption);
  const OVERLAY_ENGINES = ['offline'];

  const pauseToggle = document.getElementById('pause-toggle');
  // The overlay's Alt+Shift shortcuts: key, label, element, action. The
  // keydown dispatch is built from this list, so only listed keys work.
  // (The panel owns the other Alt+Shift letters.)
  const shortcutDefinitions = [
    ['X', 'Focus transcript', finalized, () => finalized.focus()],
  ];
  if (pauseToggle) {
    shortcutDefinitions.push(['M', 'Pause or resume the microphone', pauseToggle,
      () => { if (!pauseToggle.disabled) pauseToggle.click(); }]);
  }
  // Session recording is a developer tool (the page shows it with ?dev=1).
  // Nemotron sessions open no /audio socket, so recording saves nothing;
  // warn instead of failing silently.
  const recordCheckbox = document.getElementById('record-session');
  if (recordCheckbox) {
    const OFFLINE_CANNOT_RECORD = 'Recording is on, but Nemotron cannot record. '
      + 'Switch to a model that uses an API key or nothing will be saved.';
    const warnIfRecordingOffline = () => {
      if (recordCheckbox.checked && OVERLAY_ENGINES.includes(engine.value)) {
        setStatus(OFFLINE_CANNOT_RECORD);
      }
    };
    recordCheckbox.addEventListener('change', warnIfRecordingOffline);
    engine.addEventListener('change', () => {
      // After the engine switch settles, or its start messages would
      // overwrite the warning at once.
      setTimeout(() => {
        engineSwitch = engineSwitch.then(() => warnIfRecordingOffline());
      }, 0);
    });
  }
  const shortcutActions = {};
  shortcutDefinitions.forEach(([key, label, element, action]) => {
    const shortcut = `Alt+Shift+${key}`;
    element.setAttribute('aria-keyshortcuts', shortcut);
    element.title = `${label} (${shortcut})`;
    shortcutActions[key] = action;
  });

  const shortcutDetails = document.createElement('details');
  shortcutDetails.id = 'dotify-keyboard-shortcuts';
  shortcutDetails.innerHTML = `
    <summary>Keyboard shortcuts</summary>
    <p>Shortcuts work from anywhere on this page.</p>
    <ul>
      <li>Alt+Shift+M: Pause or resume the microphone</li>
      <li>Alt+Shift+X: Focus transcript</li>
    </ul>
  `;
  // The shortcut list goes in Settings > Help and tools.
  const helpCategory = document.getElementById('settings-category-help');
  if (helpCategory) {
    helpCategory.appendChild(shortcutDetails);
    window.dotifySettings?.enableCategory('help');
  }
  else toggle.insertAdjacentElement('afterend', shortcutDetails);

  // --- Offline model --------------------------------------------------------
  // Status, resumable Download/Cancel and Delete for the ~650 MB model. The
  // speech server does the download; this drives /api/offline-model.
  const offlineDetails = document.createElement('details');
  offlineDetails.id = 'dotify-offline-model';
  offlineDetails.innerHTML = `
    <summary>Offline model</summary>
    <p>Nemotron transcribes on this computer. No key, no internet, nothing
    leaves the machine. It is also where captions land automatically if the
    connection drops.</p>
    <p id="dotify-offline-model-status" aria-live="polite">Offline model: checking&hellip;</p>
    <button type="button" id="dotify-offline-model-download">Download (about 650 MB)</button>
    <button type="button" id="dotify-offline-model-delete" hidden>Delete the downloaded model</button>
  `;
  // Beside the model picker it feeds.
  const offlineAnchor = document.getElementById('engine-help');
  if (offlineAnchor) offlineAnchor.insertAdjacentElement('afterend', offlineDetails);
  else shortcutDetails.insertAdjacentElement('beforebegin', offlineDetails);
  const offlineStatusLine = offlineDetails.querySelector('#dotify-offline-model-status');
  const offlineDownloadButton = offlineDetails.querySelector('#dotify-offline-model-download');
  const offlineDeleteButton = offlineDetails.querySelector('#dotify-offline-model-delete');

  let offlineModel = { ready: false, downloading: false, percent: 0, downloadedBytes: 0 };

  function renderOfflineModel() {
    const s = offlineModel;
    offlineOption.textContent = s.ready
      ? 'Nemotron (offline)'
      : 'Nemotron (offline, not downloaded)';
    let text;
    if (s.downloading) {
      // 10% steps: the line is a live region.
      text = `Downloading the offline model — ${Math.floor((s.percent || 0) / 10) * 10}%…`;
    } else if (s.ready) {
      text = 'Offline model: downloaded (about 650 MB on this computer).';
    } else if (s.downloadedBytes > 0) {
      text = 'Offline model: partly downloaded — Download resumes where it stopped.';
    } else {
      text = 'Offline model: not downloaded (about 650 MB).';
    }
    if (s.error) text += ` The last download failed: ${s.error}`;
    if (offlineStatusLine.textContent !== text) offlineStatusLine.textContent = text;
    offlineDownloadButton.textContent = s.downloading
      ? 'Cancel download' : 'Download (about 650 MB)';
    offlineDownloadButton.hidden = s.ready;
    offlineDeleteButton.hidden = !s.ready || s.downloading;
  }

  async function refreshOfflineModel() {
    const wasDownloading = offlineModel.downloading;
    try {
      const response = await fetch('/api/offline-model', { cache: 'no-store' });
      if (response.ok) offlineModel = await response.json();
    } catch (_) { /* server briefly away: keep the last known state */ }
    if (wasDownloading && !offlineModel.downloading && offlineModel.ready) {
      if (window.dotifyAnnounce) window.dotifyAnnounce('offline model ready');
    }
    renderOfflineModel();
    return offlineModel;
  }

  async function offlineModelCommand(payload) {
    try {
      const { token } = await (await fetch('/api/providers', { cache: 'no-store' })).json();
      const response = await fetch('/api/offline-model', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Dotify-Token': token },
        body: JSON.stringify(payload),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || `request failed (${response.status})`);
      offlineModel = body;
    } catch (error) {
      offlineStatusLine.textContent =
        `The offline-model request failed: ${error.message}`;
      return;
    }
    renderOfflineModel();
  }

  offlineDownloadButton.addEventListener('click', () => {
    offlineModelCommand(offlineModel.downloading ? { cancel: true } : { download: true });
  });
  offlineDeleteButton.addEventListener('click', () => {
    if (!window.confirm('Delete the downloaded offline model (about 650 MB)? '
      + 'Nemotron and offline fallback captions stop working until it '
      + 'is downloaded again.')) return;
    offlineModelCommand({ remove: true });
  });

  let active = false;
  let meterStream = null;
  let meterContext = null;
  let meterSource = null;
  let meterFrame = null;
  let offlineSocket = null;
  let audioProcessor = null;
  let silentOutput = null;

  function setStatus(message) { status.textContent = message; }

  function stopMeter() {
    if (meterFrame) cancelAnimationFrame(meterFrame);
    meterFrame = null;
    if (meterStream) meterStream.getTracks().forEach((track) => track.stop());
    meterStream = null;
    if (meterContext) meterContext.close().catch(() => {});
    meterContext = null;
    meterSource = null;
    meter.value = 0;
    meter.textContent = '0';
  }

  async function startMeter() {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1 } });
    if (!active) {
      // stop() ran while the permission prompt or device open was pending
      // (an engine switch can land then); it had no stream to release yet.
      stream.getTracks().forEach((track) => track.stop());
      return;
    }
    meterStream = stream;
    const tracks = meterStream.getAudioTracks();
    if (!tracks.length) throw new Error('Windows did not provide a microphone audio track.');
    meterContext = new AudioContext();
    await meterContext.resume();
    if (!active) return;   // stopped meanwhile; stopMeter() released it all
    meterSource = meterContext.createMediaStreamSource(meterStream);
    const analyser = meterContext.createAnalyser();
    analyser.fftSize = 1024;
    meterSource.connect(analyser);
    const samples = new Float32Array(analyser.fftSize);
    const tick = () => {
      if (!active || !meterContext) return;
      analyser.getFloatTimeDomainData(samples);
      let sum = 0;
      for (const sample of samples) sum += sample * sample;
      const level = Math.min(100, Math.round(Math.sqrt(sum / samples.length) * 400));
      meter.value = level;
      meter.textContent = String(level);
      meterFrame = requestAnimationFrame(tick);
    };
    tick();
  }

  function pcm16At16k(float32, inputRate) {
    const outputLength = Math.max(1, Math.floor(float32.length * 16000 / inputRate));
    const output = new Int16Array(outputLength);
    for (let index = 0; index < outputLength; index++) {
      const sourceIndex = Math.min(float32.length - 1, Math.floor(index * inputRate / 16000));
      const sample = Math.max(-1, Math.min(1, float32[sourceIndex]));
      output[index] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
    }
    return output;
  }

  function stopOfflineRecognition() {
    if (audioProcessor) {
      audioProcessor.onaudioprocess = null;
      try { audioProcessor.disconnect(); } catch (_) {}
    }
    if (silentOutput) {
      try { silentOutput.disconnect(); } catch (_) {}
    }
    audioProcessor = null;
    silentOutput = null;
    const socket = offlineSocket;
    offlineSocket = null;
    if (socket && socket.readyState === WebSocket.OPEN) {
      try { socket.send(JSON.stringify({ command: 'finish' })); } catch (_) {}
      setTimeout(() => { try { socket.close(); } catch (_) {} }, 150);
    }
  }

  async function startOfflineRecognition() {
    return new Promise((resolve) => {
      const socket = new WebSocket('ws://127.0.0.1:8791/transcribe');
      socket.binaryType = 'arraybuffer';
      let settled = false;
      let connected = false;
      const settle = (value) => {
        if (settled) return;
        settled = true;
        clearTimeout(timeout);
        resolve(value);
      };
      const timeout = setTimeout(() => {
        try { socket.close(); } catch (_) {}
        settle(false);
      }, 3000);

      // The service speaks first: a status when it has the model, an error
      // when not. Audio flows from the first status; while the model loads
      // the service buffers it, so no speech is lost.
      const beginPiping = () => {
        if (connected) return;
        if (!active || !meterContext || !meterSource) {
          socket.close();
          settle(false);
          return;
        }
        offlineSocket = socket;
        connected = true;
        audioProcessor = meterContext.createScriptProcessor(4096, 1, 1);
        silentOutput = meterContext.createGain();
        silentOutput.gain.value = 0;
        audioProcessor.onaudioprocess = (event) => {
          if (!active || socket.readyState !== WebSocket.OPEN) return;
          const samples = event.inputBuffer.getChannelData(0);
          socket.send(pcm16At16k(samples, meterContext.sampleRate).buffer);
        };
        meterSource.connect(audioProcessor);
        audioProcessor.connect(silentOutput);
        silentOutput.connect(meterContext.destination);
        settle(true);
      };
      socket.onmessage = (event) => {
        let message;
        try { message = JSON.parse(event.data); } catch (_) { return; }
        if (message.type === 'error') {
          setStatus(`Offline speech is unavailable: ${message.message}`);
          try { socket.close(); } catch (_) {}
          settle(false);
          return;
        }
        if (message.type === 'status') {
          beginPiping();
          if (!connected) return;
          if (message.state === 'loading') {
            setStatus('Loading the offline model — the first offline start takes a moment…');
          } else if (message.state === 'behind') {
            // Decode is slower than real time and dropping old audio.
            setStatus('Offline captions fell behind. Skipping older audio to stay live.');
          } else {
            setStatus('Transcribing offline on this computer. Speak now.');
          }
          return;
        }
        if (message.type === 'partial') {
          partial.textContent = message.text || '';
          postInterim((message.text || '').trim());
        }
        if (message.type === 'final' && message.text) {
          partial.textContent = '';
          postFinal(message.text);
        } else if (message.type === 'final') {
          // An empty final still ends the utterance: withdraw its soft
          // text, or the braille queue waits behind the open segment.
          partial.textContent = '';
          postInterim('');
        }
      };
      socket.onerror = () => settle(false);
      socket.onclose = () => {
        if (offlineSocket === socket) offlineSocket = null;
        if (active && connected) {
          // The decode service died mid-session: stop, then retry.
          stop(false);
          setStatus('Offline speech stopped. Restarting automatically.');
          if (window.dotifyAnnounce) window.dotifyAnnounce('offline speech stopped');
          scheduleOfflineRetry();
        }
      };
    });
  }

  // Interim hypotheses are posted as {interim, client, segment, text}; the
  // server streams their stable prefix to braille as soft text it can still
  // revise, and the final for the same {client, segment} hardens it.
  const INGEST_CLIENT = Math.random().toString(36).slice(2, 10);
  const INTERIM_POST_MS = 250;
  let ingestSegment = 0;
  let lastInterimSent = '';
  let lastInterimPostAt = 0;

  // A non-2xx /ingest answer means braille and transcript.txt are not
  // getting the words. Report it once per outage (interims post four times
  // a second); the next accepted post re-arms the report.
  let ingestRefusalReported = false;
  function noteIngestAccepted() {
    ingestRefusalReported = false;
  }
  function reportIngestRefusal(httpStatus) {
    if (ingestRefusalReported) return;
    ingestRefusalReported = true;
    setStatus(`Speech was recognized, but the server refused the text (HTTP ${httpStatus}). Braille and the transcript are not receiving these words.`);
    if (window.dotifyAnnounce) window.dotifyAnnounce('server refused the transcribed text');
  }

  function postInterim(text) {
    const now = performance.now();
    if (text === lastInterimSent) return;
    if (text && now - lastInterimPostAt < INTERIM_POST_MS) return;
    lastInterimSent = text;
    lastInterimPostAt = now;
    // engine selects the server's Nemotron-specific stability filter.
    fetch('/ingest', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ interim: true, client: INGEST_CLIENT,
                             segment: ingestSegment, text, engine: 'nemotron' }),
    }).then((res) => {
      if (!res.ok) reportIngestRefusal(res.status);
      else noteIngestAccepted();
    }).catch(() => {}); // interims are best-effort; finals report reach errors
  }

  function postFinal(text) {
    // The transcript shows what the server emitted (corrected, with any
    // speaker prefix), so it matches braille. If the server can't be
    // reached the raw text is shown with an error; if it refuses the text
    // nothing is shown, since braille did not get it either.
    const appendBox = (line) => {
      // Through the page's hook, which keeps the reading position.
      if (window.dotifyTranscript) window.dotifyTranscript.append(line);
      else finalized.textContent += (finalized.textContent ? '\n' : '') + line;
    };
    fetch('/ingest', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, client: INGEST_CLIENT, segment: ingestSegment++ }),
    }).then(async (response) => {
      if (!response.ok) {
        reportIngestRefusal(response.status);
        return;
      }
      noteIngestAccepted();
      let ruled = null;
      try { ruled = await response.json(); } catch { /* no JSON body */ }
      // {dropped}: the server discarded the words. {deferred}: the result is
      // pending (a speaker-label hold), so the posted text stands in.
      if (ruled && ruled.dropped) return;
      appendBox(ruled && ruled.text ? ruled.text : text);
    }).catch(() => {
      appendBox(text);
      setStatus('Transcription worked, but Dotify could not stream the text.');
    });
    // Reset the dedupe for the new segment.
    lastInterimSent = '';
  }

  async function start() {
    active = true;
    engine.disabled = true;
    toggle.disabled = true;
    toggle.textContent = 'Stop transcription';
    setStatus('Requesting microphone access...');
    try {
      if (!offlineModel.ready) {
        throw new Error('the Nemotron offline model is not downloaded. '
          + 'Download it under Settings > Transcription > Offline model, or pick a model '
          + 'that uses an API key.');
      }
      await startMeter();
      if (!active) return;
      setStatus('Connecting to Nemotron...');
      const connected = await startOfflineRecognition();
      if (!active) return;
      if (!connected) {
        throw new Error('the offline decode service is not answering. '
          + 'Check that the model finished downloading in Settings, then '
          + 'stop and start transcription — or relaunch Dotify.');
      }
      toggle.disabled = false;
    } catch (error) {
      stop(false);
      const detail = error && error.message ? error.message : String(error);
      setStatus(`Could not start speech recognition: ${detail}`);
    }
  }

  function stop(announce = true) {
    active = false;
    stopOfflineRecognition();
    stopMeter();
    partial.textContent = '';
    // Withdraw any soft text and retire the segment: the flush final can
    // be empty or lose the race with the socket close, and an open segment
    // holds up the braille queue. A restart must not reuse the segment key.
    postInterim('');
    ingestSegment += 1;
    engine.disabled = false;
    toggle.disabled = false;
    toggle.textContent = 'Start transcription';
    cancelOfflineRetry();
    if (announce) setStatus('Stopped.');
  }

  // A dead offline session retries forever on a doubling backoff (there is
  // nothing to fall back to). A session that holds for 8 s resets it.
  const OFFLINE_RETRY_FIRST_MS = 1500;
  const OFFLINE_RETRY_CAP_MS = 60000;
  let offlineRetryTimer = null;
  let offlineRetryDelay = OFFLINE_RETRY_FIRST_MS;

  function cancelOfflineRetry() {
    if (offlineRetryTimer) clearTimeout(offlineRetryTimer);
    offlineRetryTimer = null;
  }

  function scheduleOfflineRetry() {
    cancelOfflineRetry();
    const delay = offlineRetryDelay;
    offlineRetryDelay = Math.min(offlineRetryDelay * 2, OFFLINE_RETRY_CAP_MS);
    offlineRetryTimer = setTimeout(() => {
      offlineRetryTimer = null;
      engineSwitch = engineSwitch.then(async () => {
        if (document.documentElement.dataset.dotifyStopped) return;
        if (engine.value !== 'offline') return; // the user moved on
        if (active || isTranscribing() || toggle.disabled) return;
        if (window.dotifyPause && window.dotifyPause.state() !== 'off') return;
        await start();
        // A start that failed outright (service still down, model deleted)
        // keeps the ladder going; one that died later re-enters via onclose.
        if (!active && engine.value === 'offline'
          && !document.documentElement.dataset.dotifyStopped) {
          scheduleOfflineRetry();
        }
      });
    }, delay);
  }

  // The decode service loads the model (~1.5 s, ~780 MB) on its first
  // connection. At the first sign of connection loss, open a connection
  // with no audio so the model loads during the quick retry and a fallback
  // finds it ready, without paying the memory when nothing goes wrong.
  let warmupSocket = null;
  function warmOfflineSlot() {
    if (!offlineModel.ready || offlineSocket || warmupSocket) return;
    let socket;
    try { socket = new WebSocket('ws://127.0.0.1:8791/transcribe'); } catch (_) { return; }
    warmupSocket = socket;
    const finish = () => {
      clearTimeout(deadline);
      if (warmupSocket === socket) warmupSocket = null;
      try { socket.close(); } catch (_) {}
    };
    const deadline = setTimeout(finish, 30000);
    socket.onmessage = (event) => {
      let message;
      try { message = JSON.parse(event.data); } catch (_) { return; }
      if (message.type === 'error'
        || (message.type === 'status' && message.state !== 'loading')) finish();
    };
    socket.onerror = finish;
    socket.onclose = () => {
      clearTimeout(deadline);
      if (warmupSocket === socket) warmupSocket = null;
    };
  }

  toggle.addEventListener('click', (event) => {
    if (!OVERLAY_ENGINES.includes(engine.value)) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    if (active) stop();
    else start();
  }, true);

  document.addEventListener('keydown', (event) => {
    if (!event.altKey || !event.shiftKey || event.ctrlKey || event.metaKey || event.repeat) return;
    const action = shortcutActions[event.key.toUpperCase()];
    if (!action) return;
    event.preventDefault();
    event.stopPropagation();
    action();
  }, true);

  window.addEventListener('beforeunload', () => stop(false));

  // Transcription starts by itself and engine changes restart it, so
  // Start/Stop shows only when it is the way out: stopped, not paused, not
  // quit (maintained by the interval below).
  toggle.hidden = true;
  const engineHelp = document.getElementById('engine-help');
  if (engineHelp) {
    engineHelp.textContent = 'Model changes apply immediately. Nemotron '
      + 'needs no key and no internet after its one-time model '
      + 'download (Settings > Transcription > Offline model).';
  }

  // Picking an engine stops the current session and starts the new one,
  // one switch at a time. The page dispatches the toggle by the selector's
  // value, so the stop click briefly restores the running engine's value.
  const isTranscribing = () => toggle.textContent.trim().startsWith('Stop');
  let activeEngine = engine.value;
  let engineSwitch = Promise.resolve();

  async function settleStopped(timeoutMs) {
    const deadline = Date.now() + timeoutMs;
    while ((isTranscribing() || toggle.disabled) && Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
  }

  engine.addEventListener('change', () => {
    const target = engine.value;
    // A user's pick ends any automatic connection-loss fallback.
    if (!programmaticSwitch) clearNetFallback();
    engineSwitch = engineSwitch.then(async () => {
      if (active) {
        stop();                        // this overlay's own offline session
      } else if (isTranscribing()) {
        engine.value = activeEngine;
        toggle.click();
        await settleStopped(5000);
        engine.value = target;
      }
      if (!isTranscribing() && !toggle.disabled) {
        activeEngine = target;
        // While paused, wait for resume, which starts the new pick.
        if (window.dotifyPause && window.dotifyPause.state() !== 'off') return;
        toggle.click();
      }
    });
  });

  // The page locks the selector while running; keep it usable so switching
  // needs no stop. After quit it locks for good.
  let offlinePollTick = 0;
  setInterval(() => {
    const stopped = Boolean(document.documentElement.dataset.dotifyStopped);
    engine.disabled = stopped;
    if (stopped && pauseToggle) pauseToggle.disabled = true;
    const paused = Boolean(
      window.dotifyPause && window.dotifyPause.state() !== 'off');
    toggle.hidden = stopped || paused || toggle.disabled || isTranscribing();
    netWatchdog();
    // Offline-model status every 1.5 s while downloading, else every 12 s.
    offlinePollTick += 1;
    if (!stopped && (offlineModel.downloading || offlinePollTick % 8 === 0)) {
      refreshOfflineModel();
    }
  }, 1500);

  // Opening Dotify means listening: at boot, start the first keyed engine
  // with a key (ElevenLabs, then AssemblyAI, then OpenAI), else Nemotron.
  async function startupEnginePick() {
    await refreshOfflineModel();
    let startup = null;
    try {
      const response = await fetch('/api/providers', { cache: 'no-store' });
      const config = await response.json();
      const configured = (name) => config.providers
        && config.providers[name] && config.providers[name].configured;
      startup = ['elevenlabs', 'assemblyai', 'openai'].find(configured) || null;
    } catch (_) { /* provider status unknown: use the keyless default */ }
    // A start, pause or quit may have happened during the awaits; the
    // toggle click would then stop the user's session or start after quit.
    if (active || isTranscribing() || toggle.disabled) return;
    if (document.documentElement.dataset.dotifyStopped) return;
    if (window.dotifyPause && window.dotifyPause.state() !== 'off') return;
    if (startup) {
      engine.value = startup;
      activeEngine = startup;
      toggle.click();
      return;
    }
    // Without the model, start() says how to download it.
    engine.value = 'offline';
    activeEngine = 'offline';
    start();
  }

  engineSwitch = engineSwitch.then(startupEnginePick);

  // --- Connection loss ------------------------------------------------------
  // The page calls dotifySessionLost when a running keyed session dies
  // unexpectedly (never on a user stop). Recovery: one quick retry of the
  // same engine, then offline captions, then a return to the lost engine.
  // The return is gated by /api/net-probe, which checks the vendor is
  // reachable, because a failed switch costs the reader their fallback
  // captions while a failed probe costs nothing. A return that still fails
  // backs off. Alerts go to the page status and the braille display.
  const NET_QUICK_RETRY_MS = 1200;   // same-engine retry after a first death
  const NET_PROBE_INTERVAL_MS = 3000; // vendor-reachability poll while fallen back
  const NET_RETURN_FIRST_MS = 15000; // probe suspension after a FAILED return
  const NET_RETURN_CAP_MS = 300000;  // backoff ceiling between failed returns
  const NET_HEALTHY_MS = 8000;       // session alive this long = it held
  let netFallback = false;     // captions are on the offline slot
  let preferredEngine = null;  // the keyed engine to return to
  let returnAttempt = false;   // a return to preferredEngine is in flight
  let quickRetried = false;    // the one fast retry was spent
  let returnDelay = NET_RETURN_FIRST_MS;
  let returnTimer = null;
  let healthySince = 0;
  let programmaticSwitch = false;

  function switchEngine(value) {
    programmaticSwitch = true;
    try {
      engine.value = value;
      engine.dispatchEvent(new Event('change', { bubbles: true }));
    } finally {
      programmaticSwitch = false;
    }
  }

  function clearNetFallback() {
    netFallback = false;
    preferredEngine = null;
    returnAttempt = false;
    quickRetried = false;
    returnDelay = NET_RETURN_FIRST_MS;
    if (returnTimer) clearTimeout(returnTimer);
    returnTimer = null;
  }

  function scheduleReturn(delay) {
    if (returnTimer) clearTimeout(returnTimer);
    returnTimer = setTimeout(attemptReturn, delay);
  }

  function attemptReturn() {
    returnTimer = null;
    if (!netFallback || document.documentElement.dataset.dotifyStopped) return;
    if (window.dotifyPause && window.dotifyPause.state() !== 'off') {
      // Nothing may start while paused; keep the heartbeat so a long pause
      // still returns eventually.
      scheduleReturn(returnDelay);
      return;
    }
    if (navigator.onLine === false) {
      // Definitely no network interface: probing is pointless and the
      // 'online' listener fires the next attempt the moment one appears;
      // this heartbeat is only a backstop.
      scheduleReturn(returnDelay);
      return;
    }
    // Probe before switching (via the local server, so browser cross-origin
    // rules never apply): reachable means spend a real attempt now;
    // unreachable means look again in a few seconds. Probe failures never
    // touch the running fallback captions.
    fetch('/api/net-probe?engine=' + encodeURIComponent(preferredEngine),
      { cache: 'no-store' })
      .then((response) => response.json())
      .then(({ reachable }) => {
        if (!netFallback || returnAttempt || returnTimer) return;
        if (document.documentElement.dataset.dotifyStopped) return;
        if (window.dotifyPause && window.dotifyPause.state() !== 'off') {
          // Paused while the probe was in flight: nothing may start.
          scheduleReturn(returnDelay);
          return;
        }
        if (!reachable) {
          scheduleReturn(NET_PROBE_INTERVAL_MS);
          return;
        }
        returnAttempt = true;
        // The healthy clock must confirm the NEW session, not inherit the
        // fallback session's uptime through a quick engine swap.
        healthySince = 0;
        switchEngine(preferredEngine);
      })
      .catch(() => scheduleReturn(NET_PROBE_INTERVAL_MS));
  }

  // Called from the 1.5 s interval above: track how long the current session
  // has held, spend it to settle retry/return verdicts. "Held for 8 s" is
  // the success proxy — the overlay never sees the provider's ready message.
  function netWatchdog() {
    if (isTranscribing()) {
      if (!healthySince) healthySince = Date.now();
    } else {
      healthySince = 0;
    }
    const healthy = healthySince && Date.now() - healthySince >= NET_HEALTHY_MS;
    if (!healthy) return;
    if (active) offlineRetryDelay = OFFLINE_RETRY_FIRST_MS;  // offline held: reset its ladder
    if (quickRetried && !netFallback) quickRetried = false;  // retry held
    if (returnAttempt && engine.value === preferredEngine) {
      // The chosen engine held a session again: the return succeeded.
      const name = window.dotifyEngineFlashName
        ? window.dotifyEngineFlashName(preferredEngine) : preferredEngine;
      clearNetFallback();
      if (window.dotifyAnnounce) window.dotifyAnnounce('back online - ' + name);
      setStatus('The connection is back — transcribing with the chosen model again.');
    }
  }

  window.addEventListener('online', () => {
    if (!netFallback) return;
    returnDelay = NET_RETURN_FIRST_MS;
    // Let the interface settle before dialing the provider.
    scheduleReturn(2000);
  });

  // First suspicion of connection loss — before any retry verdict — starts
  // the keyless slot's one-time warm-up, so a fallback moments later finds
  // it already loaded (free lunch: no standing cost when the connection
  // never drops). The OS 'offline' event is the earliest such signal.
  window.addEventListener('offline', () => warmOfflineSlot());

  window.dotifySessionLost = ({ engine: lostEngine }) => {
    if (OVERLAY_ENGINES.includes(lostEngine)) return;  // ours never open /audio
    if (document.documentElement.dataset.dotifyStopped) return;
    warmOfflineSlot();
    engineSwitch = engineSwitch.then(async () => {
      // Serialized behind any in-flight switch: if something is already
      // running again (a user restart, an engine change), stand down.
      if (active || isTranscribing()) return;
      if (window.dotifyPause && window.dotifyPause.state() !== 'off') return;
      if (returnAttempt) {
        // The return attempt failed — back to offline captions, wait longer.
        returnAttempt = false;
        returnDelay = Math.min(returnDelay * 2, NET_RETURN_CAP_MS);
        switchEngine('offline');
        scheduleReturn(returnDelay);
        return;
      }
      if (!quickRetried && navigator.onLine !== false) {
        quickRetried = true;
        setStatus('Transcription connection lost — reconnecting…');
        await new Promise((resolve) => setTimeout(resolve, NET_QUICK_RETRY_MS));
        if (!isTranscribing() && !toggle.disabled) toggle.click();
        return;
      }
      // Fall back. Without the downloaded model there are no offline
      // captions, and the alerts must not promise them.
      if (!preferredEngine) preferredEngine = lostEngine;
      netFallback = true;
      quickRetried = false;
      returnDelay = NET_RETURN_FIRST_MS;
      switchEngine('offline');
      if (window.dotifyAnnounce) {
        window.dotifyAnnounce(offlineModel.ready
          ? 'offline captions' : 'connection lost');
      }
      setStatus(offlineModel.ready
        ? ('Transcription lost its internet connection — now transcribing '
          + 'offline on this computer. Dotify switches back to '
          + preferredEngine + ' automatically when the connection returns.')
        : ('Transcription lost its internet connection and the offline '
          + 'model is not downloaded, so captions stop until the '
          + 'connection returns. Dotify keeps retrying '
          + preferredEngine + ' automatically.'));
      scheduleReturn(NET_PROBE_INTERVAL_MS);
    });
  };
})();
