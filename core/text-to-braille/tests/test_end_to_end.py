import asyncio
import threading

from braille_engine.cells import BLANK
from braille_engine.engine import BrailleEngine
from braille_engine.sinks.simulated import SimulatedSink
from braille_engine.translator import DevUebTranslator
from run import run_pipeline


async def _list_source(items):
    for it in items:
        yield it


def test_end_to_end_file_to_simulated_display():
    width = 8
    translator = DevUebTranslator()
    sink = SimulatedSink(width=width, echo=False)
    engine = BrailleEngine(translator, sink)
    engine.start()

    interval = {"v": 0.0}
    stop = threading.Event()
    # feed "hi bob" one char at a time, like typing-speed replay
    source = _list_source(list("hi bob"))

    asyncio.run(run_pipeline(source, engine, interval, stop))

    # expected full cell stream: hi + space + bob
    expected_cells = (
        translator.translate("hi") + [BLANK] + translator.translate("bob")
    )
    expected_frame = expected_cells[-width:]
    pad = width - len(expected_frame)
    expected_frame = [BLANK] * pad + expected_frame

    assert sink.frames[-1] == expected_frame
    assert engine.has_pending() is False


def test_speech_discarded_in_type_mode():
    from braille_engine.controls import PacerControls, MODE_TYPE

    translator = DevUebTranslator()
    sink = SimulatedSink(width=8, echo=False)
    engine = BrailleEngine(translator, sink)
    engine.start()

    interval = {"v": 0.0}
    stop = threading.Event()
    controls = PacerControls(engine, interval)
    controls.mode = MODE_TYPE          # simulate having Tabbed into Type

    source = _list_source(list("hi bob"))

    async def scenario():
        # The pipeline deliberately stays alive after EOF while in HUMAN mode
        # (the reader may still be composing), so stop it explicitly.
        task = asyncio.ensure_future(
            run_pipeline(source, engine, interval, stop, controls))
        await asyncio.sleep(0.05)
        stop.set()
        await asyncio.wait_for(task, 2)

    asyncio.run(scenario())

    assert sink.frames == []           # nothing reached the display
    assert engine.has_pending() is False
