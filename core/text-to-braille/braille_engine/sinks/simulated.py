"""Simulated braille display.

Renders each frame to the terminal as Unicode braille, refreshing in place, so
the entire engine can be built and demoed with no hardware. Also keeps every
frame in ``self.frames`` for tests. Its width stands in for a real display's
queried cell count.
"""

import sys

from ..cells import frame_to_str


class SimulatedSink:
    def __init__(self, width: int = 40, stream=None, echo: bool = True):
        self.width = width
        self.stream = stream if stream is not None else sys.stdout
        self.echo = echo
        self.frames = []

    def connect(self) -> int:
        return self.width

    def write(self, cells) -> None:
        frame = list(cells)
        self.frames.append(frame)
        if self.echo:
            self.stream.write("\r" + frame_to_str(frame))
            self.stream.flush()

    def close(self) -> None:
        if not self.echo:
            return
        try:
            self.stream.write("\n")
            self.stream.flush()
        except (OSError, ValueError):
            # Broken pipe (| head) or an already-closed stream: the sink
            # contract says close() is best-effort and must never raise.
            pass
