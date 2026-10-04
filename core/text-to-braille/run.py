"""Braille reading engine: command-line runner.

Wires a text source -> word assembler -> translator -> engine -> display
sink, and runs the paced output loop with live keyboard controls.

Examples:
    python run.py --file sample.txt            # replay a file, simulated display
    echo "hello world" | python run.py --source stdin
    python run.py --source ws                  # live from the speech server
    python run.py --file sample.txt --sink brlapi   # real display via BRLTTY
"""

import argparse
import asyncio
import os
import sys
import threading
import time

from braille_engine.engine import BrailleEngine
from braille_engine.translator import get_translator
from braille_engine.controls import (
    ADVANCE_MANUAL,
    ADVANCE_TICKER,
    DEFAULT_ADVANCE_MODE,
    MODE_TYPE,
    PacerControls,
    default_interval_s,
    display_owner,
    read_key,
)


def build_sink(name, width):
    if name == "sim":
        from braille_engine.sinks.simulated import SimulatedSink
        return SimulatedSink(width=width)
    if name == "brlapi":
        from braille_engine.sinks.brlapi_sink import BrlapiSink
        return BrlapiSink()
    raise ValueError(f"unknown sink: {name}")


def build_gauge(no_gauge, width=None):
    """Backlog gauge, on by default. ``width=None`` means unknown until the
    sink connects; the engine applies the same too-narrow rule at start()."""
    if no_gauge:
        return None
    if width is not None and width < 4:
        return None
    from braille_engine.gauge import BacklogGauge
    return BacklogGauge()


def build_source(args):
    """Returns ``(source_iter, ack)``. ``ack(seg, text)`` reports a hardened
    segment's effective text back to the speech server, so the page
    transcript prints what the display actually got. None for file/stdin."""
    if args.source == "file" or args.file:
        from braille_engine.sources.file_source import file_source
        path = args.file or "-"
        return file_source(path, char_delay=args.char_delay), None
    if args.source == "stdin":
        from braille_engine.sources.file_source import file_source
        return file_source("-", char_delay=args.char_delay), None
    if args.source == "ws":
        from braille_engine.sources.ws_source import ws_source
        queue = asyncio.Queue(maxsize=64)

        def ack(seg, text):
            # Dropping under backpressure is safe: the server's ack timeout
            # falls back to the provider text for that final.
            try:
                queue.put_nowait({"type": "shown", "id": seg, "text": text})
            except asyncio.QueueFull:
                pass

        return ws_source(args.url, reconnect=not args.ws_once,
                         ack_queue=queue), ack
    raise ValueError(f"unknown source: {args.source}")


async def _sleep_interruptibly(seconds, stop_event):
    """Sleep in short slices so a quit is honoured promptly."""
    remaining = seconds
    while remaining > 0 and not stop_event.is_set():
        step = min(0.25, remaining)
        await asyncio.sleep(step)
        remaining -= step


def wait_for_display(engine, stop_event, initial_delay=1.0, max_delay=10.0):
    """Start the engine, waiting for the display instead of failing when it
    is busy or absent at launch (a screen reader holding it, not plugged in
    yet, BRLTTY not up).

    Retries ``engine.start()`` with backoff (it changes nothing until
    connect() succeeds), logs each distinct reason once, and publishes the
    reason on the engine so platform UIs can speak it. Errors outside the
    sink contract's OSError/RuntimeError still fail fast.

    Returns the display width, or None when the reader quit while waiting.
    """
    delay = initial_delay
    last_reason = None
    while not stop_event.is_set():
        try:
            width = engine.start()
        except (OSError, RuntimeError) as error:
            engine.display_wait_reason = str(error)
            reason = f"{type(error).__name__}: {error}"
            if reason != last_reason:
                last_reason = reason
                sys.stderr.write(f"[warn] braille display not available "
                                 f"({error}); waiting — Dotify connects "
                                 "automatically the moment it is free\n")
            stop_event.wait(delay)
            delay = min(delay * 2, max_delay)
            continue
        engine.display_wait_reason = None
        if last_reason is not None:
            sys.stderr.write("[info] braille display connected; starting\n")
        return width
    return None


async def recover_display(engine, stop_event, error,
                          initial_delay=1.0, max_delay=10.0):
    """Win the display back after a write failure escaped the sink.

    The sink has already spent its own quick retries, so this is a real
    outage. Ticking stops (input keeps queueing, so nothing scrolls past
    unseen), ``sink.connect()`` is retried with backoff, and the reader's
    view is repainted. Returns True to resume, False when the run should end
    (the reader quit, or a display of a different width appeared).
    """
    sys.stderr.write(f"[warn] display connection lost ({error}); incoming "
                     "text keeps queueing while we reconnect\n")
    engine.display_wait_reason = str(error)
    delay = initial_delay
    last_reason = None
    while not stop_event.is_set():
        try:
            width = await asyncio.to_thread(engine.sink.connect)
        except (OSError, RuntimeError) as connect_error:
            # Some reasons are actionable ("another program owns the
            # display"): log each distinct one, and publish it so the
            # browser panel can announce it while the display is dead.
            engine.display_wait_reason = str(connect_error)
            reason = f"{type(connect_error).__name__}: {connect_error}"
            if reason != last_reason:
                last_reason = reason
                sys.stderr.write(f"[warn] display reconnect failing "
                                 f"({connect_error}); still retrying\n")
            await _sleep_interruptibly(delay, stop_event)
            delay = min(delay * 2, max_delay)
            continue
        if width != engine.width:
            engine.display_wait_reason = (
                f"reconnected display has {width} cells but this session is "
                f"sized for {engine.width}; restart Dotify with the new "
                "display")
            sys.stderr.write(f"[error] reconnected display has {width} cells "
                             f"but this session is sized for {engine.width}; "
                             "restart Dotify with the new display\n")
            return False
        try:
            # repaint, not frame(): a panned or paused reader gets back the
            # view they were holding, not the live buffer.
            engine.repaint(paused_ok=True)
        except (OSError, RuntimeError):
            # connect() can keep succeeding while writes fail (BRLTTY up,
            # display detached): back off here too, or this spins.
            await _sleep_interruptibly(delay, stop_event)
            delay = min(delay * 2, max_delay)
            continue
        engine.display_wait_reason = None
        sys.stderr.write("[info] display reconnected; resuming from the "
                         "queue\n")
        return True
    return False


def handle_key_safely(controls, key) -> None:
    """Run one key command without letting a display-write failure kill the
    key thread; the pacer's next tick hits the same error and reconnects."""
    try:
        controls.handle(key)
    except (OSError, RuntimeError) as error:
        sys.stderr.write(f"[warn] display write failed during a key command "
                         f"({error}); the ticker is reconnecting\n")


async def run_pipeline(source_iter, engine, interval, stop_event,
                       controls=None, ack=None):
    """Consume the source into the engine while the pacer shows it.

    ``controls`` (optional) supplies the live modes; in Human mode new speech
    is discarded and the keyboard feeds the engine instead. ``ack``
    (optional, from the ws source) is called with ``(seg, effective_text)``
    after every hardening op.
    """

    def typing():
        return controls is not None and controls.mode == MODE_TYPE

    async def consume():
        first_chunk = True
        first_revise = True
        async for chunk in source_iter:
            if isinstance(chunk, tuple) and chunk[0] == "latency":
                # The render gate is a mode change, not speech: it applies
                # in Human mode too.
                engine.set_eager(chunk[1] == "eager")
                continue
            if typing() and not (isinstance(chunk, tuple)
                                 and engine.knows_segment(chunk[1])):
                # Human mode discards new speech, but ops for segments
                # already queued still apply: dropping a final would strand
                # its soft text forever (the source never re-sends it).
                continue
            if first_chunk:
                # With this line logged but nothing on the display, the
                # fault is in the tick/write path; without it, in the source.
                sys.stderr.write("[info] live text is flowing "
                                 "from the source\n")
                first_chunk = False
            # Plain text (file/stdin) or op tuples (ws):
            # ("feed", seg, text, final) / ("revise", seg, text, final).
            if isinstance(chunk, str):
                engine.feed(chunk)
            elif chunk[0] == "feed":
                engine.feed(chunk[2], seg=chunk[1], final=chunk[3])
            elif chunk[0] == "revise":
                if engine.revise_segment(chunk[1], chunk[2],
                                         final=chunk[3]):
                    if first_revise:
                        sys.stderr.write("[info] segment revisions are being "
                                         "applied to the queue\n")
                        first_revise = False
                elif chunk[2].strip() and not engine.knows_segment(chunk[1]):
                    # The engine never saw this segment (its first delivery
                    # was discarded, e.g. in Human mode, or it was dropped as
                    # a stale orphan): feed it fresh rather than lose it.
                    engine.feed(chunk[2].rstrip() + " ",
                                seg=chunk[1], final=chunk[3])
            if (ack is not None and isinstance(chunk, tuple)
                    and chunk[0] in ("feed", "revise")
                    and chunk[3] and chunk[1] is not None):
                shown = engine.segment_text(chunk[1])
                if shown is not None:
                    ack(chunk[1], shown)
        # End of source: the soft text on hand is final, so let it render,
        # then commit the partial speech word. In Human mode the shared
        # assembler holds the reader's half-typed word, which only they may
        # commit.
        engine.harden_all()
        if not typing():
            engine.flush_input()

    async def pace():
        # Runs until input is done and the queue drained, or a stop. Auto
        # mode (ADVANCE_TICKER) ticks on the per-cell clock; manual flips
        # only on request, and does so here rather than on the key thread
        # so a display outage always surfaces on this one error path.
        consumer = asyncio.ensure_future(consume())
        dwelled = False      # an announcement flash just finished its dwell
        while not stop_event.is_set():
            mode = controls.advance_mode if controls else ADVANCE_TICKER
            if controls and not controls.demo_active:
                # The idle watchdog turns off a microphone nobody is
                # reading; the demo feeder uses no microphone, so it is not
                # consulted while the feeder runs.
                idle_event = controls.idle_watchdog.poll()
                if idle_event == "warn":
                    # Unsolicited and not repeatable: missing it costs the
                    # mic.
                    controls.announce("still reading? press any key",
                                      important=True)
                elif idle_event == "pause":
                    controls.idle_pause_requests += 1
            if controls and display_owner(controls):
                # The reply echo owns the display: no ticks, gauge updates or
                # repaints; speech keeps queueing. end_reply restores.
                await asyncio.sleep(0.05)
                continue
            if controls and time.monotonic() < controls.announce_until:
                dwelled = True
                await asyncio.sleep(0.05)
                continue
            emitted = 0
            # The owed-repaint marker covers a flash dismissed between two
            # polls of this loop, whose dwell it never observed.
            owed = controls.take_announce_repaint() if controls else False
            try:
                if dwelled or owed:
                    # Restore the real frame, or the flash sticks wherever no
                    # write follows (manual mode, a caught-up display, a
                    # paused reader).
                    dwelled = False
                    engine.repaint(paused_ok=True)
                if mode == ADVANCE_TICKER:
                    emitted = engine.tick()
                elif controls.take_advance():          # manual, page asked
                    emitted = engine.flip()
                    if not emitted:
                        _explain_empty_flip(engine, controls)
                elif not engine.has_shown_content:
                    # A fresh session's first page was never read, so manual
                    # delivers it unasked rather than sit dark.
                    emitted = engine.flip()
                else:
                    # Holding still, but text that arrives while the page has
                    # blank room renders into it.
                    emitted = engine.top_up()
                    if not emitted:
                        engine.refresh_gauge()
            except (OSError, RuntimeError) as error:
                if not await recover_display(engine, stop_event, error):
                    stop_event.set()
                    break
                continue
            if not emitted:
                if controls and not engine.has_pending() \
                        and not getattr(engine, "pan_offset", 0) \
                        and not getattr(engine, "paused", False):
                    # A demo or caption track has fully drained: leave an
                    # "ended" frame up so the reader knows nothing more is
                    # coming. Consumed once per run; a pan or pause defers
                    # it. getattr: test fakes are minimal.
                    take_ended = getattr(controls.demo, "take_ended", None)
                    label = take_ended() if take_ended else ""
                    if label:
                        try:
                            engine.flash(f"{label} ended")
                        except (OSError, RuntimeError):
                            pass   # display outage: the pacer reconnects
                        controls._status_line(
                            f"{label} ended — the whole track has been "
                            "shown")
                # Stay alive while a summary fetch is in flight (the queue
                # is empty then, and the recap must not land on a dead
                # loop) and while the reader is panned back.
                if consumer.done() and not engine.has_pending() \
                        and not typing() \
                        and not (controls and controls.catchup) \
                        and not getattr(engine, "pan_offset", 0):
                    break
                for seg in engine.drop_stale_soft():
                    sys.stderr.write(
                        "[warn] dropped soft segment "
                        f"{seg}: it never hardened (stream gap?) — "
                        "its undelivered words are lost, later text "
                        "now flows\n")
                await asyncio.sleep(min(0.02, interval["v"])
                                    if mode == ADVANCE_TICKER else 0.05)
                continue
            if mode == ADVANCE_TICKER:
                await _dwell(engine, controls, interval, emitted, stop_event)
            # Manual: no sleep after a flip; the reader is the clock.
        if not consumer.done():
            consumer.cancel()
        elif not consumer.cancelled() and consumer.exception() is not None:
            sys.stderr.write(f"[error] text source failed: "
                             f"{consumer.exception()}\n")

    await pace()


def _explain_empty_flip(engine, controls):
    """Answer a manual advance that flipped nothing, on the display, so it
    can't be mistaken for a lost key press."""
    if controls.catchup == "fetching":
        # No flash: flashes are refused under the fetch hold, and the
        # press is already answered by the hold's own frame.
        controls._status_line(
            "summarizing what you missed — the recap "
            "flips like ordinary pages when it lands")
    elif getattr(engine, "paused", False):
        controls._status_line("display is paused")
        controls.announce("display paused")
    elif getattr(engine, "pan_offset", 0):
        controls._status_line(
            "viewing history — thumb Right pans "
            "forward; thumb Next jumps to live")
        controls.announce("viewing history")
    elif engine.has_pending():
        # Queued, but the head segment has not hardened.
        controls._status_line(
            "text is on its way (not yet final) — it "
            "streams the moment it settles")
        controls.announce("text on its way")
    else:
        controls._status_line("caught up — nothing to advance")
        controls.announce("caught up")


async def _dwell(engine, controls, interval, emitted, stop_event):
    """Sleep after an auto-mode tick for its cells' read time.

    ``interval`` is seconds per cell and the engine prices the tick's cells
    by dwell class (``last_tick_cost``), so reading speed is the same at any
    window size; at the full-display window this is the page dwell. The
    sleep is sliced so a speed or mode change takes effect mid-dwell and a
    quit is prompt.
    """
    cost = getattr(engine, "last_tick_cost", None)
    if cost is None or cost <= 0:
        cost = emitted
    started = time.monotonic()
    page = getattr(engine, "window_is_full", False)
    was_announcing = False
    while not stop_event.is_set():
        remaining = (interval["v"] * cost
                     - (time.monotonic() - started))
        if remaining <= 0:
            break
        slice_began = time.monotonic()
        await asyncio.sleep(min(0.25, remaining))
        if controls is None:
            continue
        if controls.advance_mode != ADVANCE_TICKER:
            break        # a switch to manual applies within a slice
        if display_owner(controls):
            # A gauge write now would paint over the reply echo.
            continue
        announcing = time.monotonic() < controls.announce_until
        if getattr(engine, "paused", False):
            # A reader pause stops the reading clock too, so the page keeps
            # its unread time for the resume.
            started += time.monotonic() - slice_began
        elif page and announcing:
            # A flash covers the page: stop its clock for the covered
            # slices, or a long flash swallows the rest of the page.
            started += time.monotonic() - slice_began
        if page and not announcing:
            # A page holds for many cell-intervals: keep the gauge current,
            # and when a flash's dwell ends inside the page dwell, bring the
            # page back now. The owed marker is consumed only here, at the
            # full window; smaller windows leave it for the outer loop.
            # A failed write ends the dwell; the next tick reconnects.
            owed_now = controls.take_announce_repaint()
            try:
                if was_announcing or owed_now:
                    engine.repaint(paused_ok=True)
                else:
                    engine.refresh_gauge()
            except (OSError, RuntimeError):
                break
        was_announcing = announcing


def install_stop_signals(stop_event):
    """POSIX: make SIGTERM/SIGHUP a clean quit. Default signal death skips
    the atexit hook that takes the terminal out of raw mode (leaving the
    shell with echo off) and skips ``sink.close()``."""
    if sys.platform == "win32":
        return
    import signal
    for name in ("SIGTERM", "SIGHUP"):
        signum = getattr(signal, name, None)
        if signum is not None:
            signal.signal(signum, lambda *_: stop_event.set())


def start_controls(controls, stop_event):
    def loop():
        while not stop_event.is_set():
            key = read_key()
            if key is None:      # swallowed arrow/function-key sequence
                continue
            if key == "":
                break
            handle_key_safely(controls, key)
            if controls.stop:
                stop_event.set()
                break

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    return t


def start_display_keys(engine, controls):
    """Take commands from the display's own keys.

    Any sink that can deliver key reports exposes
    ``start_key_listener(keys_changed)`` (the BrlAPI sink and the Windows
    native sinks); with others the terminal keys are the only controls.
    Returns the DisplayKeyControls, or None when unavailable.
    """
    starter = getattr(engine.sink, "start_key_listener", None)
    if starter is None:
        return None
    from braille_engine.display_keys import DisplayKeyControls

    display_keys = DisplayKeyControls(controls)
    try:
        starter(display_keys.keys_changed)
    except Exception as error:
        sys.stderr.write(f"[warn] display keys unavailable ({error}); "
                         "terminal keys still work\n")
        return None
    sys.stderr.write("[keys] display keys active: thumb Left/Right = "
                     "pan back/forward, thumb Previous = advance mode "
                     "(auto/manual), thumb Next = jump to live (plain "
                     "snap), Space+dot1/dot4 = slower/faster, "
                     "Space+dot3 = pause "
                     "braille, Space+dot6 = pause the input source, "
                     "Space+G = grade, "
                     "Space+W = cell window, Space+S = summarize, "
                     "Space+I = status, "
                     "Space+R = type a reply (Space+R again ends it)\n")
    return display_keys


def build_parser():
    p = argparse.ArgumentParser(description="Dotify braille reading engine")
    p.add_argument("--source", choices=["file", "stdin", "ws"], default="file")
    p.add_argument("--file", help="path to replay ('-' for stdin)")
    p.add_argument("--url", default="ws://localhost:8788/finalized")
    p.add_argument("--ws-once", action="store_true",
                   help="exit when the finalized stream closes instead of "
                        "reconnecting forever (dev/testing; the default "
                        "rides out speech-server restarts)")
    p.add_argument("--translator", choices=["auto", "liblouis", "dev"],
                   default="auto")
    p.add_argument("--grade", type=int, choices=[1, 2, 3], default=2,
                   help="starting braille grade: 2 contracted (default; "
                        "liblouis only, falls back to 1 with a warning), "
                        "1 uncontracted, or experimental English 3; "
                        "cycle live with 'g'")
    p.add_argument("--sink", choices=["sim", "brlapi"], default="sim")
    p.add_argument("--width", type=int, default=40,
                   help="simulated display width (ignored for brlapi)")
    p.add_argument("--pace", type=float, default=None,
                   help="ms per cell (initial). Default: sized to the "
                        "connected display so a full page lasts 5 s, the "
                        "JAWS auto-advance default. An explicit value is "
                        "never resized")
    p.add_argument("--window", type=int, default=None, metavar="N",
                   help="new cells per refresh in auto mode (default: the "
                        "display's full content width, so every refresh is "
                        "a word-wrapped page flip on the reading clock). "
                        "Smaller windows stream N new cells per refresh "
                        "(1 is ticker-tape scrolling); values past the "
                        "content width clamp to it; cycle live with 'w'. "
                        "Manual mode always flips the full display")
    p.add_argument("--advance-mode",
                   choices=[ADVANCE_MANUAL, "auto", ADVANCE_TICKER],
                   default=DEFAULT_ADVANCE_MODE,
                   help="starting reading advance mode (default auto: "
                        "pages flip on a steady clock, or N cells stream "
                        "per refresh with a smaller --window; toggle live "
                        "with 'r'). manual: nothing moves until the reader "
                        "flips the next page (f / thumb Right). 'ticker' is "
                        "an alias of auto")
    p.add_argument("--char-delay", type=float, default=0.05,
                   help="seconds between replayed characters")
    p.add_argument("--no-gauge", action="store_true",
                   help="disable the backlog gauge (cells 1-2: how far "
                        "behind live the reader is)")
    p.add_argument("--no-display-keys", action="store_true",
                   help="ignore key input from the braille display itself "
                        "(Perkins chords / thumb keys); terminal keys only")
    p.add_argument("--no-summary", action="store_true",
                   help="disable summarize-on-demand (Space+S / terminal "
                        "shift+S asks the speech server to summarize the "
                        "backlog); with it off the command degrades to the "
                        "plain snap. The jump itself never summarizes")
    p.add_argument("--space-time", type=int, default=100, metavar="PCT",
                   help="percent of the per-cell pace a SPACE cell dwells "
                        "(1-100, default 100). Lower makes the word gap "
                        "flash past instead of costing a full refresh; the "
                        "text itself is untouched")
    p.add_argument("--punct-time", type=int, default=100, metavar="PCT",
                   help="percent of the per-cell pace a punctuation cell "
                        "dwells (1-100, default 100); like --space-time, "
                        "timing only — nothing is stripped")
    p.add_argument("--lowercase", action="store_true",
                   help="translate everything lowercased so no cell is "
                        "spent on capital indicators (display only; the "
                        "transcript keeps its capitals). Default off")
    p.add_argument("--no-digits", action="store_true",
                   help="keep spoken numbers as words instead of rewriting "
                        "them as numerals ('twenty five' stays words "
                        "rather than becoming '25')")
    return p


def build_summarizer(args):
    """On-demand summarizer for live (ws) runs. The endpoint is derived from
    the finalized-text URL (same server); DOTIFY_SUMMARY_URL overrides it.
    File/stdin replays have no server, so the command degrades to the plain
    snap there."""
    if args.no_summary or args.source != "ws":
        return None
    from braille_engine.summary import Summarizer, summary_url_from_ws
    url = os.environ.get("DOTIFY_SUMMARY_URL") or summary_url_from_ws(args.url)
    return Summarizer(url) if url else None


def main(argv=None):
    # Braille glyphs need UTF-8; the Windows console defaults to cp1252.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            try:
                reconfigure(encoding="utf-8")
            except (ValueError, OSError):
                pass

    args = build_parser().parse_args(argv)
    if args.window is not None and args.window < 1:
        build_parser().error("--window must be at least 1")
    if args.pace is not None and args.pace <= 0:
        build_parser().error("--pace must be a positive number of ms per "
                             "cell (0 would stream faster than any reader)")
    if args.file and args.source != "file":
        build_parser().error(f"--file replays a file, but --source "
                             f"{args.source} was requested; drop one")

    translator, tname = get_translator(args.translator, grade=args.grade)
    if tname == "dev":
        sys.stderr.write(
            "[warn] liblouis unavailable - using the pure-Python dev UEB "
            "stand-in. Install python3-louis (Linux/WSL) for real UEB.\n")
        if args.grade in (2, 3):
            sys.stderr.write(
                f"[warn] grade {args.grade} needs liblouis; starting in grade 1.\n")

    sink = build_sink(args.sink, args.width)
    gauge = build_gauge(args.no_gauge,
                        width=args.width if args.sink == "sim" else None)
    engine = BrailleEngine(translator, sink, gauge=gauge,
                           window=args.window or 1)
    if args.window is None:
        # Full display by default; sized to the real width at start().
        engine.set_window_full()
    if not engine.set_space_dwell(args.space_time):
        build_parser().error("--space-time must be a percent from 1 to 100")
    if not engine.set_punct_dwell(args.punct_time):
        build_parser().error("--punct-time must be a percent from 1 to 100")
    engine.set_lowercase(args.lowercase)
    engine.set_digits_filter(not args.no_digits)

    if args.advance_mode == "auto":
        args.advance_mode = ADVANCE_TICKER

    # Without --pace the default is seconds per page, sized to the real
    # content width once the display connects; this provisional value only
    # matters for a speed key pressed while waiting.
    derive_pace = args.pace is None
    interval = {"v": args.pace / 1000.0 if not derive_pace
                else default_interval_s(max(1, args.width - 2))}
    # Shared by the asyncio loop and the key thread; a real Event so waiters
    # wake the instant the reader quits.
    stop_event = threading.Event()
    install_stop_signals(stop_event)
    controls = PacerControls(engine, interval,
                             summarizer=build_summarizer(args))
    if controls.summarizer:
        sys.stderr.write("[info] summarize-on-demand on: Space+S (terminal "
                         "shift+S) streams a summary sized to what you "
                         "missed, with s in cell 2 while it plays (press "
                         "again to skip it; the jump itself always snaps "
                         "plain)\n")

    source_iter, source_ack = build_source(args)
    # Controls (and on Windows the browser bridge they carry) come up before
    # the display connects: if another program owns the display at launch,
    # the app stays up, says why on the browser page, and connects the
    # moment the display is free. Quit works while waiting.
    start_controls(controls, stop_event)
    try:
        width = wait_for_display(engine, stop_event)
        if width is None:
            return   # the reader quit while waiting
        if derive_pace:
            interval["v"] = default_interval_s(engine.content_width)
        grade = getattr(translator, "grade", 1)
        sys.stderr.write(f"[info] translator={tname} grade={grade} "
                         f"sink={args.sink} width={width} "
                         f"gauge={'on' if engine.gauge else 'off'} "
                         f"pace={interval['v'] * 1000:.0f}ms/cell "
                         f"window={engine.window}\n")
        if controls.summarizer and not engine.gauge:
            # Checked after start(), which drops the gauge on a display
            # narrower than 4 cells. The s marker lives in the gauge's
            # separator cell.
            sys.stderr.write("[warn] no gauge, so no cell-2 s marker: the "
                             "on-demand summary will read like live text "
                             "with nothing marking it as a recap; pass "
                             "--no-summary if that distinction matters\n")
        sys.stderr.write("[keys] f/s faster/slower, g = grade 1/2/3 cycle, "
                         "w = cell window (full display = pages), "
                         "r = advance mode (auto/manual), "
                         "p = pause/resume, "
                         "space or l = jump to live (plain snap), "
                         "S = summarize the backlog, q = quit, "
                         "Tab = human mode (keys become content; "
                         "Ctrl+G/F/S/W/R/P "
                         "= grade/faster/slower/window/advance-mode/pause)\n")
        if args.advance_mode != controls.advance_mode:
            # Announced only for a non-default start, once the display is up.
            controls.set_advance_mode(args.advance_mode)
        if not args.no_display_keys:
            start_display_keys(engine, controls)
        asyncio.run(run_pipeline(source_iter, engine, interval, stop_event,
                                 controls, source_ack))
    except KeyboardInterrupt:
        pass
    finally:
        sink.close()


if __name__ == "__main__":
    main()
