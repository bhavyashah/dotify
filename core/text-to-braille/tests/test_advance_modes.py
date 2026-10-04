"""Reading advance modes: auto (internally "ticker") and manual.

Auto is the paced mode — at the full-display window size (the default)
each refresh is a word-wrapped page (engine.flip) on the reading clock;
smaller windows stream cells, down to one-cell ticker-tape scrolling
(hence the internal value). Manual flips only when the reader asks
(f / thumb Right). The mode switch (r / Ctrl+R / thumb Previous) announces
itself on the display via a transient frame that never enters the rolling
buffer.
"""

import asyncio
import threading
import time

import pytest

from braille_engine.cells import BLANK
from braille_engine.controls import (
    ADVANCE_MANUAL,
    ADVANCE_TICKER,
    CTRL_R,
    MODE_TYPE,
    PacerControls,
)
from braille_engine.engine import BrailleEngine
from braille_engine.gauge import BacklogGauge
from braille_engine.sinks.simulated import SimulatedSink
from braille_engine.translator import DevUebTranslator
from run import run_pipeline


class FakeEngine:
    WINDOW_SIZES = (1, 2, 4, 6, 8)

    def __init__(self):
        self.window = 1
        self.flashes = []
        self.fed = []

    def flash(self, text):
        self.flashes.append(text)

    def feed(self, text):
        self.fed.append(text)


@pytest.fixture()
def controls():
    return PacerControls(FakeEngine(), {"v": 1.0})


async def _list_source(items):
    for it in items:
        yield it


def _rig(window=1, gauge=None, width=8):
    sink = SimulatedSink(width=width, echo=False)
    engine = BrailleEngine(DevUebTranslator(), sink, gauge=gauge,
                           window=window)
    engine.start()
    return sink, engine


# ---------------------------------------------------------------- key layer

def test_starts_in_auto_mode(controls):
    # Live captions must flow without a flip press, so a fresh session
    # starts paced (at the full-display default window, auto is discrete
    # page flips, not a cell-by-cell crawl).
    assert controls.advance_mode == ADVANCE_TICKER


def test_r_cycles_modes_and_announces(controls, capsys):
    for expected in (ADVANCE_MANUAL, ADVANCE_TICKER):
        controls.handle("r")
        assert controls.advance_mode == expected
    # The paced mode announces under its user-facing name.
    assert controls.engine.flashes == ["manual", "auto"]
    assert "mode: auto" in capsys.readouterr().err


def test_ctrl_r_works_in_type_mode_but_plain_r_is_content(controls):
    controls.mode = MODE_TYPE
    controls.handle("r")
    assert controls.advance_mode == ADVANCE_TICKER   # typed, not a command
    assert controls.engine.fed == ["r"]
    controls.handle(CTRL_R)
    assert controls.advance_mode == ADVANCE_MANUAL


def test_f_requests_an_advance_in_manual_mode(controls):
    controls.advance_mode = ADVANCE_MANUAL
    controls.handle("f")
    assert controls.take_advance() is True
    assert controls.take_advance() is False          # one press, one window
    controls.handle("s")                             # no pace to slow
    assert controls.take_advance() is False
    assert controls.interval["v"] == pytest.approx(1.0)
    # 's' must never read as a dead key by touch: the display says why
    # nothing changed instead of a stderr-only line.
    assert controls.engine.flashes == ["manual, no pace"]


def test_mode_switch_drops_a_stale_advance_request(controls):
    controls.advance_mode = ADVANCE_MANUAL
    controls.request_advance()
    controls._cycle_advance_mode()                   # leaves manual
    assert controls.take_advance() is False


# ------------------------------------------------------------- engine layer

def test_flash_never_enters_the_rolling_buffer():
    sink, engine = _rig()
    translator = engine.translator
    engine.flash("hi")
    hi = translator.translate("hi")
    assert sink.frames[-1] == [BLANK] * (8 - len(hi)) + hi
    engine.feed("go ")
    engine.tick()                                    # window 1: one cell
    first = translator.translate("go")[:1]
    # The announcement vanished with the first real write — only content.
    assert sink.frames[-1] == [BLANK] * 7 + first


def test_refresh_gauge_writes_only_on_bucket_change_and_not_paused():
    sink, engine = _rig(gauge=BacklogGauge())
    engine.refresh_gauge()
    assert sink.frames == []                         # caught up: no write
    engine.feed("one two three four five six seven eight nine ten ")
    engine.refresh_gauge()
    assert len(sink.frames) == 1                     # bucket moved: one write
    engine.refresh_gauge()
    assert len(sink.frames) == 1                     # same bucket: silent
    engine.set_paused(True)
    engine.feed(" ".join(["more"] * 40) + " ")
    engine.refresh_gauge()
    assert len(sink.frames) == 1                     # pause: nothing changes


# ------------------------------------------------------- manual page top-up

def test_top_up_fills_the_blank_tail_of_the_current_page_in_place():
    """A short utterance finalized onto a manual page leaves most of the
    line blank; the next utterance must not queue behind an advance press.
    The blank tail was never reading material — new text renders into it
    in place, and the cells already shown never move."""
    sink, engine = _rig()
    translator = engine.translator
    engine.feed("go ", seg="s1", final=True)
    engine.flip()                                    # "go" + space, blank tail
    go = translator.translate("go")
    assert sink.frames[-1][:len(go)] == go
    engine.feed("hi ", seg="s2", final=True)
    assert engine.top_up() > 0
    hi = translator.translate("hi")
    line = go + [BLANK] + hi
    assert sink.frames[-1] == line + [BLANK] * (8 - len(line))
    assert sink.frames[-1][:len(go)] == go           # prefix never moved
    assert engine.shown_source() == "go hi"


def test_top_up_wraps_a_word_that_does_not_fit_the_tail():
    sink, engine = _rig(width=6)
    engine.feed("abcd ")
    engine.flip()                                    # 4 cells + space: 1 blank
    frames = len(sink.frames)
    engine.feed("efg ")                              # 3 cells > 1 of room
    assert engine.top_up() == 0
    assert len(sink.frames) == frames                # display untouched
    assert engine.backlog_words() == 1               # still queued (revisable)
    engine.flip()
    assert engine.shown_source() == "efg"            # it leads the next page


def test_top_up_never_renders_soft_text_and_waits_for_the_harden():
    sink, engine = _rig()
    engine.feed("go ", seg="s1", final=True)
    engine.flip()
    frames = len(sink.frames)
    engine.feed("hi ", seg="s2", final=False)        # soft: queues only
    assert engine.top_up() == 0
    assert len(sink.frames) == frames
    engine.feed("hi ", seg="s2", final=True)         # the segment hardens
    assert engine.top_up() > 0
    assert engine.shown_source() == "go hi"


def test_top_up_respects_pause_and_pan():
    sink, engine = _rig()
    engine.feed("go ")
    engine.flip()
    engine.feed("hi ")
    engine.set_paused(True)
    assert engine.top_up() == 0                      # pause: nothing moves
    engine.set_paused(False)
    engine.feed("yo more words to build history ")
    engine.top_up()
    engine.flip()
    engine.pan_back(2)
    assert engine.top_up() == 0                      # panned: view is frozen
    engine.pan_forward(2)


def test_top_up_keeps_the_pan_history_replaying_the_final_page():
    """The padding lifted by a top-up must leave the history too: a
    replayed page is the page as it was FINALLY read — topped up, not the
    gappy intermediate."""
    sink, engine = _rig()
    engine.feed("go ")
    engine.flip()
    engine.feed("hi ")
    engine.top_up()
    page = sink.frames[-1]
    engine.feed("yolo ")
    engine.flip()                                    # a fresh page
    assert sink.frames[-1] != page
    engine.pan_back(8)                               # one page back
    assert sink.frames[-1] == page                   # replays topped up
    assert "go" in engine.shown_display() and "hi" in engine.shown_display()


# ----------------------------------------------------------- pipeline layer

def test_manual_mode_delivers_page_one_unasked_then_only_on_request():
    # Manual holds pages the reader has READ — a
    # fresh session's page one was never read, so it arrives on its own
    # (a session in manual must never sit dark while text queues). Every
    # page after that waits for an explicit request.
    sink, engine = _rig(window=4)
    translator = engine.translator
    interval = {"v": 0.0}
    controls = PacerControls(engine, interval)
    controls.advance_mode = ADVANCE_MANUAL
    stop = threading.Event()

    async def scenario():
        task = asyncio.ensure_future(run_pipeline(
            _list_source(list("hello world ")), engine, interval, stop,
            controls))
        await asyncio.sleep(0.3)
        assert len(sink.frames) == 1                 # page one, unasked
        await asyncio.sleep(0.3)
        assert len(sink.frames) == 1                 # then it HOLDS
        controls.request_advance()
        await asyncio.sleep(0.3)
        assert len(sink.frames) == 2                 # one ask, one page
        stop.set()
        await task

    asyncio.run(scenario())
    # Full word-wrapped pages: "hello" padded, then "world" on request.
    first_page = translator.translate("hello")
    assert sink.frames[0] == first_page + [BLANK] * (8 - len(first_page))
    second_page = translator.translate("world")
    assert sink.frames[1] == second_page + [BLANK] * (8 - len(second_page))


def test_manual_mode_tops_up_the_current_page_as_text_arrives():
    """The pipeline wiring of the top-up: say 'go', pause, say
    'hi' — the second utterance renders into the current page's blank tail
    on its own, no advance press owed, and no press is consumed."""
    sink, engine = _rig(window=4)
    translator = engine.translator
    interval = {"v": 0.0}
    controls = PacerControls(engine, interval)
    controls.advance_mode = ADVANCE_MANUAL
    stop = threading.Event()

    async def scenario():
        task = asyncio.ensure_future(run_pipeline(
            _open_ended_source("go "), engine, interval, stop, controls))
        await asyncio.sleep(0.3)               # page one arrives unasked
        go = translator.translate("go")
        assert sink.frames[-1] == go + [BLANK] * (8 - len(go))
        engine.feed("hi ")                     # the next utterance lands
        await asyncio.sleep(0.3)
        hi = translator.translate("hi")
        line = go + [BLANK] + hi
        assert sink.frames[-1] == line + [BLANK] * (8 - len(line))
        assert controls.take_advance() is False    # no press was spent
        stop.set()
        await task

    asyncio.run(scenario())


def test_full_window_ticker_flips_word_wrapped_pages_on_its_clock():
    """The full-display window: every tick is a
    word-wrapped page flip, and the pacer's per-cell arithmetic is the
    page dwell — no separate clock, no separate mode."""
    sink, engine = _rig(window=8)                    # width 8, no gauge: full
    translator = engine.translator
    interval = {"v": 0.01}
    controls = PacerControls(engine, interval)
    controls.advance_mode = ADVANCE_TICKER   # paced mode under test
    stop = threading.Event()

    async def scenario():
        task = asyncio.ensure_future(run_pipeline(
            _list_source(list("hello world ")), engine, interval, stop,
            controls))
        await asyncio.sleep(0.6)
        stop.set()
        await task

    asyncio.run(scenario())
    # Word wrap, exactly as manual pages: "hello" padded, then "world".
    first_page = translator.translate("hello")
    assert sink.frames[0] == first_page + [BLANK] * (8 - len(first_page))
    second_page = translator.translate("world")
    assert second_page + [BLANK] * (8 - len(second_page)) in sink.frames


# -------------------------------------------- catch-up summary across modes

class FakeSummarizer:
    """Stands in for braille_engine.summary.Summarizer (no server)."""

    def __init__(self, text="ok", delay=0.0):
        self.text = text
        self.delay = delay
        self.calls = 0

    def summarize(self, text, chars, grade):
        self.calls += 1
        time.sleep(self.delay)
        return self.text


async def _open_ended_source(text, tail_seconds=30):
    """Yield ``text`` then stay open (like live speech mid-session); the
    trailing sleep is cancelled by the pipeline when the test sets stop."""
    for ch in text:
        yield ch
    await asyncio.sleep(tail_seconds)


def test_manual_mode_streams_the_summary_page_by_page(capsys):
    """Manual mode: the summary is ordinary queued text —
    advance presses flip recap pages at the reader's own pace, and a
    jump-to-live press mid-recap skips the rest and snaps to the live
    edge. While the fetch is out, an advance press says so honestly."""
    sink, engine = _rig(window=4)
    interval = {"v": 0.0}
    controls = PacerControls(engine, interval,
                             summarizer=FakeSummarizer("one two three",
                                                       delay=0.3))
    controls.advance_mode = ADVANCE_MANUAL
    stop = threading.Event()

    async def scenario():
        task = asyncio.ensure_future(run_pipeline(
            _open_ended_source("alpha beta gamma delta epsilon zeta "),
            engine, interval, stop, controls))
        await asyncio.sleep(0.3)              # queued; display untouched
        controls.summarize_now()              # backlog > width: summary path
        controls.request_advance()            # pressed during the fetch...
        await asyncio.sleep(0.15)
        assert "summarizing what you missed" in capsys.readouterr().err
        await asyncio.sleep(0.5)              # fetch done, recap queued
        assert controls.catchup == "streaming"
        controls.request_advance()            # first recap page
        await asyncio.sleep(0.3)
        one = engine.translator.translate("one")
        two = engine.translator.translate("two")
        first_page = one + [BLANK] + two
        assert sink.frames[-1] == first_page + [BLANK] * (8 - len(first_page))
        assert controls.catchup == "streaming"
        controls.jump_to_live()               # skip the rest of the recap
        await asyncio.sleep(0.2)
        assert controls.catchup is None
        assert not engine.summary_pending()
        stop.set()
        await task

    asyncio.run(scenario())


def test_full_window_page_dwell_keeps_the_gauge_alive():
    """A full page dwells many cell-intervals while speech keeps
    queueing — the backlog gauge must keep filling between flips, not
    freeze at its flip-time value for the whole dwell."""
    from braille_engine.gauge import BacklogGauge
    sink, engine = _rig(window=8, gauge=BacklogGauge(), width=8)
    interval = {"v": 0.5}                        # page dwell of seconds
    controls = PacerControls(engine, interval)
    controls.advance_mode = ADVANCE_TICKER   # paced mode under test
    stop = threading.Event()

    async def scenario():
        task = asyncio.ensure_future(run_pipeline(
            _open_ended_source("hi "), engine, interval, stop, controls))
        await asyncio.sleep(0.3)                 # first page is up
        assert sink.frames
        page = sink.frames[-1]
        engine.feed(" ".join(["word"] * 30) + " ")   # backlog grows mid-dwell
        await asyncio.sleep(0.8)                 # well inside the dwell
        assert len(sink.frames) > sink.frames.index(page) + 1
        assert sink.frames[-1][0] != page[0]     # gauge cell moved...
        assert sink.frames[-1][2:] == page[2:]   # ...content held still
        stop.set()
        await task

    asyncio.run(scenario())


def test_mode_switch_mid_page_dwell_takes_effect_within_a_slice():
    """Switching to manual mid-dwell must not
    wait out the rest of a page's dwell (up to a display's worth of
    cell-intervals) before the advance key answers."""
    sink, engine = _rig(window=8)
    translator = engine.translator
    interval = {"v": 0.5}                        # page dwell of seconds
    controls = PacerControls(engine, interval)
    controls.advance_mode = ADVANCE_TICKER   # paced mode under test
    stop = threading.Event()

    async def scenario():
        task = asyncio.ensure_future(run_pipeline(
            _open_ended_source("hello world "), engine, interval, stop,
            controls))
        await asyncio.sleep(0.3)                 # first page ("hello") is up
        assert len(sink.frames) == 1
        controls.advance_mode = ADVANCE_MANUAL   # switch mid-dwell...
        controls.request_advance()               # ...and ask for the page
        await asyncio.sleep(0.6)                 # far less than the dwell
        second_page = translator.translate("world")
        assert sink.frames[-1] == \
            second_page + [BLANK] * (8 - len(second_page))
        stop.set()
        await task

    asyncio.run(scenario())


def test_full_window_ticker_flips_the_summary_like_ordinary_pages():
    """Full-display window: the ticker clock keeps firing and the summary
    rides it like ordinary pages; when the recap drains, live speech
    follows and the catch-up ends by itself."""
    sink, engine = _rig(window=8)
    interval = {"v": 0.03}
    controls = PacerControls(engine, interval,
                             summarizer=FakeSummarizer("ok"))
    controls.advance_mode = ADVANCE_TICKER   # paced mode under test
    stop = threading.Event()

    async def scenario():
        task = asyncio.ensure_future(run_pipeline(
            _list_source(list(
                "alpha beta gamma delta epsilon zeta eta theta ")),
            engine, interval, stop, controls))
        await asyncio.sleep(0.4)              # a few flips have happened
        assert len(sink.frames) >= 2
        controls.summarize_now()              # big backlog: summary path
        ok = engine.translator.translate("ok")
        recap_page = ok + [BLANK] * (8 - len(ok))
        for _ in range(40):                   # the recap lands and flips
            if recap_page in sink.frames:
                break
            await asyncio.sleep(0.1)
        assert recap_page in sink.frames
        for _ in range(40):                   # recap drained on the clock
            if controls.catchup is None:
                break
            await asyncio.sleep(0.1)
        assert controls.catchup is None
        stop.set()
        await task

    asyncio.run(scenario())


def test_flash_is_replaced_by_a_repaint_when_the_dwell_ends():
    """A flash must not stick where no organic write follows (manual
    idle, caught-up ticker, a recap page in manual mode): the pace loop
    repaints the real frame once the announce dwell expires."""
    sink, engine = _rig(window=4)
    interval = {"v": 0.0}
    controls = PacerControls(engine, interval)
    controls.advance_mode = ADVANCE_MANUAL
    stop = threading.Event()

    async def scenario():
        task = asyncio.ensure_future(run_pipeline(
            _open_ended_source("hello world "), engine, interval, stop,
            controls))
        await asyncio.sleep(0.2)
        controls.request_advance()
        await asyncio.sleep(0.2)               # first page shown
        page = sink.frames[-1]
        engine.flash("zz")
        controls.announce_until = time.monotonic() + 0.2
        await asyncio.sleep(0.1)
        assert sink.frames[-1] != page          # the flash owns the display
        await asyncio.sleep(0.5)                # dwell over: repaint restores
        assert sink.frames[-1] == page
        assert sink.frames[-1] == engine.frame()
        stop.set()
        await task

    asyncio.run(scenario())


def test_dismissed_flash_is_repainted_even_if_the_pacer_never_saw_it():
    """Dismiss-and-execute zeroes announce_until from the key thread, so
    a claim -> dismiss whose whole lifetime falls between two pacer polls
    could strand the flash on a caught-up display (nothing organic follows
    to replace it). The owed-repaint
    marker set by announce() backstops the pacer's own dwell observation."""
    sink, engine = _rig(window=4)
    interval = {"v": 0.0}
    controls = PacerControls(engine, interval)
    controls.advance_mode = ADVANCE_MANUAL
    stop = threading.Event()

    async def scenario():
        task = asyncio.ensure_future(run_pipeline(
            _open_ended_source("hello world "), engine, interval, stop,
            controls))
        await asyncio.sleep(0.2)
        controls.request_advance()
        await asyncio.sleep(0.2)               # first page shown
        page = sink.frames[-1]
        # Claim + flash + dismiss with no await in between: the pacer
        # cannot have observed the dwell.
        controls.announce("zz")
        controls.dismiss_announce()
        await asyncio.sleep(0.3)
        assert sink.frames[-1] == page          # repainted, not stranded
        stop.set()
        await task

    asyncio.run(scenario())


def test_dismissed_flash_mid_page_dwell_repaints_within_a_slice():
    """The full-window variant of the stranding race: a page dwells many
    cell-intervals, and an announce dismissed inside one 0.25 s slice is
    never seen dwelling by the slice loop either. The owed marker must
    bring the page back within the NEXT slice — not at the next flip."""
    sink, engine = _rig(window=8, width=8)
    interval = {"v": 0.5}                        # page dwell of seconds
    controls = PacerControls(engine, interval)
    controls.advance_mode = ADVANCE_TICKER   # paced mode under test
    stop = threading.Event()

    async def scenario():
        task = asyncio.ensure_future(run_pipeline(
            _open_ended_source("hi "), engine, interval, stop, controls))
        await asyncio.sleep(0.3)                 # first page up, mid-dwell
        page = sink.frames[-1]
        controls.announce("zz")                  # flash owns the display...
        assert sink.frames[-1] != page
        controls.dismiss_announce()              # ...and dies unobserved
        await asyncio.sleep(0.35)                # one slice + margin
        assert sink.frames[-1] == page           # back well inside the dwell
        stop.set()
        await task

    asyncio.run(scenario())


def test_flash_while_paused_renders_and_the_frozen_frame_returns():
    """Pause-safe flashes: a chord answer (mic toggle, grade, status) must
    not vanish while braille is paused — the display is a deaf-blind
    reader's only channel. A flash under pause renders (the reader asked
    for it),
    and the pacer's dwell-end repaint restores the frozen frame: the
    pause contract holds for everything the reader didn't request, and
    nothing advances."""
    sink, engine = _rig(window=4)
    interval = {"v": 0.0}
    controls = PacerControls(engine, interval)
    controls.advance_mode = ADVANCE_MANUAL
    stop = threading.Event()

    async def scenario():
        # Three pages of text: page one arrives unasked (manual
        # first-page delivery), the request below shows page two, and
        # "again" stays queued so the nothing-advanced assert has teeth.
        task = asyncio.ensure_future(run_pipeline(
            _open_ended_source("hello world again "), engine, interval, stop,
            controls))
        await asyncio.sleep(0.2)
        controls.request_advance()
        await asyncio.sleep(0.2)               # page two shown
        page = sink.frames[-1]
        engine.set_paused(True)
        controls.announce("zz")                 # e.g. the mic-toggle answer
        assert sink.frames[-1] != page          # it rendered despite pause
        controls.announce_until = time.monotonic() + 0.2   # shorten the dwell
        await asyncio.sleep(0.6)                # dwell over: pacer repaints
        assert sink.frames[-1] == page          # frozen frame is back...
        assert engine.paused
        assert engine.has_pending()             # ...and nothing advanced
        stop.set()
        await task

    asyncio.run(scenario())


def test_flash_while_paused_repaints_in_ticker_mode_too():
    """The ticker twin of the paused-flash cycle: ticks return 0 while
    paused, but the pacer's announce-dwell wait and dwell-end repaint sit
    before/independent of emission, so the flash -> dwell -> repaint
    cycle still completes on a paused ticker."""
    sink, engine = _rig(window=4)
    interval = {"v": 0.01}
    controls = PacerControls(engine, interval)
    controls.advance_mode = ADVANCE_TICKER   # paced mode under test
    stop = threading.Event()

    async def scenario():
        task = asyncio.ensure_future(run_pipeline(
            _open_ended_source("hello world "), engine, interval, stop,
            controls))
        await asyncio.sleep(0.3)               # some cells streamed
        engine.set_paused(True)
        await asyncio.sleep(0.1)               # any in-flight tick settles
        page = sink.frames[-1]                 # the frozen frame
        controls.announce("zz")
        assert sink.frames[-1] != page
        controls.announce_until = time.monotonic() + 0.2
        await asyncio.sleep(0.6)
        assert sink.frames[-1] == page          # restored, still frozen
        assert engine.paused
        stop.set()
        await task

    asyncio.run(scenario())


def test_the_watchdog_warn_is_flashed_as_an_unsolicited_alert():
    """The pace loop is the only production caller of the important flag
    (run.py's idle warn). Nothing else pins it: the flag's mechanism is
    tested in test_controls, so this test is what fails if
    `important=True` is dropped from the warn. The warn
    is timer-issued, never repeated, and missing it costs the mic — so
    its dwell must exceed the ordinary confirmation ceiling."""
    from braille_engine.controls import ANNOUNCE_CEILING
    from braille_engine.idle_watchdog import IdleWatchdog

    class Clock:
        now = 0.0

        def __call__(self):
            return self.now

    sink, engine = _rig(window=4, width=20)
    # 0.8 s/cell: the 20 shown cells bill 1.5 + 20 * 0.8 * 0.7 = 12.7 s,
    # so an ordinary dwell would be CAPPED to 10 and only the alert class
    # can exceed it. The source stays open but silent, so the loop idles
    # at its polling rate instead of sitting inside a post-emit dwell.
    interval = {"v": 0.8}
    controls = PacerControls(engine, interval)
    clock = Clock()
    controls.idle_watchdog = IdleWatchdog(clock=clock)
    stop = threading.Event()

    async def scenario():
        task = asyncio.ensure_future(run_pipeline(
            _open_ended_source(""), engine, interval, stop, controls))
        await asyncio.sleep(0.2)
        clock.now += 1800.0          # idle long enough for the warn
        deadline = time.monotonic() + 5.0
        while not controls.announce_until:
            assert time.monotonic() < deadline, "the warn never flashed"
            await asyncio.sleep(0.05)
        assert controls.announce_until - time.monotonic() > ANNOUNCE_CEILING
        stop.set()
        await task

    asyncio.run(scenario())


def test_the_watchdog_is_gated_while_the_demo_feeder_streams():
    """The watchdog turns off a microphone nobody is reading, and the
    demo/caption feeder uses no microphone — so while it is the active
    source the attention check must not fire (a "still reading?" / mic-off notice
    mid-caption-replay is noise). The gate skips the poll without
    touching the watchdog's state, so the moment the feeder stops, the
    already-elapsed idle stretch gets its check on the next poll."""
    from braille_engine.idle_watchdog import IdleWatchdog

    class Clock:
        now = 0.0

        def __call__(self):
            return self.now

    class Feeder:
        # Stands in for DemoStream via the demo_active property.
        active = True
        source_label = "captions"

    sink, engine = _rig(window=4, width=20)
    interval = {"v": 0.8}
    controls = PacerControls(engine, interval)
    clock = Clock()
    controls.idle_watchdog = IdleWatchdog(clock=clock)
    controls.demo = Feeder()
    stop = threading.Event()

    async def scenario():
        task = asyncio.ensure_future(run_pipeline(
            _open_ended_source(""), engine, interval, stop, controls))
        await asyncio.sleep(0.2)
        clock.now += 1800.0 + 120.0     # past the warn AND pause windows
        await asyncio.sleep(0.3)        # many pacer polls go by
        assert controls.announce_until == 0.0      # no warn flashed
        assert controls.idle_pause_requests == 0   # no mic pause relayed
        Feeder.active = False           # the feeder stops: metered again
        deadline = time.monotonic() + 5.0
        while not controls.announce_until:
            assert time.monotonic() < deadline, \
                "the warn never fired after the feeder stopped"
            await asyncio.sleep(0.05)
        stop.set()
        await task

    asyncio.run(scenario())


def test_announce_freezes_the_full_window_page_clock():
    """The page-dwell clock must freeze while an announce covers the
    page; otherwise a pace-scaled flash (>= 1.5x a full page's worth of
    dwell) swallows the rest of the page — the dwell-end repaint would
    restore it for one iteration and tick straight past. Frozen, the page
    gets its remaining read time back when the flash ends."""
    sink, engine = _rig(window=8, width=8)
    interval = {"v": 0.25}                       # page dwell 8 cells = 2 s
    controls = PacerControls(engine, interval)
    controls.advance_mode = ADVANCE_TICKER   # paced mode under test
    stop = threading.Event()

    async def until(cond, timeout=10.0):
        deadline = time.monotonic() + timeout
        while not cond():
            assert time.monotonic() < deadline, "condition never held"
            await asyncio.sleep(0.05)

    async def scenario():
        task = asyncio.ensure_future(run_pipeline(
            _open_ended_source("aa bb cc dd ee ff gg hh "), engine,
            interval, stop, controls))
        await until(lambda: sink.frames)         # first page up, mid-dwell
        page = sink.frames[-1]
        n0 = len(sink.frames)
        # Full-width flash: dwell 1.5 + 8 * 0.25 * 0.7 = 2.9 s — longer
        # than the page's whole 2 s dwell, the swallowing case.
        controls.announce("zzzzzzzz")
        await until(lambda: len(sink.frames) > n0)      # flash landed
        assert sink.frames[-1] != page
        # Event-anchored (suite load stretches sleeps): wait for the
        # dwell-end repaint to bring the page back, then measure how long
        # the restored page LASTS. The page's own 2 s dwell ends inside
        # the 3 s flash, so without the freeze the very pacer iteration
        # that repaints also ticks the next page — a gap of ~0. With the
        # freeze the page keeps its unread remainder (nearly the whole
        # 2 s; the flash landed ~50 ms in).
        await until(lambda: page in sink.frames[n0 + 1:])
        i = n0 + 1 + sink.frames[n0 + 1:].index(page)
        restored = time.monotonic()
        await until(lambda: len(sink.frames) > i + 1)
        assert time.monotonic() - restored > 0.4
        stop.set()
        await task

    asyncio.run(scenario())


def test_pause_mid_page_dwell_keeps_the_page_read_time():
    """A reader pause mid-page stops the page's dwell clock: after a pause
    longer than the page's whole dwell, resuming must not flip straight
    past the half-read page."""
    sink, engine = _rig(window=8, width=8)
    interval = {"v": 0.25}                       # page dwell 8 cells = 2 s
    controls = PacerControls(engine, interval)
    stop = threading.Event()

    async def until(cond, timeout=10.0):
        deadline = time.monotonic() + timeout
        while not cond():
            assert time.monotonic() < deadline, "condition never held"
            await asyncio.sleep(0.05)

    async def scenario():
        task = asyncio.ensure_future(run_pipeline(
            _open_ended_source("aa bb cc dd ee ff gg hh "), engine,
            interval, stop, controls))
        await until(lambda: sink.frames)         # first page up, mid-dwell
        engine.set_paused(True)
        await asyncio.sleep(2.5)                 # longer than the dwell
        n = len(sink.frames)
        resumed = time.monotonic()
        engine.set_paused(False)
        await until(lambda: len(sink.frames) > n)
        assert time.monotonic() - resumed > 0.8
        stop.set()
        await task

    asyncio.run(scenario())


def test_manual_advance_on_a_soft_head_does_not_claim_caught_up(capsys):
    """A still-soft queue head blocks the flip with real text queued —
    the status must say the text is pending, never 'caught up'."""
    sink, engine = _rig()
    interval = {"v": 0.0}
    controls = PacerControls(engine, interval)
    controls.advance_mode = ADVANCE_MANUAL
    stop = threading.Event()

    async def scenario():
        task = asyncio.ensure_future(run_pipeline(
            _open_ended_source(""), engine, interval, stop, controls))
        engine.feed("hello ", seg="s1", final=False)   # soft: queues only
        controls.request_advance()
        await asyncio.sleep(0.3)
        stop.set()
        await task

    asyncio.run(scenario())
    err = capsys.readouterr().err
    assert "caught up" not in err
    assert "not yet final" in err
    # The refusal answers ON the display too: the only
    # frame is the "text on its way" flash — the soft text itself never
    # rendered (nothing entered the rolling buffer).
    assert engine.frame_event()["kind"] == "flash"
    assert "text on its way" in engine.frame_event()["text"]
    assert not engine.has_shown_content


def test_manual_advance_when_caught_up_flashes_caught_up(capsys):
    """A manual advance press with nothing to flip must be
    feelable — the display says "caught up" instead of holding still like
    a lost key."""
    sink, engine = _rig(width=20)
    interval = {"v": 0.0}
    controls = PacerControls(engine, interval)
    controls.advance_mode = ADVANCE_MANUAL
    stop = threading.Event()

    async def scenario():
        task = asyncio.ensure_future(run_pipeline(
            _open_ended_source(""), engine, interval, stop, controls))
        controls.request_advance()
        deadline = time.monotonic() + 5.0
        while (engine.frame_event() or {}).get("kind") != "flash":
            assert time.monotonic() < deadline, "the flash never landed"
            await asyncio.sleep(0.02)
        stop.set()
        await task

    asyncio.run(scenario())
    assert "caught up" in engine.frame_event()["text"]
    assert "caught up — nothing to advance" in capsys.readouterr().err


def test_flip_word_wrap_leaves_the_next_word_revisable():
    """The word that doesn't fit the page stays QUEUED, not preloaded into
    the emit buffer: a revision arriving before the next flip must still
    apply to it (freezing it at wrap time would let the display and the
    finalized transcript disagree)."""
    sink, engine = _rig(width=6)
    engine.feed("abc defgh ", seg="s1", final=True)
    shown = engine.flip()
    assert shown == 4                       # "abc" + its trailing space
    assert engine.shown_source() == "abc"
    # The wrapped word is still queued: the gauge counts it...
    assert engine.backlog_words() == 1
    # ...and a correction of it still lands (frozen count is 1, only "abc").
    assert engine.revise_segment("s1", "abc queued ", final=True)
    engine.flip()
    assert engine.shown_source() == "queued"
    assert "defgh" not in engine.shown_source()


def test_flip_word_wrap_retranslates_at_the_flip_time_grade():
    """The wrapped word is translated when it finally streams, not when it
    failed to fit — a display-form change between flips (lowercase filter,
    grade toggle) must govern it."""
    sink, engine = _rig(width=6)
    engine.feed("abc DEFGH ")
    engine.flip()                           # "DEFGH" wraps (capitals cost
    assert engine.shown_source() == "abc"   # indicator cells; 6+ > 2 room)
    engine.lowercase = True                 # display-form change mid-wrap
    engine.flip()
    # 5 letter cells fit a 6-cell page only without capital indicators —
    # the wrapped word was translated at flip time, under the new form.
    assert engine.shown_source() == "DEFGH"
    assert len([c for c in sink.frames[-1] if c != BLANK]) == 5
