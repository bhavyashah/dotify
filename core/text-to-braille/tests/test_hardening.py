"""Regression tests for three hardening areas:

1. Human-mode lifecycle — the pipeline must not exit, and source EOF must not
   commit the typist's partial word, while the reader is in HUMAN mode.
2. Engine thread safety — the keyboard thread and the pacer mutate shared
   engine state; mutations must serialize on the engine lock.
3. read_key parsing — ESC drains must consume exactly one escape sequence
   (not buffered type-ahead/pastes), and Windows must not misread a typed
   '\xe0' (the character 'a-grave') as an arrow-key prefix.
"""

import asyncio
import sys
import threading

import pytest

from braille_engine.controls import (
    PacerControls, MODE_TYPE, _consume_escape, _win_read_key,
)
from braille_engine.engine import BrailleEngine
from braille_engine.sinks.simulated import SimulatedSink
from braille_engine.translator import DevUebTranslator
from run import run_pipeline


async def _list_source(items):
    for it in items:
        yield it


def make_engine(width=8):
    eng = BrailleEngine(DevUebTranslator(),
                        SimulatedSink(width=width, echo=False))
    eng.start()
    return eng


# --- 1. Human-mode lifecycle --------------------------------------------------

def test_pipeline_survives_source_eof_in_type_mode():
    # The source draining must not end a TYPE session: the reader may still
    # be composing, with nothing pending in the engine between typed words.
    engine = make_engine()
    interval = {"v": 0.0}
    stop = threading.Event()
    controls = PacerControls(engine, interval)
    controls.mode = MODE_TYPE

    async def scenario():
        task = asyncio.ensure_future(run_pipeline(
            _list_source([]), engine, interval, stop, controls))
        await asyncio.sleep(0.1)
        alive = not task.done()
        stop.set()
        await asyncio.wait_for(task, 2)
        return alive

    assert asyncio.run(scenario()) is True


def test_pipeline_exits_after_return_to_listen():
    engine = make_engine()
    interval = {"v": 0.0}
    stop = threading.Event()
    controls = PacerControls(engine, interval)
    controls.mode = MODE_TYPE

    async def scenario():
        task = asyncio.ensure_future(run_pipeline(
            _list_source([]), engine, interval, stop, controls))
        await asyncio.sleep(0.05)
        assert not task.done()
        controls.handle("\t")            # back to LISTEN: session may end
        await asyncio.wait_for(task, 2)

    asyncio.run(scenario())


def test_source_eof_does_not_commit_partial_typed_word():
    # Reader is mid-word in HUMAN mode when the speech source hits EOF; the
    # half-typed word must stay buffered, not stream to the display.
    engine = make_engine()
    interval = {"v": 0.0}
    stop = threading.Event()
    controls = PacerControls(engine, interval)
    controls.mode = MODE_TYPE
    for ch in "hel":
        controls.handle(ch)

    async def scenario():
        task = asyncio.ensure_future(run_pipeline(
            _list_source([]), engine, interval, stop, controls))
        await asyncio.sleep(0.1)
        frames = list(engine.sink.frames)
        pending = engine.has_pending()
        stop.set()
        await asyncio.wait_for(task, 2)
        return frames, pending

    frames, pending = asyncio.run(scenario())
    assert frames == []                  # 'hel' never reached the display
    assert pending is False              # ...and was never committed


def test_toggle_mode_flips_before_flush():
    # An in-flight speech chunk must see mode == TYPE by the time the toggle
    # flushes/jumps, so it is discarded rather than fed after the jump.
    seen = []

    class ModeRecordingEngine:
        def flush_input(self):
            seen.append(controls.mode)

        def discard_soft(self):
            seen.append(controls.mode)
            return []

        def jump_to_live(self):
            seen.append(controls.mode)

    controls = PacerControls(ModeRecordingEngine(), {"v": 0.15})
    controls.handle("\t")
    assert seen == [MODE_TYPE, MODE_TYPE, MODE_TYPE]


def test_type_entry_discards_the_soft_tail(capsys):
    # The entry jump leaves still-soft tokens queued (the
    # take_pending contract, right for catch-up jumps) — but the reader who
    # switched to typing has abandoned that hypothesis text. Entering TYPE
    # must withdraw it AND forget its segments, or the closing speech
    # session's flush hardens it moments later and stale speech streams
    # onto the display mid-typing, left of the typed characters.
    engine = make_engine(width=20)
    controls = PacerControls(engine, {"v": 0.0})
    engine.feed("hi ", seg="a", final=True)          # committed backlog
    engine.feed("the meeting is about ", seg="b", final=False)  # soft tail
    controls.handle("\t")                            # enter HUMAN mode
    assert engine.knows_segment("b") is False        # forgotten, not just cut
    assert engine.has_pending() is False             # nothing left to stream
    # The committed backlog was snapped at entry, exactly as before.
    assert "hi" in engine.shown_source()
    assert "meeting" not in engine.shown_source()
    assert "dropped 1 in-flight speech segment" in capsys.readouterr().err


def test_close_flush_final_after_type_entry_never_renders():
    # The companion pipeline half: the dying session's close flush hardens
    # its soft segments (the speech server's close-time flush). With the
    # segment forgotten at TYPE entry the
    # op arrives as an UNKNOWN segment and the Human-mode gate discards it;
    # only the typed words may render.
    engine = make_engine(width=20)
    interval = {"v": 0.0}
    stop = threading.Event()
    controls = PacerControls(engine, interval)
    controls.advance_mode = "ticker"   # paced: typed words stream unaided
    engine.feed("the meeting is about ", seg="b", final=False)
    controls.handle("\t")                            # enter HUMAN mode

    async def scenario():
        task = asyncio.ensure_future(run_pipeline(
            _list_source([("revise", "b", "the meeting is about to begin",
                           True)]),
            engine, interval, stop, controls))
        await asyncio.sleep(0.05)
        for ch in "ok ":                             # the typist's words
            controls.handle(ch)
        await asyncio.sleep(0.2)
        shown = engine.shown_source()
        pending = engine.has_pending()
        stop.set()
        await asyncio.wait_for(task, 2)
        return shown, pending

    shown, pending = asyncio.run(scenario())
    assert "ok" in shown
    assert "meeting" not in shown and "begin" not in shown
    assert pending is False


# --- 2. Engine thread safety -------------------------------------------------

def test_engine_mutations_serialize_on_the_lock():
    engine = make_engine()
    engine.feed("hello world ")
    with engine._lock:
        t = threading.Thread(target=engine.jump_to_live, daemon=True)
        t.start()
        t.join(0.2)
        blocked = t.is_alive()           # jump waits for the lock we hold
    t.join(2)
    assert blocked is True
    assert not t.is_alive()


def test_concurrent_tick_and_jump_do_not_crash():
    # Regression stress for the tick()/jump_to_live() check-then-pop race.
    engine = make_engine()
    stop = threading.Event()
    errors = []

    def jumper():
        while not stop.is_set():
            try:
                engine.jump_to_live()
            except Exception as e:       # noqa: BLE001 - recording any crash
                errors.append(e)
                return

    t = threading.Thread(target=jumper, daemon=True)
    t.start()
    try:
        for _ in range(2000):
            engine.feed("word here ")
            engine.tick()
    finally:
        stop.set()
        t.join(2)
    assert errors == []


# --- 3. read_key parsing -----------------------------------------------------

def _fake_reader(pending):
    buf = list(pending)

    def read1():
        return buf.pop(0)

    def poll(_timeout=0.0):
        return bool(buf)

    return read1, poll, buf


def test_escape_csi_consumes_only_the_sequence():
    read1, poll, buf = _fake_reader("[Aabc")   # Up arrow, then typed 'abc'
    _consume_escape(read1, poll)
    assert buf == list("abc")


def test_escape_csi_with_params_stops_at_final_byte():
    read1, poll, buf = _fake_reader("[1;5Crest")   # Ctrl+Right, then 'rest'
    _consume_escape(read1, poll)
    assert buf == list("rest")


def test_escape_ss3_consumes_final_byte_only():
    read1, poll, buf = _fake_reader("OPxyz")   # F1, then typed 'xyz'
    _consume_escape(read1, poll)
    assert buf == list("xyz")


def test_lone_escape_consumes_nothing():
    read1, poll, buf = _fake_reader("")
    _consume_escape(read1, poll)
    assert buf == []


def test_alt_letter_consumes_one_char():
    read1, poll, buf = _fake_reader("xhello")   # Alt+x, then typed 'hello'
    _consume_escape(read1, poll)
    assert buf == list("hello")


def test_win_extended_key_swallows_its_code():
    seq = ["\xe0", "H"]                  # Up arrow as delivered by conhost

    def getwch():
        return seq.pop(0)

    def kbhit():
        return bool(seq)

    assert _win_read_key(getwch, kbhit) is None
    assert seq == []


def test_win_accented_char_is_delivered():
    seq = ["\xe0"]                       # a typed 'a-grave': no code pending

    def getwch():
        return seq.pop(0)

    def kbhit():
        return bool(seq)

    assert _win_read_key(getwch, kbhit) == "\xe0"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX termios only")
def test_posix_raw_mode_enter_and_restore_roundtrip():
    # The shell must get its original termios back (echo, canonical mode,
    # IXON) — even when a daemon key thread is killed mid-read.
    import os
    import pty
    import termios

    from braille_engine import controls

    master, slave = pty.openpty()
    before = termios.tcgetattr(slave)
    try:
        controls._enter_raw_posix(slave)
        during = termios.tcgetattr(slave)
        assert not during[0] & termios.IXON       # Ctrl+S is deliverable
        assert not during[3] & termios.ICANON     # byte-at-a-time reads
        assert not during[3] & termios.ECHO
        controls._restore_posix(slave)
        assert termios.tcgetattr(slave) == before
        assert controls._posix_saved is None      # atexit call is now a no-op
    finally:
        controls._posix_saved = None
        os.close(master)
        os.close(slave)
