"""DemoStream: preset text through the live soft/revise/final protocol."""

import time

from braille_engine.controls import PacerControls
from braille_engine.demo import DemoStream
from braille_engine.engine import ORIGIN_SPEECH, BrailleEngine
from braille_engine.sinks.simulated import SimulatedSink
from braille_engine.translator import DevUebTranslator


def make_engine(width=12):
    eng = BrailleEngine(DevUebTranslator(),
                        SimulatedSink(width=width, echo=False))
    eng.start()
    return eng


def wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_demo_feeds_sentences_as_hardened_speech_segments():
    eng = make_engine()
    demo = DemoStream(eng, beat_s=0.0)
    assert demo.toggle(text="One two three. Four five.") is True
    demo._thread.join(timeout=5.0)
    assert not demo.active
    # Each sentence is one segment, hardened by its final — renderable to
    # the last word, exactly like real finalized speech. Real speech runs
    # through the digits filter, so the demo does too ("two" -> "2"; the
    # standalone "One" is pronoun-guarded and stays a word).
    assert eng.segment_text(("demo", 1, 0)) == "One 2 3."
    assert eng.segment_text(("demo", 1, 1)) == "4 5."
    while eng.tick():
        pass
    assert eng.has_pending() is False


def test_demo_segments_read_as_speech_in_cell_2():
    eng = make_engine()
    demo = DemoStream(eng, beat_s=0.0)
    demo.toggle(text="Hello there.")
    demo._thread.join(timeout=5.0)
    eng.tick()
    # The demo IS transcribed speech to the engine: the speech tag, not
    # typed/summary (no letter renders for it).
    assert eng._origin[-1] == ORIGIN_SPEECH


def test_toggle_stops_and_withdraws_queued_demo_text():
    eng = make_engine()
    demo = DemoStream(eng, beat_s=30.0)     # park the feeder mid-sentence
    demo.toggle(text="Alpha beta gamma delta. Second sentence here.")
    assert wait_for(eng.has_pending)        # first soft partial queued
    assert demo.toggle() is False           # second press: stop
    demo._thread.join(timeout=5.0)
    assert not demo.active
    # The soft partial was withdrawn: nothing renders, nothing lingers.
    assert eng.has_pending() is False
    assert eng.tick() == 0


def test_demo_bows_out_when_human_mode_discards_its_segment():
    eng = make_engine()
    demo = DemoStream(eng, beat_s=0.5)
    demo.toggle(text=" ".join(["word"] * 40) + ".")
    assert wait_for(lambda: eng.knows_segment(("demo", 1, 0)))
    eng.discard_soft()                      # Human-mode entry's clean slate
    demo._thread.join(timeout=5.0)
    assert not demo.active
    assert eng.has_pending() is False


def test_natural_completion_arms_the_ended_notice_once():
    # A run that feeds its WHOLE track remembers it: take_ended answers
    # the source label exactly once (the pace loop parks the persistent
    # "<label> ended" frame after the tail drains), and a restarted
    # stream reopens the story.
    eng = make_engine()
    demo = DemoStream(eng, beat_s=0.0)
    assert demo.take_ended() == ""          # nothing ever ran
    demo.toggle(text="One two three.")
    demo._thread.join(timeout=5.0)
    assert wait_for(lambda: demo.take_ended() == "demo")
    assert demo.take_ended() == ""          # consumed: one frame per run
    demo.toggle(text="Again.")
    demo._thread.join(timeout=5.0)
    assert wait_for(lambda: demo.take_ended() == "demo")


def test_timed_completion_reports_captions_as_the_ended_label():
    eng = make_engine()
    demo = DemoStream(eng, beat_s=0.0)
    srt = "1\n00:00:00,000 --> 00:00:01,000\nHello there.\n"
    demo.toggle(text=srt, timed=True)
    demo._thread.join(timeout=5.0)
    assert wait_for(lambda: demo.take_ended() == "captions")


def test_stop_press_never_arms_the_ended_notice():
    # The reader stopped it: "off" is the answer, not an end frame.
    eng = make_engine()
    demo = DemoStream(eng, beat_s=30.0)     # park the feeder mid-sentence
    demo.toggle(text="Alpha beta gamma delta. Second sentence here.")
    assert wait_for(eng.has_pending)
    demo.toggle()                           # stop-press
    demo._thread.join(timeout=5.0)
    assert demo.take_ended() == ""


def test_starting_a_new_stream_clears_a_stale_ended_notice():
    # Completion armed but never consumed (the tail had not drained when
    # the reader restarted): the new stream must not end with a stale
    # frame waiting to fire mid-play.
    eng = make_engine()
    demo = DemoStream(eng, beat_s=0.0)
    demo.toggle(text="Done.")
    demo._thread.join(timeout=5.0)
    assert wait_for(lambda: demo._ended_source == "demo")
    demo.toggle(text=" ".join(["word"] * 40) + ".")
    assert demo.take_ended() == ""
    demo._thread.join(timeout=5.0)


def test_source_label_names_the_running_feeder_mode():
    # The status flash's input-source
    # item reads the feeder while it streams — "demo" for preset text —
    # and goes back to "" (the mic-label fallback) when it stops.
    eng = make_engine()
    demo = DemoStream(eng, beat_s=30.0)     # park it mid-sentence
    assert demo.source_label == ""
    demo.toggle(text="Alpha beta gamma delta. Second sentence here.")
    assert demo.source_label == "demo"
    demo.toggle()                           # second press: stop
    demo._thread.join(timeout=5.0)
    assert demo.source_label == ""


def test_source_label_reads_captions_for_a_timed_replay():
    # The timed caption replay is a different source under the fingers
    # and names itself so. The cue sits minutes out, so the feeder is
    # parked in its wall-clock wait while we look.
    eng = make_engine()
    demo = DemoStream(eng)
    demo.toggle(text="1\n00:10:00,000 --> 00:10:02,000\nHello there.\n",
                timed=True)
    assert demo.source_label == "captions"
    demo.toggle()
    demo._thread.join(timeout=5.0)
    assert demo.source_label == ""


def test_timed_replay_tags_captions_and_restores_the_flag_on_exit():
    # The replay's text IS a caption stream, so its feeds tag captions
    # (truthful tags; no letter renders): speech_is_stream is
    # raised for the thread's lifetime and restored when the run ends, so
    # a stopped replay hands tagging back to the mic's classification.
    from braille_engine.engine import ORIGIN_CAPTIONS
    eng = make_engine()
    demo = DemoStream(eng)
    demo.toggle(text="1\n00:00:00,000 --> 00:00:02,000\nHello there.\n"
                     "\n2\n00:10:00,000 --> 00:10:02,000\nStill here.\n",
                timed=True)
    assert wait_for(lambda: eng.has_pending())   # first cue fed
    assert eng.speech_is_stream is True
    eng.tick()
    assert eng._origin[-1] == ORIGIN_CAPTIONS
    demo.toggle()                                # stop the replay
    demo._thread.join(timeout=5.0)
    assert eng.speech_is_stream is False


def test_toggle_demo_announces_on_and_off():
    eng = make_engine()
    controls = PacerControls(eng, {"v": 0.1})
    controls.demo.beat_s = 30.0             # park it; the toggle is the test
    flashes = []
    controls.announce = flashes.append
    assert controls.toggle_demo() is True
    assert controls.demo_active is True
    assert controls.toggle_demo() is False
    controls.demo._thread.join(timeout=5.0)
    assert flashes == ["demo on", "demo off"]
    assert controls.demo_active is False


def test_ends_sentence_hardens_on_ellipses_and_wrapped_terminators():
    # The timed replay shares reply.py's _SENTENCE_END: a cue ending on
    # "…" (U+2026) or "..." is a sentence end, or it would pile up soft
    # instead of hardening.
    ends = DemoStream._ends_sentence
    assert ends("word…") is True                # U+2026 ellipsis
    assert ends("word...") is True              # ASCII ellipsis
    assert ends("word.") is True
    assert ends('he said…"') is True            # wrapped in closing quotes
    assert ends("word… ") is True               # caption trailing whitespace
    assert ends("word") is False
    assert ends("word,") is False
