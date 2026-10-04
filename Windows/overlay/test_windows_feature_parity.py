"""Windows-overlay checks for core features inherited from the shipping runner."""

from pathlib import Path
import re
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).parents[2]
BRAILLE_APP = REPO_ROOT / "core" / "text-to-braille"
WINDOWS_DIR = REPO_ROOT / "Windows"


from braille_engine.cells import dots_to_pattern
from braille_engine.engine import TYPED_MARKER, BrailleEngine
from braille_engine.sinks.simulated import SimulatedSink
from braille_engine.translator import DevUebTranslator
import windows_run


class WindowsFeatureParityTests(unittest.TestCase):
    def test_shipping_page_keeps_the_overlay_anchor_nodes(self):
        # The Windows overlay bails out entirely when its anchor nodes are
        # missing, so the shipping page must keep every one of them.
        shipping_index = (
            BRAILLE_APP.parent / "speech-to-text" / "public" / "index.html"
        ).read_text(encoding="utf-8")
        for anchor in ('id="toggle"', 'id="miclevel"', 'id="partial"',
                       'id="finalized"', 'id="engine"'):
            self.assertIn(anchor, shipping_index)

    def test_build_stages_every_overlay_module(self):
        # build.ps1 copies the overlay file by file; a module it misses
        # imports fine in the repo and fails only on installed machines.
        build = (WINDOWS_DIR / "installer" / "build.ps1").read_text(encoding="utf-8")
        modules = [path.name for path in Path(__file__).parent.glob("*.py")
                   if not path.name.startswith(("test_", "conftest"))]
        self.assertIn("sink_reconnect.py", modules)
        for name in modules:
            self.assertIn(f'"$Platform\\overlay\\{name}"', build, name)

    def test_appliance_ui_is_a_staging_overlay_not_a_shipping_app_edit(self):
        build = (WINDOWS_DIR / "installer" / "build.ps1").read_text(encoding="utf-8")
        shipping_index = (BRAILLE_APP.parent / "speech-to-text" / "public" / "index.html").read_text(
            encoding="utf-8"
        )
        self.assertIn("dotify-controls-loader.js", build)
        self.assertIn("windows-speech-overlay.js", build)
        self.assertNotIn("dotify-controls-loader.js", shipping_index)

        speech_overlay = (
            WINDOWS_DIR / "overlay" / "speech_ui" / "windows-speech-overlay.js"
        ).read_text(encoding="utf-8")
        appliance_controls = (
            WINDOWS_DIR / "overlay" / "appliance_controls.js"
        ).read_text(encoding="utf-8")
        # The overlay owns one engine: the offline decode service.
        self.assertIn("getUserMedia", speech_overlay)
        self.assertIn("ws://127.0.0.1:8791/transcribe", speech_overlay)
        self.assertIn("Alt+Shift+M", speech_overlay)
        self.assertIn("Alt+Shift+X", speech_overlay)
        # Recording is a developer tool; the overlay only warns that a
        # Nemotron session (no /audio socket) records nothing.
        self.assertIn("getElementById('record-session')", speech_overlay)
        self.assertIn("Nemotron cannot record", speech_overlay)
        # Both scripts capture Alt+Shift+<key> at the document, so they must
        # not claim the same key. The overlay's keys come from its registry,
        # and its keydown handler dispatches only from that registry.
        overlay_keys = set(re.findall(r"\[\s*'([A-Z])',", speech_overlay))
        self.assertEqual({"M", "X"}, overlay_keys)
        panel_keys = set(re.findall(r'data-shortcut="([A-Z])"', appliance_controls))
        self.assertEqual(set(), overlay_keys & (panel_keys | {"T"}))
        keydown_block = speech_overlay[
            speech_overlay.index("document.addEventListener('keydown'"):]
        keydown_block = keydown_block[: keydown_block.index("}, true);")]
        self.assertIn("shortcutActions[", keydown_block)
        self.assertNotRegex(keydown_block, r"\b[A-Z]:\s*\(")
        self.assertIn("toggle.hidden", speech_overlay)

        # Transcription pause (the quota guard): the shipping page owns the
        # button and the window.dotifyPause hook; both Windows overlay scripts
        # ride that hook (Alt+Shift+M above, Human-mode auto-pause below).
        self.assertIn("window.dotifyPause", shipping_index)
        self.assertIn("dotifyPause", speech_overlay)
        self.assertIn("dotifyPause", appliance_controls)

        # The core page owns the banded transcript and the
        # window.dotifyTranscript API; the speech overlay appends through it
        # and the panel feeds the bands and the reading boundary.
        self.assertIn("window.dotifyTranscript", shipping_index)
        self.assertIn('role="textbox"', shipping_index)
        self.assertNotIn("<textarea id=\"finalized\"", shipping_index)
        self.assertIn('id="current-band"', shipping_index)
        self.assertIn('id="pending-band"', shipping_index)
        pending_rule = shipping_index.split("#pending-band {", 1)[1].split("}", 1)[0]
        self.assertIn("color: var(--ink-2)", pending_rule)
        self.assertNotIn("font-style", pending_rule)
        self.assertIn("setBands", shipping_index)
        self.assertIn("dotifyTranscript", speech_overlay)
        self.assertIn("setBands", appliance_controls)
        self.assertIn("updateBoundary", appliance_controls)
        # Braille replies print at the reading boundary, as a visible
        # reply band.
        self.assertIn("function insertReplyText(fresh)", appliance_controls)
        self.assertIn("'Reply: ' + fresh.trimStart()", appliance_controls)
        self.assertIn("bands.insert(at, block)", appliance_controls)
        self.assertNotIn("CSS.highlights", appliance_controls)

        # The sherpa-onnx runtime is pinned and staged; the model itself is
        # a user-driven download and is not shipped.
        lock = (WINDOWS_DIR / "installer" / "vendor.lock.json").read_text(encoding="utf-8")
        self.assertIn('"sherpa-onnx==1.13.4"', lock)
        self.assertIn("$Lock.sherpa_onnx.pin", build)

    def test_offline_engine_is_a_first_class_selectable_option(self):
        """The offline model is its own engine option, with a download flow
        in Settings."""
        overlay = (
            WINDOWS_DIR / "overlay" / "speech_ui" / "windows-speech-overlay.js"
        ).read_text(encoding="utf-8")
        launcher = (WINDOWS_DIR / "launcher.ps1").read_text(encoding="utf-8")
        server = (
            BRAILLE_APP.parent / "speech-to-text" / "server.js"
        ).read_text(encoding="utf-8")
        # Always listed; before the download, start() says how to get it.
        self.assertIn("offlineOption.value = 'offline'", overlay)
        self.assertIn("'Nemotron (offline)'", overlay)
        self.assertIn("'Nemotron (offline, not downloaded)'", overlay)
        self.assertNotIn("offlineOption.disabled", overlay)
        self.assertIn("OVERLAY_ENGINES", overlay)
        self.assertIn("/api/offline-model", overlay)
        self.assertIn("dotify-offline-model-download", overlay)
        # The server owns the resumable download; the launcher points the
        # decode service at the user-data model dir plus the staged manifest.
        self.assertIn("'/api/offline-model'", server)
        self.assertIn("offline-model.json", launcher)
        self.assertIn("models\\nemotron-3.5-560ms-int8", launcher)
        self.assertIn("--manifest", launcher)

    def test_launcher_stops_orphaned_services_before_starting(self):
        # Hidden services left by a launcher that died hold the ports with no
        # window to close; the launcher clears them first, but only ones
        # from this install whose launcher is gone (a live session is kept).
        launcher = (WINDOWS_DIR / "launcher.ps1").read_text(encoding="utf-8")
        body = launcher[launcher.index("function Stop-OrphanedServices"):]
        body = body[:body.index("\n}\n")]
        for port in ("8788", "8790", "8791", "8792"):
            self.assertIn(port, body)
        self.assertIn("$ours -notcontains", body)
        self.assertIn("ParentProcessId", body)
        self.assertLess(launcher.index("  Stop-OrphanedServices"),
                        launcher.index("$server = Start-Process"))

    def test_launcher_prefers_built_in_edge_for_offline_speech(self):
        launcher = (WINDOWS_DIR / "launcher.ps1").read_text(encoding="utf-8")
        candidates = launcher[launcher.index("$candidates = @(") :]
        self.assertLess(candidates.index("Microsoft\\Edge"), candidates.index("Google\\Chrome"))

    def test_settings_landing_page_routes_to_second_level_categories(self):
        # The Settings landing page contains category buttons only. Existing
        # controls live in persistent hidden panels so their state survives
        # moving between categories; Windows fills its Braille and Help
        # panels at overlay load.
        appliance_controls = (
            WINDOWS_DIR / "overlay" / "appliance_controls.js"
        ).read_text(encoding="utf-8")
        speech_overlay = (
            WINDOWS_DIR / "overlay" / "speech_ui" / "windows-speech-overlay.js"
        ).read_text(encoding="utf-8")
        shipping_index = (
            BRAILLE_APP.parent / "speech-to-text" / "public" / "index.html"
        ).read_text(encoding="utf-8")
        self.assertIn('id="settings-categories"', shipping_index)
        for category in ("transcription", "braille", "help", "developer"):
            self.assertIn(
                f'data-settings-category="{category}"', shipping_index)
        self.assertIn(
            '<section id="settings-transcription" class="settings-category-panel"',
            shipping_index)
        self.assertIn('id="settings-category-braille"', shipping_index)
        self.assertIn('id="settings-category-help"', shipping_index)
        self.assertIn('id="settings-category-developer"', shipping_index)
        self.assertIn("window.dotifySettings =", shipping_index)
        self.assertIn("event.key === 'Escape'", shipping_index)
        self.assertIn(
            "brailleCategory.appendChild(windowGroup)", appliance_controls)
        self.assertIn(
            "window.dotifySettings?.enableCategory('braille')", appliance_controls)
        self.assertIn(
            "helpCategory.appendChild(shortcutDetails)", speech_overlay)
        self.assertIn(
            "helpCategory.appendChild(demoGroup)", appliance_controls)
        self.assertIn(
            "window.dotifySettings?.enableCategory('help')", speech_overlay)
        # The offline model download sits beside the model picker it feeds.
        self.assertIn(
            "offlineAnchor.insertAdjacentElement('afterend', offlineDetails)",
            speech_overlay)
        developer_at = shipping_index.index('id="settings-category-developer"')
        self.assertGreater(shipping_index.index('id="record-session"'), developer_at)
        # The recording control is dev-gated on the core page itself, so the
        # same gate holds on this staged overlay page: hidden by default,
        # revealed only by ?dev=1.
        self.assertIn("get('dev') === '1'", shipping_index)
        # The text player moves out of the panel into Settings, so it keeps
        # its Alt+Shift+D lookup and stop-time disable through the
        # commandButtons list captured before the move.
        self.assertIn("Play text on braille display", appliance_controls)
        self.assertIn('id="dotify-demo-source"', appliance_controls)
        for option in ("Short sample", "News article", "Novel",
                       "Text file from this computer"):
            self.assertIn(f">{option}</option>", appliance_controls)
        self.assertIn('id="dotify-demo-file"', appliance_controls)
        self.assertIn("const commandButtons = Array.from(", appliance_controls)
        self.assertIn("demoButton.disabled = true", appliance_controls)
        # The bundled texts are Windows staging assets: build.ps1 must put
        # them where the panel fetches them, and the files must exist.
        build = (WINDOWS_DIR / "installer" / "build.ps1").read_text(
            encoding="utf-8")
        self.assertIn(r"public\texts\news-article.txt", build)
        self.assertIn(r"public\texts\novel.txt", build)
        for name in ("news-article.txt", "novel.txt"):
            bundled = WINDOWS_DIR / "overlay" / "texts" / name
            self.assertTrue(bundled.is_file(), f"{name} is missing")
            self.assertGreater(bundled.stat().st_size, 1000)
        self.assertIn('<div id="partial" hidden', shipping_index)

    def test_caption_replay_control_on_the_panel(self):
        # The panel can pick an .srt/.vtt file and start the timed caption
        # replay.
        appliance_controls = (
            WINDOWS_DIR / "overlay" / "appliance_controls.js"
        ).read_text(encoding="utf-8")
        # Its own labeled picker and button, in the same relocated demo
        # group (Settings > Help and tools), filtered to caption files.
        self.assertIn("Play caption file", appliance_controls)
        self.assertIn('id="dotify-caption-file"', appliance_controls)
        self.assertIn(
            'accept=".srt,.vtt,text/vtt,application/x-subrip"',
            appliance_controls)
        self.assertIn(
            "Caption file to play (SRT or WebVTT)", appliance_controls)
        # The wire shape is the bridge's timed marker: the demo command
        # with an OBJECT value carrying the file's CONTENT.
        self.assertIn("command('demo', { captions: text })",
                      appliance_controls)
        # Toggle parity with the play-text button: while ANY text streams
        # (state.demo covers preset and timed alike), the button is a stop
        # control — a bare demo command stops the feeder.
        self.assertIn("'Stop playing text' : 'Play caption file'",
                      appliance_controls)
        self.assertIn("if (demoActive) { command('demo'); return; }",
                      appliance_controls)
        # Oversized files are refused client-side with a clear message —
        # the same 1 MB ceiling the bridge enforces. The whole file leg
        # (no-file guard, size cap, read, BOM strip, empty check) lives in
        # ONE shared helper so the text and caption messages cannot drift
        # apart again: both handlers call it, only the noun differs.
        self.assertIn("file.size > DEMO_FILE_LIMIT_BYTES",
                      appliance_controls)
        self.assertIn("showError(`That ${noun} is too large. The limit is 1 MB.`)",
                      appliance_controls)
        self.assertIn("await readChosenTextFile(demoFile, 'file')",
                      appliance_controls)
        self.assertIn("await readChosenTextFile(captionFile, 'caption file')",
                      appliance_controls)
        # The helper is the ONLY place that sizes, reads, or rejects a
        # chosen file — a second copy of any of these means the split is
        # creeping back.
        self.assertEqual(appliance_controls.count("is too large"), 1)
        self.assertEqual(appliance_controls.count("has no text in it"), 2)
        self.assertEqual(
            appliance_controls.count("file.size > DEMO_FILE_LIMIT_BYTES"), 1)
        # Alt+Shift+V (for VTT), bound and listed like every panel button.
        self.assertIn('data-command="captions" data-shortcut="V"',
                      appliance_controls)
        self.assertIn(
            "['V', 'Play or stop a caption file on the braille display']",
            appliance_controls)
        # Stop-time sweep: the relocated controls live outside the panel,
        # where the generic disable pass does not reach them.
        self.assertIn("captionButton.disabled = true", appliance_controls)
        self.assertIn("captionFile.disabled = true", appliance_controls)

    def test_appliance_auto_listens_at_boot(self):
        # Opening Dotify means listening: boot runs the startup engine pick
        # once, serialized behind any in-flight engine switch.
        overlay = (
            WINDOWS_DIR / "overlay" / "speech_ui" / "windows-speech-overlay.js"
        ).read_text(encoding="utf-8")
        self.assertIn("function startupEnginePick", overlay)
        self.assertEqual(overlay.count("startupEnginePick"), 2)
        self.assertIn(
            "engineSwitch = engineSwitch.then(startupEnginePick);", overlay)
        # The pick checks after its awaits so it can never STOP a session a
        # fast user press just started, start after Quit, or start while
        # transcription is paused.
        pick = overlay[
            overlay.index("function startupEnginePick"):
            overlay.index("engineSwitch = engineSwitch.then(startupEnginePick);")]
        self.assertIn(
            "if (active || isTranscribing() || toggle.disabled) return;", pick)
        self.assertIn("dataset.dotifyStopped) return;", pick)
        self.assertIn("window.dotifyPause.state() !== 'off'", pick)

    def test_ingest_refusals_fail_loud_not_silent(self):
        # A non-2xx /ingest answer means braille and transcript.txt did not
        # get the words. The overlay once parsed a refusal's body as a ruling
        # and appended the raw text to the finalized box with no error — the
        # sighted page looked healthy while braille and transcript.txt got
        # nothing.
        overlay = (
            WINDOWS_DIR / "overlay" / "speech_ui" / "windows-speech-overlay.js"
        ).read_text(encoding="utf-8")
        appliance_controls = (
            WINDOWS_DIR / "overlay" / "appliance_controls.js"
        ).read_text(encoding="utf-8")
        # The final post checks the HTTP status BEFORE reading a ruling, and
        # a refusal returns without appending — the no-append is the pinned
        # behavior, not just the message.
        self.assertIn(
            "        reportIngestRefusal(response.status);\n        return;\n"
            "      }\n"
            "      noteIngestAccepted();\n"
            "      let ruled = null;",
            overlay)
        # The interim post surfaces the refusal too (an utterance's interims
        # are all refused long before its final).
        self.assertIn("if (!res.ok) reportIngestRefusal(res.status)", overlay)
        # Once per outage, on the page's own status line; the
        # network-failure .catch path is unchanged.
        self.assertIn("if (ingestRefusalReported) return;", overlay)
        self.assertIn(
            "Braille and the transcript are not receiving these words.",
            overlay)
        self.assertIn(
            "Transcription worked, but Dotify could not stream the text.",
            overlay)
        # The panel's typed-line and reply records both drain through the one
        # postIngestRecord wire, which checks the response and warns once per
        # page load on the existing error line; network errors stay
        # fire-and-forget.
        self.assertEqual(
            appliance_controls.count(
                "if (!response.ok) warnIngestRecordRefused(response.status);"),
            1)
        self.assertIn("if (ingestRecordWarned) return;", appliance_controls)
        self.assertIn(
            "refused to record it in the transcript", appliance_controls)

    def test_typed_records_post_through_one_keepalive_wire(self):
        # The typed-line and reply records are the reader's own words in
        # transcript.txt. Both drain through ONE post helper whose fetch
        # carries keepalive, so the pagehide and delivered-quit paths can
        # record an in-progress line while window.close() tears the page down.
        appliance_controls = (
            WINDOWS_DIR / "overlay" / "appliance_controls.js"
        ).read_text(encoding="utf-8")
        self.assertEqual(
            appliance_controls.count("postIngestRecord(text);"), 2)
        self.assertEqual(appliance_controls.count("keepalive: true"), 1)
        self.assertIn("body: JSON.stringify({ typed: true, text })",
                      appliance_controls)
        # pagehide AND the delivered-quit path (markStopped) record both
        # lines before the page goes away.
        self.assertEqual(
            appliance_controls.count("    closeTypedLine();\n"), 2)
        self.assertEqual(
            appliance_controls.count("    recordReplyLine();\n"), 2)

    def test_display_wait_is_announced_on_the_panel(self):
        # while the ticker waits for the display (a screen
        # reader owns it at launch, or a mid-run outage) the panel's
        # aria-live status must carry the reason — the browser page is the
        # accessible channel while the braille display is dead.
        appliance_controls = (
            WINDOWS_DIR / "overlay" / "appliance_controls.js"
        ).read_text(encoding="utf-8")
        self.assertIn("display_wait", appliance_controls)
        self.assertIn("Waiting for the braille display", appliance_controls)

    def test_panel_renders_the_engines_advance_label(self):
        # The bridge publishes the engine's own word for the mode
        # (advance_label); the page renders it and keeps no ticker->auto
        # map of its own.
        appliance_controls = (
            WINDOWS_DIR / "overlay" / "appliance_controls.js"
        ).read_text(encoding="utf-8")
        self.assertIn("state.advance_label", appliance_controls)
        self.assertNotIn("advanceLabels", appliance_controls)
        self.assertNotIn("'Auto", appliance_controls)
        self.assertNotIn("'Manual'", appliance_controls)

    def test_panel_window_row_can_actually_hide_and_respects_focus(self):
        appliance_controls = (
            WINDOWS_DIR / "overlay" / "appliance_controls.js"
        ).read_text(encoding="utf-8")
        # The injected author rule `.dotify-row { display: flex }` overrides
        # the UA stylesheet's [hidden]{display:none}; without this explicit
        # equal-specificity override (placed after the flex rule) the
        # manual mode could never actually hide the window row.
        self.assertIn("[hidden] { display: none; }", appliance_controls)
        # Both branches must leave a focused element alone: the ticker branch
        # before writing .value, and the manual branch before hiding the
        # Braille settings disclosure (hiding or disabling a focused element
        # blurs it and drops screen-reader focus to <body>). The hide guard
        # covers the whole disclosure — its summary is focusable too.
        self.assertIn("document.activeElement !== windowInput", appliance_controls)
        self.assertIn(
            "!windowGroup.contains(document.activeElement)", appliance_controls)

    def test_stop_dotify_closes_its_own_app_mode_window(self):
        # the launcher opens a dedicated app-mode window (no
        # shared tabs), which is what makes the panel's window.close() on
        # Quit Dotify both permitted and safe for the user's other tabs.
        launcher = (WINDOWS_DIR / "launcher.ps1").read_text(encoding="utf-8")
        self.assertIn("--app=", launcher)
        self.assertNotIn("--new-window", launcher)
        appliance_controls = (
            WINDOWS_DIR / "overlay" / "appliance_controls.js"
        ).read_text(encoding="utf-8")
        self.assertIn("window.close()", appliance_controls)
        # The stopped notice must survive as the fallback for browsers that
        # refuse the close, and it must be set before the close attempt
        # (rindex: the call site, not the comment explaining it).
        self.assertIn("Dotify is stopped", appliance_controls)
        self.assertLess(
            appliance_controls.index("Dotify is stopped"),
            appliance_controls.rindex("window.close()"),
        )

    def test_type_mode_compose_box_sits_under_the_transcript_and_echoes(self):
        # Human-mode UX rework: a sighted typist's eyes settle on the streaming
        # transcript, so the typing field must live directly UNDER it (chat
        # compose pattern), echo delivered words back into the box through
        # window.dotifyTranscript, make the mic state visible, and offer an
        # affirmative exit (Back to AI mode button / Escape) instead of relying on the
        # unannounced click-outside blur alone.
        appliance_controls = (
            WINDOWS_DIR / "overlay" / "appliance_controls.js"
        ).read_text(encoding="utf-8")
        # Placement: injected after the finalized transcript, not in the panel.
        self.assertIn("insertAdjacentElement('afterend', compose)", appliance_controls)
        # The typing field must not creep back into the panel's markup — check
        # the panel.innerHTML template itself, not a prefix slice (the CSS
        # block up top mentions both ids, which made a naive slice vacuous).
        panel_markup = appliance_controls[
            appliance_controls.index("panel.innerHTML")
            : appliance_controls.index("insertBefore(panel")]
        self.assertNotIn("dotify-type-text", panel_markup)
        # Echo: delivered words only, via the shipping page's transcript hook.
        self.assertIn("window.dotifyTranscript", appliance_controls)
        self.assertIn("echoInserted(inserted)", appliance_controls)
        self.assertIn('{ typed: true, text }', appliance_controls)
        # The shipping page must offer the same-line hook the echo rides.
        shipping_index = (
            REPO_ROOT / "core" / "speech-to-text" / "public" / "index.html"
        ).read_text(encoding="utf-8")
        self.assertIn("extend: extendFinal", shipping_index)
        # Visible mic state, both transitions, under the user-facing mode
        # names (Human mode / AI mode — wire values stay listen/type); the
        # panel's Mode readout uses the same naming.
        self.assertIn("Human mode. Microphone paused.", appliance_controls)
        self.assertIn("AI mode. Microphone live.", appliance_controls)
        self.assertIn("listen: 'AI'", appliance_controls)
        self.assertIn("command('mode', 'type')", appliance_controls)
        self.assertIn("command('mode', 'listen')", appliance_controls)
        # Affirmative exits and the self-explaining field.
        self.assertIn("dotify-type-done", appliance_controls)
        self.assertIn("'Escape'", appliance_controls)
        self.assertIn("placeholder=", appliance_controls)
        # The compose section must not take the textarea's label as its
        # region name (screen readers read it twice). Escape is announced
        # through the exit button's aria-keyshortcuts.
        self.assertNotIn("aria-labelledby', 'dotify-compose-label", appliance_controls)
        self.assertIn('aria-keyshortcuts="Escape"', appliance_controls)
        # The exit button shows only in Human mode: hidden in the markup,
        # synced to the polled mode, and focus moves off it before it hides.
        self.assertIn("hidden>Back to AI mode</button>", appliance_controls)
        self.assertIn("typeDone.hidden = lastMode !== 'type'", appliance_controls)
        self.assertIn(
            "if (transcript) transcript.focus();\n    typeDone.hidden = true;",
            appliance_controls,
        )

    def test_type_mode_review_fixes_hold_their_shape(self):
        # Fixes from the post-merge review of the compose-box rework. Each
        # assertion pins a mechanism whose absence was a verified bug.
        appliance_controls = (
            WINDOWS_DIR / "overlay" / "appliance_controls.js"
        ).read_text(encoding="utf-8")
        # A pass-through focus (Tab / screen reader) must not enter Human mode:
        # the mode command waits out a dwell that blur cancels.
        self.assertIn("FOCUS_DWELL_MS", appliance_controls)
        # The mic-state line is level-driven from the polled mode (a failed
        # optimistic write self-heals), and silent until the field is used.
        self.assertIn("typeStatusArmed", appliance_controls)
        self.assertNotIn("prevMode", appliance_controls)
        # ...and it must not promise a live mic the user manually paused: the
        # out-of-Human-mode string consults the sticky dotifyPause state.
        self.assertIn("liveStatusText", appliance_controls)
        self.assertIn("is still paused", appliance_controls)
        # Quit / window close must not lose the typed record.
        self.assertIn("pagehide", appliance_controls)
        self.assertIn("keepalive: true", appliance_controls)
        # Quit Dotify sweeps the compose box too (it lives outside the panel),
        # and straggler errors can't shout over the stopped notice.
        self.assertIn("compose.querySelectorAll", appliance_controls)
        self.assertIn(
            "dotifyStopped", appliance_controls[
                appliance_controls.index("function showError")
                : appliance_controls.index("errorSetAt = Date.now()")])
        # The echo re-anchors when a spoken final interleaves mid-stint.
        self.assertIn("echoAnchor", appliance_controls)
        # Whitespace is normalized before the bridge (NBSP would be dropped
        # there while the echo split on it — one tokenizer rule for all).
        self.assertIn("replace(/\\s+/g, ' ')", appliance_controls)
        # The server flushes a held diarization final before a typed record,
        # and both writers share one transcript appender.
        server_js = (
            REPO_ROOT / "core" / "speech-to-text" / "server.js"
        ).read_text(encoding="utf-8")
        # Slice forward from the typed branch itself: other submitTurn call
        # sites precede it in file order, so an unanchored index() would
        # slice backwards and assert against an empty string.
        typed_start = server_js.index("if (typed) {")
        typed_branch = server_js[
            typed_start: server_js.index("submitTurn", typed_start)]
        self.assertIn("flushHold()", typed_branch)
        self.assertIn("appendTranscript(text)", typed_branch)
        self.assertEqual(server_js.count("fs.appendFile(TRANSCRIPT_FILE"), 1)

    def test_installer_targets_windows_11_x64_and_arm64_without_a_console(self):
        installer = (WINDOWS_DIR / "installer" / "installer.iss").read_text(encoding="utf-8")
        self.assertIn("MinVersion=10.0.22000", installer)
        self.assertIn("ArchitecturesAllowed=x64compatible", installer)
        self.assertIn("-WindowStyle Hidden", installer)
        # Upgrades install the pinned stage as-is, never a mix of versions.
        stage_entry = next(line for line in installer.splitlines()
                           if line.startswith('Source: "stage'))
        self.assertIn("ignoreversion", stage_entry)

    def test_windows_runner_adds_native_hid_without_disabling_gauge(self):
        args = windows_run.build_parser().parse_args(["--sink", "native-hid"])

        self.assertEqual("native-hid", args.sink)
        self.assertFalse(args.no_gauge)
        self.assertIsNotNone(windows_run.ticker.build_gauge(args.no_gauge, width=20))

    def test_requested_profile_and_reconnect_limit_reach_native_sink(self):
        windows_run.build_parser().parse_args(
            [
                "--sink",
                "native-hid",
                "--display",
                "brailliant-bi-20x",
                "--reconnect-attempts",
                "5",
            ]
        )
        sink = windows_run.build_sink("native-hid", 40)
        self.assertEqual("brailliant-bi-20x", sink._requested_profile_id)
        self.assertEqual(5, sink._reconnect_attempts)

    def test_runner_closes_its_loopback_services_at_shutdown(self):
        # The control bridge holds port 8790; closing it
        # when the ticker ends (even by an error) releases the ports with
        # the session instead of whenever the process finally goes.
        closed = []

        class FakeBridge:
            name = "control"

            def __init__(self, controls, stop_event):
                pass

            def start(self):
                return self

            def close(self):
                closed.append(self.name)

        controls = SimpleNamespace(engine=SimpleNamespace(sink=None))

        def fake_main(argv=None):
            windows_run.start_controls(controls, threading.Event())
            raise RuntimeError("ticker died")

        with mock.patch.object(windows_run, "ControlBridge", FakeBridge), \
                mock.patch.object(windows_run, "_original_start_controls",
                                  lambda controls, stop_event: None), \
                mock.patch.object(windows_run.ticker, "main", fake_main):
            with self.assertRaises(RuntimeError):
                windows_run.main([])
        self.assertEqual(["control"], closed)
        self.assertEqual([], windows_run._shutdown)

    def test_standard_hid_is_an_explicit_windows_sink(self):
        args = windows_run.build_parser().parse_args(["--sink", "standard-hid"])
        self.assertEqual("standard-hid", args.sink)
        self.assertEqual("StandardHidSink", type(windows_run.build_sink(args.sink, 40)).__name__)

    def test_normal_windows_sink_auto_detects_hid_and_serial_transports(self):
        args = windows_run.build_parser().parse_args(["--sink", "auto-display"])
        self.assertEqual("auto-display", args.sink)
        self.assertEqual(
            "AutoDisplaySink", type(windows_run.build_sink(args.sink, 40)).__name__
        )

    def test_all_native_profiles_are_selectable_from_packaged_runner(self):
        parser = windows_run.build_parser()
        for profile in windows_run.profiles.native_profiles():
            args = parser.parse_args(
                ["--sink", "auto-display", "--display", profile.id]
            )
            self.assertEqual(profile.id, args.display)

    def test_default_twenty_cell_frame_reserves_thermometer_and_marker(self):
        args = windows_run.build_parser().parse_args(["--sink", "native-hid"])
        gauge = windows_run.ticker.build_gauge(args.no_gauge, width=20)
        sink = SimulatedSink(width=20, echo=False)
        engine = BrailleEngine(DevUebTranslator(), sink, gauge=gauge)
        engine.start()

        engine.feed("w " * 12)
        engine.tick()

        frame = sink.frames[-1]
        self.assertEqual(20, len(frame))
        self.assertEqual(dots_to_pattern("78"), frame[0])
        # Cell 2 is the source marker: this untagged feed reads as typed (h).
        self.assertEqual(TYPED_MARKER, frame[1])
        self.assertEqual(18, len(frame[2:]))

    def test_connection_loss_fallback_wiring(self):
        """Keyed-session death -> offline fallback, braille-flash alert, and
        automatic return when the network is back."""
        shipping_index = (
            BRAILLE_APP.parent / "speech-to-text" / "public" / "index.html"
        ).read_text(encoding="utf-8")
        overlay = (
            WINDOWS_DIR / "overlay" / "speech_ui" / "windows-speech-overlay.js"
        ).read_text(encoding="utf-8")
        panel = (WINDOWS_DIR / "overlay" / "appliance_controls.js").read_text(
            encoding="utf-8"
        )
        # The shipping page fires the hook on every unexpected-death path —
        # server-reported error, closed message, socket error, socket close,
        # and the OS 'offline' event (the earliest signal there is) — and
        # nowhere on the user-stop path.
        self.assertIn("window.dotifySessionLost", shipping_index)
        self.assertRegex(shipping_index, r"sessionLost\([^;]*'error'")
        self.assertIn("sessionLost('closed')", shipping_index)
        self.assertIn("sessionLost('socket-error')", shipping_index)
        self.assertIn("sessionLost('socket-close')", shipping_index)
        self.assertIn("sessionLost('offline')", shipping_index)
        # The panel exposes the braille alert channel the overlay flashes.
        self.assertIn("window.dotifyAnnounce = announce", panel)
        # The idle watchdog's mic-off notice is UNSOLICITED and never
        # repeated — it carries the only recovery instruction the
        # reader gets — so it must go out on the alert channel (higher
        # dwell ceiling), not as an ordinary confirmation. Reverting
        # this one call is otherwise invisible to every test.
        self.assertIn("alertFlash('idle - mic off", panel)
        self.assertIn("function alertFlash(text) { command('alert', text); }",
                      panel)
        self.assertIn("window.dotifyEngineFlashName", panel)
        # The overlay retries once, falls back to offline captions, and
        # returns when /api/net-probe says the vendor is reachable, with
        # 'online' as a shortcut and a backoff for a vendor outage.
        self.assertIn("window.dotifySessionLost = ", overlay)
        self.assertIn("switchEngine('offline')", overlay)
        self.assertIn("offline captions", overlay)
        self.assertIn("back online - ", overlay)
        self.assertIn("addEventListener('online'", overlay)
        self.assertIn("NET_RETURN_CAP_MS", overlay)
        self.assertIn("/api/net-probe?engine=", overlay)
        self.assertIn("NET_PROBE_INTERVAL_MS", overlay)
        # First suspicion of loss warms the keyless slot's decode service
        # (the free-lunch load overlap), on both suspicion channels.
        self.assertIn("warmOfflineSlot()", overlay)
        self.assertIn("addEventListener('offline'", overlay)
        # The dead-socket verdict itself is the providers' ping watchdog —
        # seconds, not TCP's 30-60+.
        providers_dir = (
            BRAILLE_APP.parent / "speech-to-text" / "providers"
        )
        self.assertTrue((providers_dir / "socket-watchdog.js").exists())
        for provider_file in ("elevenlabs-realtime.js", "deepgram-realtime.js",
                              "assemblyai-realtime.js", "openai-realtime.js"):
            self.assertIn(
                "attachWatchdog(upstream",
                (providers_dir / provider_file).read_text(encoding="utf-8"),
                f"{provider_file} must attach the socket watchdog",
            )
        # Offline-model independence: the network fallback may be re-pointed
        # by swapping what backs the keyless slot, so the recovery path never
        # names the model it falls back to.
        recovery = overlay[overlay.index("window.dotifySessionLost = "):]
        self.assertNotIn("nemotron", recovery.lower())
        # A user's own engine pick must end the automatic fallback.
        self.assertIn("if (!programmaticSwitch) clearNetFallback()", overlay)


if __name__ == "__main__":
    unittest.main()
