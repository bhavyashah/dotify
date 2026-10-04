"""The in-write reconnect every Windows display sink shares.

A write that fails mid-session gets a few quick reopen-and-resend attempts
before the error reaches the engine. They run inside write(), so under the
sink lock and the engine lock: the pacer, key commands and the panel's
state poll all wait on them. They are therefore kept short; a longer
outage is run.py's recover_display, which reconnects off the lock with a
stop-interruptible backoff.
"""

from __future__ import annotations

import time

# Quick attempts inside write() before recover_display takes over. Enough to
# ride through a fast replug without holding the engine lock for long.
DEFAULT_RECONNECT_ATTEMPTS = 2
DEFAULT_MAX_DELAY = 3.0


def reconnect_and_resend(
    label,
    first_error,
    *,
    attempts,
    delay,
    connect,
    resend,
    close,
    expected_width,
    errors,
    max_delay=DEFAULT_MAX_DELAY,
    failure_help=lambda error: "",
):
    """Reopen the display and resend the frame whose write failed.

    connect() reopens the same unit and returns its width, resend() writes
    the frame again, and close() drops a half-open link after a failed
    attempt. Attempts back off from ``delay`` (doubling, capped at
    ``max_delay``). A unit that comes back with a different width is a
    failed attempt, because the session is sized to the original display.
    After ``attempts`` failures, raises RuntimeError chained to the last
    error, which is what the engine's recovery catches.
    """
    last_error = first_error
    for attempt in range(attempts):
        pause = min(delay * 2 ** attempt, max_delay)
        if pause > 0:
            time.sleep(pause)
        try:
            width = connect()
            if width != expected_width:
                raise RuntimeError(
                    f"display width changed from {expected_width} to "
                    f"{width} during reconnect")
            resend()
            return
        except errors as error:
            last_error = error
            close()
    raise RuntimeError(
        f"{label} could not reconnect after {attempts} attempts: "
        f"{last_error}.{failure_help(last_error)}".rstrip()
    ) from last_error


def forget_held_chord(keys_changed):
    """Clear the chord layer's partly-pressed keys after a reconnect.

    Keys held when the link died never report their release, so without
    this they would join the reader's next chord. ``keys_changed`` is the
    bound ``DisplayKeyControls.keys_changed``; any other callback is left
    alone.
    """
    forget = getattr(getattr(keys_changed, "__self__", None), "reset", None)
    if callable(forget):
        forget()
