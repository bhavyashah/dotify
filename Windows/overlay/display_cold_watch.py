"""Notices a display that disconnects or returns while nothing is written.

The other reconnect paths are write-triggered: the sinks' in-write
retries and run.py's recover_display only run after a frame FAILS, and
wait_for_display only dials until the session's first claim. While the pacer
is idle — caught up, holding still in manual mode, paused — an unplugged or
powered-off display dies silently: no write is in flight, so nothing
notices, display keys go dead without a word, and a returning display sits
unclaimed until the next organic write, which may never come.

Every dial goes through AutoDisplaySink.connect(), so profile pinning and
the USB-over-Bluetooth rule still apply.

Ears: a message-only window registered for HID and COM-port device-interface
notifications (WM_DEVICECHANGE, the same mechanism NVDA uses for display
hotplug). The pump thread only ENQUEUES events; every decision — identity
match, presence verification, outage state, dialing — runs on the single
watch thread, so the watch cannot race itself and the pump never blocks on
device scans or transport teardown.

On removal of the very device this session is on — matched by device
identity AND re-verified against live enumeration for both transports, so a
stale or second-unit broadcast can never close a healthy link (enumeration
failure counts as still-present: a false drop closes a healthy link, while
a real death is still caught by the write path) — the watch closes the dead
transport, announces once through engine.display_wait_reason (the display
is dead; the browser panel is the channel that still works), and dials on a
patient doubling ladder (1 s -> 30 s cap). An arrival of the lost unit
short-circuits the ladder; an arrival of an unknown device schedules at
most one extra dial, never resetting the ladder, so a flapping unrelated
USB device cannot defeat the backoff. Arrivals also rescue the states
with no dialer at all, such as a sink still bound to a device whose removal
was never delivered (unplug during system sleep).

One-dialer rule: while the engine publishes a
display_wait_reason of its own, wait_for_display/recover_display owns the
dial and the watch stands down, ticking once a second only to notice that
loop winning (fast stale-status clearing). Before the engine's first
start() the watch never dials at all — the initial claim is
wait_for_display's.

Bluetooth is out of scope by design: a paired unit's virtual COM port
persists while the display is off or out of range, so Windows never raises
a removal event for a BT power-off. The Bluetooth sinks expose no
device_identity, which keeps this watch inert there; the write path's
capped retries and recover_display own that outage once a write happens.
"""

from __future__ import annotations

import sys
import threading
import time
from collections import deque


DROP_REASON = (
    "the braille display disconnected (unplugged or powered off). "
    "Reconnect it; Dotify reconnects the moment it is back"
)


def _default_com_ports():
    from serial.tools import list_ports
    return [port.device for port in list_ports.comports()]


_hid_api = None


def _default_hid_paths():
    # Own WinApi instance (never shared with a sink's) — SetupDi enumeration
    # is self-contained. windows_hid is importable in the staged layout;
    # tests inject their own provider.
    global _hid_api
    import windows_hid as hid
    if _hid_api is None:
        _hid_api = hid.WinApi()
    return hid.enumerate_paths(_hid_api)


class DisplayColdWatch:
    """Event-armed redial loop over AutoDisplaySink's connect path.

    Pure logic, no Win32: DeviceChangeListener (below) enqueues
    on_device_removed/on_device_arrived from the notification pump, and
    tests feed them directly. All state changes happen on the one watch
    thread.
    """

    def __init__(self, engine, stop_event, *, announce=None,
                 com_ports_provider=None, hid_paths_provider=None,
                 initial_delay=1.0, max_delay=30.0, arrival_grace=0.5,
                 tick=1.0, clock=time.monotonic):
        self._engine = engine
        self._stop_event = stop_event
        self._announce = announce
        self._com_ports_provider = com_ports_provider or _default_com_ports
        self._hid_paths_provider = hid_paths_provider or _default_hid_paths
        self._initial_delay = initial_delay
        self._max_delay = max_delay
        self._arrival_grace = arrival_grace
        self._tick = tick
        self._clock = clock
        self._cond = threading.Condition()
        # Bounded: a device-broadcast storm (or events after quit) must
        # never grow memory; old events are the least interesting.
        self._events = deque(maxlen=64)
        self._outage = False        # watch-owned outage is open
        self._await_arrival = False  # width refusal: dial only on an arrival
        self._delay = initial_delay  # current ladder rung
        self._dial_at = None        # monotonic deadline for the next dial
        self._last_dial = None      # when the last dial attempt started
        self._last_identity = None  # identity of the unit that was lost
        self._own_reason = None     # the reason text WE published; anything
                                    # else in display_wait_reason means an
                                    # engine dialer owns the dial
        self._warned = set()
        self._stopped = False
        self._thread = None

    # ------------------------------------------------------------------
    # lifecycle

    def start(self):
        self._thread = threading.Thread(
            target=self._run, name="display-cold-watch", daemon=True)
        self._thread.start()
        return self

    def stop(self):
        with self._cond:
            self._stopped = True
            self._cond.notify_all()

    # ------------------------------------------------------------------
    # device events (called from the notification pump thread; enqueue
    # only — the pump must never block on device scans or teardown)

    def on_device_removed(self, kind, name):
        """kind: 'hid' | 'com'; name: the interface path from the event."""
        self._enqueue(("removed", kind, (name or "")))

    def on_device_arrived(self, kind, name):
        self._enqueue(("arrived", kind, (name or "")))

    def _enqueue(self, event):
        with self._cond:
            if self._stopped or self._stop_event.is_set():
                return  # a quit session's engine is not ours to touch
            self._events.append(event)
            self._cond.notify_all()

    # ------------------------------------------------------------------
    # the watch thread

    def _run(self):
        while True:
            with self._cond:
                if self._stopped or self._stop_event.is_set():
                    return
                if not self._events:
                    self._cond.wait(self._wait_timeout_locked())
                if self._stopped or self._stop_event.is_set():
                    return
                events = list(self._events)
                self._events.clear()
                due = (self._outage
                       and not self._await_arrival
                       and self._dial_at is not None
                       and self._clock() >= self._dial_at)
                if due:
                    self._dial_at = None  # claim this attempt
                outage = self._outage
            try:
                for event in events:
                    self._process(event)
                if outage or self._outage:
                    self._service(due)
            except Exception as error:
                # A surprise here must degrade to a longer ladder rung,
                # never kill the thread: a dead watch is the silent cold
                # death this module exists to prevent.
                print(f"[watch] internal error ({error!r}); the watch "
                      "keeps running", file=sys.stderr, flush=True)
                with self._cond:
                    if self._outage and self._dial_at is None:
                        self._delay = min(self._delay * 2, self._max_delay)
                        self._dial_at = self._clock() + self._delay

    def _wait_timeout_locked(self):
        if self._outage:
            if self._dial_at is not None and not self._await_arrival:
                return max(0.0, min(self._tick,
                                    self._dial_at - self._clock()))
            return self._tick
        # Idle: events and stop() notify the condition; the long heartbeat
        # only guards an external stop_event.set() with no stop() call.
        return self._tick * 30

    # ------------------------------------------------------------------
    # event processing (watch thread)

    def _process(self, event):
        action, kind, name = event
        sink = getattr(self._engine, "sink", None)
        if sink is None:
            return
        if action == "removed":
            self._handle_removed(sink, kind, name)
        else:
            self._handle_arrived(sink, kind, name)

    def _handle_removed(self, sink, kind, name):
        identity = getattr(sink, "device_identity", None)
        if identity is None:
            return  # nothing held (already down, or Bluetooth)
        id_kind, id_value = identity
        if id_kind != kind:
            return
        if kind == "hid" and name.lower() != id_value:
            # A different unit left; this session's link is untouched.
            return
        # Re-verify against live enumeration for BOTH transports: a
        # removal broadcast can be stale — a fast unplug/replug reconnects
        # on the SAME interface path via the sink's in-write retries
        # before the pump drains its queue, and closing that healthy link
        # here would be a self-inflicted outage.
        if self._device_present(identity):
            return
        self._open_outage(sink, identity)

    def _handle_arrived(self, sink, kind, name):
        if self._engine_owns_dial():
            return  # wait_for_display/recover_display claims it (<= 10 s)
        if getattr(self._engine, "width", None) is None:
            # The engine has never started: the initial claim belongs to
            # wait_for_display, and engine.frame() does not exist yet.
            return
        now = self._clock()
        with self._cond:
            outage = self._outage
        if not outage:
            if getattr(sink, "connected", False):
                identity = getattr(sink, "device_identity", None)
                if identity is None or self._device_present(identity):
                    return  # healthy (or unverifiable): nothing to do
                # Bound to a device that is no longer present: the removal
                # was never delivered (unplug during sleep/undock). Treat
                # this arrival as the wake-up call for that cold drop.
                self._open_outage(sink, identity)
                with self._cond:
                    self._dial_at = now + self._arrival_grace
                    self._cond.notify_all()
                return
            # No outage open, nothing connected, nobody dialing: a
            # dialer-less state. Arm and dial.
            with self._cond:
                self._outage = True
                self._await_arrival = False
                self._delay = self._initial_delay
                self._dial_at = now + self._arrival_grace
                self._cond.notify_all()
            return
        # Outage open. Only the lost unit's return resets the ladder — an
        # unknown arrival dials at most once per initial_delay and keeps
        # the current rung, so a flapping unrelated device cannot defeat
        # the backoff. (COM arrivals name a device interface, not a port,
        # so "our unit is back" is answered by enumeration instead.)
        lost = self._last_identity
        if lost is not None and lost[0] == kind:
            if kind == "hid":
                ours = name.lower() == lost[1]
            else:
                ours = self._device_present(lost)
        else:
            ours = False
        with self._cond:
            if self._await_arrival or ours:
                # A fresh arrival also lifts a width refusal — the right
                # unit may be back.
                self._await_arrival = False
                self._delay = self._initial_delay
                self._dial_at = now + self._arrival_grace
            else:
                floor = ((self._last_dial or 0) + self._initial_delay)
                candidate = max(now + self._arrival_grace, floor)
                if self._dial_at is None or candidate < self._dial_at:
                    self._dial_at = candidate
            self._cond.notify_all()

    def _device_present(self, identity):
        kind, value = identity
        try:
            if kind == "hid":
                return any(path.lower() == value
                           for path in self._hid_paths_provider())
            return value in self._com_ports_provider()
        except Exception as error:
            # Can't verify -> still present: a false drop closes a healthy
            # link; a real death is still caught by the write path.
            self._warn_once(
                f"device presence check failed ({error}); treating the "
                "display as still connected")
            return True

    def _open_outage(self, sink, identity):
        with self._cond:
            if self._outage:
                return
        if self._engine_owns_dial():
            return  # an engine loop is already dialing and announcing
        self._last_identity = identity
        # Publish BEFORE the (possibly slow) teardown: if an engine dialer
        # starts in between, its reason overwrites ours and the watch
        # stands down — the actionable engine message always wins.
        self._publish(DROP_REASON)
        print(f"[watch] {DROP_REASON}", file=sys.stderr, flush=True)
        # drop_cold serializes with in-flight dials on the sink's connect
        # lock; blocking here is fine — this is the watch thread.
        dropper = getattr(sink, "drop_cold", None)
        if dropper is not None:
            dropper()
        with self._cond:
            self._outage = True
            self._await_arrival = False
            self._delay = self._initial_delay
            self._dial_at = self._clock() + self._initial_delay
            self._cond.notify_all()

    # ------------------------------------------------------------------
    # dialing (watch thread)

    def _engine_owns_dial(self):
        reason = getattr(self._engine, "display_wait_reason", None)
        return reason is not None and reason != self._own_reason

    def _publish(self, reason):
        self._own_reason = reason
        self._engine.display_wait_reason = reason

    def _clear_own_reason(self):
        if self._own_reason is None:
            return
        if getattr(self._engine, "display_wait_reason", None) == self._own_reason:
            # Narrow race: an engine dialer publishing between this compare
            # and the store gets wiped — but recover_display republishes on
            # every failed attempt (<= 10 s), so the panel self-heals.
            self._engine.display_wait_reason = None
        self._own_reason = None

    def _warn_once(self, message):
        if message in self._warned:
            return
        self._warned.add(message)
        print(f"[watch] {message}", file=sys.stderr, flush=True)

    def _service(self, dial_permitted):
        sink = getattr(self._engine, "sink", None)
        if sink is None:
            return
        if getattr(sink, "connected", False):
            # Someone else won — the engine loop or a quick
            # in-write retry. Close the outage and clear a stale watch
            # reason within a tick; announce nothing over their recovery.
            with self._cond:
                self._outage = False
                self._await_arrival = False
                self._dial_at = None
            self._clear_own_reason()
            return
        if not dial_permitted:
            return
        if getattr(self._engine, "width", None) is None:
            # Engine not started: never dial under wait_for_display.
            with self._cond:
                if self._dial_at is None:
                    self._dial_at = self._clock() + self._tick
            return
        if self._engine_owns_dial():
            # Stand down without growing the ladder; re-check next tick.
            with self._cond:
                if self._dial_at is None:
                    self._dial_at = self._clock() + self._tick
            return
        self._dial(sink)

    def _dial(self, sink):
        self._last_dial = self._clock()
        try:
            width = sink.connect()
        except Exception:
            self._schedule_next_rung()
            return
        expected = getattr(self._engine, "width", None)
        if expected is not None and width != expected:
            # Honest refusal, not "reconnected" over dark cells: a session
            # is sized to its display. Tear the wrong unit back down (a
            # later engine write must hit "not connected", not a width
            # ValueError) and wait for a fresh arrival.
            dropper = getattr(sink, "drop_cold", None)
            if dropper is not None:
                dropper()
            reason = (f"the reconnected display has {width} cells but this "
                      f"session is sized for {expected}; restart Dotify "
                      "with the new display")
            self._publish(reason)
            print(f"[watch] {reason}", file=sys.stderr, flush=True)
            with self._cond:
                self._await_arrival = True
                self._dial_at = None
            return
        try:
            # Repaint the frame exactly as the reader left it — same as
            # recover_display, so the reader resumes mid-word.
            self._engine.sink.write(self._engine.frame())
        except Exception:
            # connect() succeeded but the transport is not actually usable
            # (a port that opens while the unit is detached). Tear it back
            # down so `connected` stays honest and the outage keeps
            # dialing instead of silently closing over a dead display.
            dropper = getattr(sink, "drop_cold", None)
            if dropper is not None:
                dropper()
            self._schedule_next_rung()
            return
        with self._cond:
            self._outage = False
            self._await_arrival = False
            self._dial_at = None
            self._delay = self._initial_delay
        self._last_identity = getattr(sink, "device_identity", None)
        self._clear_own_reason()
        print("[watch] display reconnected; resuming where the reader "
              "left off", file=sys.stderr, flush=True)
        if self._announce is not None:
            try:
                self._announce("display reconnected")
            except Exception:
                pass  # a failed flash must never kill the watch

    def _schedule_next_rung(self):
        with self._cond:
            self._delay = min(self._delay * 2, self._max_delay)
            self._dial_at = self._clock() + self._delay


# ----------------------------------------------------------------------
# Win32 device-interface notifications


WM_DEVICECHANGE = 0x0219
WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
DBT_DEVICEARRIVAL = 0x8000
DBT_DEVICEREMOVECOMPLETE = 0x8004
DBT_DEVTYP_DEVICEINTERFACE = 5
DEVICE_NOTIFY_WINDOW_HANDLE = 0
HWND_MESSAGE = -3

# In-box interface classes: every transport the auto sink can hold.
GUID_DEVINTERFACE_HID = "{4D1E55B2-F16F-11CF-88CB-001111000030}"
GUID_DEVINTERFACE_COMPORT = "{86E0D1E0-8089-11D0-9CE4-08003E301F73}"


class DeviceChangeListener:
    """Message-only window + RegisterDeviceNotification pump.

    Runs its own thread (a window's message queue has thread affinity) and
    forwards arrivals/removals as on_event(kind, action, name) with kind
    'hid'/'com', action 'arrived'/'removed', name the interface path. The
    handler must be non-blocking — DisplayColdWatch's entry points only
    enqueue.
    """

    def __init__(self, on_event):
        self._on_event = on_event
        self._hwnd = None
        self._thread = None
        self._ready = threading.Event()
        self._failure = None
        self._wndproc = None       # keep the callback alive for the window
        self._notifications = []

    def start(self):
        self._thread = threading.Thread(
            target=self._pump, name="display-device-events", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=10)
        if self._failure is not None:
            raise RuntimeError(
                f"device notifications unavailable: {self._failure}")
        if self._hwnd is None:
            raise RuntimeError("device notification window did not start")
        return self

    def stop(self):
        import ctypes
        hwnd = self._hwnd
        if hwnd:
            ctypes.windll.user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)

    # -- pump thread ----------------------------------------------------

    def _pump(self):
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        # Explicit prototypes: without them ctypes passes/returns HWNDs as
        # 32-bit ints, silently truncating 64-bit handles — the failure is
        # a baffling ERROR_INVALID_WINDOW_HANDLE far from the real bug.
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.CreateWindowExW.argtypes = (
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
            wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE,
            wintypes.LPVOID)
        user32.DefWindowProcW.restype = ctypes.c_ssize_t
        user32.DefWindowProcW.argtypes = (
            wintypes.HWND, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM)
        user32.RegisterDeviceNotificationW.restype = wintypes.HANDLE
        user32.RegisterDeviceNotificationW.argtypes = (
            wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD)
        user32.UnregisterDeviceNotification.argtypes = (wintypes.HANDLE,)
        user32.DestroyWindow.argtypes = (wintypes.HWND,)
        user32.PostMessageW.argtypes = (
            wintypes.HWND, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM)
        user32.GetMessageW.argtypes = (
            wintypes.LPVOID, wintypes.HWND, ctypes.c_uint, ctypes.c_uint)
        user32.TranslateMessage.argtypes = (wintypes.LPVOID,)
        user32.DispatchMessageW.restype = ctypes.c_ssize_t
        user32.DispatchMessageW.argtypes = (wintypes.LPVOID,)

        class GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", ctypes.c_uint32),
                ("Data2", ctypes.c_uint16),
                ("Data3", ctypes.c_uint16),
                ("Data4", ctypes.c_ubyte * 8),
            ]

        def guid_from_string(text):
            guid = GUID()
            if ctypes.oledll.ole32.CLSIDFromString(text, ctypes.byref(guid)):
                raise RuntimeError(f"bad GUID {text}")
            return guid

        def guid_equal(a, b):
            return (a.Data1 == b.Data1 and a.Data2 == b.Data2
                    and a.Data3 == b.Data3
                    and bytes(a.Data4) == bytes(b.Data4))

        class DEV_BROADCAST_DEVICEINTERFACE_W(ctypes.Structure):
            _fields_ = [
                ("dbcc_size", wintypes.DWORD),
                ("dbcc_devicetype", wintypes.DWORD),
                ("dbcc_reserved", wintypes.DWORD),
                ("dbcc_classguid", GUID),
                ("dbcc_name", ctypes.c_wchar * 1),
            ]

        try:
            hid_guid = guid_from_string(GUID_DEVINTERFACE_HID)
            com_guid = guid_from_string(GUID_DEVINTERFACE_COMPORT)

            WNDPROC = ctypes.WINFUNCTYPE(
                ctypes.c_ssize_t, wintypes.HWND, ctypes.c_uint,
                wintypes.WPARAM, wintypes.LPARAM)

            def wndproc(hwnd, message, wparam, lparam):
                if message == WM_DEVICECHANGE and lparam and wparam in (
                        DBT_DEVICEARRIVAL, DBT_DEVICEREMOVECOMPLETE):
                    header = ctypes.cast(
                        lparam,
                        ctypes.POINTER(DEV_BROADCAST_DEVICEINTERFACE_W),
                    ).contents
                    if header.dbcc_devicetype == DBT_DEVTYP_DEVICEINTERFACE:
                        if guid_equal(header.dbcc_classguid, hid_guid):
                            kind = "hid"
                        elif guid_equal(header.dbcc_classguid, com_guid):
                            kind = "com"
                        else:
                            kind = None
                        if kind is not None:
                            name = ctypes.wstring_at(
                                lparam
                                + DEV_BROADCAST_DEVICEINTERFACE_W.dbcc_name.offset)
                            action = ("arrived"
                                      if wparam == DBT_DEVICEARRIVAL
                                      else "removed")
                            try:
                                self._on_event(kind, action, name)
                            except Exception as error:
                                # One bad event must not kill the pump.
                                print(f"[watch] device event handler error: "
                                      f"{error}", file=sys.stderr, flush=True)
                    return 1  # TRUE: request granted
                if message == WM_CLOSE:
                    user32.DestroyWindow(hwnd)
                    return 0
                if message == WM_DESTROY:
                    user32.PostQuitMessage(0)
                    return 0
                return user32.DefWindowProcW(hwnd, message, wparam, lparam)

            self._wndproc = WNDPROC(wndproc)

            class WNDCLASSW(ctypes.Structure):
                _fields_ = [
                    ("style", ctypes.c_uint),
                    ("lpfnWndProc", WNDPROC),
                    ("cbClsExtra", ctypes.c_int),
                    ("cbWndExtra", ctypes.c_int),
                    ("hInstance", wintypes.HINSTANCE),
                    ("hIcon", wintypes.HANDLE),
                    ("hCursor", wintypes.HANDLE),
                    ("hbrBackground", wintypes.HANDLE),
                    ("lpszMenuName", wintypes.LPCWSTR),
                    ("lpszClassName", wintypes.LPCWSTR),
                ]

            hinstance = kernel32.GetModuleHandleW(None)
            wndclass = WNDCLASSW()
            wndclass.lpfnWndProc = self._wndproc
            wndclass.hInstance = hinstance
            wndclass.lpszClassName = "DotifyDisplayColdWatch"
            if not user32.RegisterClassW(ctypes.byref(wndclass)):
                raise ctypes.WinError()

            hwnd = user32.CreateWindowExW(
                0, wndclass.lpszClassName, "Dotify display watch", 0,
                0, 0, 0, 0, HWND_MESSAGE, None, hinstance, None)
            if not hwnd:
                raise ctypes.WinError()

            for guid in (hid_guid, com_guid):
                dev_filter = DEV_BROADCAST_DEVICEINTERFACE_W()
                dev_filter.dbcc_size = ctypes.sizeof(dev_filter)
                dev_filter.dbcc_devicetype = DBT_DEVTYP_DEVICEINTERFACE
                dev_filter.dbcc_classguid = guid
                handle = user32.RegisterDeviceNotificationW(
                    hwnd, ctypes.byref(dev_filter),
                    DEVICE_NOTIFY_WINDOW_HANDLE)
                if not handle:
                    raise ctypes.WinError()
                self._notifications.append(handle)

            self._hwnd = hwnd
        except Exception as error:
            self._failure = error
            self._ready.set()
            return
        self._ready.set()

        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        for handle in self._notifications:
            user32.UnregisterDeviceNotification(handle)
        self._notifications = []
        self._hwnd = None


def start_cold_watch(engine, stop_event, *, announce=None):
    """Wire the watch to the OS. Returns (watch, listener); raises if the
    notification window cannot start (the caller degrades loudly — the
    write-triggered recovery still works without the watch). A failed
    listener start also stops the watch thread, so nothing leaks."""
    watch = DisplayColdWatch(engine, stop_event, announce=announce).start()

    def on_event(kind, action, name):
        if action == "removed":
            watch.on_device_removed(kind, name)
        else:
            watch.on_device_arrived(kind, name)

    try:
        listener = DeviceChangeListener(on_event).start()
    except Exception:
        watch.stop()
        raise
    return watch, listener
