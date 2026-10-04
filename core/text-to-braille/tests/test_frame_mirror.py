"""The screen-mirror contract (sighted-speaker view).

Every physical frame write publishes a mirror event — kind ('content' /
'flash' / 'reply'), the literal display text (summary recap included), and
the transcript anchor (shown_source, summary excluded) — served by the
frame_event() poll accessor. The contract under test: an event per write
and only after it, seq is monotonic, and shown_display carries the recap
the highlight must not.
"""

import unittest

from braille_engine.engine import BrailleEngine
from braille_engine.sinks.simulated import SimulatedSink
from braille_engine.translator import DevUebTranslator


def make_engine(width=12, window=1):
    engine = BrailleEngine(DevUebTranslator(),
                           SimulatedSink(width=width, echo=False),
                           window=window)
    engine.start()
    return engine


def drain(engine, ticks=64):
    for _ in range(ticks):
        if not engine.tick():
            break


class FrameMirrorTests(unittest.TestCase):
    def test_no_event_before_the_first_write(self):
        self.assertIsNone(make_engine().frame_event())

    def test_content_event_per_tick_with_monotonic_seq(self):
        engine = make_engine()
        events = []
        engine.feed("hi ")
        engine.tick()
        events.append(engine.frame_event())
        engine.tick()
        events.append(engine.frame_event())
        self.assertEqual(2, len(events))
        self.assertEqual(["content", "content"], [e["kind"] for e in events])
        self.assertEqual(events[0]["seq"] + 1, events[1]["seq"])
        self.assertEqual("hi", events[-1]["text"])
        self.assertEqual("hi", events[-1]["source"])

    def test_flash_event_carries_the_announcement_text(self):
        engine = make_engine()
        engine.feed("steady ")
        drain(engine)
        engine.flash("manual")
        event = engine.frame_event()
        self.assertEqual("flash", event["kind"])
        self.assertEqual("manual", event["text"])
        # The underlying content returns when the dwell ends — the anchor
        # keeps naming it through the flash.
        self.assertEqual("steady", event["source"])

    def test_reply_event_has_no_print_text(self):
        engine = make_engine()
        engine.feed("words ")
        drain(engine)
        engine.reply_frame([0x1D])
        event = engine.frame_event()
        self.assertEqual("reply", event["kind"])
        self.assertEqual("", event["text"])

    def test_summary_shows_in_display_text_but_not_the_anchor(self):
        engine = make_engine(width=24)
        engine.feed_summary("recap words")
        drain(engine)
        self.assertEqual("recap words", engine.shown_display())
        self.assertEqual("", engine.shown_source())
        event = engine.frame_event()
        self.assertEqual("recap words", event["text"])
        self.assertEqual("", event["source"])

    def test_mixed_frame_keeps_speech_in_the_anchor(self):
        # A recap draining into live speech: the display line carries both,
        # the anchor only the transcript words.
        engine = make_engine(width=24)
        engine.feed("after ")
        engine.feed_summary("recap")
        drain(engine)
        self.assertEqual("recap after", engine.shown_display())
        self.assertEqual("after", engine.shown_source())

    def test_pending_band_carries_the_queue_head(self):
        engine = make_engine(width=6)
        engine.feed("one two three ")
        engine.tick()                      # "one" starts streaming
        event = engine.frame_event()
        self.assertEqual("one", event["text"])
        self.assertIn("two", event["pending"])
        self.assertIn("three", event["pending"])
        # Soft text queues into the pending band too — it is text the
        # display holds even though it may not render yet.
        engine.feed("maybe ", seg="s1", final=False)
        drain(engine)
        self.assertIn("maybe", engine.pending_text())

    def test_pending_band_is_bounded_with_an_ellipsis(self):
        engine = make_engine()
        engine.feed(" ".join(f"w{i}" for i in range(45)) + " ")
        pending = engine.pending_text()
        self.assertIn("…", pending)
        self.assertNotIn("w44", pending)
        self.assertEqual(41, len(pending.split()))

    def test_no_event_when_nothing_was_written(self):
        engine = make_engine()
        engine.tick()                      # empty queue: no write, no event
        self.assertIsNone(engine.frame_event())


if __name__ == "__main__":
    unittest.main()
