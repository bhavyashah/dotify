"""Display sink interface.

A sink is the only hardware-aware edge of the pipeline. Keeping it this small
means swapping BRLTTY for another display library later is a new file here, not
a rewrite of the core.

    connect() -> int      open the display, return its cell count (N)
    write(cells)          write one full N-cell frame (list[int] of patterns)
    close()               release the display

Contract with display recovery (run.py) — every sink must honor this:

* A recoverable display failure must surface as ``OSError`` or
  ``RuntimeError``. Those are the only types the pacer treats as "display
  lost: stop ticking, reconnect, repaint"; anything else escaping
  ``write()`` is a programming error and fail-stops the run. Sinks built on
  libraries with their own exception types must normalize them at this edge
  (see brlapi_sink for the pattern).
* ``connect()`` must be safe to call again on a live object — display
  recovery re-runs it after a failed ``write()``. Close or reuse the
  previous connection; never leak it.
* ``close()`` must be best-effort and idempotent: it runs on quit, possibly
  mid-outage on a dead connection, and must not raise.
"""

from typing import Protocol, List


class DisplaySink(Protocol):
    def connect(self) -> int: ...
    def write(self, cells: List[int]) -> None: ...
    def close(self) -> None: ...
