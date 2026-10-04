"""Runtime grade switching against REAL liblouis (auto-skips without it).

Runs where the liblouis Python module is installed (Linux/WSL); elsewhere
the whole module skips. Asserts the properties the live 'g' key depends on:

  * grade 2 actually contracts (fewer cells for the same text);
  * toggling grade re-grades every word that has NOT yet started streaming
    (the whole not-yet-shown tail), because translation is deferred to emission;
  * cells already shown are frozen — the word mid-scroll finishes in its
    original grade and previously emitted cells are never rewritten.
"""
import pytest

pytest.importorskip("louis")

from braille_engine.engine import BrailleEngine
from braille_engine.translator import get_translator
from braille_engine.controls import PacerControls


class CapturingSink:
    """Records the newest (rightmost) cell shown on each write, in order —
    i.e. the exact stream of cells as the reader would feel them scroll by."""

    def __init__(self, width=40):
        self._w = width
        self.emitted = []

    def connect(self):
        return self._w

    def write(self, cells):
        self.emitted.append(cells[-1])


def make_engine(grade=1):
    translator, name = get_translator("liblouis", grade=grade)
    assert name == "liblouis"
    engine = BrailleEngine(translator, CapturingSink())
    engine.start()
    return engine, translator


def drain(engine):
    while engine.tick():
        pass


def stream_of(text, grade):
    """The full cell stream a fresh engine emits for `text` at a fixed grade."""
    engine, _ = make_engine(grade=grade)
    engine.feed(text)
    drain(engine)
    return list(engine.sink.emitted)


TEXT = "knowledge and the "


def test_grade2_contracts():
    assert len(stream_of(TEXT, 2)) < len(stream_of(TEXT, 1))


def test_grade_flag_starts_contracted():
    translator, _ = get_translator("liblouis", grade=2)
    assert translator.grade == 2
    assert translator.tables[-1] == "en-ueb-g2.ctb"


def test_toggle_regrades_not_yet_shown_tail():
    """Toggle BEFORE anything streams -> the entire queued phrase comes out
    contracted, identical to a native grade-2 stream. This is the fix: queued
    text is translated at the grade current when it reaches the display."""
    engine, tr = make_engine(grade=1)
    controls = PacerControls(engine, {"v": 0.1})
    engine.feed(TEXT)                 # all queued as tokens, nothing shown yet
    controls.handle("g")              # -> grade 2 before the first tick
    assert tr.grade == 2
    announced = len(engine.sink.emitted)  # the "grade 2" acknowledgment frame
    drain(engine)
    assert engine.sink.emitted[announced:] == stream_of(TEXT, 2)


def test_shown_cells_frozen_and_current_word_keeps_grade():
    """Toggle MID first word: the cells already shown are never rewritten, the
    in-flight word finishes in grade 1, and the rest of the tail contracts."""
    engine, tr = make_engine(grade=1)
    controls = PacerControls(engine, {"v": 0.1})
    engine.feed(TEXT)
    engine.tick(); engine.tick(); engine.tick()   # 3 grade-1 cells of "knowledge"
    shown_before = list(engine.sink.emitted)
    assert len(shown_before) == 3

    controls.handle("g")                          # toggle while "knowledge" streams
    assert tr.grade == 2
    drain(engine)

    # append-only: the first 3 shown cells are untouched
    assert engine.sink.emitted[:3] == shown_before
    # the tail contracted, so the whole stream is shorter than a pure grade-1 run
    assert len(engine.sink.emitted) < len(stream_of(TEXT, 1))
    # ...but longer than a pure grade-2 run, since word 1 finished uncontracted
    assert len(engine.sink.emitted) > len(stream_of(TEXT, 2))
