# Known issues

Problems known at release. File and function names are given so you can
find the code; line numbers drift.

## Things you may notice

- **The Brailliant BI 40X suspends itself off the USB bus when idle.** When
  the reader is caught up or paused, Dotify writes nothing, and the display
  can drop off the bus. Pressing a key on the display wakes it; if braille
  doesn't come back, relaunch. Dotify sends no keep-alive write.

- **Quiet speech can come out as nothing on the OpenAI engine.** The OpenAI
  adapter (`providers/openai-realtime.js`) only commits audio that crossed
  the speech gate's level (`SPEECH_RMS` = 0.008 in
  `providers/speech-gate.js`). Speech that stays below it is discarded.
  The other engines stream everything and use the vendor's own endpointing,
  so they transcribe the same audio. Measured quiet speech sits around
  RMS 0.010, just above the gate. `tools/wer-replay.js` can score a retuned
  threshold.

- **Pausing transcription does nothing while a session is still
  connecting.** `window.dotifyPause` (in `public/index.html`) decides
  whether transcription is running from the Start/Stop button's label,
  which only changes once the provider socket opens. A pause in that window
  (including the automatic pause when you start typing in Human mode)
  announces that the microphone is off without stopping it.

- **After a quick pause and resume, late text from the old session can
  appear after the new session's text.** A provider takes up to ~3 s to
  flush its last finals on close, and nothing marks which session they
  belong to.

- **Two Bluetooth serial ports for one display stop the connection.** If a
  stale port survives an unpair/re-pair, the same display is listed twice
  and Dotify refuses to guess (`bluetooth_ports.py`
  `enumerate_spp_ports`). The message says what to do: remove the stale
  port in Device Manager, or unpair and re-pair.

- **The last page of a file replay vanishes at once on Linux.** When a
  file or stdin replay ends, `BrlapiSink.close()` hands the display back to
  BRLTTY with no dwell, so the final page disappears before it can be read.
  Live sessions don't end this way, and the Windows sinks leave the last
  frame on the display.

- **A display outage freezes the controls for a few seconds.** The Windows
  sinks retry a failed write twice (`sink_reconnect.py`) while holding the
  engine lock, so key commands and the panel wait up to ~5 s before
  background reconnection (`run.py` `recover_display`) takes over.

- **Without the gauge, summaries aren't marked.** The `s` (summary) and `h`
  (typed text) markers live in cell 2, which `--no-gauge` removes (as does
  a display narrower than 4 cells). A summary then reads like live speech.
  The engine prints a warning at startup; `--no-summary` avoids it. The
  normal launch always has the gauge on.

## In the code

These don't show up in normal use, but are worth knowing before changing
the code around them.

- **Revisable-segment machinery.** `engine.py` `revise_segment`,
  `sources/ws_source.py` and the consume path in `run.py` implement the
  protocol between the speech server and the braille engine. Changes there
  usually need a protocol-level decision, not a local fix.
- **OpenAI item ordering.** `server.js` keeps a per-session `openItems`
  list to emit OpenAI items in order, but an item that completes without
  any partial text bypasses it and can arrive ahead of an older item.
- **No send backpressure in the audio path.** If a vendor's TCP connection
  stalls, the `ws` client buffers audio without limit and sends it all
  late; the browser sender doesn't check `bufferedAmount` either.
- **Bluetooth key-reader shutdown.** A Bluetooth key reader stopped during
  reconnection can still finish one key action it had already started
  (`HumanWareBluetoothSink._close_unlocked`).
- **Summary model capabilities** are inferred from the model name
  (`summarize.js`, `startsWith('gpt-4')`); a `DOTIFY_SUMMARY_MODEL`
  override that doesn't fit the pattern may lose the hard length limit or
  fail into the local fallback.
- **Ports are hardcoded in several places** (8788/8790/8791/8792 in
  `launcher.ps1`, `control_bridge.py`, `local_speech_server.py`, the
  overlay scripts, `run.py` and `server.js`). Change them together.
- **`core/` reaches into the Windows folder once.** `translator.py`
  `_default_liblouis_dir` falls back to `Windows/installer/vendor/liblouis`
  in a source checkout; the installed app sets `DOTIFY_LIBLOUIS_DIR`
  instead.
- **Performance at very large backlogs.** Several engine paths are linear
  in the queue length (`backlog_words`, `revise_segment`, and the liblouis
  translation in `take_pending`). This only matters when a reader is hours
  behind.
