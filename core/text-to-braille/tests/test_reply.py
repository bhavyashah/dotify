"""Reply channel (Space+R is the only entry): composer state machine,
display-key routing, back-translation, pacer freeze flag, and the echo
frames."""

import time

import pytest

from braille_engine.cells import BLANK, dots_to_pattern
from braille_engine.controls import PacerControls
from braille_engine.display_keys import DisplayKeyControls
from braille_engine.engine import REPLY_MARKER, BrailleEngine
from braille_engine.gauge import BacklogGauge
from braille_engine.translator import DevUebTranslator


class RecordingSink:
    def __init__(self, width=20):
        self.width = width
        self.frames = []

    def connect(self):
        return self.width

    def write(self, cells):
        self.frames.append(list(cells))

    def close(self):
        pass


@pytest.fixture()
def rig():
    engine = BrailleEngine(DevUebTranslator(), RecordingSink(),
                           gauge=BacklogGauge())
    engine.start()
    controls = PacerControls(engine, {"v": 0.05})
    return engine, controls, DisplayKeyControls(controls)


def press(dkc, *keys):
    dkc.keys_changed(set(keys))
    dkc.keys_changed(set())


# Space+R (dots 1-2-3-5): the reply toggle — the only way in and out
# (there is no implicit bare-dot entry).
REPLY_TOGGLE = ("space", "dot1", "dot2", "dot3", "dot5")


def enter_reply(dkc):
    press(dkc, *REPLY_TOGGLE)


def type_word(dkc, *patterns):
    """Each pattern is a dot string like "125" (h)."""
    for dots in patterns:
        press(dkc, *(f"dot{d}" for d in dots))


# --- dev translator back-translation --------------------------------------

def test_dev_back_translate_letters_and_punctuation():
    tr = DevUebTranslator()
    cells = [dots_to_pattern(d) for d in ("125", "15", "245", "245", "135")]
    assert tr.back_translate(cells) == "hejjo"
    assert tr.back_translate([dots_to_pattern("125"),
                              dots_to_pattern("256")]) == "h."


def test_dev_back_translate_roundtrips_its_own_output():
    # No digit-then-letter words: the dev stand-in emits no grade-1
    # terminator after numbers, so "1b" is genuinely ambiguous with "12" —
    # liblouis handles that case; the dev fallback doesn't claim to.
    tr = DevUebTranslator()
    for word in ("hello", "Dot", "42", "it's", "ready?"):
        assert tr.back_translate(tr.translate(word)) == word


# --- entering / typing / word commit --------------------------------------

def test_space_r_enters_reply_and_freezes_streaming(rig):
    engine, controls, dkc = rig
    assert controls.reply_active is False
    enter_reply(dkc)                       # Space+R: the only way in
    assert controls.reply_active is True
    state = controls.reply.state()
    assert state["active"] is True and state["session"] == 1
    assert state["text"] == ""             # nothing typed yet


def test_bare_dots_while_reading_do_not_enter_reply(rig, capsys):
    # No implicit bare-dot entry — a resting finger or a brushed key must
    # not freeze the stream and start swallowing keys as typing. Bare dots
    # while reading are unmapped no-ops,
    # still logged for the key-event survey.
    engine, controls, dkc = rig
    frames_before = len(engine.sink.frames)
    press(dkc, "dot1", "dot2", "dot5")     # h — bare dots, no space
    assert controls.reply_active is False
    assert controls.reply.state()["session"] == 0
    assert len(engine.sink.frames) == frames_before   # no echo frame
    assert "unmapped: dot1+dot2+dot5" in capsys.readouterr().err


def test_space_commits_a_back_translated_word(rig):
    engine, controls, dkc = rig
    enter_reply(dkc)
    type_word(dkc, "125", "24")            # h i
    press(dkc, "space")
    state = controls.reply.state()
    assert state["text"] == "hi"
    assert state["sentences"] == []        # no terminal punctuation yet


def test_sentence_closes_on_terminal_punctuation(rig):
    engine, controls, dkc = rig
    enter_reply(dkc)
    type_word(dkc, "125", "24", "256")     # h i .
    press(dkc, "space")
    type_word(dkc, "1345", "135", "2456", "256")   # n o w .
    press(dkc, "space")
    state = controls.reply.state()
    assert state["text"] == "hi. now."
    assert state["sentences"] == ["hi.", "now."]


def test_dot8_speaks_the_sentence_without_punctuation(rig):
    engine, controls, dkc = rig
    enter_reply(dkc)
    type_word(dkc, "135", "13")            # o k
    press(dkc, "dot8")                     # Perkins Enter
    state = controls.reply.state()
    assert state["sentences"] == ["ok"]


def test_dot7_erases_the_last_typed_cell(rig):
    engine, controls, dkc = rig
    enter_reply(dkc)
    type_word(dkc, "135", "1346")          # o x
    press(dkc, "dot7")                     # backspace the x
    type_word(dkc, "13")                   # k
    press(dkc, "space")
    assert controls.reply.state()["text"] == "ok"


def test_exit_flushes_word_and_speaks_the_remainder(rig):
    engine, controls, dkc = rig
    enter_reply(dkc)
    type_word(dkc, "135", "13")            # o k (no space, no punctuation)
    press(dkc, *REPLY_TOGGLE)              # Space+R again: exit
    state = controls.reply.state()
    assert controls.reply_active is False
    assert state["active"] is False
    assert state["text"] == "ok"
    assert state["sentences"] == ["ok"]    # exit closes the open sentence


def test_reply_chord_toggles_and_runs_no_other_command(rig):
    engine, controls, dkc = rig
    press(dkc, *REPLY_TOGGLE)              # in
    assert controls.reply_active is True
    assert controls.mic_toggle_requests == 0
    press(dkc, "dot1")                     # typing lands in THIS session
    assert controls.reply.state()["session"] == 1
    press(dkc, *REPLY_TOGGLE)              # out
    assert controls.reply_active is False
    assert controls.mic_toggle_requests == 0


# --- command isolation ------------------------------------------------------

def test_commands_are_refused_while_replying(rig, capsys):
    engine, controls, dkc = rig
    enter_reply(dkc)
    interval_before = controls.interval["v"]
    press(dkc, "thumb_right")              # would be faster
    press(dkc, "space", "dot1", "dot2", "dot3", "dot4")   # would be pause
    assert controls.interval["v"] == interval_before
    assert engine.paused is False
    assert capsys.readouterr().err.count("ignored (Space+R") == 2


def test_grade_chord_still_works_outside_reply(rig):
    engine, controls, dkc = rig
    press(dkc, "space", "dot1")
    assert engine.translator.grade == 1    # command map untouched


def test_refused_chord_answers_on_the_display(rig):
    # A stderr-only refusal would leave the deaf-blind typist with no
    # perceivable way out. The line must answer
    # with the exit hint, at the current grade, with the r marker intact.
    engine, controls, dkc = rig
    enter_reply(dkc)
    press(dkc, "dot1", "dot2", "dot5")     # typing (h)
    echo = engine.sink.frames[-1]
    press(dkc, "space", "dot1", "dot2", "dot5")   # Space+H mid-reply
    hint = engine.sink.frames[-1]
    assert hint != echo
    assert hint[1] == REPLY_MARKER
    expected, _ = engine.fit_cells(controls.reply.HINT_TEXT,
                                   engine.content_width)
    assert hint[2:2 + len(expected)] == expected


def test_typing_returns_the_echo_after_a_hint(rig):
    engine, controls, dkc = rig
    enter_reply(dkc)
    press(dkc, "dot1")                     # typing (a)
    press(dkc, "thumb_right")              # refused: hint takes the line
    press(dkc, "dot1", "dot2")             # more typing (b)
    frame = engine.sink.frames[-1]
    assert frame[2] == dots_to_pattern("1")
    assert frame[3] == dots_to_pattern("12")


def test_hint_timer_restores_the_echo(rig):
    engine, controls, dkc = rig
    enter_reply(dkc)
    press(dkc, "dot1")
    press(dkc, "thumb_right")              # hint painted, timer armed
    hint = engine.sink.frames[-1]
    controls.reply._end_hint(controls.reply._hint_seq)   # the timer's call
    assert engine.sink.frames[-1] != hint
    assert engine.sink.frames[-1][2] == dots_to_pattern("1")


def test_stale_hint_timer_never_overwrites_the_reading_frame(rig):
    engine, controls, dkc = rig
    engine.feed("abc ")
    engine.tick()
    enter_reply(dkc)
    press(dkc, "dot1")
    press(dkc, "thumb_right")              # hint + timer
    seq = controls.reply._hint_seq
    controls.end_reply()                   # reading frame restored
    frames_after_exit = len(engine.sink.frames)
    controls.reply._end_hint(seq)          # late timer: must be a no-op
    assert len(engine.sink.frames) == frames_after_exit


# --- echo frames ------------------------------------------------------------

def test_echo_frame_shows_typed_cells_with_reply_marker(rig):
    engine, controls, dkc = rig
    enter_reply(dkc)
    press(dkc, "dot1", "dot2", "dot5")     # h
    frame = engine.sink.frames[-1]
    assert frame[1] == REPLY_MARKER
    assert frame[2] == dots_to_pattern("125")
    assert frame[3:] == [BLANK] * (engine.width - 3)


def test_exit_restores_the_reading_frame(rig):
    engine, controls, dkc = rig
    engine.feed("abc ")
    engine.tick()
    reading = engine.sink.frames[-1]
    enter_reply(dkc)
    press(dkc, "dot1")
    assert engine.sink.frames[-1] != reading    # echo frame took the line
    controls.end_reply()
    # reply_restore rewrote the frozen reading frame (announce may flash
    # after it; find the restore among the trailing writes).
    assert reading in engine.sink.frames[-2:]


def test_backspace_on_empty_word_is_harmless(rig):
    engine, controls, dkc = rig
    enter_reply(dkc)
    press(dkc, "dot1")                     # one cell (a)
    press(dkc, "space")                    # commit
    press(dkc, "dot7")                     # nothing left in the word
    assert controls.reply.state()["text"] == "a"


def test_state_carries_final_text_until_next_session(rig):
    engine, controls, dkc = rig
    enter_reply(dkc)
    type_word(dkc, "125", "24")            # h i
    press(dkc, "dot8")
    controls.end_reply()
    state = controls.reply.state()
    assert state == {"active": False, "session": 1, "seq": state["seq"],
                     "text": "hi", "sentences": ["hi"]}


# --- remote announces vs the reply echo -------------------------------------
# Chords cannot reach announce() mid-reply (display_keys reroutes every
# mapped combo to the reply dispatch), but a remote surface — the
# Windows panel bridge — rides the same PacerControls entry points and
# CAN land mid-reply. The echo frame owns the display and
# the pace loop idles on reply_active before any dwell/repaint handling,
# so an unguarded flash would bury the typist's half-composed line until
# the next keypress. Only the flash is suppressed: the state change (and
# the status line) must still apply.


def test_remote_resume_mid_reply_changes_state_without_flashing(rig):
    engine, controls, dkc = rig
    assert controls.set_paused(True) is True     # remote pause: silent
    enter_reply(dkc)
    press(dkc, "dot1")                           # echo owns the line
    echo = engine.sink.frames[-1]
    frames = len(engine.sink.frames)
    assert controls.set_paused(False) is True    # remote resume mid-reply
    assert engine.paused is False                # the state change applies
    assert len(engine.sink.frames) == frames     # ...but nothing flashed
    assert engine.sink.frames[-1] == echo        # echo still under fingers


def test_set_paused_is_idempotent_never_a_double_toggle(rig):
    engine, controls, dkc = rig
    assert controls.set_paused(True) is True
    assert controls.set_paused(True) is False    # stale press: no re-toggle
    assert engine.paused is True
    assert controls.set_paused(False) is True
    assert controls.set_paused(False) is False
    assert engine.paused is False


def test_any_remote_announce_mid_reply_is_suppressed(rig):
    # The guard is general (announce()), not resume-specific: a panel
    # grade change or mic confirmation mid-reply has the same clobber
    # problem as the resume flash.
    engine, controls, dkc = rig
    enter_reply(dkc)
    press(dkc, "dot1")
    frames = len(engine.sink.frames)
    controls.announce("grade 2")
    assert len(engine.sink.frames) == frames     # no frame written
    assert controls.announce_until == 0.0        # no dwell claimed either


def test_remote_advance_change_mid_reply_applies_without_flashing(rig):
    # set_advance_mode's confirmation flash goes through announce(), whose
    # reply guard suppresses it; the switch itself still applies.
    engine, controls, dkc = rig
    enter_reply(dkc)
    press(dkc, "dot1")
    frames = len(engine.sink.frames)
    controls.set_advance_mode("ticker")
    assert controls.advance_mode == "ticker"     # the switch applies
    assert len(engine.sink.frames) == frames     # no flash over the echo


def test_reply_sent_confirmation_still_flashes_on_exit(rig):
    # The reply flow's own confirmation must keep working: end_reply
    # announces AFTER reply.exit() flips the flag, so the guard never
    # touches it.
    engine, controls, dkc = rig
    enter_reply(dkc)
    press(dkc, "dot1")
    controls.end_reply()
    expected, _ = engine.fit_cells("reply sent", engine.content_width)
    flash = engine.sink.frames[-1]
    assert flash[-len(expected):] == expected    # the right-aligned flash
    assert time.monotonic() < controls.announce_until   # dwell claimed


def test_remote_resume_outside_reply_still_flashes(rig):
    # The guard must not dull the ordinary answer: the panel's resume
    # keeps the chord's "resumed" flash when no reply owns the display.
    engine, controls, dkc = rig
    controls.set_paused(True)
    frames = len(engine.sink.frames)
    assert controls.set_paused(False) is True
    assert len(engine.sink.frames) == frames + 1
    expected, _ = engine.fit_cells("resumed", engine.content_width)
    assert engine.sink.frames[-1][-len(expected):] == expected
