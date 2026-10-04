"""Display sink for HumanWare HID displays through Windows' in-box HID driver.

Covers the Brailliant BI X family and the NLS eReader (usage page 0x93),
following NVDA's brailliantB driver: overlapped I/O, an input read armed at
open, cell count from feature report 0x01, cells via output report 0x05.
Also reads the display's keys.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import subprocess
import sys
import threading
import time
from typing import Callable, Sequence

import display_profiles as profiles
from sink_reconnect import DEFAULT_RECONNECT_ATTEMPTS, reconnect_and_resend
import windows_hid as hid

_WAIT_OBJECT_0 = 0x0000
_WAIT_TIMEOUT = 0x0102

# Best-effort culprit naming when the display's HID handle is owned by
# another program (sharing violation / access denied). Windows offers no
# cheap way to prove WHICH process holds a device handle, but a process-name
# scan needs no privileges and names the usual suspects — and each one has a
# product-specific fix that keeps the user's screen reader speech working.
# Ordered by likelihood; the first running match wins.
_BRAILLE_OWNERS = (
    ("nvda.exe", "NVDA",
     "In NVDA, set the braille display to 'no braille' (NVDA menu > "
     "Preferences > Settings > Braille) — NVDA speech keeps working."),
    ("jfw.exe", "JAWS",
     "In JAWS, set the braille display to 'No display' in Settings Center."),
    ("brltty.exe", "BRLTTY",
     "Stop the BRLTTY service."),
    ("narrator.exe", "Windows Narrator",
     "Turn off Narrator, or disable its braille support."),
)


def _process_listing() -> str:
    """Lowercased names of running processes; '' when the scan fails.

    tasklist needs no elevation; failures (missing tool, timeout) degrade to
    the generic no-culprit message, never to an error.
    """
    try:
        return subprocess.run(
            ["tasklist", "/fo", "csv", "/nh"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout.lower()
    except Exception:
        return ""


def _owned_display_help() -> str:
    listing = _process_listing()
    for image, name, fix in _BRAILLE_OWNERS:
        if image in listing:
            # "appears to be": the scan proves the program is RUNNING, not
            # that it holds the handle — a wrong guess costs nothing, since
            # the runner keeps retrying and connects whenever the display is
            # actually freed.
            return (f" {name} appears to be using the braille display. {fix}"
                    " Dotify will connect automatically once the display is"
                    " free.")
    return (
        " Another program owns the display. Close NVDA, JAWS, BRLTTY, or any "
        "other braille-display application; Dotify will connect automatically "
        "once the display is free."
    )


class NativeHidSink:
    """DisplaySink using Windows' in-box HidUsb driver.

    The first connection may auto-detect one supported display. Reconnects are
    pinned to that profile and width so an unplug cannot silently switch output
    to a different unit or corrupt a frame.
    """

    def __init__(
        self,
        *,
        api=None,
        profile_id: str | None = None,
        settle_seconds: float = 1.0,
        warmup_delay: float = 0.25,
        connect_attempts: int = 3,
        reconnect_attempts: int = DEFAULT_RECONNECT_ATTEMPTS,
        reconnect_delay: float = 0.5,
        reconnect_max_delay: float = 3.0,
    ) -> None:
        self._api = api
        self._requested_profile_id = profile_id
        self._settle_seconds = settle_seconds
        self._warmup_delay = warmup_delay
        self._connect_attempts = connect_attempts
        self._reconnect_attempts = reconnect_attempts
        self._reconnect_delay = reconnect_delay
        self._reconnect_max_delay = reconnect_max_delay
        self._handle = None
        self._pending = None
        self._width = None
        self._profile: profiles.DisplayProfile | None = None
        self._lock = threading.RLock()
        self._keys_stop = threading.Event()
        self._keys_thread: threading.Thread | None = None
        self._announced_first_frame = False
        self._input_length = 0
        self._device_path: str | None = None

    @property
    def profile_id(self) -> str | None:
        return self._profile.id if self._profile else None

    @property
    def device_identity(self):
        """Cold-watch identity: the exact HID interface path this session
        opened, so a removal broadcast for any other unit can never close
        a healthy link. None while disconnected."""
        if self._handle is None or not self._device_path:
            return None
        return ("hid", self._device_path.lower())

    @property
    def display_name(self) -> str | None:
        return self._profile.name if self._profile else None

    def _close_handle_unlocked(self) -> None:
        if self._api is not None:
            hid.cancel_input_read(self._api, self._handle, self._pending)
            self._api.close_handle(self._handle)
        self._pending = None
        self._handle = None
        self._width = None
        self._device_path = None

    def _candidate_from_collections(
        self,
        collections: Sequence[hid.Collection],
        requested_profile_id: str | None,
    ) -> tuple[profiles.DisplayProfile, hid.Collection]:
        if requested_profile_id:
            requested = profiles.get_profile(requested_profile_id)
            if not requested.native_supported or requested.transport != profiles.HUMANWARE_HID:
                raise RuntimeError(
                    f"{requested.name} uses {requested.transport}, which is discovery-only "
                    "in the native Windows package"
                )
            identity_matches = [
                item
                for item in collections
                if requested.matches_identity(item.vid, item.pid)
            ]
            if not identity_matches:
                raise RuntimeError(f"{requested.name} is not connected")
            return requested, hid.select_profile_collection(
                self._api, requested, collections=collections
            )

        matches: list[tuple[profiles.DisplayProfile, hid.Collection]] = []
        incompatible: list[str] = []
        for profile in profiles.native_hid_profiles():
            identity_matches = [
                item for item in collections if profile.matches_identity(item.vid, item.pid)
            ]
            if not identity_matches:
                continue
            try:
                collection = hid.select_profile_collection(
                    self._api, profile, collections=collections
                )
            except RuntimeError as error:
                incompatible.append(str(error))
                continue
            matches.append((profile, collection))

        if incompatible and not matches:
            raise RuntimeError("; ".join(incompatible))
        if not matches:
            names = ", ".join(profile.name for profile in profiles.native_hid_profiles())
            raise RuntimeError(f"no supported native HID display is connected ({names})")
        if len(matches) > 1:
            names = ", ".join(profile.name for profile, _ in matches)
            raise RuntimeError(
                f"multiple supported displays are connected ({names}); use --display to select one"
            )
        return matches[0]

    def _warm_up(
        self, requested_profile_id: str | None
    ) -> tuple[profiles.DisplayProfile, hid.Collection]:
        """Wake collections through two short shared enumeration passes."""
        selected = None
        for pass_number in range(2):
            collections = hid.enumerate_known_displays(self._api)
            selected = self._candidate_from_collections(collections, requested_profile_id)
            if pass_number == 0:
                time.sleep(self._warmup_delay)
        assert selected is not None
        return selected

    def _connect_once(self, requested_profile_id: str | None) -> int:
        profile, selected = self._warm_up(requested_profile_id)
        handle = self._api.open_handle(selected.path, shared=False, overlapped=True)
        pending = None
        try:
            collection = hid.inspect_open_handle(self._api, handle, selected.path)
            if collection is None or not profile.matches_collection(collection):
                raise RuntimeError(
                    f"opened HID path is not the expected collection for {profile.name}"
                )
            if collection.input_length > 0:
                pending = hid.queue_input_read(self._api, handle, collection.input_length)
                print(f"[keys:diag] input read armed at connect "
                      f"(handle {handle})", file=sys.stderr, flush=True)
            time.sleep(self._settle_seconds)
            width = hid._read_humanware_cell_count(
                self._api,
                handle,
                collection,
                attempts=3,
                retry_delay=0.2,
                verbose=False,
            )
            if collection.output_length != width + 4:
                raise RuntimeError(
                    f"{profile.name} reports {width} cells but its HID output length is "
                    f"{collection.output_length}"
                )
        except Exception:
            hid.cancel_input_read(self._api, handle, pending)
            self._api.close_handle(handle)
            raise

        self._handle = handle
        self._pending = pending
        self._width = width
        self._profile = profile
        self._input_length = collection.input_length
        self._device_path = selected.path
        return width

    @staticmethod
    def _help_for_error(error: Exception | None) -> str:
        if isinstance(error, OSError) and error.errno in (5, 32, 33):
            return _owned_display_help()
        if isinstance(error, OSError) and error.errno == 1167:
            return (
                " Re-enter USB terminal mode and ensure usbipd reports the device as "
                "Not shared."
            )
        return ""

    def _connect_with_attempts(
        self, requested_profile_id: str | None, attempts: int
    ) -> int:
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                return self._connect_once(requested_profile_id)
            except (OSError, RuntimeError, ValueError) as error:
                last_error = error
                self._close_handle_unlocked()
                if attempt < attempts:
                    time.sleep(self._warmup_delay)

        detail = f": {last_error}" if last_error else ""
        help_text = self._help_for_error(last_error)
        raise RuntimeError(f"native HID connection failed{detail}.{help_text}".rstrip()) from last_error

    def connect(self) -> int:
        with self._lock:
            self._close_handle_unlocked()
            self._profile = None
            if self._api is None:
                self._api = hid.WinApi()
            width = self._connect_with_attempts(
                self._requested_profile_id, self._connect_attempts
            )
            print(
                f"[info] display={self._profile.name} profile={self._profile.id} "
                f"transport=humanware-hid",
                file=sys.stderr,
            )
            return width

    def _send_frame(self, cells: Sequence[int]) -> None:
        report = hid.build_humanware_report(cells)
        hid.set_output_report(self._api, self._handle, report)

    def _reconnect_and_retry(self, cells: Sequence[int], first_error: OSError) -> None:
        assert self._profile is not None and self._width is not None
        profile = self._profile
        expected_width = self._width
        self._close_handle_unlocked()
        print(
            f"[warn] display write failed ({first_error}); reconnecting to "
            f"{profile.name}",
            file=sys.stderr,
        )
        reconnect_and_resend(
            "display disconnected and",
            first_error,
            attempts=self._reconnect_attempts,
            delay=self._reconnect_delay,
            max_delay=self._reconnect_max_delay,
            connect=lambda: self._connect_once(profile.id),
            resend=lambda: self._send_frame(cells),
            close=self._close_handle_unlocked,
            expected_width=expected_width,
            errors=(OSError, RuntimeError, ValueError),
            failure_help=self._help_for_error,
        )
        print(
            f"[info] reconnected to {profile.name} ({expected_width} cells)",
            file=sys.stderr,
        )

    def write(self, cells: Sequence[int]) -> None:
        with self._lock:
            if self._handle is None or self._width is None:
                raise RuntimeError("native HID sink is not connected")
            if len(cells) != self._width:
                raise ValueError(f"expected {self._width} cells, got {len(cells)}")
            if any(not isinstance(cell, int) or not 0 <= cell <= 0xFF for cell in cells):
                raise ValueError("braille cells must be integers from 0 through 255")
            try:
                self._send_frame(cells)
            except OSError as error:
                self._reconnect_and_retry(cells, error)
            if not self._announced_first_frame:
                # Appliance-debugging breadcrumb: proves the tick/write path
                # ran end-to-end at least once this session.
                self._announced_first_frame = True
                print("[info] first frame delivered to the display",
                      file=sys.stderr, flush=True)

    def start_key_listener(
        self, keys_changed: Callable[[frozenset], None]
    ) -> threading.Thread:
        """Deliver display key reports on a daemon thread.

        ``keys_changed(frozenset)`` receives the FULL currently-pressed key
        set (symbolic names from ``windows_hid.HW_KEY_NAMES``) on every key
        report. Non-key reports are hex-logged for discovery. The thread
        rides the sink's own armed input read and survives reconnects by
        re-reading handle/pending under the lock each cycle.
        """
        if self._keys_thread is not None:
            raise RuntimeError("display key listener is already running")
        if self._handle is None:
            raise RuntimeError("native HID sink is not connected")
        self._keys_stop.clear()
        self._keys_thread = threading.Thread(
            target=self._key_listener_loop, args=(keys_changed,), daemon=True
        )
        self._keys_thread.start()
        return self._keys_thread

    def _key_listener_loop(self, keys_changed: Callable[[frozenset], None]) -> None:
        rearm_warned = False
        announced_first_report = False
        last_diag = None

        def diag(state, message):
            # State-change-only breadcrumbs: the loop spins at ~4 Hz, so log
            # transitions, never steady states. Cheap enough to keep in
            # production — reconnect bugs here are invisible without them.
            nonlocal last_diag
            if state != last_diag:
                last_diag = state
                print(f"[keys:diag] {message}", file=sys.stderr, flush=True)

        while not self._keys_stop.is_set():
            with self._lock:
                handle = self._handle
                if handle is not None and self._pending is None \
                        and self._input_length > 0:
                    # The only place a read is re-armed. A pending input
                    # read is part of the session recipe that works on the
                    # hardware (docs/HARDWARE-FINDINGS.md); losing it would
                    # silently end key input for the session.
                    try:
                        self._pending = hid.queue_input_read(
                            self._api, handle, self._input_length
                        )
                        rearm_warned = False
                        diag(("armed", id(self._pending)),
                             f"input read re-armed (handle {handle})")
                    except OSError as error:
                        if not rearm_warned:
                            rearm_warned = True
                            print(
                                f"[keys:display] input read re-arm failed "
                                f"({error}); retrying",
                                file=sys.stderr, flush=True,
                            )
                pending = self._pending
            if handle is None or pending is None:
                # Mid-reconnect, closed, or re-arm failing: retry shortly.
                diag("idle", "no device handle; waiting for reconnect")
                time.sleep(0.2)
                continue
            diag(("bound", id(pending)),
                 f"listening on input read (handle {handle})")
            # Wait OUTSIDE the lock: a blocked wait must never stall writes.
            # Short timeout so stop/reconnect swaps are noticed promptly.
            rc = self._api.kernel32.WaitForSingleObject(pending.event, 250)
            if rc != _WAIT_OBJECT_0:
                if rc != _WAIT_TIMEOUT:
                    diag(("waitfail", id(pending), rc),
                         f"input event wait returned 0x{rc & 0xFFFFFFFF:x} "
                         "(event closed by a reconnect?)")
                continue  # timeout, or event invalidated by a reconnect
            data = b""
            with self._lock:
                if self._pending is not pending or self._handle != handle:
                    diag("stale", "read swapped by a reconnect; rebinding")
                    continue  # a reconnect swapped the read under us
                transferred = wintypes.DWORD(0)
                ok = self._api.kernel32.GetOverlappedResult(
                    handle, ctypes.byref(pending.operation),
                    ctypes.byref(transferred), False,
                )
                if ok:
                    data = bytes(pending.buffer[: transferred.value])
                else:
                    diag(("readerr", id(pending)),
                         "input read completed with an error "
                         "(device lost mid-read)")
                # This read is consumed either way; release its event. The
                # top of the loop re-arms the next read immediately.
                hid.cancel_input_read(self._api, None, pending)
                self._pending = None
            if not data:
                continue
            if not announced_first_report:
                # Distinguishes an inert listener (device never sends input
                # reports) from an active one when reading logs after a
                # hands-off hardware session.
                announced_first_report = True
                print("[keys:display] first input report received",
                      file=sys.stderr, flush=True)
            keys = hid.parse_humanware_pressed_keys(data)
            if keys is None:
                if data[0] == hid.HW_REPORT_POWERING_OFF:
                    print("[keys:display] display is powering off",
                          file=sys.stderr, flush=True)
                else:
                    print(f"[keys:display] raw report: {data.hex(' ')}",
                          file=sys.stderr, flush=True)
                continue
            try:
                keys_changed(keys)
            except Exception as error:  # a bad mapping must not kill input
                print(f"[keys:display] handler error: {error}",
                      file=sys.stderr, flush=True)

    def close(self) -> None:
        self._keys_stop.set()
        with self._lock:
            self._close_handle_unlocked()
            self._profile = None
