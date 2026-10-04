"""BRLTTY / BrlAPI display sink (Linux + Windows, USB).

The only piece that requires real hardware and a running BRLTTY. Imported
lazily inside ``connect`` so this module loads on any platform — only
``connect`` needs a BrlAPI client (the ``brlapi`` module on Linux, or the
ctypes ``brlapi.dll`` client on Windows where the module doesn't exist).

Cell width is queried from the display at runtime, never hardcoded.

Key input: BRLTTY delivers one already-translated event per physical
chord — a braille command (thumb keys), a PASSDOTS dot pattern (bare
Perkins dots), or a keysym (space+letter chords, which the display's keyboard table types as
the letter). DisplayKeyControls instead wants raw key-state transitions
and does its own chording, so the listener re-synthesizes each event as a
press of the full combo followed by an all-up release — the accumulator
then fires exactly once per event, matching the Windows HID transports.
"""

import threading
import time

# space+letter chords arrive as the *typed letter's* keysym; recover the
# combo from the letter's braille dots (standard English braille a-z).
_LETTER_DOTS = {
    "a": (1,), "b": (1, 2), "c": (1, 4), "d": (1, 4, 5), "e": (1, 5),
    "f": (1, 2, 4), "g": (1, 2, 4, 5), "h": (1, 2, 5), "i": (2, 4),
    "j": (2, 4, 5), "k": (1, 3), "l": (1, 2, 3), "m": (1, 3, 4),
    "n": (1, 3, 4, 5), "o": (1, 3, 5), "p": (1, 2, 3, 4),
    "q": (1, 2, 3, 4, 5), "r": (1, 2, 3, 5), "s": (2, 3, 4),
    "t": (2, 3, 4, 5), "u": (1, 3, 6), "v": (1, 2, 3, 6),
    "w": (2, 4, 5, 6), "x": (1, 3, 4, 6), "y": (1, 3, 4, 5, 6),
    "z": (1, 3, 5, 6),
}

# The display's BRLTTY keytable (Input/hw/NLS.ktb -> chords.kti) binds many
# space+letter chords to BRLTTY's own screen-reader commands; expandKeyCode
# then reports the command, not the chord. Undo the table: command name ->
# the letter the reader physically chorded. Values are resolved against the
# installed brlapi's KEY_CMD_* constants, so this holds for any display whose
# table includes the stock chords.kti.
_CMD_LETTER_CHORDS = {
    "SKPBLNKWINS": "b", "CSRVIS": "c", "DISPMD": "d", "FREEZE": "f",
    "CONTRACTED": "g", "HELP": "h", "SKPIDLNS": "i", "LEARN": "l",
    "PREFMENU": "p", "AUTOREPEAT": "r", "INFO": "s", "CSRTRK": "t",
    "ATTRVIS": "u", "CSRJMP_VERT": "v", "SLIDEWIN": "w", "PASTE": "x",
}

# Thumb-key combos likewise arrive as bound commands (Input/hw/thumb.kti).
_CMD_THUMB_COMBOS = {
    "HOME": ("thumb_left", "thumb_right"),
    "TOP_LEFT": ("thumb_left", "thumb_previous"),
    "BOT_LEFT": ("thumb_left", "thumb_next"),
    "PRDIFLN": ("thumb_right", "thumb_previous"),
    "NXDIFLN": ("thumb_right", "thumb_next"),
}

# thumb+routing combos: the routed cell arrives in the event's argument.
_CMD_THUMB_ROUTING = {
    "CLIP_NEW": "thumb_previous", "CLIP_ADD": "thumb_left",
    "COPY_LINE": "thumb_right", "COPY_RECT": "thumb_next",
}

# Space+dot navigation chords are typed as keyboard keysyms (chords.kti:
# "bind Space+Dot3 KEY_CURSOR_LEFT" etc.); map each keysym back to the
# chord. Bare Dot7/Dot8 type Backspace/Enter.
_SYM_CHORDS = {
    0xff52: ("space", "dot1"),           # Up        <- space+dot1
    0xff50: ("space", "dot2"),           # Home      <- space+dot2
    0xff51: ("space", "dot3"),           # Left      <- space+dot3
    0xff54: ("space", "dot4"),           # Down      <- space+dot4
    0xff57: ("space", "dot5"),           # End       <- space+dot5
    0xff53: ("space", "dot6"),           # Right     <- space+dot6
    0xff55: ("space", "dot2", "dot3"),   # Page Up
    0xff56: ("space", "dot5", "dot6"),   # Page Down
    0xff09: ("space", "dot4", "dot5"),   # Tab
    0xffff: ("space", "dot2", "dot5", "dot6"),  # Delete
    0xff1b: ("space", "dot2", "dot6"),   # Escape
    0xff63: ("space", "dot3", "dot5"),   # Insert
    0xff08: ("dot7",),                   # Backspace <- Dot7 alone
    0xff0d: ("dot8",),                   # Enter     <- Dot8 alone
}


class BrlapiSink:
    def __init__(self):
        self._conn = None
        self.width = None
        self._key_thread = None

    def connect(self) -> int:
        if self._conn is not None:
            # Display recovery re-runs connect(); drop the dead connection
            # first (close() is best-effort and never raises).
            self.close()
        try:
            import brlapi  # BRLTTY's Python bindings (Linux)
        except ImportError:
            # Windows: BRLTTY ships brlapi.dll but no Python module.
            from .brlapi_dll import DllConnection
            factory = DllConnection
        else:
            factory = brlapi.Connection
        try:
            self._conn = factory()
            # Claim the display GLOBALLY (empty tty path) rather than binding
            # a tty: tty-bound clients are only shown when BRLTTY considers
            # that tty focused, which never happens without a VT console
            # (WSL/SSH) — the display just shows the idle banner. Global mode
            # is the appliance semantics we want: our frames are the only
            # thing on the cells. Requires BRLTTY started with the NoScreen
            # driver (-x no) and no competing auto-spawned instance (see the
            # README's real-display notes).
            self._conn.enterTtyModeWithPath([])
            cols, _rows = self._conn.displaySize
        except (OSError, RuntimeError):
            raise            # already shaped for the recovery/wait loops
        except Exception as error:
            # brlapi's own exception types (brlapi.ConnectionError /
            # OperationError) subclass Exception directly, not OSError —
            # normalize at the hardware edge exactly as write() does, so a
            # BRLTTY that isn't up yet reaches wait_for_display's graceful
            # wait-and-instruct loop instead of crashing startup.
            self.close()
            raise RuntimeError(f"BrlAPI connect failed: {error}") from error
        self.width = cols
        return cols

    def write(self, cells) -> None:
        frame = list(cells)[: self.width]
        frame += [0] * (self.width - len(frame))
        # A malformed frame (a cell outside 0..255, a non-int) is a
        # programming error, not a display failure: build the payload OUTSIDE
        # the normalizing try so it raises raw and fail-stops with a real
        # traceback instead of feeding the reconnect loop a fake outage.
        payload = bytes(frame)
        try:
            # writeDots takes a byte string, one dot pattern per cell.
            self._conn.writeDots(payload)
        except Exception as error:
            # brlapi raises its own exception types (brlapi.OperationError /
            # ConnectionError), which the engine's display-recovery path does
            # not know. The sink is the hardware edge: a writeDots failure
            # IS a display failure, so normalize it for the reconnect loop.
            raise RuntimeError(f"BrlAPI write failed: {error}") from error

    def start_key_listener(self, keys_changed) -> None:
        """Deliver display key combos to ``keys_changed`` (run.py hook).

        Requires a live connection (connect() first). Runs forever on a
        daemon thread; a dropped connection is waited out quietly — the
        engine's display-recovery path replaces self._conn and the loop
        picks the new one up on its next iteration.
        """
        import brlapi
        if self._conn is None:
            raise RuntimeError("BrlAPI key listener needs connect() first")
        self._accept_all_keys(brlapi)
        if self._key_thread is not None:
            return
        self._key_thread = threading.Thread(
            target=self._key_listener_loop, args=(brlapi, keys_changed),
            daemon=True,
        )
        self._key_thread.start()

    def _accept_all_keys(self, brlapi) -> None:
        try:
            self._conn.acceptKeys(brlapi.rangeType_all, [0])
        except Exception as error:
            raise RuntimeError(f"BrlAPI acceptKeys failed: {error}") from error

    def _key_listener_loop(self, brlapi, keys_changed) -> None:
        accepted_conn = self._conn
        while True:
            conn = self._conn
            if conn is None:
                time.sleep(0.5)
                continue
            if conn is not accepted_conn:
                # display recovery made a fresh connection: re-subscribe
                try:
                    self._accept_all_keys(brlapi)
                    accepted_conn = conn
                except Exception:
                    time.sleep(0.5)
                    continue
            try:
                code = conn.readKeyWithTimeout(300)
            except Exception:
                time.sleep(0.5)
                continue
            if code is None:
                continue
            combo = self._decode_key(brlapi, conn, code)
            if not combo:
                continue
            # one translated event = one full chord: press, then all-up,
            # so DisplayKeyControls' accumulator fires exactly once.
            keys_changed(frozenset(combo))
            keys_changed(frozenset())

    @staticmethod
    def _decode_key(brlapi, conn, code):
        """A BRLTTY key event as a display_keys combo (or None)."""
        try:
            info = conn.expandKeyCode(code)
        except Exception:
            return None
        kind, command, argument = (
            info.get("type"), info.get("command"), info.get("argument"))
        cmd = getattr(brlapi, "KEY_TYPE_CMD", 0x20000000)
        sym = getattr(brlapi, "KEY_TYPE_SYM", 0)
        if kind == cmd:
            passdots = getattr(brlapi, "KEY_CMD_PASSDOTS", 0x220000)
            if command == passdots:
                combo = {f"dot{n}" for n in range(1, 9)
                         if argument & (1 << (n - 1))}
                if argument & getattr(brlapi, "DOTC", 0x100):
                    combo.add("space")
                return combo or None
            thumbs = {
                getattr(brlapi, "KEY_CMD_FWINLT", 23): "thumb_left",
                getattr(brlapi, "KEY_CMD_FWINRT", 24): "thumb_right",
                getattr(brlapi, "KEY_CMD_LNUP", 1): "thumb_previous",
                getattr(brlapi, "KEY_CMD_LNDN", 2): "thumb_next",
            }
            name = thumbs.get(command)
            if name:
                return {name}
            route = getattr(brlapi, "KEY_CMD_ROUTE", 0x10000)
            if command == route:
                return {f"routing:{argument or 0}"}
            for cmd_name, letter in _CMD_LETTER_CHORDS.items():
                if command == getattr(brlapi, "KEY_CMD_" + cmd_name, None):
                    return {"space",
                            *(f"dot{n}" for n in _LETTER_DOTS[letter])}
            # COMPBRL6+on/off: bound to space+dots235 / space+dots236 —
            # the on/off half rides in the TOGGLE_ON flag
            if command == getattr(brlapi, "KEY_CMD_COMPBRL6", 0x9a):
                flg_on = getattr(brlapi, "KEY_FLG_TOGGLE_ON", 0x100)
                flags = info.get("flags") or 0
                last = "dot5" if flags & flg_on else "dot6"
                return {"space", "dot2", "dot3", last}
            for cmd_name, keys in _CMD_THUMB_COMBOS.items():
                if command == getattr(brlapi, "KEY_CMD_" + cmd_name, None):
                    return set(keys)
            for cmd_name, thumb in _CMD_THUMB_ROUTING.items():
                if command == getattr(brlapi, "KEY_CMD_" + cmd_name, None):
                    return {thumb, f"routing:{argument or 0}"}
            if command:  # unmapped command -> discovery log, by number
                return {f"brltty_cmd:{command:#x}"}
            return None
        if kind == sym:
            # the keysym is split across command (high bits) and argument
            keysym = (command or 0) | (argument or 0)
            # space+letter chords surface as the typed letter's keysym
            if 0x20 <= keysym < 0x100:
                dots = _LETTER_DOTS.get(chr(keysym).lower())
                if dots:
                    return {"space", *(f"dot{n}" for n in dots)}
                return {f"brltty_sym:{keysym:#x}"}
            # navigation chords surface as typed keyboard keysyms (proven
            # on the NLS eReader: space+dot4 -> XK_Down); map them back to
            # the chords the reader physically pressed.
            keys = _SYM_CHORDS.get(keysym)
            if keys:
                return set(keys)
            # space+routing key types a function key (F1.. = 0xffbe..)
            if 0xffbe <= keysym <= 0xffe0:
                return {"space", f"routing:{keysym - 0xffbe}"}
            return {f"brltty_sym:{keysym:#x}"}
        return None

    def close(self) -> None:
        # Best-effort and idempotent: close() runs on quit — possibly
        # mid-outage on a dead connection — and must never turn a clean
        # exit into a brlapi traceback.
        conn, self._conn = self._conn, None
        if conn is None:
            return
        try:
            conn.leaveTtyMode()
        except Exception:
            pass
        try:
            conn.closeConnection()
        except Exception:
            pass
