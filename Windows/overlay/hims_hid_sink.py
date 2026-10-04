"""Native Windows HID sink for NVDA-compatible HIMS HID displays."""

from __future__ import annotations

import sys
import threading
import time
from typing import Sequence

import display_profiles as profiles
from sink_reconnect import DEFAULT_RECONNECT_ATTEMPTS, reconnect_and_resend
import windows_hid as hid


class HimsHidSink:
    def __init__(
        self,
        *,
        api=None,
        profile_id: str | None = None,
        settle_seconds: float = 0.2,
        warmup_delay: float = 0.2,
        connect_attempts: int = 3,
        reconnect_attempts: int = DEFAULT_RECONNECT_ATTEMPTS,
        reconnect_delay: float = 0.5,
    ):
        self._api = api
        self._requested_profile_id = profile_id
        self._settle_seconds = settle_seconds
        self._warmup_delay = warmup_delay
        self._connect_attempts = connect_attempts
        self._reconnect_attempts = reconnect_attempts
        self._reconnect_delay = reconnect_delay
        self._handle = None
        self._pending = None
        self._profile = None
        self._width = None
        self._device_path = None
        self._lock = threading.RLock()

    @property
    def profile_id(self):
        return self._profile.id if self._profile else None

    @property
    def device_identity(self):
        """Cold-watch identity: the exact HID interface path this session
        opened (see native_hid_sink). None while disconnected."""
        if self._handle is None or not self._device_path:
            return None
        return ("hid", self._device_path.lower())

    @property
    def display_name(self):
        return self._profile.name if self._profile else None

    def _close_unlocked(self):
        if self._api is not None:
            hid.cancel_input_read(self._api, self._handle, self._pending)
            self._api.close_handle(self._handle)
        self._handle = None
        self._pending = None
        self._width = None
        self._device_path = None

    def _select(self, collections, pin_profile_id=None):
        candidates = profiles.hims_hid_profiles()
        pinned = pin_profile_id or self._requested_profile_id
        if pinned:
            requested = profiles.get_profile(pinned)
            if requested.transport != profiles.HIMS_HID or not requested.native_supported:
                raise RuntimeError(f"{requested.name} is not a native HIMS HID profile")
            candidates = (requested,)
        matches = []
        for profile in candidates:
            if not any(profile.matches_identity(item.vid, item.pid) for item in collections):
                continue
            matches.append(
                (profile, hid.select_profile_collection(self._api, profile, collections=collections))
            )
        if not matches:
            raise RuntimeError("no supported HIMS HID display is connected")
        if len(matches) > 1:
            raise RuntimeError(
                "multiple HIMS HID displays are connected ("
                + ", ".join(profile.name for profile, _ in matches)
                + ")"
            )
        return matches[0]

    def _connect_once(self, pin_profile_id=None):
        selected = None
        for pass_number in range(2):
            selected = self._select(
                hid.enumerate_known_displays(self._api), pin_profile_id)
            if pass_number == 0 and self._warmup_delay:
                time.sleep(self._warmup_delay)
        profile, collection = selected
        handle = self._api.open_handle(collection.path, shared=False, overlapped=True)
        pending = None
        try:
            opened = hid.inspect_open_handle(self._api, handle, collection.path)
            if opened is None or not profile.matches_collection(opened):
                raise RuntimeError(f"opened path is not the expected collection for {profile.name}")
            if opened.input_length > 0:
                pending = hid.queue_input_read(self._api, handle, opened.input_length)
            if self._settle_seconds:
                time.sleep(self._settle_seconds)
            caps = hid.get_feature(self._api, handle, opened.feature_length, 0x01)
            if len(caps) <= 9:
                raise RuntimeError(
                    f"{profile.name} returned a {len(caps)}-byte feature report, "
                    "too short to read a cell count"
                )
            width = caps[9]
            if width < 1:
                raise RuntimeError(f"{profile.name} returned an invalid zero cell count")
            if opened.output_length != width + 1:
                raise RuntimeError(
                    f"{profile.name} reports {width} cells but its HID output length is "
                    f"{opened.output_length}"
                )
        except Exception:
            hid.cancel_input_read(self._api, handle, pending)
            self._api.close_handle(handle)
            raise
        self._handle = handle
        self._pending = pending
        self._profile = profile
        self._width = width
        self._device_path = collection.path
        return width

    def _connect_with_attempts(self, attempts):
        last_error = None
        for attempt in range(attempts):
            try:
                return self._connect_once()
            except (OSError, RuntimeError, ValueError) as error:
                last_error = error
                self._close_unlocked()
                if attempt + 1 < attempts and self._warmup_delay:
                    time.sleep(self._warmup_delay)
        raise RuntimeError(f"HIMS HID connection failed: {last_error}") from last_error

    def connect(self):
        with self._lock:
            self._close_unlocked()
            self._profile = None
            if self._api is None:
                self._api = hid.WinApi()
            width = self._connect_with_attempts(self._connect_attempts)
            print(
                f"[info] display={self.display_name} profile={self.profile_id} transport=hims-hid",
                file=sys.stderr,
            )
            return width

    def _send(self, cells: Sequence[int]):
        hid.set_output_report(self._api, self._handle, bytes((self._width,)) + bytes(cells))

    def write(self, cells: Sequence[int]):
        with self._lock:
            if self._handle is None or self._width is None:
                raise RuntimeError("HIMS HID sink is not connected")
            if len(cells) != self._width:
                raise ValueError(f"expected {self._width} cells, got {len(cells)}")
            try:
                self._send(cells)
                return
            except OSError as error:
                first_error = error
            expected_width = self._width
            # Pin the reconnect to the unit that was connected: auto-selection
            # could reattach to a different same-width display, or fail on
            # "multiple displays" if a second unit appeared mid-outage.
            profile_id = (self._profile.id if self._profile
                          else self._requested_profile_id)
            self._close_unlocked()
            reconnect_and_resend(
                "HIMS HID display",
                first_error,
                attempts=self._reconnect_attempts,
                delay=self._reconnect_delay,
                connect=lambda: self._connect_once(profile_id),
                resend=lambda: self._send(cells),
                close=self._close_unlocked,
                expected_width=expected_width,
                errors=(OSError, RuntimeError, ValueError),
            )

    def close(self):
        with self._lock:
            self._close_unlocked()
            self._profile = None
