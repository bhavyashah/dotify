"""Reader controls: pacing, catch-up and mode commands, plus terminal keys.

``PacerControls`` is the one object every input surface drives: the
terminal keys handled here, the display's own keys (``display_keys``) and
the platform shells' control panels.

Two input modes, toggled with Tab. User-facing names are "AI mode" (speech
is transcribed; value ``"listen"``) and "Human mode" (a person types; value
``"type"``).

  AI mode (default): speech streams, keys are commands
    f / s       read faster / slower
    g           cycle braille grade 1 -> 2 -> experimental English 3 -> 1
    w           cycle the cell window (1/2/4/6/8 cells per refresh, then
                the full display: word-wrapped pages; auto mode only)
    r           toggle the advance mode (auto / manual)
    p           pause/resume the display (input keeps queueing)
    space / l   jump to the live edge (a plain snap)
    S           summarize the backlog and stream the recap
    q           quit
  Human mode: speech is discarded and printable keys are content (Enter is
  a space; no editing keys).

In both modes: Ctrl+G grade, Ctrl+F faster, Ctrl+S slower, Ctrl+W window,
Ctrl+R advance mode, Ctrl+P pause, Ctrl-C / Ctrl-D quit. Control chords
because plain letters must stay typeable in Human mode.

The speed keys depend on the advance mode: in auto they change the pace; in
manual, faster flips the next page and slower has nothing to slow.
"""

import sys
import threading
import time

from .demo import DemoStream
from .idle_watchdog import IdleWatchdog
from .reply import ReplyComposer

MODE_LISTEN = "listen"
MODE_TYPE = "type"

# Reading advance modes:
#   ticker  the paced mode, shown to users as "auto" (the value is kept for
#           saved settings and the shell protocols). At the default
#           full-display window each refresh is a word-wrapped page on the
#           pace clock; a one-cell window is ticker-tape scrolling.
#   manual  nothing moves until the reader flips the next page.
ADVANCE_TICKER = "ticker"
ADVANCE_MANUAL = "manual"
ADVANCE_MODES = (ADVANCE_TICKER, ADVANCE_MANUAL)
# Auto by default: live captions keep coming whether or not the reader
# presses anything, unlike the documents screen readers pan through. The
# shells import this rather than repeating the literal.
DEFAULT_ADVANCE_MODE = ADVANCE_TICKER

# User-facing advance-mode names. The shells render advance_label() rather
# than translating the values themselves.
ADVANCE_MODE_LABELS = {
    ADVANCE_TICKER: "auto",
    ADVANCE_MANUAL: "manual",
}


def advance_label(mode: str) -> str:
    """The user-facing name of an advance mode (``"ticker"`` -> ``"auto"``).
    Unknown values pass through unchanged."""
    return ADVANCE_MODE_LABELS.get(mode, mode)


# Which modal surface, if any, owns the display. While the reader types a
# reply the echo frame owns it: the pace loop idles, announce() flashes are
# suppressed, and display_keys routes every combo to the reply.
OWNER_REPLY = "reply"
# Why announce() dropped a flash, per owner (logged on the status line).
DISPLAY_OWNER_REASONS = {
    OWNER_REPLY: "reply in progress",
}


def display_owner(controls):
    """``None`` or ``OWNER_REPLY``. A free function over ``getattr`` so the
    callers in run.py also work with minimal test fakes."""
    if getattr(controls, "reply_active", False):
        return OWNER_REPLY
    return None


# Announcement dwell (see _announce_dwell): a fixed acquisition cost for
# noticing the frame changed and finding the text, plus a per-cell reading
# allowance below the reader's streaming pace (these are short confirmations
# where one token carries the news), floored and capped. Every second of
# dwell is a second of live speech queueing behind it.
ANNOUNCE_SECONDS = 1.0     # floor
ANNOUNCE_ACQUIRE = 0.75
ANNOUNCE_MARGIN = 0.35
ANNOUNCE_CEILING = 5.0     # solicited messages can simply be asked again
# Unsolicited messages that cannot be re-requested and cost something if
# missed (the idle watchdog's "still reading?" before it turns the mic off)
# get a higher cap. Sized to cover the 28-cell warning at a slow 1.5 s/cell:
# 0.75 + 28 x 1.5 x 0.35 = 15.45 s.
ANNOUNCE_CEILING_ALERT = 17.5

# Summarize-on-demand sizing: the recap streams like ordinary text, so its
# budget scales with the backlog (about 1 character per 5 missed), floored at
# one display's worth and capped to bound both the reading time and the LLM
# round trip the reader is waiting on.
SUMMARY_COMPRESSION = 5
SUMMARY_MAX_CHARS = 700

# Space+I status labels per mode: the readable form and the one-letter
# fallback used when space is short.
STATUS_MODE_LABELS = {
    ADVANCE_TICKER: (ADVANCE_MODE_LABELS[ADVANCE_TICKER], "a"),
    ADVANCE_MANUAL: (ADVANCE_MODE_LABELS[ADVANCE_MANUAL][:3], "m"),
}

# Control bytes usable in both modes (never typed as text).
CTRL_G = "\x07"   # grade
CTRL_F = "\x06"   # faster
CTRL_S = "\x13"   # slower (deliverable because IXON stays off; see below)
CTRL_W = "\x17"   # cell window
CTRL_R = "\x12"   # advance mode
CTRL_P = "\x10"   # pause/resume
QUIT_CHORDS = ("\x03", "\x04")   # Ctrl-C, Ctrl-D

_posix_saved = None   # original termios; restored by atexit / _restore_posix


def _enter_raw_posix(fd):
    """Put the tty in raw-ish input mode once for the process lifetime.

    Switching per keypress re-enabled IXON between reads (eating Ctrl+S as
    XOFF) and could leave the shell raw when the daemon key thread died
    mid-read. Only input processing changes: OPOST stays on so status lines
    render normally, and ISIG is cleared so Ctrl-C arrives as the quit
    chord.
    """
    global _posix_saved
    if _posix_saved is not None:
        return
    import atexit
    import termios

    _posix_saved = termios.tcgetattr(fd)
    new = termios.tcgetattr(fd)
    new[0] &= ~(termios.IXON | termios.ICRNL)   # deliver ^S/^Q; Enter = \r
    new[3] &= ~(termios.ICANON | termios.ECHO | termios.ISIG)
    new[6][termios.VMIN] = 1
    new[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSADRAIN, new)
    atexit.register(_restore_posix, fd)


def _restore_posix(fd):
    global _posix_saved
    if _posix_saved is None:
        return
    import termios

    termios.tcsetattr(fd, termios.TCSADRAIN, _posix_saved)
    _posix_saved = None


def _consume_escape(read1, poll):
    """Consume exactly one ESC-prefixed sequence (the ESC already read).

    Bounded by sequence grammar, not timing, so type-ahead or a paste right
    behind an arrow key is left intact. CSI (ESC [ params final) and SS3
    (ESC O final) cover arrows, F-keys and Home/End; anything else is
    Alt+<char>.

    ``read1()`` returns the next char; ``poll(timeout)`` says if one is ready.
    """
    if not poll(0.01):
        return                        # a lone ESC keypress
    ch = read1()
    if ch == "O":                     # SS3: exactly one final byte
        if poll(0.05):
            read1()
        return
    if ch != "[":                     # Alt+<char>: it was just consumed
        return
    while poll(0.05):                 # CSI: params until a final byte @..~
        if "\x40" <= read1() <= "\x7e":
            return


def _win_read_key(getwch, kbhit):
    """One keypress via msvcrt (injectable for tests).

    An extended key delivers a '\\x00'/'\\xe0' prefix with its keycode
    already buffered; a typed a-grave is also '\\xe0' (U+00E0) but arrives
    alone, so kbhit() tells them apart.
    """
    ch = getwch()
    if ch in ("\x00", "\xe0") and kbhit():
        getwch()                      # discard the extended keycode
        return None
    return ch


def read_key():
    """Read a single keypress, cross-platform.

    Returns the key as a 1-char string; None for a swallowed multi-byte
    sequence (arrows, F-keys: read again); "" on EOF or when stdin is not a
    terminal (stop listening).
    """
    if not sys.stdin.isatty():
        return ""
    if sys.platform == "win32":
        import msvcrt

        return _win_read_key(msvcrt.getwch, msvcrt.kbhit)
    import select

    fd = sys.stdin.fileno()
    _enter_raw_posix(fd)
    ch = sys.stdin.read(1)
    if ch == "\x1b":
        _consume_escape(
            lambda: sys.stdin.read(1),
            lambda timeout: bool(select.select([fd], [], [], timeout)[0]))
        return None
    if ch == "":
        _restore_posix(fd)            # EOF ends the key thread cleanly
    return ch


# Words-per-minute estimate for user-facing readouts:
# wpm = 60 / (interval * cost-per-word), cost-per-word being the pacer's own
# price for an average word. With an engine it is measured from recently
# streamed words (engine.reading_cost_per_word); until enough have streamed
# it falls back to these per-grade constants: about 6 cells per word
# uncontracted (5 letters + a space) and 4.5 in grade 2. Window size never
# enters: the pacer sleeps per cell emitted.
CELLS_PER_WORD = {1: 6.0, 2: 4.5}

# Default pace, stated as how long a full page stays up: JAWS's braille
# auto-advance default (TalkBack and NVDA's BrailleExtender use 3 s).
DEFAULT_PAGE_SECONDS = 5.0


def default_interval_s(content_width) -> float:
    """The per-cell interval (seconds) that makes a full page last
    ``DEFAULT_PAGE_SECONDS`` on a display ``content_width`` cells wide, so
    every display opens at the same seconds per page."""
    return DEFAULT_PAGE_SECONDS / max(1, int(content_width or 0))


def reading_wpm(interval_seconds: float, grade: int = 2, engine=None) -> int:
    """Approximate reader words per minute at a per-cell interval (>= 1)."""
    cost = None
    if engine is not None:
        measure = getattr(engine, "reading_cost_per_word", None)
        if measure is not None:
            cost = measure()
    if cost is None:
        cells = CELLS_PER_WORD.get(grade, CELLS_PER_WORD[2])
        # The constant includes one space; price it at the live space dwell.
        space = getattr(engine, "space_dwell", 1.0) if engine is not None \
            else 1.0
        cost = cells - 1.0 + space
    return max(1, round(60.0 / (interval_seconds * cost)))


class PacerControls:
    """Maps commands onto pacing, catch-up and mode actions.

    ``interval`` is a one-key dict (``{"v": seconds per cell}``) shared with
    the pace loop, so changes apply on its next tick.
    """

    STEP_FASTER = 0.8
    STEP_SLOWER = 1.25
    # At the full-display auto window the speed keys step the page duration
    # by half a second (the TalkBack/JAWS grain) instead of scaling the
    # per-cell interval; seconds per page is what the reader feels there.
    # Below 1 s a page outruns any reader; past 30 s the display feels stuck.
    PAGE_SECONDS_STEP = 0.5
    PAGE_SECONDS_MIN = 1.0
    PAGE_SECONDS_MAX = 30.0

    def __init__(self, engine, interval, min_interval: float = 0.02,
                 max_interval: float = 5.0, summarizer=None):
        self.engine = engine
        self.interval = interval
        self.min_interval = min_interval
        # Without a ceiling, auto-repeat on 's' compounds 1.25x per press.
        self.max_interval = max_interval
        self.stop = False
        self.mode = MODE_LISTEN
        # Read by the pace loop every iteration; shells re-apply a saved or
        # CLI choice through set_advance_mode.
        self.advance_mode = DEFAULT_ADVANCE_MODE
        # Manual-advance request from the key threads, consumed by the pace
        # loop, which performs the flip so display errors stay on its one
        # recovery path.
        self._advance_event = threading.Event()
        # Monotonic deadline until which an announcement owns the display.
        self.announce_until = 0.0
        # A flash landed and owes one repaint when its dwell ends. Sticky,
        # so a dismissal between two pacer polls can't strand the flash.
        self._announce_repaint = False
        # Optional braille_engine.summary.Summarizer for summarize_now. When
        # None the command degrades to the plain snap.
        self.summarizer = summarizer
        # Catch-up state: None, {"phase": "fetching", ...} while the summary
        # request is out, then {"phase": "streaming"} while the recap
        # streams. Cleared when the recap drains (noticed lazily by the
        # catchup property) or a catch-up press skips it.
        self._catchup = None
        self._catchup_lock = threading.Lock()
        self.last_summary = None      # most recent summary text (panel UX)
        # Requests for things the shell owns, as monotonic counters in the
        # state poll; the shell acts on increases, so a fresh panel load
        # never replays an old press. No-ops without a shell.
        # mic_toggle_requests: Space+dot-6, pause/resume the input source.
        # idle_*_requests: the idle watchdog's mic pause and resume.
        self.mic_toggle_requests = 0
        self.idle_watchdog = IdleWatchdog()
        self.idle_pause_requests = 0
        self.idle_resume_requests = 0
        # Shell-reported microphone name, shown in the Space+I status when
        # no demo or caption replay is the source. "" when none reported.
        self.mic_label = ""
        self.demo = DemoStream(engine, self._status_line)
        self.reply = ReplyComposer(engine, self._status_line)

    def set_input_source(self, label: str) -> None:
        """The shell reports which microphone feeds transcription."""
        self.mic_label = label

    def touch_activity(self) -> None:
        """A person touched any input channel. Feeds the idle watchdog and
        relays a resume to the shell when it wakes a watchdog pause."""
        if self.idle_watchdog.touch() == "resume":
            self.idle_resume_requests += 1

    def dismiss_announce(self) -> None:
        """End an active announcement dwell early: a meaningful key press
        acknowledges the message and still runs its own action. Only the
        deadline moves; the repaint stays owed to the pacer."""
        self.announce_until = 0.0

    def take_announce_repaint(self) -> bool:
        """Consume the owed-repaint marker (pace loop only): True once after
        a flash landed, however its dwell ended."""
        if self._announce_repaint:
            self._announce_repaint = False
            return True
        return False

    # Terminal key -> method name. The control chords work in both modes;
    # the letters only in AI mode (in Human mode they are content).
    _ANY_MODE_KEYS = {
        "\t": "_toggle_mode",
        CTRL_G: "_toggle_grade",
        CTRL_F: "_faster",
        CTRL_S: "_slower",
        CTRL_W: "_cycle_window",
        CTRL_R: "_cycle_advance_mode",
        CTRL_P: "_toggle_pause",
        **{chord: "_quit" for chord in QUIT_CHORDS},
    }
    _LISTEN_KEYS = {
        "f": "_faster",
        "s": "_slower",
        "g": "_toggle_grade",
        "w": "_cycle_window",
        "r": "_cycle_advance_mode",
        "p": "_toggle_pause",
        " ": "jump_to_live",
        "l": "jump_to_live",
        "S": "summarize_now",
        "q": "_quit",
    }

    def handle(self, key: str) -> None:
        self.touch_activity()
        command = self._ANY_MODE_KEYS.get(key)
        if command is None and self.mode != MODE_TYPE:
            command = self._LISTEN_KEYS.get(key)
        if command is None:
            if self.mode == MODE_TYPE:
                self._handle_type(key)
            return
        # Like the display keys: a key that does something also dismisses
        # an announcement mid-dwell; typed content never does.
        self.dismiss_announce()
        getattr(self, command)()

    def _quit(self) -> None:
        self.stop = True

    def jump_to_live(self) -> None:
        """Catch up with a plain snap to the live edge (thumb Next, terminal
        space/l, the panel). Mid-recap it skips the rest of the summary;
        mid-fetch it cancels the summary down to the snap. Already live with
        nothing pending, it answers "already live" so the press is never
        silent. (The check is on an explicit False: minimal test fakes
        return None.)"""
        if self._interrupt_catchup():
            return
        if self.engine.jump_to_live() is False:
            self._status_line("jump to live: already at the live edge")
            self.announce("already live")

    def summarize_now(self) -> None:
        """Summarize-on-demand (Space+S, terminal S, the panel).

        A backlog that fits on the display just snaps. A bigger one is
        captured, the gauge drains as an immediate acknowledgement, the
        display holds while the missed speech is summarized, and the summary
        then streams like ordinary text with s in cell 2; live speech
        queues behind it. Pressing again mid-recap skips to live; pressing
        during the fetch cancels to the plain snap. Any failure also
        degrades to the plain snap.
        """
        if self._interrupt_catchup():
            return
        engine = self.engine
        if self.summarizer is None:
            engine.jump_to_live()
            self._status_line("summary unavailable (no summarizer) — "
                              "jumped to live")
            # Short enough for the smallest (18-cell) content line.
            self.announce("no summary")
            return
        # Claim the catch-up state and hold ticks BEFORE draining the queue:
        # otherwise the pacer could see the drained queue and end the run
        # (the recap would land on a dead loop) or stream a newer word ahead
        # of the recap of older speech.
        state = {"phase": "fetching", "cells": None, "done": False}
        with self._catchup_lock:
            self._catchup = state
        engine.set_hold(True)
        # Read before take_pending clears it: returning a pan to live is a
        # real change and must not answer "nothing to recap".
        panned = bool(getattr(engine, "pan_offset", 0))
        text, cells = engine.take_pending()
        width = engine.content_width or 0
        with self._catchup_lock:
            # A concurrent press may have cancelled while we captured; its
            # snap had no cells yet, so snap what we captured.
            snap_now = state["done"] or not text or len(cells) <= width
            if snap_now:
                state["done"] = True
                if self._catchup is state:
                    self._catchup = None
            else:
                state["cells"] = cells
        if snap_now:
            try:
                engine.show_frame(cells)
            finally:
                engine.set_hold(False)     # never strand a hold on a raise
            if not text and not panned:
                self._status_line("nothing to summarize — already at the "
                                  "live edge")
                self.announce("nothing to recap")
            return
        self._status_line("summarizing what you missed…")
        # Repaint now so the drained gauge and the s marker acknowledge the
        # press immediately. A failed write is the pacer's to recover.
        try:
            engine.repaint()
        except (OSError, RuntimeError):
            pass
        threading.Thread(target=self._fetch_summary, args=(text, state),
                         daemon=True).start()

    @property
    def catchup(self):
        """'fetching' | 'streaming' | None, for the panel. 'streaming'
        clears itself once the recap has left the queue."""
        with self._catchup_lock:
            state = self._catchup
            if state is None:
                return None
            phase = state.get("phase")
            if phase == "streaming" and not self.engine.summary_pending():
                self._catchup = None
                return None
            return phase

    def _interrupt_catchup(self) -> bool:
        """Shared opening of both catch-up commands: pressed mid-recap,
        either one skips to live; mid-fetch, either cancels to the snap.
        Returns True when the press was spent that way."""
        # The press acts now, not after an announcement's dwell.
        self.dismiss_announce()
        if self._exit_summary():
            return True
        if self._cancel_catchup(snap=True):
            self._status_line("summary cancelled — jumped to live")
            return True
        return False

    def _exit_summary(self) -> bool:
        """End a streaming summary early: discard the rest of the recap and
        snap to live. Returns True when a summary was streaming."""
        with self._catchup_lock:
            state = self._catchup
            if state is None or state.get("phase") != "streaming":
                return False
            self._catchup = None
            if not self.engine.summary_pending():
                # Already drained: treat this as a normal press.
                return False
        self.engine.jump_to_live()
        self.engine.set_hold(False)
        self._status_line("summary skipped — jumped to live")
        return True

    def _cancel_catchup(self, snap: bool) -> bool:
        """Claim and abort an in-flight summary fetch; with ``snap``, show
        the captured backlog's newest cells. Returns True when there was a
        fetch to cancel."""
        with self._catchup_lock:
            state = self._catchup
            if state is None or state.get("phase") != "fetching" \
                    or state["done"]:
                return False
            state["done"] = True
            self._catchup = None
        if snap and state["cells"] is not None:
            # cells is None only while summarize_now is still capturing;
            # it sees done=True and snaps itself.
            self.engine.show_frame(state["cells"])
        self.engine.set_hold(False)
        return True

    def _abandon_catchup(self) -> None:
        """Drop any catch-up without a snap and release the hold (Human-mode
        and reply entry want a clean display)."""
        with self._catchup_lock:
            state = self._catchup
            if state is None:
                return
            if state.get("phase") == "fetching":
                state["done"] = True
            self._catchup = None
        self.engine.set_hold(False)

    def _fetch_summary(self, text, state) -> None:
        """Background worker: fetch a summary sized to the backlog and queue
        it, unless a cancel claimed the catch-up first."""
        engine = self.engine
        summary = None
        try:
            grade = getattr(engine.translator, "grade", 1)
            # The server enforces the budget, so no fit loop is needed.
            width = engine.content_width or 8
            chars = min(SUMMARY_MAX_CHARS,
                        max(8, width, len(text) // SUMMARY_COMPRESSION))
            summary = self.summarizer.summarize(text, chars, grade)
        except Exception:
            summary = None
        with self._catchup_lock:
            if state["done"]:
                return          # cancelled; the snap and hold are handled
            state["done"] = True
            if summary:
                # Queue and phase flip under the lock, so a concurrent press
                # can't snap first and then have the summary land on top.
                # Lock order (catch-up, then engine) matches _exit_summary.
                engine.feed_summary(summary)
                self.last_summary = summary
                self._catchup = {"phase": "streaming"}
                engine.set_hold(False)
            else:
                # Same lock for the fallback snap. Even if the display is
                # gone, release the hold and clear the phase, or ticks and
                # the panel stay frozen.
                self._catchup = None
                try:
                    engine.show_frame(state["cells"])
                except (OSError, RuntimeError):
                    pass        # recovery repaints when the display returns
                engine.set_hold(False)
        if summary:
            self._status_line("summary is streaming (s in cell 2) — live "
                              "speech queues behind it; press jump-to-live "
                              "to skip to the live edge")
        else:
            self._status_line("summary unavailable — jumped to live")
            self.announce("no summary")

    def _handle_type(self, key: str) -> None:
        if key in ("\r", "\n"):
            key = " "
        if key != " " and not key.isprintable():
            return  # backspace, escape bytes, other control codes
        self.engine.feed(key)

    def _toggle_mode(self) -> None:
        if self.mode == MODE_LISTEN:
            # Flip the mode first, so a speech chunk in flight is already
            # discarded by the time the queue is cleared below.
            self.mode = MODE_TYPE
            # A late summary would land on the typing, and a held fetch
            # would keep the display frozen.
            self._abandon_catchup()
            self.engine.flush_input()
            # Soft speech too: the closing speech session would harden it
            # moments later and stream it into the typing.
            dropped = self.engine.discard_soft()
            if dropped:
                self._status_line(f"dropped {len(dropped)} in-flight speech "
                                  "segment(s) — typing starts clean")
            self.engine.jump_to_live()
            marker = (" (h in cell 2)"
                      if getattr(self.engine, "gauge", None) else "")
            self._status_line(f"mode: HUMAN — keys are content{marker}; "
                              "Ctrl+G grade, Ctrl+F/S speed, Tab = AI mode")
        else:
            self.engine.flush_input()   # commit the partial typed word
            self.mode = MODE_LISTEN
            self._status_line("mode: AI — speech streams")

    def _toggle_pause(self) -> None:
        """Freeze or unfreeze braille output (Space+dot-3, p, Ctrl+P).

        Pausing is silent: the freeze is the confirmation, and a flash would
        cover the very cells the reader chose to hold. Resuming announces,
        because in manual mode nothing visibly moves on resume. Unpause
        first so the announcement's repaint shows live content."""
        paused = not getattr(self.engine, "paused", False)
        self.engine.set_paused(paused)
        if paused:
            self._status_line("paused — display holds; speech keeps queueing")
        else:
            self._status_line("resumed")
            self.announce("resumed")

    def set_paused(self, on) -> bool:
        """Set the output pause explicitly (panel relay). Idempotent: asking
        for the current state changes nothing. Returns True when the state
        flipped."""
        if bool(on) == bool(getattr(self.engine, "paused", False)):
            return False
        self._toggle_pause()
        return True

    def _cycle_window(self) -> None:
        """Step to the next preset window size, wrapping. A size between
        presets advances to the next larger one; presets as wide as the
        content width fold into the last stop, the full display.

        Auto mode only: manual always flips full pages, so the key says so
        instead of changing a setting nothing would show."""
        if self.advance_mode != ADVANCE_TICKER:
            self._status_line("cell window applies to auto mode only — "
                              "manual always flips the full display")
            self.announce("window: auto only")
            return
        limit = getattr(self.engine, "content_width", None)
        sizes = [s for s in self.engine.WINDOW_SIZES
                 if limit is None or s < limit]
        if limit is not None:
            sizes.append(limit)          # the full-display stop (pages)
        current = self.engine.window
        target = next((s for s in sizes if s > current), sizes[0])
        if self.engine.set_window(target):
            if limit is not None and target >= limit:
                self._status_line("window: full display — word-wrapped "
                                  "pages at the reading pace")
                self.announce("window: full display")
            else:
                noun = "cell" if target == 1 else "cells"
                self._status_line(f"window: {target} {noun} per refresh")
                self.announce(f"window: {target}")

    def _cycle_advance_mode(self) -> None:
        """r / Ctrl+R / thumb Previous: auto <-> manual."""
        modes = ADVANCE_MODES
        self.set_advance_mode(
            modes[(modes.index(self.advance_mode) + 1) % len(modes)])

    def set_advance_mode(self, mode: str) -> None:
        """Switch the advance mode and announce it on the display."""
        self.advance_mode = mode
        self._advance_event.clear()   # a stale press must not fire later
        label = advance_label(mode)
        if mode == ADVANCE_MANUAL:
            hint = f"mode: {label} — f / thumb Right flips the next page"
        else:
            hint = f"mode: {label} — paced reading; f/s = speed"
        self._status_line(hint)
        self.announce(label)

    def _announce_dwell(self, text: str, important: bool = False) -> float:
        """How long a transient message owns the display:
        ``ACQUIRE + cells x interval x MARGIN``, between the floor and the
        ceiling (the alert ceiling when ``important``).

        Finding a message is a fixed cost whatever its length, so it is an
        addend; reading it is per cell, billed below the streaming pace.
        Cells are the real translated cells, capped at the display width.
        Falls back to the floor when no display is connected."""
        width = getattr(self.engine, "content_width", None)
        fit_cells = getattr(self.engine, "fit_cells", None)
        if not width or fit_cells is None:
            return ANNOUNCE_SECONDS
        cells, _total = fit_cells(text, width)
        ceiling = ANNOUNCE_CEILING_ALERT if important else ANNOUNCE_CEILING
        return min(ceiling,
                   max(ANNOUNCE_SECONDS,
                       ANNOUNCE_ACQUIRE
                       + len(cells) * self.interval["v"] * ANNOUNCE_MARGIN))

    def announce(self, text: str, important: bool = False) -> None:
        """Flash a transient message and let it dwell long enough to read
        (``_announce_dwell``). ``important`` marks an unsolicited message
        the reader cannot ask for again (see ANNOUNCE_CEILING_ALERT).

        Best-effort: a display outage only reaches the status line; the
        pacer's next tick owns the reconnect. Suppressed while a reply owns
        the display (a flash would bury the typist's own echo); the state
        change behind it has applied either way."""
        owner = display_owner(self)
        if owner:
            self._status_line(f"not shown on the display "
                              f"({DISPLAY_OWNER_REASONS[owner]}): {text}")
            return
        try:
            # Claim the dwell before writing: the pacer polls announce_until
            # from another thread, and could otherwise flip a page over the
            # message within milliseconds.
            self.announce_until = (time.monotonic()
                                   + self._announce_dwell(text, important))
            self.engine.flash(text)
            self._announce_repaint = True
        except (OSError, RuntimeError):
            self.announce_until = 0.0
            self._status_line(f"not shown on the display: {text}")

    def show_status(self) -> None:
        """Space+I: flash the current settings, composed to the display.

        Items in priority order: mode, window (auto mode only; "full" at
        the full-display window), speed, input source, grade, measured in
        real translated cells. The mode label falls back to one letter and
        the speed drops its unit before an item is dropped; once an item is
        dropped, nothing of lower priority is shown. The speed reads in wpm,
        or in seconds per page at the full-display window. The input source
        is the demo/caption feeder while it streams, else the shell's mic
        label, shortened to its first word if needed.
        """
        engine = self.engine
        width = getattr(engine, "content_width", None)
        if not width:
            self._status_line("status unavailable (display not connected)")
            return
        if self.advance_mode == ADVANCE_MANUAL:
            speeds = []            # nothing moves on its own: no pace to show
        elif engine.window >= width:
            secs = self.interval["v"] * width
            if secs < 1:
                speeds = [f"{secs:.1f}s", f"{secs:.1f}"]   # never "0s"
            else:
                secs = round(secs)
                speeds = [f"{secs}s", f"{secs}"]
        else:
            wpm = reading_wpm(self.interval["v"],
                              getattr(engine.translator, "grade", 1),
                              engine=engine)
            speeds = [f"{wpm}wpm", f"{wpm}"]

        def fits(parts):
            return engine.fit_cells(" ".join(parts), width)[1] <= width

        if self.advance_mode == ADVANCE_TICKER:
            window_item = ("full" if engine.window >= width
                           else f"c{engine.window}")
        else:
            window_item = None
        candidates = []
        for label in STATUS_MODE_LABELS[self.advance_mode]:
            parts = [label]
            complete = True
            if window_item is not None:
                if fits(parts + [window_item]):
                    parts.append(window_item)
                else:
                    complete = False
            if complete and speeds:
                speed = next((s for s in speeds if fits(parts + [s])), None)
                if speed is None:
                    complete = False
                else:
                    parts.append(speed)
            candidates.append((parts, complete))
        # More items win; a tie keeps the readable long label (listed first).
        parts, complete = max(candidates, key=lambda cand: len(cand[0]))
        # mic_label is shell-reported free text: whitespace-only means none.
        source_label = self.demo.source_label or self.mic_label.strip()
        if complete and source_label:
            source_item = next(
                (m for m in (source_label, source_label.split()[0])
                 if fits(parts + [m])), None)
            if source_item is not None:
                parts.append(source_item)
            else:
                complete = False
        grade_item = f"g{getattr(engine.translator, 'grade', 1)}"
        if complete and fits(parts + [grade_item]):
            parts.append(grade_item)
        text = " ".join(parts)
        self._status_line(f"status: {text}")
        self.announce(text)

    def request_mic_toggle(self) -> None:
        """Space+dot-6: ask the shell to pause/resume the input source (the
        microphone and transcription). The shell confirms with its own
        flash; this one is the only answer on a shell-less run."""
        self.mic_toggle_requests += 1
        self._status_line("transcription pause/resume requested "
                          "(the shell confirms)")
        self.announce("input pause asked")

    def _pan_step(self) -> int:
        """How far one thumb press pans: the cell window when auto mode
        streams cells, a full page in manual mode and at the full-display
        window."""
        engine = self.engine
        width = getattr(engine, "content_width", None) or 1
        if self.advance_mode == ADVANCE_MANUAL \
                or getattr(engine, "window_is_full", False):
            return width
        return min(getattr(engine, "window", 1), width)

    def _refuse_while_paused(self) -> bool:
        if not getattr(self.engine, "paused", False):
            return False
        self._status_line("display is paused — resume before panning")
        self.announce("display paused")
        return True

    def pan_back(self) -> None:
        """Thumb Left: pan back through the shown-cell history. Streaming
        freezes while panned (speech keeps queueing); thumb Next returns to
        live."""
        if self._refuse_while_paused():
            return
        engine = self.engine
        moved = engine.pan_back(self._pan_step())
        if moved:
            self._status_line(
                f"pan back {moved} cells "
                f"({engine.pan_offset} behind live)")
        else:
            self._status_line("pan back: at the start of history")
            self.announce("at start")

    def pan_forward(self) -> None:
        """Thumb Right: forward, wherever forward is. A panned view moves
        toward live. At the live edge, manual mode flips the next page and
        auto mode fast-forwards one refresh ahead of the pace; only a reader
        with nothing renderable queued gets "live"."""
        if self._refuse_while_paused():
            return
        engine = self.engine
        if not getattr(engine, "pan_offset", 0):
            if self.advance_mode == ADVANCE_MANUAL:
                self.request_advance()
                return
            # record_cost=False: this tick runs on the key thread and must
            # not steer the pacer's next sleep.
            emitted = engine.tick(record_cost=False)
            if emitted:
                self._status_line(
                    f"fast-forward: pulled {emitted} cells ahead of the "
                    "reading clock")
            else:
                self._status_line("pan forward: already at the live edge")
                self.announce("live")
            return
        moved = engine.pan_forward(self._pan_step())
        if getattr(engine, "pan_offset", 0):
            self._status_line(f"pan forward {moved} cells "
                              f"({engine.pan_offset} behind live)")
        else:
            self._status_line("pan forward: back at the live edge — "
                              "streaming resumes")
            self.announce("live")

    @property
    def reply_active(self) -> bool:
        """True while the reader is typing a reply on the display's keys.
        The pace loop idles on it and the shells pause the microphone."""
        return self.reply.active

    def begin_reply(self) -> None:
        """Enter reply mode (Space+R). Any catch-up is abandoned and any pan
        dropped: the echo owns the display, and the restore afterwards
        repaints the live buffer."""
        if self.reply.active:
            return
        self._abandon_catchup()
        exit_pan = getattr(self.engine, "exit_pan", None)
        if exit_pan:
            exit_pan()
        self.reply.enter()
        self._status_line(
            "reply mode: braille streaming is frozen while you type on the "
            "display's dot keys — Space ends a word, dot-8 speaks the "
            "sentence now, dot-7 erases a cell, Space+R ends the reply")

    def end_reply(self) -> None:
        """Exit reply mode: the composer commits and closes the open
        sentence, the frozen reading frame returns, and streaming
        resumes."""
        if not self.reply.exit():
            return
        try:
            self.engine.reply_restore()
        except (OSError, RuntimeError):
            pass    # display outage: the resumed pacer owns the reconnect
        self._status_line("reply sent — braille streaming resumes")
        self.announce("reply sent")

    @property
    def demo_active(self) -> bool:
        """True while the demo feeder is streaming."""
        return self.demo.active

    def toggle_demo(self, text=None, timed=False) -> bool:
        """Space+T / the panel's demo button: start or stop the demo stream
        (``braille_engine.demo``). With ``timed`` True, ``text`` is SRT or
        WebVTT content replayed at its real cadence; unusable content raises
        ValueError before anything is announced. Returns True when this
        call started a stream."""
        # The stop flash names what was streaming, and a stop press carries
        # no timed flag, so read the label before toggling.
        was = self.demo.source_label
        if self.demo.toggle(text, timed=timed):
            self._status_line(
                "caption track replaying at its real cadence — press "
                "again to stop" if timed
                else "demo text streaming — press again to stop")
            self.announce("captions on" if timed else "demo on")
            return True
        label = was or ("captions" if timed else "demo")
        self._status_line("caption replay stopping" if label == "captions"
                          else "demo text stopping")
        self.announce(label + " off")
        return False

    def request_advance(self) -> None:
        """Manual mode: ask the pace loop for the next page."""
        self._advance_event.set()

    def take_advance(self) -> bool:
        """Consume a pending manual-advance request (pace loop only)."""
        if self._advance_event.is_set():
            self._advance_event.clear()
            return True
        return False

    def _full_page_width(self):
        """content_width when the seconds-per-page speed step applies (auto
        mode at the full-display window), else None."""
        engine = self.engine
        width = getattr(engine, "content_width", None)
        if not width:
            return None
        full = getattr(engine, "window_is_full", None)
        if full is None:
            full = getattr(engine, "window", 0) >= width
        return width if full else None

    def _step_page_seconds(self, delta: float, width: int) -> None:
        """Add ``delta`` seconds to the page duration, clamp, and announce
        the result."""
        per_page = self.interval["v"] * width
        # Snap onto the half-second grid first, so an arbitrary --pace joins
        # it on the first press.
        per_page = round((per_page + delta) * 2) / 2
        per_page = min(self.PAGE_SECONDS_MAX,
                       max(self.PAGE_SECONDS_MIN, per_page))
        # On a very narrow display the per-cell limits can bind too; then
        # announce what the display will actually do.
        interval = min(self.max_interval,
                       max(self.min_interval, per_page / width))
        self.interval["v"] = interval
        if interval != per_page / width:
            per_page = interval * width
        text = f"{per_page:g}s per page"   # "5s", "4.5s"
        self._status_line(f"pace: {text}")
        self.announce(text)

    def _announce_wpm(self) -> None:
        grade = getattr(getattr(self.engine, "translator", None),
                        "grade", 1)
        wpm = reading_wpm(self.interval["v"], grade, engine=self.engine)
        text = f"about {wpm} wpm"
        self._status_line(f"pace: {text}")
        self.announce(text)

    def _faster(self) -> None:
        if self.advance_mode == ADVANCE_MANUAL:
            self.request_advance()
            return
        width = self._full_page_width()
        if width:
            self._step_page_seconds(-self.PAGE_SECONDS_STEP, width)
        else:
            self.interval["v"] = max(self.min_interval,
                                     self.interval["v"] * self.STEP_FASTER)
            self._announce_wpm()

    def _slower(self) -> None:
        if self.advance_mode == ADVANCE_MANUAL:
            self._status_line("manual mode: f / thumb Right advances; "
                              "no pace to slow")
            self.announce("manual, no pace")
            return
        width = self._full_page_width()
        if width:
            self._step_page_seconds(self.PAGE_SECONDS_STEP, width)
        else:
            self.interval["v"] = min(self.max_interval,
                                     self.interval["v"] * self.STEP_SLOWER)
            self._announce_wpm()

    @staticmethod
    def _status_line(msg: str) -> None:
        sys.stderr.write(f"[keys] {msg}\n")
        sys.stderr.flush()

    def _toggle_grade(self) -> None:
        """Cycle grade 1 -> 2 -> 3 -> 1 for words not yet shown."""
        current = getattr(self.engine.translator, "grade", 1)
        self.set_grade({1: 2, 2: 3}.get(current, 1))

    def set_grade(self, target: int) -> None:
        """Set the grade explicitly (panel relay). Idempotent."""
        tr = self.engine.translator
        if getattr(tr, "grade", 1) == target:
            return
        if tr.set_grade(target):
            # Every future word's cells change, so the measured density
            # behind the wpm readout is stale.
            reset = getattr(self.engine, "reset_reading_density", None)
            if reset is not None:
                reset()
            label = {
                1: "uncontracted",
                2: "contracted",
                3: "experimental English grade 3",
            }.get(target, "unknown")
            self._status_line(f"grade {target} ({label})")
            self.announce(f"grade {target}")
        else:
            current = getattr(tr, "grade", 1)
            self._status_line(f"grade {target} unavailable "
                              f"(needs liblouis; still grade {current})")
            self.announce(f"no grade {target}")

    def set_space_time(self, percent) -> bool:
        """Panel relay: percent of the pace a space cell dwells."""
        if self.engine.set_space_dwell(percent):
            self._status_line(f"space time {int(percent)}%")
            return True
        return False

    def set_punct_time(self, percent) -> bool:
        """Panel relay: percent of the pace a punctuation cell dwells."""
        if self.engine.set_punct_dwell(percent):
            self._status_line(f"punctuation time {int(percent)}%")
            return True
        return False

    def set_lowercase(self, on) -> None:
        """Panel relay: the lowercase display filter."""
        self.engine.set_lowercase(on)
        self._status_line(f"lowercase {'on' if on else 'off'}")
