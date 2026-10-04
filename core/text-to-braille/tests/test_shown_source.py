"""shown_source(): the speaker-facing highlight's anchor.

The engine tracks, per cell in the rolling buffer, which source word the cell
came from; shown_source() returns those words as print text so the browser
page can highlight where the braille reader is. The contract under test:
words count WHOLE even when partially visible, repeats stay distinct, catch-up
snaps keep their annotation, and frames with no source (the jump-to-live
summary) honestly read as "" instead of guessing.
"""

import unittest

from braille_engine.engine import BrailleEngine
from braille_engine.gauge import BacklogGauge
from braille_engine.sinks.simulated import SimulatedSink
from braille_engine.translator import DevUebTranslator


def make_engine(width=12, window=1, gauge=None):
    engine = BrailleEngine(DevUebTranslator(),
                           SimulatedSink(width=width, echo=False),
                           gauge=gauge, window=window)
    engine.start()
    return engine


def drain(engine, ticks=64):
    for _ in range(ticks):
        if not engine.tick():
            break


class ShownSourceTests(unittest.TestCase):
    def test_nothing_shown_reads_empty(self):
        self.assertEqual("", make_engine().shown_source())

    def test_words_appear_as_their_cells_stream(self):
        engine = make_engine(width=12)
        engine.feed("hi there ")
        engine.tick()                       # first cell of "hi"
        self.assertEqual("hi", engine.shown_source())
        for _ in range(3):                  # i, space, first cell of "there"
            engine.tick()
        self.assertEqual("hi there", engine.shown_source())

    def test_words_fall_off_as_their_cells_scroll_away(self):
        engine = make_engine(width=6)
        engine.feed("alpha beta gamma ")
        drain(engine)
        # The last 6 of 16 cells are a space plus all of "gamma"; every
        # "alpha"/"beta" cell has rolled off the display.
        self.assertEqual("gamma", engine.shown_source())

    def test_partially_visible_word_counts_whole(self):
        engine = make_engine(width=6)
        engine.feed("elephants ")
        for _ in range(6):                  # 6 of the word's 9 cells
            engine.tick()
        self.assertEqual("elephants", engine.shown_source())
        drain(engine)                       # head cells roll off the display
        self.assertEqual("elephants", engine.shown_source())

    def test_adjacent_repeats_stay_two_words(self):
        engine = make_engine(width=12)
        engine.feed("had had ")
        drain(engine)
        self.assertEqual("had had", engine.shown_source())

    def test_jump_to_live_snap_keeps_the_annotation(self):
        engine = make_engine(width=12)
        engine.feed("one two three four five six ")
        engine.jump_to_live()
        # The snap keeps the newest 12 cells: mid-"four" onward. The cut word
        # still counts whole.
        self.assertEqual("four five six", engine.shown_source())

    def test_summary_frame_has_no_source_and_streaming_recovers(self):
        engine = make_engine(width=12)
        engine.feed("plenty of words before ")
        drain(engine)
        summary_cells, _total = engine.fit_cells("llm summary", 12)
        # Padded to the full width so no earlier cell stays on the display.
        engine.show_frame([0] * (12 - len(summary_cells)) + summary_cells)
        # The summary's text never came from the transcript: no highlight.
        self.assertEqual("", engine.shown_source())
        engine.feed("after ")
        drain(engine)
        self.assertIn("after", engine.shown_source())

    def test_flash_does_not_disturb_the_annotation(self):
        engine = make_engine(width=12)
        engine.feed("steady words ")
        drain(engine)
        before = engine.shown_source()
        engine.flash("manual")              # transient announcement frame
        self.assertEqual(before, engine.shown_source())

    def test_gauge_reserved_cells_do_not_shift_the_annotation(self):
        engine = make_engine(width=8, gauge=BacklogGauge())
        engine.feed("wide open ")           # content width is 8 - 2 = 6
        drain(engine)
        # 10 cells streamed (trailing space included); the visible 6 hold
        # "open" and its flanking blanks — every "wide" cell rolled off.
        self.assertEqual("open", engine.shown_source())


if __name__ == "__main__":
    unittest.main()
