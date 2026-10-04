import io

from braille_engine.cells import frame_to_str
from braille_engine.sinks.simulated import SimulatedSink


def test_connect_returns_width():
    assert SimulatedSink(width=5).connect() == 5


def test_write_captures_frames():
    s = SimulatedSink(width=5, echo=False)
    s.write([0, 1])
    assert s.frames[-1] == [0, 1]


def test_echo_renders_braille():
    buf = io.StringIO()
    s = SimulatedSink(width=2, stream=buf, echo=True)
    s.write([0, 1])
    assert frame_to_str([0, 1]) in buf.getvalue()
