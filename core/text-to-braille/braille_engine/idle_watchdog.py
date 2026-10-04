"""Idle watchdog: turn the microphone off when nobody is reading.

A TV or a busy room is real speech and keeps transcription running whether
or not a reader is present. Nothing in the
audio or the queue tells "the reader is following the room" from "the room
is talking to an empty chair"; what differs is that a present reader
eventually touches something. So the watchdog measures time since the last
human input on any channel and resolves the doubt with an attention check:

  ACTIVE   any input restarts the clock. After idle_s (30 min) with no
           input -> WARNED, and the pace loop flashes "still reading? press
           any key".
  WARNED   input within warn_s (2 min) -> ACTIVE. Otherwise -> PAUSED: the
           pace loop bumps the idle-pause counter and the platform shell
           turns the microphone off.
  PAUSED   the next input returns 'resume': the pace loop bumps the resume
           counter and the shell brings the microphone back.

No thread or timer: the pace loop polls, the clock is injectable, and the
microphone itself belongs to the shell. The pace loop skips the watchdog
while the demo/caption feeder (which uses no microphone) is the source.
"""

import time

IDLE_S = 1800.0  # no human input for this long -> attention check
WARN_S = 120.0   # the check's answer window before the mic is paused

STATE_ACTIVE = "active"
STATE_WARNED = "warned"
STATE_PAUSED = "paused"


class IdleWatchdog:
    def __init__(self, idle_s=IDLE_S, warn_s=WARN_S, clock=time.monotonic):
        self._idle_s = float(idle_s)
        self._warn_s = float(warn_s)
        self._clock = clock
        self._last_touch = clock()
        self._warned_at = 0.0
        self.state = STATE_ACTIVE

    def touch(self):
        """Record human input. Returns 'resume' when it wakes a watchdog
        pause (the caller relays that to the shell); None otherwise."""
        self._last_touch = self._clock()
        was = self.state
        self.state = STATE_ACTIVE
        return "resume" if was == STATE_PAUSED else None

    def poll(self):
        """Advance the clock. Returns 'warn' or 'pause' exactly once at
        each transition; None on every other call."""
        now = self._clock()
        if self.state == STATE_ACTIVE:
            if now - self._last_touch >= self._idle_s:
                self.state = STATE_WARNED
                self._warned_at = now
                return "warn"
        elif self.state == STATE_WARNED:
            if now - self._warned_at >= self._warn_s:
                self.state = STATE_PAUSED
                return "pause"
        return None
