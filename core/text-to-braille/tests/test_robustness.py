"""Reconnect-instead-of-restart behavior.

Two failure seams that must not be fail-stop:
  * display writes — the pacer must hold the queue through an outage, win the
    display back with sink.connect(), repaint the frame, and resume;
  * the finalized WebSocket — ws_source must ride out speech-server restarts.
"""

import asyncio
import json
import pathlib
import subprocess
import sys
import threading
import time

import pytest

from braille_engine.cells import BLANK
from braille_engine.engine import BrailleEngine
from braille_engine.sinks.simulated import SimulatedSink
from braille_engine.translator import DevUebTranslator
from run import (run_pipeline, recover_display, handle_key_safely,
                 wait_for_display)

try:
    import websockets
except ImportError:          # pragma: no cover - optional extra
    websockets = None


async def _list_source(items):
    for it in items:
        yield it


class FlakySink(SimulatedSink):
    """Simulated display whose connection can die and come back.

    ``fail_write_numbers``: 1-based write() calls that raise OSError (the
    unplug). ``connect_failures``: how many post-start connect() attempts
    fail before the display "reappears".
    """

    def __init__(self, width=8, fail_write_numbers=(), connect_failures=0,
                 reconnect_width=None):
        super().__init__(width=width, echo=False)
        self.fail_write_numbers = set(fail_write_numbers)
        self.connect_failures = connect_failures
        self.reconnect_width = reconnect_width
        self.connects = 0
        self.writes = 0

    def connect(self):
        self.connects += 1
        if self.connects > 1:            # a recovery attempt, not start()
            if self.connect_failures > 0:
                self.connect_failures -= 1
                raise OSError(22, "display still absent")
            if self.reconnect_width is not None:
                return self.reconnect_width
        return super().connect()

    def write(self, cells):
        self.writes += 1
        if self.writes in self.fail_write_numbers:
            raise OSError(22, "simulated unplug")
        super().write(cells)


def _engine(sink):
    engine = BrailleEngine(DevUebTranslator(), sink)
    engine.start()
    return engine


def test_pipeline_survives_display_outage():
    """An unplug mid-stream must not end the run or lose queued text."""
    width = 8
    sink = FlakySink(width=width, fail_write_numbers={2})
    engine = _engine(sink)
    translator = engine.translator

    interval = {"v": 0.0}
    stop = threading.Event()
    source = _list_source(list("hi bob"))

    asyncio.run(run_pipeline(source, engine, interval, stop))

    # Everything fed during and after the outage reached the display.
    expected_cells = (
        translator.translate("hi") + [BLANK] + translator.translate("bob")
    )
    expected_frame = expected_cells[-width:]
    expected_frame = [BLANK] * (width - len(expected_frame)) + expected_frame
    assert sink.frames[-1] == expected_frame
    assert engine.has_pending() is False
    assert sink.connects == 2            # start() + one recovery


def test_recover_display_retries_until_display_returns():
    sink = FlakySink(width=8, connect_failures=2)
    engine = _engine(sink)
    stop = threading.Event()

    resumed = asyncio.run(recover_display(
        engine, stop, OSError("unplugged"),
        initial_delay=0.01, max_delay=0.02))

    assert resumed is True
    assert sink.connects == 4            # start() + 2 failures + 1 success
    # The reader's frame was repainted the instant the display came back.
    assert sink.frames[-1] == engine.frame()


def test_recover_display_restores_a_panned_view_not_the_live_buffer():
    sink = FlakySink(width=8)
    engine = _engine(sink)
    engine.feed("aa bb cc dd ee ff ")
    while engine.tick():
        pass
    assert engine.pan_back(4)
    panned_frame = sink.frames[-1]
    assert panned_frame != engine.frame()

    resumed = asyncio.run(recover_display(
        engine, threading.Event(), OSError("unplugged"), initial_delay=0.01))

    assert resumed is True
    assert engine.pan_offset == 4
    assert sink.frames[-1] == panned_frame


def test_recover_display_rejects_a_different_width_display():
    sink = FlakySink(width=8, reconnect_width=20)
    engine = _engine(sink)
    stop = threading.Event()

    resumed = asyncio.run(recover_display(
        engine, stop, OSError("unplugged"), initial_delay=0.01))

    assert resumed is False              # session is sized to 8 cells
    assert sink.frames == []             # nothing was written to the stranger


def test_recover_display_honours_reader_quit():
    sink = FlakySink(width=8, connect_failures=10 ** 6)
    engine = _engine(sink)
    stop = threading.Event()
    stop.set()                           # reader pressed q during the outage

    resumed = asyncio.run(recover_display(
        engine, stop, OSError("unplugged"), initial_delay=0.01))

    assert resumed is False


def test_recover_display_reports_why_reconnect_fails(capsys):
    """A failing reconnect must say WHY (once per distinct reason), so an
    actionable permanent failure ("Close NVDA...", "multiple compatible
    displays...") is diagnosable from the log instead of looping invisibly
    forever."""
    sink = FlakySink(width=8, connect_failures=3)   # same reason each time
    engine = _engine(sink)
    stop = threading.Event()

    resumed = asyncio.run(recover_display(
        engine, stop, OSError("unplugged"),
        initial_delay=0.01, max_delay=0.02))

    assert resumed is True
    err = capsys.readouterr().err
    assert err.count("display reconnect failing") == 1   # not per attempt
    assert "display still absent" in err                 # the actual reason


def test_recover_display_backs_off_when_repaint_keeps_failing():
    """connect() can keep succeeding while writes fail (BRLTTY daemon up,
    display detached): the repaint-failure path must back off like a failed
    connect, not spin connect/write/fail at full speed."""

    class DaemonUpDisplayGone(FlakySink):
        def write(self, cells):
            self.writes += 1
            raise OSError(22, "display detached from daemon")

    sink = DaemonUpDisplayGone(width=8)
    engine = _engine(sink)
    stop = threading.Event()

    async def scenario():
        task = asyncio.ensure_future(recover_display(
            engine, stop, OSError("unplugged"),
            initial_delay=0.05, max_delay=0.2))
        await asyncio.sleep(0.4)
        stop.set()
        return await task

    resumed = asyncio.run(scenario())

    assert resumed is False              # quit landed during the outage
    # Backoff pacing: ~4 attempts fit in 0.4s; an unthrottled spin would
    # rack up thousands of connects.
    assert sink.connects <= 15


def test_handle_key_safely_swallows_display_errors():
    class DeadDisplayControls:
        def __init__(self):
            self.seen = []

        def handle(self, key):
            self.seen.append(key)
            raise OSError(22, "display gone")

    controls = DeadDisplayControls()
    handle_key_safely(controls, " ")     # must not raise
    assert controls.seen == [" "]


# --- BrlAPI sink edge: what counts as a display failure ---------------------

class _FakeBrlapiConn:
    def __init__(self, write_error=None):
        self.write_error = write_error
        self.payloads = []

    def writeDots(self, payload):
        if self.write_error is not None:
            raise self.write_error
        self.payloads.append(payload)


def test_brlapi_write_normalizes_writedots_failures():
    """Library-specific write failures become RuntimeError so the pacer's
    display recovery recognizes them."""
    from braille_engine.sinks.brlapi_sink import BrlapiSink

    sink = BrlapiSink()
    sink.width = 4
    sink._conn = _FakeBrlapiConn(write_error=Exception("daemon went away"))
    with pytest.raises(RuntimeError, match="BrlAPI write failed"):
        sink.write([0, 0, 0, 0])


def test_brlapi_write_lets_a_malformed_frame_raise_raw():
    """A cell outside 0..255 is a programming error, not a display outage:
    it must NOT be dressed up as RuntimeError and fed to the reconnect
    loop."""
    from braille_engine.sinks.brlapi_sink import BrlapiSink

    sink = BrlapiSink()
    sink.width = 4
    sink._conn = _FakeBrlapiConn()
    with pytest.raises(ValueError):
        sink.write([999, 0, 0, 0])


def test_brlapi_close_never_raises_and_is_idempotent():
    """close() runs on quit — possibly mid-outage on a dead connection —
    and must not turn a clean exit into a traceback."""
    from braille_engine.sinks.brlapi_sink import BrlapiSink

    class DeadConn:
        def leaveTtyMode(self):
            raise Exception("connection lost")

        def closeConnection(self):
            raise Exception("connection lost")

    sink = BrlapiSink()
    sink._conn = DeadConn()
    sink.close()                         # must not raise
    assert sink._conn is None
    sink.close()                         # second close: a no-op


# --- ws_source reconnect ----------------------------------------------------

def _final(text):
    return json.dumps({"type": "final", "text": text})


@pytest.mark.skipif(websockets is None, reason="websockets not installed")
def test_ws_source_reconnects_across_server_restart():
    from braille_engine.sources.ws_source import ws_source

    async def scenario():
        texts = ["hello", "again"]

        async def handler(ws, *_path):
            await ws.send(_final(texts.pop(0)))
            await ws.close()

        server = await websockets.serve(handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        gen = ws_source(f"ws://127.0.0.1:{port}/finalized",
                        initial_delay=0.05, max_delay=0.1)
        received = [await asyncio.wait_for(anext(gen), 5)]

        # Speech server restart: old process gone, new one on the same port.
        server.close()
        await server.wait_closed()
        server2 = await websockets.serve(handler, "127.0.0.1", port)
        try:
            received.append(await asyncio.wait_for(anext(gen), 5))
        finally:
            await gen.aclose()
            server2.close()
            await server2.wait_closed()
        return received

    assert asyncio.run(scenario()) == [("feed", None, "hello ", True),
                                       ("feed", None, "again ", True)]


@pytest.mark.skipif(websockets is None, reason="websockets not installed")
def test_ws_source_once_ends_when_the_stream_closes():
    from braille_engine.sources.ws_source import ws_source

    async def scenario():
        async def handler(ws, *_path):
            await ws.send(_final("only"))
            await ws.close()

        server = await websockets.serve(handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            received = []
            async for text in ws_source(f"ws://127.0.0.1:{port}/finalized",
                                        reconnect=False):
                received.append(text)
            return received              # generator ended: old behavior
        finally:
            server.close()
            await server.wait_closed()

    assert asyncio.run(scenario()) == [("feed", None, "only ", True)]


@pytest.mark.skipif(websockets is None, reason="websockets not installed")
def test_ws_source_once_surfaces_an_abnormal_close():
    """--ws-once preserves the OLD contract: a server that dies mid-stream
    must raise, not exit 0 looking like a finished session."""
    from braille_engine.sources.ws_source import ws_source

    async def scenario():
        async def handler(ws, *_path):
            await ws.send(_final("cut"))
            await ws.close(code=1011, reason="server crash")

        server = await websockets.serve(handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        gen = ws_source(f"ws://127.0.0.1:{port}/finalized", reconnect=False)
        try:
            assert await asyncio.wait_for(anext(gen), 5) == ("feed", None, "cut ", True)
            with pytest.raises(websockets.exceptions.ConnectionClosedError):
                await asyncio.wait_for(anext(gen), 5)
        finally:
            await gen.aclose()
            server.close()
            await server.wait_closed()

    asyncio.run(scenario())


@pytest.mark.skipif(websockets is None, reason="websockets not installed")
def test_ws_source_survives_non_object_json_frames():
    """Valid JSON that isn't an object (42, "ok", ["ping"]) must be ignored,
    not raise AttributeError on msg.get() and kill the pipeline."""
    from braille_engine.sources.ws_source import ws_source

    async def scenario():
        async def handler(ws, *_path):
            await ws.send("42")
            await ws.send('"ok"')
            await ws.send('["ping"]')
            await ws.send(_final("real"))
            await ws.close()

        server = await websockets.serve(handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        gen = ws_source(f"ws://127.0.0.1:{port}/finalized", reconnect=False)
        try:
            return await asyncio.wait_for(anext(gen), 5)
        finally:
            await gen.aclose()
            server.close()
            await server.wait_closed()

    assert asyncio.run(scenario()) == ("feed", None, "real ", True)


@pytest.mark.skipif(websockets is None, reason="websockets not installed")
def test_ws_source_backs_off_when_server_accepts_then_closes():
    """A server that accepts the handshake and instantly closes must keep
    the backoff growing (delay resets only once a message arrives), not
    reconnect at full speed."""
    from braille_engine.sources.ws_source import ws_source

    async def scenario():
        slams = 3

        async def handler(ws, *_path):
            nonlocal slams
            if slams > 0:
                slams -= 1
                await ws.close()         # accept-then-close, no message
                return
            await ws.send(_final("finally"))
            await ws.close()

        server = await websockets.serve(handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        gen = ws_source(f"ws://127.0.0.1:{port}/finalized",
                        initial_delay=0.05, max_delay=0.2)
        started = time.monotonic()
        try:
            text = await asyncio.wait_for(anext(gen), 10)
        finally:
            await gen.aclose()
            server.close()
            await server.wait_closed()
        return text, time.monotonic() - started

    text, elapsed = asyncio.run(scenario())
    assert text == ("feed", None, "finally ", True)
    # Three slams paced by growing backoff (0.05 + 0.1 + 0.2); a hot loop
    # would burn through them in milliseconds.
    assert elapsed >= 0.3


@pytest.mark.skipif(websockets is None, reason="websockets not installed")
def test_ws_source_imports_are_self_contained():
    """Regression: websockets lazy-loads submodules, so ws_source must
    import websockets.exceptions itself. In-process tests can't catch this
    (websockets.serve above pre-imports it), so probe a fresh interpreter."""
    code = (
        "import asyncio\n"
        "from braille_engine.sources.ws_source import ws_source\n"
        "async def main():\n"
        "    gen = ws_source('ws://127.0.0.1:9/finalized', reconnect=False)\n"
        "    try:\n"
        "        await anext(gen)\n"
        "    except OSError:\n"
        "        print('OK: connection error, not an import error')\n"
        "asyncio.run(main())\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, timeout=60,
        cwd=pathlib.Path(__file__).resolve().parents[1],
    )
    assert "OK" in result.stdout, result.stderr


@pytest.mark.skipif(websockets is None, reason="websockets not installed")
def test_ws_source_once_raises_when_server_is_absent():
    from braille_engine.sources.ws_source import ws_source

    async def scenario():
        # Grab a port the OS just released; nothing is listening on it.
        server = await websockets.serve(lambda ws, *_: None, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        server.close()
        await server.wait_closed()
        gen = ws_source(f"ws://127.0.0.1:{port}/finalized", reconnect=False)
        with pytest.raises(OSError):
            await anext(gen)

    asyncio.run(scenario())


# --- Startup: wait for the display instead of dying -------------------------


class StartupBlockedSink(SimulatedSink):
    """Display whose first connect() attempts fail (another program owns it,
    not plugged in yet). ``blocked`` is consumed one exception per attempt.
    Records the engine's published wait reason at each attempt so tests can
    prove the browser panel would have had something to announce."""

    def __init__(self, width=8, blocked=()):
        super().__init__(width=width, echo=False)
        self.blocked = list(blocked)
        self.connects = 0
        self.engine = None            # set by tests after engine creation
        self.seen_wait_reasons = []

    def connect(self):
        self.connects += 1
        if self.engine is not None:
            self.seen_wait_reasons.append(self.engine.display_wait_reason)
        if self.blocked:
            raise self.blocked.pop(0)
        return super().connect()


def test_wait_for_display_waits_out_a_startup_collision(capsys):
    """A screen reader holding the display at launch must not kill the
    appliance: startup retries with backoff and connects once it is freed."""
    owned = ("native HID connection failed. NVDA appears to be using the "
             "braille display.")
    sink = StartupBlockedSink(width=8, blocked=[
        RuntimeError(owned), RuntimeError(owned)])
    engine = BrailleEngine(DevUebTranslator(), sink)
    sink.engine = engine

    width = wait_for_display(engine, threading.Event(),
                             initial_delay=0.01, max_delay=0.02)

    assert width == 8
    assert sink.connects == 3
    # The reason was published for the panel DURING the wait and cleared
    # after the display connected.
    assert sink.seen_wait_reasons[1] == owned
    assert engine.display_wait_reason is None
    err = capsys.readouterr().err
    assert err.count("braille display not available") == 1   # warn once
    assert "NVDA appears to be using" in err
    assert "braille display connected; starting" in err


def test_wait_for_display_logs_each_distinct_reason_once(capsys):
    sink = StartupBlockedSink(width=8, blocked=[
        RuntimeError("display owned"), RuntimeError("display owned"),
        OSError(22, "display unplugged"), OSError(22, "display unplugged")])
    engine = BrailleEngine(DevUebTranslator(), sink)

    width = wait_for_display(engine, threading.Event(),
                             initial_delay=0.01, max_delay=0.02)

    assert width == 8
    err = capsys.readouterr().err
    assert err.count("display owned") == 1
    assert err.count("display unplugged") == 1


def test_wait_for_display_returns_none_when_the_reader_quits():
    """q / Quit Dotify during the wait must end the run promptly even
    mid-backoff (the sleep is interruptible)."""

    class NeverThere(SimulatedSink):
        def connect(self):
            raise RuntimeError(
                "no compatible native Windows braille display found")

    engine = BrailleEngine(DevUebTranslator(), NeverThere(width=8, echo=False))
    stop = threading.Event()
    threading.Timer(0.15, stop.set).start()

    started = time.monotonic()
    width = wait_for_display(engine, stop, initial_delay=30.0, max_delay=30.0)

    assert width is None
    assert time.monotonic() - started < 5.0


def test_wait_for_display_lets_programming_errors_fail_fast():
    """Only the sink contract's OSError/RuntimeError mean "display not
    available"; anything else is a bug and must not retry forever."""

    class Broken(SimulatedSink):
        def connect(self):
            raise ValueError("misconfigured sink")

    engine = BrailleEngine(DevUebTranslator(), Broken(width=8, echo=False))
    with pytest.raises(ValueError):
        wait_for_display(engine, threading.Event(), initial_delay=0.01)
