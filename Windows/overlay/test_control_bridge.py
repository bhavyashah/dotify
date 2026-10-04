import json
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request

import pytest

from control_bridge import ControlBridge

HERE = Path(__file__).parent


class FakeTranslator:
    grade = 2


class FakeSink:
    display_name = "Test 20-cell display"


class FakeEngine:
    content_width = 18   # 20-cell display minus the gauge's two cells

    def __init__(self):
        self.translator = FakeTranslator()
        self.sink = FakeSink()
        self.live_jumps = 0
        self.typed = ""
        self.flushes = 0
        self.paused = False
        self.window = 1
        self.shown = ""      # words on the display (speaker-facing highlight)
        # Reading density, mirroring BrailleEngine's defaults.
        self.space_dwell = 1.0
        self.punct_dwell = 1.0
        self.lowercase = False

    def shown_source(self):
        return self.shown

    def jump_to_live(self):
        self.live_jumps += 1

    def flush_input(self):
        self.flushes += 1

    def set_paused(self, paused):
        self.paused = bool(paused)

    def set_window(self, window):
        self.window = window

    def set_window_full(self):
        self.window = self.content_width
        return True


class FakeControls:
    ADVANCE_CYCLE = ("ticker", "manual")

    def __init__(self):
        self.engine = FakeEngine()
        self.interval = {"v": 1.2}
        self.mode = "listen"
        # Starts in manual, the non-default mode, so the toggle tests begin
        # away from the engine default. Auto's wire value is "ticker".
        self.advance_mode = "manual"
        self.stop = False
        self.keys = []
        # Relay counters and the panel's announce answers, as on
        # PacerControls.
        self.mic_toggle_requests = 0
        # Idle-watchdog relay counters, mirroring PacerControls.
        self.idle_pause_requests = 0
        self.idle_resume_requests = 0
        self.announced = []
        # Preset demo stream, mirroring PacerControls: toggle_demo flips
        # the feeder and demo_active reports it.
        self.demo_active = False
        self.demo_toggles = []
        self.demo_timed = []       # the timed flag of each toggle
        self.alerted = []          # announces marked unsolicited
        self.touches = 0           # idle-watchdog input
        self.summarize_calls = 0   # summarize-on-demand presses
        self.mic_label = ""        # input-source label

    def announce(self, text, important=False):
        self.announced.append(text)
        if important:
            self.alerted.append(text)

    def touch_activity(self):
        self.touches += 1

    def set_input_source(self, label):
        self.mic_label = label

    def toggle_demo(self, text=None, timed=False):
        # Mirrors PacerControls: timed content with no usable cues raises
        # before anything toggles (the bridge answers 400).
        if timed and "-->" not in (text or ""):
            raise ValueError("no usable caption cues found")
        self.demo_toggles.append(text)
        self.demo_timed.append(timed)
        self.demo_active = not self.demo_active
        return self.demo_active

    def jump_to_live(self):
        # The plain snap (the jump never summarizes).
        self.engine.jump_to_live()

    def summarize_now(self):
        # Summarize-on-demand: counted apart from the snap so
        # the tests can pin that 'live' never summarizes and vice versa.
        self.summarize_calls += 1

    def _cycle_advance_mode(self):
        cycle = self.ADVANCE_CYCLE
        self.advance_mode = cycle[(cycle.index(self.advance_mode) + 1) % len(cycle)]

    def set_advance_mode(self, mode):
        self.advance_mode = mode

    # Reading density relays, mirroring PacerControls.
    def set_space_time(self, percent):
        self.engine.space_dwell = percent / 100.0
        return True

    def set_punct_time(self, percent):
        self.engine.punct_dwell = percent / 100.0
        return True

    def set_lowercase(self, on):
        self.engine.lowercase = bool(on)

    def _toggle_pause(self):
        # Mirrors PacerControls._toggle_pause (chord parity):
        # silent on pause — the freeze is the confirmation — and the
        # resume flashes "resumed" on the display.
        paused = not self.engine.paused
        self.engine.set_paused(paused)
        if not paused:
            self.announce("resumed")

    def set_paused(self, on):
        # Mirrors PacerControls.set_paused: explicit-valued and
        # idempotent, riding _toggle_pause on a real flip only.
        if bool(on) == bool(self.engine.paused):
            return False
        self._toggle_pause()
        return True

    def handle(self, key):
        self.keys.append(key)
        if key == "\x06":
            self.interval["v"] *= 0.8
        elif key == "\x13":
            self.interval["v"] *= 1.25
        elif key == "\x07":
            self.engine.translator.grade = 1 if self.engine.translator.grade == 2 else 2
        elif key == "\t":
            self.mode = "type" if self.mode == "listen" else "listen"
        elif self.mode == "type":
            self.engine.typed += key


class FakeStopEvent:
    def __init__(self):
        self.stopped = False

    def set(self):
        self.stopped = True


def request(bridge, path, *, method="GET", payload=None, authorized=True):
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Origin": "http://localhost:8788"}
    if authorized:
        headers["X-Dotify-Token"] = bridge.token
    if payload is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        f"http://127.0.0.1:{bridge.port}{path}",
        data=data,
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(req, timeout=2) as response:
        return response.status, response.read(), response.headers


def test_state_and_all_browser_commands():
    controls = FakeControls()
    stop = FakeStopEvent()
    bridge = ControlBridge(controls, stop, port=0).start()
    try:
        status, body, headers = request(bridge, "/api/state")
        state = json.loads(body)
        assert status == 200
        assert state == {
            "connected": True,
            "display": "Test 20-cell display",
            "grade": 2,
            "mode": "listen",
            "paused": False,
            "window": 1,
            "max_window": 18,
            # The engine's own paging predicate, published so the panel's
            # "full-display pages" label can never drift from behavior.
            "window_is_full": False,
            "pace_ms": 1200,
            "wpm": 11,         # grade-2 estimate at 1.2 s/cell (reading_wpm)
            "advance": "manual",   # this rig's explicit choice (the
                                   # product default is auto)
            # The display word for the mode, published from the core's one
            # authority (braille_engine.controls.advance_label) so the JS
            # panel renders it instead of keeping a ticker->auto map.
            "advance_label": "manual",
            "catchup": None,   # no jump-to-live summary in flight or shown
            "summary": None,
            "pan_offset": 0,       # the view sits at the live edge
            "shown": "",       # nothing on the display yet: no highlight
            "shown_display": "",   # mirror now-line (recap included)
            "pending_text": "",    # mirror band 3: the queue head
            "backlog_words": 0,     # exact speaker-facing queue depth
            "backlog_level": None,  # fake engine has no tactile gauge
            "display_wait": None,  # display connected: nothing to wait for
            "mic_toggle": 0,       # Space+dot-6 relay counter (panel acts
                                   # on the increase)
            "idle_pause": 0,       # idle-watchdog relays: pause
            "idle_resume": 0,      # the mic / bring it back, increase-driven
            "mic": "",             # no capture reported yet
            "demo": False,         # preset demo stream not running
            "space_time": 100,     # reading density: full-price spaces,
            "punct_time": 100,     # full-price punctuation,
            "lowercase": False,    # capitals kept
            "reply": None,         # no ReplyComposer on the fake: the
                                   # bridge degrades to None, never raises
        }
        assert headers["Access-Control-Allow-Origin"] == "http://localhost:8788"

        # The speaker-facing highlight rides the same poll: whatever words
        # the engine says are on the display reach the page verbatim.
        controls.engine.shown = "numbers came together"
        _, body, _ = request(bridge, "/api/state")
        assert json.loads(body)["shown"] == "numbers came together"

        # The reply channel rides the poll the same way: the composer's
        # state dict passes through verbatim (the page keys its insert and
        # speak cursors on session/seq).
        class FakeReply:
            @staticmethod
            def state():
                return {"active": True, "session": 1, "seq": 4,
                        "text": "hello there.",
                        "sentences": ["hello there."]}
        controls.reply = FakeReply()
        _, body, _ = request(bridge, "/api/state")
        assert json.loads(body)["reply"] == {
            "active": True, "session": 1, "seq": 4,
            "text": "hello there.", "sentences": ["hello there."]}

        request(bridge, "/api/command", method="POST", payload={"command": "faster"})
        request(bridge, "/api/command", method="POST", payload={"command": "slower"})
        request(bridge, "/api/command", method="POST", payload={"command": "grade"})
        request(bridge, "/api/command", method="POST", payload={"command": "live"})
        # Summarize is its own command — the live snap above
        # must not summarize, and this one must.
        request(bridge, "/api/command", method="POST",
                payload={"command": "summarize"})
        assert controls.summarize_calls == 1
        request(bridge, "/api/command", method="POST", payload={"command": "flush"})
        assert controls.engine.flushes == 0  # listen mode: speech owns flushes
        request(
            bridge,
            "/api/command",
            method="POST",
            payload={"command": "type_text", "value": "hello "},
        )
        assert controls.interval["v"] == 1.2
        assert controls.engine.translator.grade == 1
        assert controls.engine.live_jumps == 1
        assert controls.mode == "type"
        assert controls.engine.typed == "hello "

        request(bridge, "/api/command", method="POST", payload={"command": "flush"})
        assert controls.engine.flushes == 1

        status, body, _ = request(
            bridge, "/api/command", method="POST", payload={"command": "pause"})
        assert controls.engine.paused is True
        assert json.loads(body)["paused"] is True
        request(bridge, "/api/command", method="POST",
                payload={"command": "pause", "value": False})
        assert controls.engine.paused is False
        request(bridge, "/api/command", method="POST",
                payload={"command": "pause", "value": False})
        assert controls.engine.paused is False  # idempotent, not a toggle
        try:
            request(bridge, "/api/command", method="POST",
                    payload={"command": "pause", "value": "yes"})
            raise AssertionError("non-boolean pause value was accepted")
        except urllib.error.HTTPError as exc:
            assert exc.code == 400

        request(bridge, "/api/command", method="POST", payload={"command": "quit"})
        assert controls.stop is True
        assert stop.stopped is True
    finally:
        bridge.close()


def test_pause_command_answers_on_the_display_like_the_chord():
    """settings parity: a panel pause rides the Space+dot-3
    chord's own path — pausing stays silent (the freeze is the
    confirmation), resuming flashes "resumed" so a manual-mode resume is
    distinguishable from a dead button, and idempotent explicit values
    never re-flash."""
    controls = FakeControls()
    bridge = ControlBridge(controls, FakeStopEvent(), port=0).start()
    try:
        request(bridge, "/api/command", method="POST",
                payload={"command": "pause", "value": True})
        assert controls.engine.paused is True
        assert controls.announced == []          # silent by design
        request(bridge, "/api/command", method="POST",
                payload={"command": "pause", "value": True})
        assert controls.announced == []          # idempotent: no re-flash
        _, body, _ = request(bridge, "/api/command", method="POST",
                             payload={"command": "pause", "value": False})
        assert controls.engine.paused is False
        assert controls.announced == ["resumed"]  # the chord's resume flash
        assert json.loads(body)["paused"] is False
    finally:
        bridge.close()


def test_reading_density_commands():
    controls = FakeControls()
    bridge = ControlBridge(controls, FakeStopEvent(), port=0).start()
    try:
        _, body, _ = request(bridge, "/api/command", method="POST",
                             payload={"command": "space_time", "value": 25})
        assert controls.engine.space_dwell == 0.25
        assert json.loads(body)["space_time"] == 25
        request(bridge, "/api/command", method="POST",
                payload={"command": "punct_time", "value": 50})
        assert controls.engine.punct_dwell == 0.5
        for bad in (0, 101, "fast", True):
            try:
                request(bridge, "/api/command", method="POST",
                        payload={"command": "space_time", "value": bad})
                raise AssertionError(f"space_time accepted {bad!r}")
            except urllib.error.HTTPError as exc:
                assert exc.code == 400
        assert controls.engine.space_dwell == 0.25

        # Lowercase: bare command toggles, an explicit value is idempotent.
        _, body, _ = request(bridge, "/api/command", method="POST",
                             payload={"command": "lowercase"})
        assert controls.engine.lowercase is True
        assert json.loads(body)["lowercase"] is True
        request(bridge, "/api/command", method="POST",
                payload={"command": "lowercase", "value": True})
        assert controls.engine.lowercase is True
        request(bridge, "/api/command", method="POST",
                payload={"command": "lowercase", "value": False})
        assert controls.engine.lowercase is False
        try:
            request(bridge, "/api/command", method="POST",
                    payload={"command": "lowercase", "value": "yes"})
            raise AssertionError("non-boolean lowercase value was accepted")
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
    finally:
        bridge.close()


def test_demo_command_toggles_the_preset_stream():
    controls = FakeControls()
    bridge = ControlBridge(controls, FakeStopEvent(), port=0).start()
    try:
        # No value: toggle the built-in passage; state reflects the feeder.
        status, body, _ = request(
            bridge, "/api/command", method="POST",
            payload={"command": "demo"})
        assert status == 200
        assert json.loads(body)["demo"] is True
        _, body, _ = request(
            bridge, "/api/command", method="POST",
            payload={"command": "demo"})
        assert json.loads(body)["demo"] is False
        # A string value substitutes custom text for the passage.
        request(bridge, "/api/command", method="POST",
                payload={"command": "demo", "value": "Custom demo line."})
        assert controls.demo_toggles == [None, None, "Custom demo line."]
        # Whole documents ride this command now (the panel's News article /
        # Novel / uploaded file sources): a novel-sized value must clear
        # both the command's own cap and the request-body ceiling.
        novel_sized = "All in the golden afternoon full leisurely we glide. " \
            * 3000
        assert len(novel_sized) > 100_000
        request(bridge, "/api/command", method="POST",
                payload={"command": "demo", "value": novel_sized})
        assert controls.demo_toggles[-1] == novel_sized
        for bad in ("", "   ", 7, True, "x" * 1_000_001):
            try:
                request(bridge, "/api/command", method="POST",
                        payload={"command": "demo", "value": bad})
                raise AssertionError(f"demo value {bad!r} was accepted")
            except urllib.error.HTTPError as exc:
                assert exc.code == 400
        assert len(controls.demo_toggles) == 4   # rejects never reached it
        assert controls.demo_timed == [False] * 4
    finally:
        bridge.close()


def test_demo_command_object_value_starts_the_timed_caption_replay():
    controls = FakeControls()
    bridge = ControlBridge(controls, FakeStopEvent(), port=0).start()
    try:
        # An object value with a 'captions' key carries SRT/VTT FILE
        # CONTENT (never a path) and marks the toggle as timed.
        vtt = "WEBVTT\n\n00:01.000 --> 00:02.000\nA timed cue.\n"
        status, body, _ = request(
            bridge, "/api/command", method="POST",
            payload={"command": "demo", "value": {"captions": vtt}})
        assert status == 200
        assert json.loads(body)["demo"] is True
        assert controls.demo_toggles == [vtt]
        assert controls.demo_timed == [True]
        # A bare demo command still stops the timed replay (one toggle).
        _, body, _ = request(bridge, "/api/command", method="POST",
                             payload={"command": "demo"})
        assert json.loads(body)["demo"] is False
        assert controls.demo_timed == [True, False]
        # Malformed objects and unusable caption content answer 400
        # without reaching the toggle.
        for bad_value in ({}, {"captions": ""}, {"captions": "   "},
                          {"captions": 7}, {"captions": "x" * 1_000_001},
                          {"captions": "prose with no cues at all"}):
            try:
                request(bridge, "/api/command", method="POST",
                        payload={"command": "demo", "value": bad_value})
                raise AssertionError(
                    f"demo captions value {bad_value!r} was accepted")
            except urllib.error.HTTPError as exc:
                assert exc.code == 400
                # The 400 body carries the toggle's own words — the panel
                # prints error bodies verbatim, so a caption file with no
                # usable cues must answer with a message a human can act on.
                if bad_value == {"captions": "prose with no cues at all"}:
                    assert "no usable caption cues" in \
                        json.loads(exc.read())["error"]
        assert len(controls.demo_toggles) == 2
    finally:
        bridge.close()


def test_window_command_accepts_any_size_up_to_content_width():
    controls = FakeControls()
    controls.advance_mode = "ticker"   # the window is auto-mode-only config
    bridge = ControlBridge(controls, FakeStopEvent(), port=0).start()
    try:
        status, body, _ = request(
            bridge, "/api/command", method="POST",
            payload={"command": "window", "value": 7})   # not a preset
        assert controls.engine.window == 7
        assert json.loads(body)["window"] == 7
        request(bridge, "/api/command", method="POST",
                payload={"command": "window", "value": 18})   # the ceiling
        assert controls.engine.window == 18
        for bad in (0, 19, 4.5, "4", True, None):
            try:
                request(bridge, "/api/command", method="POST",
                        payload={"command": "window", "value": bad})
                raise AssertionError(f"window value {bad!r} was accepted")
            except urllib.error.HTTPError as exc:
                assert exc.code == 400
        assert controls.engine.window == 18
    finally:
        bridge.close()


def test_window_command_is_ticker_only():
    # Manual always flips the full display (word-wrapped pages), so a
    # window set there would silently change a setting nothing shows — the
    # bridge refuses, matching the w key and Space+W chord.
    controls = FakeControls()
    bridge = ControlBridge(controls, FakeStopEvent(), port=0).start()
    try:
        controls.advance_mode = "manual"
        try:
            request(bridge, "/api/command", method="POST",
                    payload={"command": "window", "value": 4})
            raise AssertionError("window accepted in manual mode")
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
            assert b"auto mode only" in exc.read()
        assert controls.engine.window == 1                # never touched
        controls.advance_mode = "ticker"
        request(bridge, "/api/command", method="POST",
                payload={"command": "window", "value": 4})
        assert controls.engine.window == 4
    finally:
        bridge.close()


def test_window_command_is_rejected_before_a_display_connects():
    """While no display is connected (content_width is None) the window
    edit must be refused: engine.set_window has no ceiling pre-connect,
    so an accepted edit would replace set_window_full()'s armed sentinel
    with a literal that start()'s one-way min() clamp preserves — and
    full-page flipping would never arm on the connecting display."""
    controls = FakeControls()
    controls.advance_mode = "ticker"
    controls.engine.content_width = None   # start() has not sized a display
    controls.engine.window = 10 ** 6       # set_window_full()'s armed sentinel
    bridge = ControlBridge(controls, FakeStopEvent(), port=0).start()
    try:
        try:
            request(bridge, "/api/command", method="POST",
                    payload={"command": "window", "value": 4})
            raise AssertionError("window accepted with no display connected")
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
            assert b"wait for the braille display to connect" in exc.read()
        # The armed sentinel survives, so start() still clamps it to the
        # real width at connect (full-display pages arm as intended).
        assert controls.engine.window == 10 ** 6

        # Connect: the edit goes through again.
        controls.engine.content_width = 18
        controls.engine.window = 18
        request(bridge, "/api/command", method="POST",
                payload={"command": "window", "value": 4})
        assert controls.engine.window == 4
    finally:
        bridge.close()


def test_pre_connect_window_sentinel_is_clamped_in_state():
    """Before a display connects, set_window_full() arms engine.window with
    an oversize sentinel (10**6) that start() clamps at connect. The state
    poll must publish a display-plausible value — clamped to max_window —
    or the panel's "Cells per refresh" input reads "one million" to a
    screen-reader user who switches to auto during a long display wait
    (another screen reader holding the display). Post-connect values must
    pass through untouched."""
    controls = FakeControls()
    controls.engine.content_width = None   # start() has not sized a display
    controls.engine.window = 10 ** 6       # set_window_full()'s armed sentinel
    bridge = ControlBridge(controls, FakeStopEvent(), port=0).start()
    try:
        _, body, _ = request(bridge, "/api/state")
        state = json.loads(body)
        assert state["max_window"] == 40    # the pre-connect fallback ceiling
        assert state["window"] == 40        # clamped: never "one million"
        # Display-only clamp: the engine's armed sentinel is untouched, so
        # start() still gets to clamp it to the REAL width at connect.
        assert controls.engine.window == 10 ** 6

        # Connect: start() clamps the engine's own value to the content
        # width; from here the poll passes real values through untouched.
        controls.engine.content_width = 18
        controls.engine.window = 18         # start()'s clamp at connect
        _, body, _ = request(bridge, "/api/state")
        state = json.loads(body)
        assert state["window"] == 18
        assert state["max_window"] == 18
        controls.engine.window = 7          # an ordinary reader choice
        _, body, _ = request(bridge, "/api/state")
        assert json.loads(body)["window"] == 7
    finally:
        bridge.close()


def test_advance_command_cycles_and_sets():
    controls = FakeControls()
    bridge = ControlBridge(controls, FakeStopEvent(), port=0).start()
    try:
        status, body, _ = request(bridge, "/api/command", method="POST",
                                  payload={"command": "advance"})
        assert controls.advance_mode == "ticker"      # no value: toggle
        state = json.loads(body)
        assert state["advance"] == "ticker"
        # The wire value stays 'ticker'; the published display word is the
        # core authority's rename ('auto') — the panel shows it verbatim.
        assert state["advance_label"] == "auto"
        request(bridge, "/api/command", method="POST",
                payload={"command": "advance", "value": "manual"})
        assert controls.advance_mode == "manual"      # explicit target
        request(bridge, "/api/command", method="POST",
                payload={"command": "advance", "value": "manual"})
        assert controls.advance_mode == "manual"      # idempotent, no cycle
        request(bridge, "/api/command", method="POST",
                payload={"command": "advance", "value": "ticker"})
        assert controls.advance_mode == "ticker"      # the legacy spelling
        try:
            request(bridge, "/api/command", method="POST",
                    payload={"command": "advance", "value": "sideways"})
            raise AssertionError("invalid advance value was accepted")
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
    finally:
        bridge.close()


def test_advance_value_auto_names_the_paced_mode():
    # 'auto' maps to the wire value 'ticker' and leaves the reader's
    # window choice alone.
    controls = FakeControls()
    bridge = ControlBridge(controls, FakeStopEvent(), port=0).start()
    try:
        controls.advance_mode = "manual"
        request(bridge, "/api/command", method="POST",
                payload={"command": "advance", "value": "auto"})
        assert controls.advance_mode == "ticker"
        assert controls.engine.window == 1            # untouched
    finally:
        bridge.close()


def test_mode_command_explicit_target_is_idempotent():
    controls = FakeControls()
    bridge = ControlBridge(controls, FakeStopEvent(), port=0).start()
    try:
        request(bridge, "/api/command", method="POST",
                payload={"command": "mode", "value": "listen"})
        assert controls.mode == "listen"  # already listening: no toggle
        request(bridge, "/api/command", method="POST",
                payload={"command": "mode", "value": "type"})
        assert controls.mode == "type"
        request(bridge, "/api/command", method="POST",
                payload={"command": "mode", "value": "type"})
        assert controls.mode == "type"  # repeat is a no-op, not a toggle
        request(bridge, "/api/command", method="POST", payload={"command": "mode"})
        assert controls.mode == "listen"  # no value keeps toggle semantics
        try:
            request(bridge, "/api/command", method="POST",
                    payload={"command": "mode", "value": "loud"})
            raise AssertionError("invalid mode value was accepted")
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
    finally:
        bridge.close()


def test_relay_counters_reach_state_and_announce_round_trips():
    """Space+dot-6 lands in the ticker as a counter; the panel polls it
    here and answers with an announce flash, so the reader feels the
    outcome on the display."""
    controls = FakeControls()
    bridge = ControlBridge(controls, FakeStopEvent(), port=0).start()
    try:
        controls.mic_toggle_requests = 1
        _, body, _ = request(bridge, "/api/state")
        state = json.loads(body)
        assert "engine_cycle" not in state
        assert state["mic_toggle"] == 1

        request(bridge, "/api/command", method="POST",
                payload={"command": "announce", "value": "deepgram"})
        request(bridge, "/api/command", method="POST",
                payload={"command": "announce", "value": "  mic off "})
        assert controls.announced == ["deepgram", "mic off"]  # trimmed
        for bad in (None, "", "   ", 42, ["mic off"], "x" * 81):
            try:
                request(bridge, "/api/command", method="POST",
                        payload={"command": "announce", "value": bad})
                raise AssertionError(f"announce value {bad!r} was accepted")
            except urllib.error.HTTPError as exc:
                assert exc.code == 400
        assert controls.announced == ["deepgram", "mic off"]

        assert controls.alerted == []      # answers are solicited

        # The alert command is the same channel for the page's
        # UNSOLICITED notices (the idle watchdog's mic-off): never
        # repeated, so they earn the higher dwell ceiling.
        request(bridge, "/api/command", method="POST",
                payload={"command": "alert", "value": " idle - mic off "})
        assert controls.announced[-1] == "idle - mic off"
        assert controls.alerted == ["idle - mic off"]
        for bad in (None, "", "   ", 42, ["x"], "x" * 81):
            try:
                request(bridge, "/api/command", method="POST",
                        payload={"command": "alert", "value": bad})
                raise AssertionError(f"alert value {bad!r} was accepted")
            except urllib.error.HTTPError as exc:
                assert exc.code == 400

    finally:
        bridge.close()


def test_machine_relays_are_not_human_input_for_the_idle_watchdog():
    """Panel commands count as human input for the idle watchdog, but the
    page's own relays must not: 'announce' confirms an outcome, 'alert'
    relays the watchdog's own mic-off notice, 'mic' is a capture label.
    Counting them would let Dotify keep itself awake (the mic-off alert
    would wake the watchdog and resume the mic on the next poll).
    """
    controls = FakeControls()
    bridge = ControlBridge(controls, FakeStopEvent(), port=0).start()
    try:
        for relay, value in (("announce", "deepgram"),
                             ("alert", "idle - mic off"),
                             ("mic", "usb mic")):
            request(bridge, "/api/command", method="POST",
                    payload={"command": relay, "value": value})
        assert controls.touches == 0
        # ...while a real command is exactly what should wake it.
        request(bridge, "/api/command", method="POST",
                payload={"command": "faster"})
        assert controls.touches == 1
    finally:
        bridge.close()


def test_mic_command_sets_the_status_flash_label():
    """the page reports which mic feeds its
    capture ("usb mic" / "bluetooth mic" / "default mic", "" when nothing
    captures); the label lands on the controls for the Space+S status
    flash and echoes in /api/state. Announcing a change is the page's own
    announce command — this channel is the quiet rider."""
    controls = FakeControls()
    bridge = ControlBridge(controls, FakeStopEvent(), port=0).start()
    try:
        request(bridge, "/api/command", method="POST",
                payload={"command": "mic", "value": " usb mic "})
        assert controls.mic_label == "usb mic"   # trimmed
        _, body, _ = request(bridge, "/api/state")
        assert json.loads(body)["mic"] == "usb mic"

        # Empty = capture stopped; the rider clears.
        request(bridge, "/api/command", method="POST",
                payload={"command": "mic", "value": ""})
        assert controls.mic_label == ""

        for bad in (None, 42, ["usb mic"], "x" * 41):
            try:
                request(bridge, "/api/command", method="POST",
                        payload={"command": "mic", "value": bad})
                raise AssertionError(f"mic value {bad!r} was accepted")
            except urllib.error.HTTPError as exc:
                assert exc.code == 400
        assert controls.mic_label == ""
    finally:
        bridge.close()


def test_display_outage_mid_command_answers_503_and_keeps_serving():
    """A display outage during a panel command (the sink boundary raises
    OSError/RuntimeError) must produce a proper HTTP error — same contract
    as the terminal key path — not a traceback that aborts the handler."""

    class OutageControls(FakeControls):
        def jump_to_live(self):
            raise RuntimeError("display sink is not connected")

    controls = OutageControls()
    bridge = ControlBridge(controls, FakeStopEvent(), port=0).start()
    try:
        try:
            request(bridge, "/api/command", method="POST",
                    payload={"command": "live"})
            raise AssertionError("outage command did not error")
        except urllib.error.HTTPError as exc:
            assert exc.code == 503
            assert "reconnecting" in json.loads(exc.read())["error"]

        # The handler survived: the very next request still works.
        status, body, _ = request(bridge, "/api/state")
        assert status == 200
        assert json.loads(body)["connected"] is True
    finally:
        bridge.close()


def test_non_object_json_body_is_rejected_not_a_crash():
    bridge = ControlBridge(FakeControls(), FakeStopEvent(), port=0).start()
    try:
        for bad in (42, "faster", ["faster"]):
            try:
                request(bridge, "/api/command", method="POST", payload=bad)
                raise AssertionError(f"non-object body {bad!r} was accepted")
            except urllib.error.HTTPError as exc:
                assert exc.code == 400
        status, _, _ = request(bridge, "/api/state")
        assert status == 200                 # bridge is still alive
    finally:
        bridge.close()


def test_api_rejects_missing_token_and_untrusted_origin():
    bridge = ControlBridge(FakeControls(), FakeStopEvent(), port=0).start()
    try:
        try:
            request(bridge, "/api/state", authorized=False)
            raise AssertionError("missing token was accepted")
        except urllib.error.HTTPError as exc:
            assert exc.code == 403

        req = urllib.request.Request(
            f"http://127.0.0.1:{bridge.port}/api/state",
            headers={"Origin": "https://example.com", "X-Dotify-Token": bridge.token},
        )
        try:
            urllib.request.urlopen(req, timeout=2)
            raise AssertionError("untrusted origin was accepted")
        except urllib.error.HTTPError as exc:
            assert exc.code == 403
    finally:
        bridge.close()


def test_generated_ui_contains_runtime_token_and_accessible_controls():
    bridge = ControlBridge(FakeControls(), FakeStopEvent(), port=0).start()
    try:
        status, body, _headers = request(
            bridge, "/dotify-controls.js", authorized=False
        )
        text = body.decode("utf-8")
        assert status == 200
        assert bridge.token in text
        assert "__DOTIFY_TOKEN_JSON__" not in text
        # The panel is a labeled region, not a boxed card with a visible
        # heading: the main screen keeps only the
        # braille status and the live actions.
        assert 'aria-label\', \'Braille\'' in text or 'aria-label="Braille"' in text
        # Set-and-forget buttons are bucketed and live
        # in Braille settings since the restyle: Speed holds pace, Reading
        # holds grade/mode/pause, and quit relocates to the last stop on
        # the Settings categories screen.
        assert 'aria-labelledby="dotify-speed-group"' in text
        assert 'aria-labelledby="dotify-reading-group"' in text
        assert "dotify-quit-row" in text
        # The technical display_wait reason lives one disclosure down.
        assert "Connection details" in text
        assert 'role="status"' in text
        assert 'role="alert"' in text
        # Catch up / Summarize split: Catch up is the plain snap
        # ("Go live now" is its fetching/streaming relabel — the press
        # cancels/skips to live), and Summarize is its own control whose
        # phase relabels carry the repeat press's mechanism.
        assert "Catch up" in text
        assert "Jump to live" not in text
        assert "Go live now" in text
        assert "Summarize" in text
        assert 'data-command="summarize"' in text
        assert 'data-shortcut="U"' in text
        assert "Cancel summary" in text
        assert "Skip recap" in text
        assert "Switch grade" in text
        # Verb-first like its row-mates.
        assert "Change reading mode" in text
        # The readout shows the live model; the cell window is a setting.
        assert "dotify-model" in text
        assert 'data-shortcut="R"' in text
        assert "Quit Dotify" in text
        assert "Words stream to the braille display" in text
        assert "IDLE_FLUSH_MS" in text
        assert "dotify-send-text" not in text
        assert 'data-shortcut="F"' in text
        assert 'data-shortcut="S"' in text
        assert 'data-shortcut="G"' in text
        assert 'data-shortcut="L"' in text
        assert 'data-shortcut="P"' in text
        assert "Pause braille" in text
        # No mode button: focusing the compose box drives the mode.
        assert 'data-shortcut="M"' not in text
        assert "Listen / Type mode" not in text
        # The panel watches the Space+dot-6 counter and confirms outcomes
        # back on the display.
        assert "mic_toggle" in text
        assert "'announce'" in text
        assert "Alt+Shift+T" in text
        assert 'data-shortcut="Q"' in text
    finally:
        bridge.close()


def test_state_carries_the_display_wait_reason():
    """while the runner waits for the display (startup
    collision with a screen reader, mid-run outage) the panel needs the
    reason to announce; when the display is back the field must clear."""
    controls = FakeControls()
    bridge = ControlBridge(controls, FakeStopEvent(), port=0).start()
    try:
        controls.engine.display_wait_reason = (
            "NVDA appears to be using the braille display. In NVDA, set "
            "the braille display to 'no braille'.")
        _, body, _ = request(bridge, "/api/state")
        assert "no braille" in json.loads(body)["display_wait"]

        controls.engine.display_wait_reason = None
        _, body, _ = request(bridge, "/api/state")
        assert json.loads(body)["display_wait"] is None
    finally:
        bridge.close()


def beacon(bridge, body):
    # The page's pagehide beacon: navigator.sendBeacon cannot set custom
    # headers, so the token rides the body as text/plain.
    req = urllib.request.Request(
        f"http://127.0.0.1:{bridge.port}/api/page-hidden",
        data=body.encode("utf-8"),
        headers={"Origin": "http://localhost:8788",
                 "Content-Type": "text/plain"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=2) as response:
        return response.status


def test_page_hidden_beacon_quits_unless_a_panel_returns():
    """Closing the panel window quits Dotify: the pagehide beacon arms a
    grace timer, silence concludes the window is gone, and a fresh
    /api/state poll inside the grace (a reload finishing) cancels it."""
    controls = FakeControls()
    stop = FakeStopEvent()
    bridge = ControlBridge(controls, stop, port=0).start()
    bridge.CLOSE_QUIT_GRACE_SECONDS = 0.15
    try:
        # Wrong token: rejected, nothing armed.
        try:
            status = beacon(bridge, "wrong-token")
        except urllib.error.HTTPError as error:
            status = error.code
        assert status == 403
        time.sleep(0.4)
        assert not stop.stopped

        # Beacon followed by a poll inside the grace: a reload, keep running.
        assert beacon(bridge, bridge.token) == 200
        request(bridge, "/api/state")
        time.sleep(0.4)
        assert not stop.stopped
        assert controls.stop is False

        # Beacon with no poll after it: the window is really gone.
        assert beacon(bridge, bridge.token) == 200
        time.sleep(0.4)
        assert stop.stopped
        assert controls.stop is True
    finally:
        bridge.close()


def test_frame_endpoint_serves_the_engine_event():
    """/api/frame is the screen-mirror's 250 ms fast poll: the engine's
    latest frame event verbatim, {} when the engine has none (or predates
    frame events), token-gated like every other API path."""
    controls = FakeControls()
    stop = FakeStopEvent()
    bridge = ControlBridge(controls, stop, port=0).start()
    try:
        # The fake engine has no frame_event: an empty object, never a 500.
        status, body, _headers = request(bridge, "/api/frame")
        assert status == 200
        assert json.loads(body) == {}

        event = {"seq": 7, "kind": "content",
                 "text": "recap words", "source": "", "pending": "more"}
        controls.engine.frame_event = lambda: event
        status, body, _headers = request(bridge, "/api/frame")
        assert status == 200
        assert json.loads(body) == event

        # Unauthorized: same refusal as the other API paths.
        try:
            request(bridge, "/api/frame", authorized=False)
            raise AssertionError("unauthorized frame poll was served")
        except urllib.error.HTTPError as error:
            assert error.code == 403
    finally:
        bridge.close()


def test_keep_alive_serves_two_requests_on_one_connection():
    """The bridge speaks HTTP/1.1 now (the mirror's 250 ms poll would open
    four TCP connections a second on 1.0): two requests down one persistent
    connection must both answer — the Content-Length framing and the
    error-path close_connection discipline keep the stream in sync."""
    import http.client

    controls = FakeControls()
    stop = FakeStopEvent()
    bridge = ControlBridge(controls, stop, port=0).start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", bridge.port, timeout=2)
        for _ in range(2):
            conn.request("GET", "/api/state", headers={
                "Origin": "http://localhost:8788",
                "X-Dotify-Token": bridge.token,
            })
            response = conn.getresponse()
            assert response.status == 200
            assert json.loads(response.read())["connected"] is True
            assert response.version == 11
        conn.close()
    finally:
        bridge.close()


@pytest.mark.skipif(sys.platform != "win32",
                    reason="the shared-port hazard is Windows socket semantics")
def test_a_second_bridge_cannot_share_a_live_bridges_port():
    """A leftover ticker's bridge must make a new one fail to bind instead
    of splitting the panel's requests (and their tokens) between them."""
    first = ControlBridge(FakeControls(), FakeStopEvent(), port=0).start()
    try:
        with pytest.raises(OSError):
            ControlBridge(FakeControls(), FakeStopEvent(),
                          port=first.port).start()
        status, _, _ = request(first, "/api/state")
        assert status == 200
    finally:
        first.close()


def test_a_closed_bridge_port_can_be_reused_at_once():
    """Exclusive binding must not block a quick relaunch: the next session
    binds the same port right after a clean close, with a served request's
    connection still settling."""
    first = ControlBridge(FakeControls(), FakeStopEvent(), port=0).start()
    port = first.port
    request(first, "/api/state")
    first.close()
    second = ControlBridge(FakeControls(), FakeStopEvent(), port=port).start()
    try:
        status, _, _ = request(second, "/api/state")
        assert status == 200
    finally:
        second.close()
