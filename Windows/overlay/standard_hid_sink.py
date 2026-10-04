"""Descriptor-driven sink for USB HID Braille usage page 0x41."""

from __future__ import annotations

import sys
import threading
import time
from typing import Sequence

from sink_reconnect import DEFAULT_RECONNECT_ATTEMPTS, reconnect_and_resend
import windows_hid as hid


class StandardHidSink:
    """Single-row standard HID Braille display sink using Windows HidUsb."""

    def __init__(
        self,
        *,
        api=None,
        warmup_delay: float = 0.25,
        connect_attempts: int = 3,
        reconnect_attempts: int = DEFAULT_RECONNECT_ATTEMPTS,
        reconnect_delay: float = 0.5,
        write_timeout_ms: int = 2000,
        identity=None,
    ) -> None:
        self._api = api
        self._warmup_delay = warmup_delay
        self._connect_attempts = connect_attempts
        self._reconnect_attempts = reconnect_attempts
        self._reconnect_delay = reconnect_delay
        self._write_timeout_ms = write_timeout_ms
        self._handle = None
        self._pending = None
        self._preparsed = None
        self._collection = None
        self._width = None
        self._identity = None
        self._requested_identity = identity
        self._device_path = None
        self._lock = threading.RLock()

    @property
    def device_identity(self):
        """Cold-watch identity: the exact HID interface path this session
        opened (see native_hid_sink). None while disconnected."""
        if self._handle is None or not self._device_path:
            return None
        return ("hid", self._device_path.lower())

    def _close_unlocked(self) -> None:
        if self._api is not None:
            hid.free_preparsed_data(self._api, self._preparsed)
            hid.cancel_input_read(self._api, self._handle, self._pending)
            self._api.close_handle(self._handle)
        self._handle = None
        self._pending = None
        self._preparsed = None
        self._collection = None
        self._width = None
        self._device_path = None

    def _select(self, identity=None):
        valid = []
        invalid = []
        for collection in hid.enumerate_hid_collections(self._api):
            if collection.usage_page != hid.STANDARD_BRAILLE_USAGE_PAGE:
                continue
            if identity and (collection.vid, collection.pid) != identity:
                continue
            try:
                hid.standard_braille_width(collection)
            except RuntimeError as error:
                invalid.append(str(error))
                continue
            valid.append(collection)
        if not valid:
            detail = f": {'; '.join(invalid)}" if invalid else ""
            raise RuntimeError(f"no compatible single-row standard HID Braille display found{detail}")
        if len(valid) > 1:
            names = ", ".join(item.product or item.path for item in valid)
            raise RuntimeError(f"multiple standard HID Braille displays found ({names})")
        return valid[0]

    def _connect_once(self, identity=None) -> int:
        # Two fresh shared inspection passes preserve the wake-up behavior that
        # proved necessary on the NLS eReader's composite HID device.
        selected = self._select(identity)
        if self._warmup_delay > 0:
            time.sleep(self._warmup_delay)
        selected = self._select(identity)
        handle = self._api.open_handle(selected.path, shared=False, overlapped=True)
        pending = None
        preparsed = None
        try:
            collection = hid.inspect_open_handle(self._api, handle, selected.path)
            if collection is None or collection.usage_page != hid.STANDARD_BRAILLE_USAGE_PAGE:
                raise RuntimeError("opened path is not a standard HID Braille collection")
            width = hid.standard_braille_width(collection)
            preparsed = hid.acquire_preparsed_data(self._api, handle)
            if collection.input_length > 0:
                pending = hid.queue_input_read(self._api, handle, collection.input_length)
        except Exception:
            hid.free_preparsed_data(self._api, preparsed)
            hid.cancel_input_read(self._api, handle, pending)
            self._api.close_handle(handle)
            raise
        self._handle = handle
        self._pending = pending
        self._preparsed = preparsed
        self._collection = collection
        self._width = width
        self._identity = (collection.vid, collection.pid)
        self._device_path = selected.path
        return width

    def _connect_with_attempts(self, identity, attempts):
        last_error = None
        for attempt in range(1, attempts + 1):
            try:
                return self._connect_once(identity)
            except (OSError, RuntimeError, ValueError) as error:
                last_error = error
                self._close_unlocked()
                if attempt < attempts:
                    time.sleep(self._warmup_delay)
        raise RuntimeError(f"standard HID connection failed: {last_error}") from last_error

    def connect(self) -> int:
        with self._lock:
            self._close_unlocked()
            self._identity = self._requested_identity
            if self._api is None:
                self._api = hid.WinApi()
            width = self._connect_with_attempts(self._requested_identity, self._connect_attempts)
            print(
                f"[info] display={self._collection.product or 'Standard HID Braille Display'} "
                f"transport=standard-hid vid={self._collection.vid:04X} "
                f"pid={self._collection.pid:04X}",
                file=sys.stderr,
            )
            return width

    @property
    def display_name(self):
        if self._collection is None:
            return None
        return self._collection.product or "Standard HID Braille Display"

    @property
    def profile_id(self):
        return None

    def _send(self, cells: Sequence[int]) -> None:
        reports = hid.build_standard_output_reports(
            self._api,
            self._handle,
            self._collection,
            cells,
            preparsed_data=self._preparsed,
        )
        for report in reports:
            written = hid.write_file(
                self._api,
                self._handle,
                report,
                overlapped=True,
                timeout_ms=self._write_timeout_ms,
            )
            if written != len(report):
                raise OSError(f"short HID write: {written} of {len(report)} bytes")

    def write(self, cells: Sequence[int]) -> None:
        with self._lock:
            if self._handle is None or self._width is None:
                raise RuntimeError("standard HID sink is not connected")
            if len(cells) != self._width:
                raise ValueError(f"expected {self._width} cells, got {len(cells)}")
            expected_width = self._width
            identity = self._identity
            try:
                self._send(cells)
                return
            except OSError as error:
                first_error = error
            self._close_unlocked()
            print(
                f"[warn] standard HID write failed ({first_error}); reconnecting",
                file=sys.stderr,
            )
            reconnect_and_resend(
                "standard HID display",
                first_error,
                attempts=self._reconnect_attempts,
                delay=self._reconnect_delay,
                connect=lambda: self._connect_once(identity),
                resend=lambda: self._send(cells),
                close=self._close_unlocked,
                expected_width=expected_width,
                errors=(OSError, RuntimeError, ValueError),
            )

    def close(self) -> None:
        with self._lock:
            self._close_unlocked()
            self._identity = None
