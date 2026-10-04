"""Timed caption replay: SRT/WebVTT parsing, and the demo feeder's
wall-clock replay mode with an injected fake clock."""

import time
from pathlib import Path

import pytest

from braille_engine.captions import parse_captions
from braille_engine.controls import PacerControls
from braille_engine.demo import DemoStream
from braille_engine.engine import BrailleEngine
from braille_engine.sinks.simulated import SimulatedSink
from braille_engine.translator import DevUebTranslator

FIXTURE_VTT = (Path(__file__).parent / "fixtures" / "sample.vtt").read_text(
    encoding="utf-8")


# ---- parser ----

def test_parses_srt_cues_with_comma_millis_and_multiline_text():
    srt = (
        "1\n"
        "00:00:01,500 --> 00:00:03,000\n"
        "Hello there,\n"
        "captioned world.\n"
        "\n"
        "2\n"
        "00:00:04,000 --> 00:00:05,250\n"
        "Second cue.\n"
    )
    cues = parse_captions(srt)
    assert [(c.start, c.end, c.text) for c in cues] == [
        (1.5, 3.0, "Hello there, captioned world."),
        (4.0, 5.25, "Second cue."),
    ]


def test_parses_vtt_header_notes_settings_and_short_timestamps():
    vtt = (
        "﻿WEBVTT - a title\n"
        "Kind: captions\n"
        "\n"
        "NOTE This block is commentary,\n"
        "not a cue.\n"
        "\n"
        "STYLE\n"
        "::cue { color: gold }\n"
        "\n"
        "greeting\n"
        "00:07.250 --> 00:09.000 position:10%,line-left align:left\n"
        "Short-form timestamps work.\n"
        "\n"
        "01:00:00.000 --> 01:00:02.000\n"
        "An hour in.\n"
    )
    cues = parse_captions(vtt)
    assert [(c.start, c.end, c.text) for c in cues] == [
        (7.25, 9.0, "Short-form timestamps work."),
        (3600.0, 3602.0, "An hour in."),
    ]


def test_strips_inline_markup_and_speaker_names():
    vtt = (
        "WEBVTT\n"
        "\n"
        "00:01.000 --> 00:02.000\n"
        "<v Rosa Marquez>Fish &amp; chips</v> <i>tonight</i>\n"
        "\n"
        "00:03.000 --> 00:04.000\n"
        "<c.yellow>Karaoke<00:00:03.500> words</c> {\\an8}anchored\n"
    )
    cues = parse_captions(vtt)
    assert cues[0].text == "Fish & chips tonight"
    assert cues[1].text == "Karaoke words anchored"
    assert "Rosa" not in cues[0].text


def test_no_space_arrows_still_parse():
    # Several converters write the timing line without spaces around the
    # arrow; the timestamps are unambiguous, so the track must not be
    # refused wholesale over the missing spaces.
    srt = (
        "1\n"
        "00:00:01,000-->00:00:03,500\n"
        "Tightly packed timing line.\n"
    )
    cues = parse_captions(srt)
    assert [(c.start, c.end, c.text) for c in cues] == [
        (1.0, 3.5, "Tightly packed timing line."),
    ]


def test_unescaped_angle_brackets_keep_their_words():
    # Only tag-shaped runs are markup. A bare "less than ... greater than"
    # in machine-generated SRT is caption TEXT — a blanket <[^>]*> strip
    # would silently delete everything between them.
    srt = (
        "1\n"
        "00:00:01,000 --> 00:00:02,000\n"
        "if x < 10 and y > 2, then stop\n"
        "\n"
        "2\n"
        "00:00:03,000 --> 00:00:04,000\n"
        "<i>styled</i> but 5 < 6 today > yesterday\n"
    )
    cues = parse_captions(srt)
    assert cues[0].text == "if x < 10 and y > 2, then stop"
    assert cues[1].text == "styled but 5 < 6 today > yesterday"


def test_malformed_cues_skip_with_a_warning_and_good_cues_survive():
    warnings = []
    srt = (
        "1\n"
        "00:00:xx,000 --> 00:00:02,000\n"
        "Broken timestamp.\n"
        "\n"
        "just some text with no timing line at all\n"
        "\n"
        "2\n"
        "00:00:05,000 --> 00:00:03,000\n"
        "Ends before it starts.\n"
        "\n"
        "3\n"
        "00:00:06,000 --> 00:00:07,000\n"
        "The survivor.\n"
    )
    cues = parse_captions(srt, status=warnings.append)
    assert [c.text for c in cues] == ["The survivor."]
    assert len(warnings) == 3
    assert all(w.startswith("captions:") for w in warnings)


def test_empty_cues_are_dropped_and_output_is_sorted_by_start():
    vtt = (
        "WEBVTT\n"
        "\n"
        "00:05.000 --> 00:06.000\n"
        "Later cue.\n"
        "\n"
        "00:01.000 --> 00:02.000\n"
        "<i></i>\n"
        "\n"
        "00:03.000 --> 00:04.000\n"
        "Earlier cue.\n"
    )
    cues = parse_captions(vtt)
    assert [c.text for c in cues] == ["Earlier cue.", "Later cue."]


def test_fixture_vtt_parses_to_the_expected_cues():
    warnings = []
    cues = parse_captions(FIXTURE_VTT, status=warnings.append)
    assert warnings == []
    assert [(c.start, c.end, c.text) for c in cues] == [
        (1.0, 2.4, "Welcome to the"),
        (2.4, 4.0, "harbor lecture series."),
        (4.1, 6.5, "Tonight we talk about tides and moonlight."),
        (9.0, 11.0, "A long pause came before this cue."),
    ]


# ---- timed replay: fake-clock harness ----

class FakeTime:
    """Injectable clock/wait pair: wait() advances the clock instead of
    sleeping, so replay runs instantly at honest timestamps."""

    def __init__(self):
        self.now = 0.0
        self.waits = []

    def clock(self):
        return self.now

    def wait(self, stop, seconds):
        self.waits.append(round(seconds, 6))
        if stop.is_set():
            return True
        self.now += seconds
        return False


class RecordingEngine:
    """Feed/revise recorder timestamped on the fake clock; ``feed_cost``
    simulates a slow engine for the catch-up test."""

    def __init__(self, fake_time, feed_cost=0.0):
        self.fake_time = fake_time
        self.feed_cost = feed_cost
        self.feeds = []       # (fake-clock time, text, seg, final)
        self.revisions = []   # (seg, text, final)
        self.known = set()
        self.soft_discards = 0   # the real engine's Human-mode entry
                                 # counter, advanced by hand in tests

    def feed(self, text, seg=None, final=False):
        self.feeds.append((self.fake_time.now, text, seg, final))
        self.known.add(seg)
        self.fake_time.now += self.feed_cost

    def revise_segment(self, seg, text, final=False):
        self.revisions.append((seg, text, final))
        return seg in self.known

    def knows_segment(self, seg):
        return seg in self.known


def replay(vtt, engine=None, fake_time=None, wait=None, **kwargs):
    fake_time = fake_time or FakeTime()
    engine = engine or RecordingEngine(fake_time)
    stream = DemoStream(engine, clock=fake_time.clock,
                        wait=wait or fake_time.wait, **kwargs)
    assert stream.toggle(vtt, timed=True) is True
    stream._thread.join(timeout=5.0)
    assert not stream.active
    return engine, fake_time, stream


def vtt_of(*cues):
    """(start, end, text) triples -> a minimal VTT string."""
    def stamp(seconds):
        return f"00:{int(seconds // 60):02d}:{seconds % 60:06.3f}"
    blocks = [f"{stamp(s)} --> {stamp(e)}\n{text}" for s, e, text in cues]
    return "WEBVTT\n\n" + "\n\n".join(blocks) + "\n"


# ---- timed replay: behavior ----

def test_each_cue_arrives_at_its_start_offset_with_gaps_preserved():
    vtt = vtt_of((1.0, 2.0, "First sentence."), (4.5, 5.5, "After a gap."))
    engine, fake_time, _ = replay(vtt)
    assert [(t, final) for t, _, _, final in engine.feeds] == [
        (1.0, True), (4.5, True)]
    # The waits are the real inter-cue gaps, not a fixed beat.
    assert fake_time.waits == [1.0, 3.5]


def test_late_replay_catches_up_instead_of_drifting():
    fake_time = FakeTime()
    engine = RecordingEngine(fake_time, feed_cost=2.0)   # slow engine
    vtt = vtt_of((0.0, 0.4, "One sentence."),
                 (0.5, 0.9, "Two sentences."),
                 (1.0, 1.4, "Three sentences."))
    replay(vtt, engine=engine, fake_time=fake_time)
    # Every cue is already overdue when its turn comes: no waiting at
    # all, each feed lands as soon as the engine frees up.
    assert fake_time.waits == []
    assert [t for t, _, _, _ in engine.feeds] == [0.0, 2.0, 4.0]


def test_cues_accumulate_into_a_segment_until_a_sentence_terminator():
    vtt = vtt_of((0.0, 1.0, "Welcome to the"),
                 (1.0, 2.0, "harbor lecture."),
                 (2.0, 3.0, "A second thought."))
    engine, _, _ = replay(vtt)
    assert [(text, seg, final) for _, text, seg, final in engine.feeds] == [
        ("Welcome to the ", ("captions", 1, 0), False),
        ("Welcome to the harbor lecture. ", ("captions", 1, 0), True),
        ("A second thought. ", ("captions", 1, 1), True),
    ]


def test_a_long_silence_gap_hardens_the_segment_midsentence():
    vtt = vtt_of((0.0, 1.0, "cut off before any terminator"),
                 (4.0, 5.0, "resumes far later"))
    engine, _, _ = replay(vtt)
    # Gap of 3.0 s > CAPTION_GAP_S: the first cue hardens on its own;
    # the last cue hardens because the track ends.
    assert [(seg, final) for _, _, seg, final in engine.feeds] == [
        (("captions", 1, 0), True),
        (("captions", 1, 1), True),
    ]


def test_stop_mid_wait_withdraws_every_fed_segment():
    fake_time = FakeTime()
    engine = RecordingEngine(fake_time)
    statuses = []
    calls = []

    def wait(stop, seconds):
        calls.append(seconds)
        return True                       # the reader pressed stop

    stream = DemoStream(engine, status=statuses.append,
                        clock=fake_time.clock, wait=wait)
    vtt = vtt_of((0.0, 1.0, "soft text with no terminator"),
                 (100.0, 101.0, "never reached"))
    assert stream.toggle(vtt, timed=True) is True
    stream._thread.join(timeout=5.0)
    # The open soft segment was withdrawn via the empty final revision.
    assert engine.revisions == [(("captions", 1, 0), "", True)]
    assert calls == [100.0]
    assert any("withdrawn" in message for message in statuses)
    assert not any("never reached" in text for _, text, _, _ in engine.feeds)


def test_bows_out_when_human_mode_forgets_the_soft_segment():
    fake_time = FakeTime()
    engine = RecordingEngine(fake_time)

    def wait(stop, seconds):
        engine.known.clear()              # Human-mode entry's clean slate
        fake_time.now += seconds
        return False

    vtt = vtt_of((0.0, 1.0, "soft text with no terminator"),
                 (2.0, 3.0, "must not follow"))
    replay(vtt, engine=engine, fake_time=fake_time, wait=wait)
    assert not any("must not follow" in text
                   for _, text, _, _ in engine.feeds)
    # Withdrawal was still attempted; the forgotten segment answers
    # False harmlessly, per the preset mode's contract.
    assert engine.revisions == [(("captions", 1, 0), "", True)]


def test_bows_out_when_human_mode_enters_between_hardened_segments():
    fake_time = FakeTime()
    engine = RecordingEngine(fake_time)
    statuses = []

    def wait(stop, seconds):
        # Human-mode entry lands in the inter-cue gap: the first cue's
        # sentence hardened at its feed, so the entry finds nothing soft
        # of ours to forget — only the discard counter moves.
        engine.soft_discards += 1
        fake_time.now += seconds
        return False

    vtt = vtt_of((0.0, 1.0, "First sentence."),
                 (5.0, 6.0, "Must not follow."))
    replay(vtt, engine=engine, fake_time=fake_time, wait=wait,
           status=statuses.append)
    # The second cue never fed; the run withdrew what it had and bowed out.
    assert [(text, final) for _, text, _, final in engine.feeds] == [
        ("First sentence. ", True)]
    assert engine.revisions == [(("captions", 1, 0), "", True)]
    assert any("withdrawn" in message for message in statuses)


def test_timed_toggle_rejects_content_with_no_usable_cues():
    engine = RecordingEngine(FakeTime())
    stream = DemoStream(engine)
    with pytest.raises(ValueError):
        stream.toggle("this is ordinary prose, not a caption file",
                      timed=True)
    assert not stream.active
    assert engine.feeds == []


# ---- timed replay: through the real engine ----

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


def test_fixture_replays_into_hardened_segments_on_a_real_engine():
    eng = make_engine()
    fake_time = FakeTime()
    stream = DemoStream(eng, clock=fake_time.clock, wait=fake_time.wait)
    assert stream.toggle(FIXTURE_VTT, timed=True) is True
    stream._thread.join(timeout=5.0)
    # Cues 1+2 accumulated into one sentence segment; 3 and 4 stand alone.
    assert eng.segment_text(("captions", 1, 0)) \
        == "Welcome to the harbor lecture series."
    assert eng.segment_text(("captions", 1, 1)) \
        == "Tonight we talk about tides and moonlight."
    assert eng.segment_text(("captions", 1, 2)) \
        == "A long pause came before this cue."
    while eng.tick():
        pass
    assert eng.has_pending() is False


def test_toggle_stops_a_parked_timed_replay_and_withdraws():
    eng = make_engine()
    stream = DemoStream(eng)                  # real clock, real waits
    vtt = vtt_of((0.0, 1.0, "queued soft words with no terminator"),
                 (30.0, 31.0, "parked far in the future"))
    assert stream.toggle(vtt, timed=True) is True
    assert wait_for(eng.has_pending)          # first cue's soft feed queued
    assert stream.toggle() is False           # second press: stop
    stream._thread.join(timeout=5.0)
    assert not stream.active
    assert eng.has_pending() is False
    assert eng.tick() == 0


def test_human_mode_entry_between_hardened_segments_on_a_real_engine():
    eng = make_engine()
    fake_time = FakeTime()

    def wait(stop, seconds):
        # Human-mode entry during the inter-cue gap: segment 0 already
        # hardened, so discard_soft finds nothing soft to drop — the
        # bump of the entry counter is the only trace it leaves.
        before = eng.soft_discards
        assert eng.discard_soft() == []
        assert eng.soft_discards == before + 1
        fake_time.now += seconds
        return False

    stream = DemoStream(eng, clock=fake_time.clock, wait=wait)
    vtt = vtt_of((0.0, 1.0, "First sentence."),
                 (5.0, 6.0, "Must not follow."))
    assert stream.toggle(vtt, timed=True) is True
    stream._thread.join(timeout=5.0)
    assert not stream.active
    # The second cue never registered, and the withdrawal emptied the
    # first segment's still-queued words: nothing is left to render on
    # top of the reader's typing.
    assert eng.knows_segment(("captions", 1, 1)) is False
    assert eng.has_pending() is False
    assert eng.tick() == 0


def test_toggle_demo_timed_announces_captions_on_and_off():
    # The flash names the SOURCE, matching the Space+I status flash: a
    # timed replay is "captions", not "demo". The stop here is a bare
    # press (no timed flag), so the OFF label must come from what WAS
    # streaming, not from the toggle's arguments.
    eng = make_engine()
    controls = PacerControls(eng, {"v": 0.1})
    flashes = []
    statuses = []
    controls.announce = flashes.append
    controls._status_line = statuses.append
    controls.demo._status = statuses.append
    vtt = vtt_of((30.0, 31.0, "parked far in the future"))
    assert controls.toggle_demo(vtt, timed=True) is True
    assert controls.demo_active is True
    assert controls.toggle_demo() is False
    controls.demo._thread.join(timeout=5.0)
    assert flashes == ["captions on", "captions off"]
    assert any("real cadence" in message for message in statuses)
    assert controls.demo_active is False


def test_toggle_demo_timed_rejects_before_any_announce():
    # The bridge answers the demo command's POST with a 400 built from
    # this ValueError — it must fire before anything is announced or a
    # stream starts, so a rejected upload leaves no trace on the display.
    eng = make_engine()
    controls = PacerControls(eng, {"v": 0.1})
    flashes = []
    controls.announce = flashes.append
    controls._status_line = lambda msg: None
    with pytest.raises(ValueError):
        controls.toggle_demo("ordinary prose, not a caption file",
                             timed=True)
    assert flashes == []
    assert controls.demo_active is False
