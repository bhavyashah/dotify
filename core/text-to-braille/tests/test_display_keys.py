"""DisplayKeyControls: chord accumulation, dispatch-on-release, mapping.

The map under test: single-dot pairs for
live-conversation commands (1/4 speed, 3/6 pause-output/pause-input,
2/5 reserved), thumbs for navigation, letter chords for settings.
"""

import time

import pytest

from braille_engine.controls import PacerControls
from braille_engine.display_keys import (
    DisplayKeyControls,
    REPLY_CHORD,
    THUMB_LEFT,
    THUMB_NEXT,
    THUMB_PREVIOUS,
    THUMB_RIGHT,
)


class FakeTranslator:
    def __init__(self, grade=2):
        self.grade = grade
        self.calls = []

    def set_grade(self, grade):
        self.calls.append(grade)
        self.grade = grade
        return True


class FakeEngine:
    WINDOW_SIZES = (1, 2, 4, 6, 8)

    def __init__(self):
        self.translator = FakeTranslator()
        self.window = 1
        self.jumps = 0
        self.flashes = []
        self.paused = False
        self.lowercase = False
        self.content_width = 18   # 20-cell display minus the gauge cells
        self.window_is_full = False
        self.pan_offset = 0
        self.pans = []            # ordered (direction, step) calls
        self.ticks = 0            # thumb Right's at-live fast-forward
        self.tick_result = 0      # 0 = caught up (nothing renderable)

    def tick(self, record_cost=True):
        self.ticks += 1
        return self.tick_result

    def pan_back(self, step):
        self.pans.append(("back", step))
        self.pan_offset += step
        return step

    def pan_forward(self, step):
        self.pans.append(("forward", step))
        moved = min(step, self.pan_offset)
        self.pan_offset -= moved
        return moved

    def jump_to_live(self):
        self.jumps += 1

    def set_window(self, window):
        self.window = window
        return True

    def set_paused(self, paused):
        self.paused = bool(paused)

    def set_lowercase(self, on):
        self.lowercase = bool(on)

    def flash(self, text):
        self.flashes.append(text)

    def reply_frame(self, cells):
        pass   # echo rendering is test_reply's territory

    def reply_restore(self):
        pass   # frozen-frame restore on exit is test_reply's territory

    def fit_cells(self, text, budget):
        # One cell per character: chord dispatch is under test here, not
        # UEB cell arithmetic (test_controls covers the composition rules).
        return list(text[:budget]), len(text)


@pytest.fixture()
def rig():
    engine = FakeEngine()
    interval = {"v": 1.0}
    controls = PacerControls(engine, interval)
    # Chord dispatch is under test, and most chords act on the paced
    # (auto — internally "ticker") mode. That is the constructor's default
    # too (pinned in test_controls/test_advance_modes); the explicit set keeps this rig
    # honest about what it needs rather than inheriting it.
    controls.advance_mode = "ticker"
    return engine, interval, controls, DisplayKeyControls(controls)


def press_and_release(dkc, *keys):
    """One clean chord: all keys down together, then all released."""
    dkc.keys_changed(set(keys))
    dkc.keys_changed(set())


# --- the speed pair (index fingers) --------------------------------------

def test_space_dot4_speeds_up(rig):
    _, interval, _, dkc = rig
    press_and_release(dkc, "space", "dot4")
    assert interval["v"] == pytest.approx(0.8)


def test_space_dot1_slows_down(rig):
    _, interval, _, dkc = rig
    press_and_release(dkc, "space", "dot1")
    assert interval["v"] == pytest.approx(1.25)


def test_space_dot4_advances_in_manual_mode(rig):
    engine, interval, controls, dkc = rig
    controls.advance_mode = "manual"
    press_and_release(dkc, "space", "dot4")
    assert controls.take_advance() is True
    assert interval["v"] == pytest.approx(1.0)   # not a speed change


# --- the stop pair (ring fingers: output left, input right) ---------------

def test_space_dot3_toggles_braille_pause(rig):
    engine, _, _, dkc = rig
    press_and_release(dkc, "space", "dot3")
    assert engine.paused is True
    press_and_release(dkc, "space", "dot3")
    assert engine.paused is False


def test_space_dot6_bumps_the_mic_relay_counter(rig):
    engine, _, controls, dkc = rig
    press_and_release(dkc, "space", "dot6")
    press_and_release(dkc, "space", "dot6")
    assert controls.mic_toggle_requests == 2
    # A relay: nothing engine-side may move...
    assert engine.paused is False and engine.jumps == 0
    # ...but the press still answers by touch: the immediate
    # "asked" flash is the only acknowledgment a shell-less run gives,
    # and a shell's own answer flash (mic off / mic on) replaces it.
    assert engine.flashes == ["input pause asked", "input pause asked"]


# --- the reserved pair ----------------------------------------------------

def test_space_dot2_and_dot5_are_reserved_unmapped(rig, capsys):
    # Reserved for future commands, so they must stay free.
    engine, interval, controls, dkc = rig
    press_and_release(dkc, "space", "dot2")
    press_and_release(dkc, "space", "dot5")
    assert interval["v"] == pytest.approx(1.0)
    assert engine.jumps == 0 and engine.paused is False
    assert engine.translator.calls == []
    assert capsys.readouterr().err.count("unmapped:") == 2


# --- thumbs: navigation ---------------------------------------------------

def test_thumb_next_jumps_to_live(rig):
    engine, _, _, dkc = rig
    press_and_release(dkc, THUMB_NEXT)
    assert engine.jumps == 1


def test_thumb_next_snaps_plain_even_with_a_summarizer(rig):
    # The jump key is the plain single-press snap — the summary
    # machinery lives on Space+S, so a configured summarizer must never
    # see a thumb Next press (no fetch hold, no LLM round trip).
    engine, _, controls, dkc = rig

    class ExplodingSummarizer:
        def summarize(self, text, chars, grade):
            raise AssertionError("thumb Next must not summarize")

    controls.summarizer = ExplodingSummarizer()
    press_and_release(dkc, THUMB_NEXT)
    assert engine.jumps == 1
    assert controls.catchup is None


def test_thumb_previous_toggles_advance_mode(rig):
    engine, interval, controls, dkc = rig
    press_and_release(dkc, THUMB_PREVIOUS)
    assert controls.advance_mode == "manual"
    press_and_release(dkc, THUMB_PREVIOUS)
    assert controls.advance_mode == "ticker"
    # Each switch announced itself on the display (the paced mode under
    # its user-facing name); nothing else moved.
    assert engine.flashes == ["manual", "auto"]
    assert engine.window == 1 and engine.jumps == 0
    assert interval["v"] == pytest.approx(1.0)


def test_thumb_right_pans_forward_in_manual_mode(rig):
    engine, interval, controls, dkc = rig
    controls.advance_mode = "manual"
    press_and_release(dkc, THUMB_RIGHT)
    assert controls.take_advance() is True
    assert interval["v"] == pytest.approx(1.0)   # never a speed change


def test_thumb_right_at_live_fast_forwards_the_backlog_in_ticker(rig):
    # At the live edge with backlog queued (a half-full gauge), thumb
    # Right pulls one extra tick's worth forward NOW — it must never
    # just flash "live" at a reader who is demonstrably behind, and must
    # never change the pace.
    engine, interval, controls, dkc = rig
    engine.tick_result = 4                # a window's worth was pending
    press_and_release(dkc, THUMB_RIGHT)
    assert engine.ticks == 1
    assert engine.flashes == []           # it advanced; nothing to excuse
    assert interval["v"] == pytest.approx(1.0)
    assert controls.take_advance() is False
    assert engine.pans == []


def test_thumb_right_when_truly_caught_up_answers_live(rig):
    # Only a reader with NO renderable backlog gets the honest "live"
    # answer — the press must still be feelable, never silent.
    engine, interval, controls, dkc = rig
    press_and_release(dkc, THUMB_RIGHT)
    assert engine.ticks == 1
    assert engine.flashes == ["live"]
    assert interval["v"] == pytest.approx(1.0)


def test_thumb_left_pans_back_by_the_mode_step(rig):
    # Ticker: the step is the cell window; manual: a full content page.
    engine, interval, controls, dkc = rig
    engine.window = 4
    press_and_release(dkc, THUMB_LEFT)
    controls.advance_mode = "manual"
    press_and_release(dkc, THUMB_LEFT)
    assert engine.pans == [("back", 4), ("back", engine.content_width)]
    assert interval["v"] == pytest.approx(1.0)
    assert controls.take_advance() is False


def test_thumb_right_pans_a_panned_view_forward_to_live(rig):
    engine, _interval, controls, dkc = rig
    engine.window = 4
    press_and_release(dkc, THUMB_LEFT)      # offset 4
    press_and_release(dkc, THUMB_RIGHT)     # back to live
    assert engine.pans == [("back", 4), ("forward", 4)]
    assert engine.pan_offset == 0
    assert engine.flashes == ["live"]       # the landing announces itself
    # The NEXT press is a plain at-live press again (no advance in ticker).
    press_and_release(dkc, THUMB_RIGHT)
    assert engine.pans == [("back", 4), ("forward", 4)]


# --- letter chords: settings ---------------------------------------------

def test_space_g_toggles_grade_and_announces(rig):
    engine, _, _, dkc = rig
    # The chord cycles 1 -> 2 -> experimental English 3 -> 1, starting
    # from the default grade 2.
    press_and_release(dkc, "space", "dot1", "dot2", "dot4", "dot5")
    assert engine.translator.grade == 3
    press_and_release(dkc, "space", "dot1", "dot2", "dot4", "dot5")
    assert engine.translator.grade == 1
    # The toggle is confirmable by touch: each flip names its landing.
    assert engine.flashes == ["grade 3", "grade 1"]


def test_space_w_chord_cycles_window(rig):
    engine, _, _, dkc = rig
    press_and_release(dkc, "space", "dot2", "dot4", "dot5", "dot6")
    assert engine.window == 2


def test_space_i_chord_flashes_the_composed_status(rig):
    # The status flash lives on Space+I (dots 2-4, mnemonic Info); S is
    # summarize-on-demand.
    # Interval 1.0 s/cell -> "13wpm" (grade-2 estimate), window 1, grade 2;
    # 16 chars fit the fake's 18-cell width, so the long label and the
    # grade both make it.
    engine, _, _, dkc = rig
    press_and_release(dkc, "space", "dot2", "dot4")
    assert engine.flashes == ["auto c1 13wpm g2"]


def test_space_s_chord_dispatches_summarize_not_status():
    # Space+S (dots 2-3-4) is Summarize-on-demand. Dispatch
    # only — the recap machinery itself is test_controls territory — so
    # the bound method is replaced BEFORE the table captures it.
    engine = FakeEngine()
    controls = PacerControls(engine, {"v": 1.0})
    calls = []
    controls.summarize_now = lambda: calls.append(True)
    dkc = DisplayKeyControls(controls)
    press_and_release(dkc, "space", "dot2", "dot3", "dot4")
    assert calls == [True]
    assert engine.flashes == []   # no status flash from the S chord


def test_space_s_without_a_summarizer_degrades_to_the_plain_snap(rig):
    # Every failure path degrades to the snap — including "no summarizer
    # configured at all" (file replay, --no-summary).
    engine, _, controls, dkc = rig
    press_and_release(dkc, "space", "dot2", "dot3", "dot4")
    assert engine.jumps == 1
    assert controls.catchup is None


def test_space_dots_1_4_is_deliberately_unmapped(rig):
    # Dots 1+4 are the exact union of the slower/faster single-dot pair,
    # so two overlapping speed presses land on Space+dots 1-4; any binding
    # there (e.g. a lowercase toggle) would silently flip a setting. The
    # combo must stay a logged no-op: no setting changes, no flash, no
    # pace change.
    engine, interval, _, dkc = rig
    press_and_release(dkc, "space", "dot1", "dot4")
    assert engine.lowercase is False
    assert engine.flashes == []
    assert interval["v"] == pytest.approx(1.0)


def test_space_t_chord_toggles_the_demo_stream():
    # The demo feeder itself is test_demo's territory; here only the chord
    # dispatch is under test, so the bound method is replaced BEFORE the
    # dispatch table captures it.
    engine = FakeEngine()
    controls = PacerControls(engine, {"v": 1.0})
    toggles = []
    controls.toggle_demo = lambda: toggles.append(True)
    dkc = DisplayKeyControls(controls)
    press_and_release(dkc, "space", "dot2", "dot3", "dot4", "dot5")
    assert toggles == [True]


# --- unmapped chords -------------------------------------------------------

def test_unassigned_letter_chords_are_unmapped(rig, capsys):
    # Space+P (pause is Space+dot-3), Space+E, Space+F, Space+L and
    # Space+M (dots 1-3-4) are unmapped and must land in the discovery log
    # as plain unmapped combos: no relay counter, no flash, nothing runs.
    engine, interval, controls, dkc = rig
    press_and_release(dkc, "space", "dot1", "dot2", "dot3", "dot4")   # P
    press_and_release(dkc, "space", "dot1", "dot5")                   # E
    press_and_release(dkc, "space", "dot1", "dot2", "dot4")           # F
    press_and_release(dkc, "space", "dot1", "dot2", "dot3")           # L
    press_and_release(dkc, "space", "dot1", "dot3", "dot4")           # M
    assert engine.paused is False
    assert controls.mic_toggle_requests == 0
    assert engine.flashes == []
    assert interval["v"] == pytest.approx(1.0)
    assert engine.jumps == 0
    assert capsys.readouterr().err.count("unmapped:") == 5


def test_recording_has_no_display_chord_and_space_r_is_reserved(rig):
    # Session recording is a buried developer setting, never a display
    # command. Space+R is handled before this table as the reply toggle
    # (the only way in and out), so it cannot collide with a
    # relay now or after an innocent map refactor.
    _, _, controls, dkc = rig
    assert REPLY_CHORD not in dkc._map
    assert all("record" not in name.lower() for name, _ in dkc._map.values())
    press_and_release(dkc, *REPLY_CHORD)      # enters reply mode...
    assert controls.reply_active is True
    assert controls.mic_toggle_requests == 0   # ...and runs nothing else
    press_and_release(dkc, *REPLY_CHORD)      # ...and the same chord exits
    assert controls.reply_active is False


def test_reply_mode_is_built_in_and_space_r_enters_without_opt_in(capsys):
    # Reply mode is a feature, not a setting: a fresh PacerControls accepts
    # Space+R without a shell or command-line opt-in.
    engine = FakeEngine()
    controls = PacerControls(engine, {"v": 1.0})
    dkc = DisplayKeyControls(controls)
    press_and_release(dkc, *REPLY_CHORD)
    assert controls.reply_active is True
    assert controls.reply.state()["session"] == 1
    assert "unmapped:" not in capsys.readouterr().err


# --- chord mechanics ------------------------------------------------------

def test_chord_fires_once_on_full_release_only(rig):
    engine, _, _, dkc = rig
    dkc.keys_changed({"space"})
    dkc.keys_changed({"space", "dot2", "dot4", "dot5", "dot6"})
    dkc.keys_changed({"space"})           # dots released first: nothing yet
    assert engine.window == 1
    dkc.keys_changed(set())               # last key up: fires exactly once
    assert engine.window == 2
    dkc.keys_changed(set())               # idle repeats never re-fire
    assert engine.window == 2


def test_staggered_presses_accumulate_into_one_chord(rig):
    engine, interval, _, dkc = rig
    dkc.keys_changed({"space"})
    dkc.keys_changed({"space", "dot1"})
    dkc.keys_changed({"space", "dot1", "dot2"})
    dkc.keys_changed(set())
    # Union {space,dot1,dot2} is unmapped — one unknown combo, not
    # "slower" followed by anything else.
    assert interval["v"] == pytest.approx(1.0)
    assert engine.translator.calls == []


def test_unmapped_combo_is_logged_not_raised(rig, capsys):
    _, _, _, dkc = rig
    press_and_release(dkc, "routing:5")
    assert "unmapped: routing:5" in capsys.readouterr().err


def test_mapped_combo_logs_action_name(rig, capsys):
    _, _, _, dkc = rig
    press_and_release(dkc, THUMB_NEXT)
    err = capsys.readouterr().err
    assert "thumb_next -> jump to live" in err


# --- dismiss-and-execute --------------------------------------------------
# A completed MEANINGFUL combo ends an active announce dwell (the reader
# pressed keys that do something, so the message under their fingers is
# acknowledged) and still runs its own meaning — the key is never spent
# on the dismissal. The pacer owns the repaint (test_advance_modes pins
# that side); only the deadline moves here.

def test_any_combo_dismisses_an_active_announce_dwell(rig):
    # The speed chord dismisses the stale dwell, still executes, and (since
    # every speed press announces now) claims a fresh dwell of its own.
    _, interval, controls, dkc = rig
    stale = time.monotonic() + 60.0                  # distinctly far out
    controls.announce_until = stale
    press_and_release(dkc, "space", "dot4")
    assert 0.0 < controls.announce_until < stale     # dismissed, re-claimed
    assert interval["v"] == pytest.approx(0.8)       # ...and still executed


def test_unmapped_combo_does_not_dismiss(rig, capsys):
    # Routing keys sit right against the cells: a stray brush while
    # READING the flash must not wipe it. Unmapped stays pure discovery.
    _, _, controls, dkc = rig
    deadline = time.monotonic() + 5.0
    controls.announce_until = deadline
    press_and_release(dkc, "routing:5")
    assert controls.announce_until == deadline
    assert "unmapped: routing:5" in capsys.readouterr().err


def test_keys_still_held_do_not_dismiss(rig):
    # A chord in the making must not acknowledge anything: the flash
    # keeps showing until the combo completes on full release.
    _, _, controls, dkc = rig
    deadline = time.monotonic() + 60.0               # distinctly far out
    controls.announce_until = deadline
    dkc.keys_changed({"space"})
    dkc.keys_changed({"space", "dot4"})
    assert controls.announce_until == deadline
    dkc.keys_changed(set())
    # Full release fires the speed press, which dismisses the stale dwell
    # and (announcing its own result now) claims a fresh, shorter one.
    assert 0.0 < controls.announce_until < deadline


def test_announcing_chord_replaces_the_dismissed_dwell(rig):
    # Space+G during someone else's dwell: the old dwell dies, the grade
    # flip lands, and the flip's own announcement claims a fresh dwell.
    engine, _, controls, dkc = rig
    stale = time.monotonic() + 60.0                  # distinctly far out
    controls.announce_until = stale
    press_and_release(dkc, "space", "dot1", "dot2", "dot4", "dot5")
    assert engine.flashes == ["grade 3"]
    assert 0.0 < controls.announce_until < stale     # a fresh dwell


def test_reserved_pair_does_not_dismiss(rig):
    # Space+dot-2/dot-5 are reserved unmapped (transcript rewind's likely
    # home) — and dots 1+4 is the speed pair's collision shadow. None of
    # them DOES anything, so none may wipe a message being read.
    _, _, controls, dkc = rig
    deadline = time.monotonic() + 5.0
    controls.announce_until = deadline
    press_and_release(dkc, "space", "dot2")
    press_and_release(dkc, "space", "dot5")
    press_and_release(dkc, "space", "dot1", "dot4")
    assert controls.announce_until == deadline


def test_refused_chord_mid_reply_still_dismisses(rig):
    # Mid-reply every non-typing combo is refused by name — but it is
    # still a deliberate press, so it acknowledges an announcement.
    _, _, controls, dkc = rig
    press_and_release(dkc, *REPLY_CHORD)         # Space+R enters reply mode
    assert controls.reply_active is True
    deadline = time.monotonic() + 5.0
    controls.announce_until = deadline
    press_and_release(dkc, THUMB_NEXT)           # refused, still a press
    assert controls.announce_until == 0.0
    assert controls.reply_active is True


def test_reply_entry_chord_dismisses_before_the_echo_takes_over(rig):
    # Space+R entering reply mode is a meaningful combo: it dismisses like
    # every command, and the reply's echo frame owns the display anyway.
    _, _, controls, dkc = rig
    controls.announce_until = time.monotonic() + 5.0
    press_and_release(dkc, *REPLY_CHORD)
    assert controls.announce_until == 0.0
    assert controls.reply_active is True


# --- dismiss agrees with dispatch, by table --------------------------------
# The dismissal is derived from the dispatch (each branch that gives a
# combo a meaning acknowledges the flash; unmapped fall-through never
# does), so there is no separate guard to drift. This table pins the
# derived behavior for representative combos in each mode state, so any
# future restructuring of the dispatch keeps answering the same way.

DISMISS_TABLE = [
    # (mode state, keys, dismisses an active flash?)
    ("reading", ("space", "dot4"), True),           # mapped command
    ("reading", ("space", "dot2"), False),          # reserved unmapped
    ("reading", ("routing:3",), False),             # routing-key brush
    ("reading", ("dot1", "dot2", "dot5"), False),   # bare dots
    ("reading", ("dot8",), False),                  # bare Enter: unmapped
    ("reading", tuple(REPLY_CHORD), True),          # built-in reply entry
    ("reply", ("dot1",), True),                     # typing
    ("reply", ("space",), True),                    # word break
    ("reply", ("dot8",), True),                     # commit the sentence
    ("reply", (THUMB_NEXT,), True),                 # refused by name
    ("reply", tuple(REPLY_CHORD), True),            # the way out
]


@pytest.mark.parametrize("state,keys,dismisses", DISMISS_TABLE,
                         ids=[f"{s}:{'+'.join(sorted(k))}"
                              for s, k, _ in DISMISS_TABLE])
def test_dismiss_agrees_with_dispatch(rig, state, keys, dismisses):
    _, _, controls, dkc = rig
    if state == "reply":
        press_and_release(dkc, *REPLY_CHORD)
        assert controls.reply_active is True
    deadline = time.monotonic() + 60.0               # distinctly far out
    controls.announce_until = deadline
    press_and_release(dkc, *keys)
    if dismisses:
        # Dismissed — the combo's own action may claim a fresh (shorter)
        # dwell right after, so "moved off the stale deadline" is the test.
        assert controls.announce_until < deadline
    else:
        assert controls.announce_until == deadline


# --- bare dots while reading (no implicit reply entry) ---------------------

def test_bare_dots_while_reading_do_nothing_and_still_log(rig, capsys):
    # An implicit reply entry on ANY bare dot combo would freeze the
    # stream and start swallowing keys as typing — too sensitive for a
    # resting finger or a brushed key. Bare dots while reading are
    # unmapped, and the discovery survey keeps recording them.
    engine, interval, controls, dkc = rig
    press_and_release(dkc, "dot1")
    press_and_release(dkc, "dot1", "dot2", "dot5")
    assert controls.reply_active is False
    assert controls.reply.state()["session"] == 0    # no session ever began
    assert interval["v"] == pytest.approx(1.0)
    assert engine.paused is False and engine.jumps == 0
    err = capsys.readouterr().err
    assert "unmapped: dot1\n" in err
    assert "unmapped: dot1+dot2+dot5" in err


def test_bare_dots_while_reading_do_not_dismiss_a_flash(rig):
    # A brushed dot key is exactly as harmless as a routing-key
    # brush: unmapped, so it must never wipe a message being read.
    _, _, controls, dkc = rig
    deadline = time.monotonic() + 5.0
    controls.announce_until = deadline
    press_and_release(dkc, "dot1", "dot2", "dot5")
    assert controls.announce_until == deadline
    assert controls.reply_active is False


def test_display_write_failure_never_kills_the_key_thread(rig):
    # A pan (or fast-forward) writes frames from the key thread; a display
    # outage there must be swallowed with a status line — an uncaught
    # raise would kill the transport's reader thread.
    engine, _, controls, dkc = rig

    def explode(step):
        raise OSError("display gone")

    engine.pan_back = explode
    press_and_release(dkc, THUMB_LEFT)     # must not raise
    press_and_release(dkc, THUMB_LEFT)     # thread still alive and mapped


def test_reset_forgets_keys_held_when_the_transport_died(rig):
    engine, _, _, dkc = rig
    # Space was down when the connection dropped: no release ever arrives.
    dkc.keys_changed({"space"})
    dkc.reset()
    press_and_release(dkc, THUMB_NEXT)
    assert engine.jumps == 1      # thumb Next alone, not space+thumb_next
