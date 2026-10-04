"""Commands from the braille display's own keys.

Transport adapters (the BrlAPI sink, the Windows display sinks) turn
hardware key reports into symbolic key sets such as ``{"space", "dot1"}``
and feed every change of the pressed set to
``DisplayKeyControls.keys_changed``. This module owns what happens next.

* Chords. Keys accumulate while anything is held, and the combo fires once
  when the last key is released, as on braille devices everywhere; a thumb
  tap is a one-key chord. Every meaningful combo also dismisses an active
  announcement before it runs (no key is spent on the dismissal); an
  unmapped press, such as a routing key brushed while reading, does not.

* The map. Single-dot space chords are the frequent, safe-to-misfire
  commands, laid out as finger pairs:
    Space+dot1 / dot4   slower / faster (in manual mode dot4 flips a page)
    Space+dot3 / dot6   pause braille output / pause the input source
                        (the microphone; relayed to the shell)
    Space+dot2 / dot5   unmapped
  Thumb keys navigate:
    Left / Right        pan back / forward through the shown-cell history
                        (a cell window's worth, or a word-snapped page at
                        the full-display window and in manual mode). At the
                        live edge Right flips the page in manual mode and
                        fast-forwards one refresh in auto mode.
    Next                jump to live (a plain snap)
    Previous            toggle the advance mode (auto / manual)
  Letter chords are settings, one mnemonic each:
    Space+G  grade      Space+W  cell window     Space+S  summarize
    Space+I  status     Space+T  demo text       Space+R  reply
  Dots 1+4 and 1-3-4 (Space+C, Space+M) stay unmapped on purpose: 1+4 is
  the union of the speed pair, so overlapping speed presses land there.

* Reply mode. Space+R toggles it, the only way in or out. Inside it a bare
  dot combo (dots 1-6, no space or thumb) types a braille cell, a bare Space
  commits the word, dot-7 is backspace and dot-8 speaks the sentence now
  (``braille_engine.reply``). Every other combo is refused with a hint
  naming the exit chord. Outside reply mode bare dots are unmapped, so a
  brushed key can never start a reply.

* Discovery logging. Every completed combo is logged, mapped or not.

Key names shared by all transports: ``dot1``..``dot8``, ``space``,
``thumb_previous`` / ``thumb_left`` / ``thumb_right`` / ``thumb_next``,
``routing:<index>`` (0-based), plus transport-specific extras that simply
flow through to the discovery log.
"""

import sys
import threading

from .cells import dots_to_pattern
from .controls import OWNER_REPLY, display_owner

SPACE = "space"
THUMB_PREVIOUS = "thumb_previous"
THUMB_LEFT = "thumb_left"
THUMB_RIGHT = "thumb_right"
THUMB_NEXT = "thumb_next"


def chord(*dots):
    """A space chord: space held together with the given dot keys."""
    return frozenset((SPACE, *dots))


# The six braille input dots: a combo drawn only from these types a cell
# while a reply is active.
DOT_KEYS = frozenset(f"dot{n}" for n in range(1, 7))
# Space+R (dots 1-2-3-5): toggles reply mode, and never means anything else.
REPLY_CHORD = chord("dot1", "dot2", "dot3", "dot5")
BACKSPACE_KEY = frozenset(("dot7",))   # Perkins backspace
ENTER_KEY = frozenset(("dot8",))       # Perkins Enter: speak the sentence


def _pattern(combo) -> int:
    """A bare dot combo as a cell bitmask (dot1=1 .. dot6=32)."""
    return sum(dots_to_pattern(key[3]) for key in combo)


class DisplayKeyControls:
    """Maps display key combos onto the same actions as the terminal keys,
    through the shared ``PacerControls``."""

    def __init__(self, controls):
        self.controls = controls
        self._held = set()   # union of everything pressed since last all-up
        # Transports report keys from their own reader threads.
        self._lock = threading.Lock()
        self._map = {
            chord("dot1"): ("slower", controls._slower),
            chord("dot4"): ("faster", controls._faster),
            chord("dot3"): ("pause braille", controls._toggle_pause),
            chord("dot6"): ("mic pause/resume", controls.request_mic_toggle),
            chord("dot1", "dot2", "dot4", "dot5"):
                ("grade toggle", controls._toggle_grade),
            chord("dot2", "dot4", "dot5", "dot6"):
                ("cell window", controls._cycle_window),
            chord("dot2", "dot3", "dot4"):
                ("summarize", controls.summarize_now),
            # Space+I (dots 2-4) is the union of dot-2 and dot-4 (faster).
            # Harmless while Space+dot-2 is unmapped; revisit if it gains a
            # meaning, since overlapping presses could land here.
            chord("dot2", "dot4"):
                ("status", controls.show_status),
            chord("dot2", "dot3", "dot4", "dot5"):
                ("demo text", controls.toggle_demo),
            frozenset((THUMB_LEFT,)): ("pan back", controls.pan_back),
            frozenset((THUMB_RIGHT,)): ("pan forward", controls.pan_forward),
            frozenset((THUMB_NEXT,)):
                ("jump to live", controls.jump_to_live),
            frozenset((THUMB_PREVIOUS,)):
                ("advance mode", controls._cycle_advance_mode),
        }

    def keys_changed(self, down) -> None:
        """Feed the full currently-pressed key set on every change.
        Dispatches outside the lock so a slow action can't stall the
        transport's reader thread."""
        with self._lock:
            down = set(down)
            self._held |= down
            if down or not self._held:
                return
            combo = frozenset(self._held)
            self._held = set()
        self._dispatch(combo)

    def reset(self) -> None:
        """Forget any partly-pressed chord. Transports call this when the
        connection drops: keys that were down when it died never report
        their release, and would otherwise join the next chord."""
        with self._lock:
            self._held = set()

    def _dispatch(self, combo: frozenset) -> None:
        label = "+".join(sorted(combo))
        controls = self.controls
        # Every completed chord, mapped or not, is human input for the idle
        # watchdog. (getattr: test fakes are minimal.)
        touch = getattr(controls, "touch_activity", None)
        if touch:
            touch()
        # Each branch that gives the combo a meaning dismisses an active
        # announcement first; an unmapped combo never does, so a routing key
        # brushed while reading the flash can't wipe it (a flash cannot be
        # brought back).
        dismiss = getattr(controls, "dismiss_announce", None) or (lambda: None)
        owner = display_owner(controls)
        if combo == REPLY_CHORD:
            dismiss()
            if owner == OWNER_REPLY:
                self._status(f"{label} -> end reply")
                controls.end_reply()
            else:
                self._status(f"{label} -> reply (composing starts)")
                controls.begin_reply()
            return
        if owner == OWNER_REPLY:
            dismiss()
            self._reply_dispatch(combo, label)
            return
        action = self._map.get(combo)
        if action is None:
            self._status(f"unmapped: {label}")
            return
        name, run = action
        dismiss()
        self._status(f"{label} -> {name}")
        try:
            run()
        except (OSError, RuntimeError) as error:
            # Pans and fast-forward write frames from this thread; an outage
            # must not kill the transport's reader. The pacer's next tick
            # hits the same error and reconnects.
            self._status(f"{name} failed (display write: {error}); "
                         "the pacer reconnects")

    def _reply_dispatch(self, combo: frozenset, label: str) -> None:
        """Key meanings while a reply is being typed. Anything that is not
        typing is refused, with a hint on the display naming the way out."""
        reply = self.controls.reply
        if combo and combo <= DOT_KEYS:
            reply.add_pattern(_pattern(combo))
        elif combo == frozenset((SPACE,)):
            reply.space()
        elif combo == BACKSPACE_KEY:
            reply.backspace()
        elif combo == ENTER_KEY:
            reply.end_sentence()
        else:
            self._status(f"reply mode: {label} ignored "
                         "(Space+R ends the reply)")
            reply.refuse()

    @staticmethod
    def _status(msg: str) -> None:
        sys.stderr.write(f"[keys:display] {msg}\n")
        sys.stderr.flush()
