"""Native COM-port sinks for HumanWare and HIMS displays.

Two port families share the protocol layer here:
- USB serial: exact VID/PID identities from pyserial's enumeration.
- Bluetooth SPP: paired-device NAME identities from the registry
  (``bluetooth_ports``) — Bluetooth virtual COM ports carry no VID/PID.
"""

from __future__ import annotations

import sys
import threading
import time
from typing import Sequence

import display_profiles as profiles
import hims_protocol
from sink_reconnect import (
    DEFAULT_RECONNECT_ATTEMPTS,
    forget_held_chord,
    reconnect_and_resend,
)


def _serial_modules():
    try:
        import serial
        from serial.tools import list_ports
    except ImportError as error:
        raise RuntimeError("the packaged pyserial runtime is missing") from error
    return serial, list_ports.comports


def enumerate_serial_displays(ports_provider=None):
    if ports_provider is None:
        _serial, ports_provider = _serial_modules()
    matches = []
    for port in ports_provider():
        vid = getattr(port, "vid", None)
        pid = getattr(port, "pid", None)
        if vid is None or pid is None:
            continue
        for profile in profiles.serial_profiles():
            if profile.matches_identity(vid, pid):
                matches.append((profile, port))
    return tuple(matches)


def _read_exact(device, length):
    data = bytearray()
    while len(data) < length:
        chunk = device.read(length - len(data))
        if not chunk:
            break
        data.extend(chunk)
    return bytes(data)


def _scan_for(device, marker, limit):
    for _index in range(limit):
        value = _read_exact(device, 1)
        if not value:
            return False
        if value == marker:
            return True
    return False


def _write_all(device, data):
    written = device.write(data)
    if written != len(data):
        raise OSError(f"short serial write: {written} of {len(data)} bytes")


class _SerialSinkBase:
    transport = None
    parity_name = None

    def __init__(
        self,
        *,
        profile_id=None,
        serial_module=None,
        ports_provider=None,
        connect_attempts=3,
        reconnect_attempts=DEFAULT_RECONNECT_ATTEMPTS,
        reconnect_delay=0.5,
        settle_seconds=0.2,
    ):
        self._requested_profile_id = profile_id
        self._serial_module = serial_module
        self._ports_provider = ports_provider
        self._connect_attempts = connect_attempts
        self._reconnect_attempts = reconnect_attempts
        self._reconnect_delay = reconnect_delay
        self._settle_seconds = settle_seconds
        self._device = None
        self._profile = None
        self._width = None
        self._port_name = None
        self._lock = threading.RLock()

    @property
    def profile_id(self):
        return self._profile.id if self._profile else None

    @property
    def device_identity(self):
        """Cold-watch identity: the COM port this session is on. None while
        disconnected. The Bluetooth mixin overrides this to None — a paired
        unit's virtual COM port persists while the display is off, so a
        port removal can never mean a Bluetooth cold death."""
        if self._device is None or not self._port_name:
            return None
        return ("com", self._port_name)

    @property
    def display_name(self):
        return self._profile.name if self._profile else None

    def _runtime(self):
        if self._serial_module is None or self._ports_provider is None:
            serial_module, ports_provider = _serial_modules()
            self._serial_module = self._serial_module or serial_module
            self._ports_provider = self._ports_provider or ports_provider

    def _select(self, pin_profile_id=None):
        matches = [
            item
            for item in enumerate_serial_displays(self._ports_provider)
            if item[0].transport == self.transport
        ]
        pinned = pin_profile_id or self._requested_profile_id
        if pinned:
            requested = profiles.get_profile(pinned)
            if requested.transport != self.transport or not requested.native_supported:
                raise RuntimeError(f"{requested.name} is not a {self.transport} profile")
            matches = [item for item in matches if item[0].id == requested.id]
        if not matches:
            raise RuntimeError(f"no supported {self.transport} display is connected")
        if len(matches) > 1:
            raise RuntimeError(
                "multiple serial braille displays are connected ("
                + ", ".join(profile.name for profile, _ in matches)
                + ")"
            )
        return matches[0]

    def _open(self, port):
        parity = getattr(self._serial_module, self.parity_name)
        return self._serial_module.Serial(
            port.device,
            baudrate=115200,
            parity=parity,
            timeout=0.2,
            write_timeout=0.2,
        )

    def _close_unlocked(self):
        if self._device is not None:
            self._device.close()
        self._device = None
        self._width = None
        self._port_name = None

    def _connect_once(self, pin_profile_id=None):
        profile, port = self._select(pin_profile_id)
        device = self._open(port)
        try:
            if self._settle_seconds:
                time.sleep(self._settle_seconds)
            width = self._initialize(device)
            if not 1 <= width <= 255:
                raise RuntimeError(f"{profile.name} returned invalid cell count {width}")
        except Exception:
            device.close()
            raise
        self._device = device
        self._profile = profile
        self._width = width
        self._port_name = port.device
        return width

    def connect(self):
        with self._lock:
            self._runtime()
            self._close_unlocked()
            self._profile = None
            last_error = None
            for attempt in range(self._connect_attempts):
                try:
                    width = self._connect_once()
                    print(
                        f"[info] display={self.display_name} profile={self.profile_id} "
                        f"transport={self.transport}",
                        file=sys.stderr,
                    )
                    return width
                except (OSError, RuntimeError, ValueError) as error:
                    last_error = error
                    self._close_unlocked()
                    if attempt + 1 < self._connect_attempts:
                        time.sleep(self._settle_seconds)
            raise RuntimeError(f"{self.transport} connection failed: {last_error}") from last_error

    def write(self, cells: Sequence[int]):
        with self._lock:
            if self._device is None or self._width is None:
                raise RuntimeError(f"{self.transport} sink is not connected")
            if len(cells) != self._width:
                raise ValueError(f"expected {self._width} cells, got {len(cells)}")
            frame = self._frame(cells)
            serial_error = self._serial_module.SerialException
            try:
                _write_all(self._device, frame)
                return
            except (OSError, serial_error) as error:
                first_error = error
            expected_width = self._width
            # Pin the reconnect to the unit that was connected, so an outage
            # can never silently switch output to a different display.
            profile_id = (self._profile.id if self._profile
                          else self._requested_profile_id)
            self._close_unlocked()
            reconnect_and_resend(
                "serial display",
                first_error,
                attempts=self._reconnect_attempts,
                delay=self._reconnect_delay,
                connect=lambda: self._connect_once(profile_id),
                resend=lambda: _write_all(self._device, frame),
                close=self._close_unlocked,
                expected_width=expected_width,
                errors=(OSError, RuntimeError, ValueError, serial_error),
            )

    def close(self):
        with self._lock:
            self._close_unlocked()
            self._profile = None


class HumanWareSerialSink(_SerialSinkBase):
    transport = profiles.HUMANWARE_SERIAL
    parity_name = "PARITY_EVEN"

    def _initialize(self, device):
        for _attempt in range(3):
            device.reset_input_buffer()
            _write_all(device, b"\x1b\x00\x00")
            if not _scan_for(device, b"\x1b", 32):
                continue
            message = _read_exact(device, 1)
            length_raw = _read_exact(device, 1)
            if not length_raw:
                continue
            payload = _read_exact(device, length_raw[0])
            if message == b"\x01" and len(payload) >= 3 and payload[0] == 0:
                return payload[2]
        raise RuntimeError("display did not answer the HumanWare serial initialization request")

    @staticmethod
    def _frame(cells):
        if len(cells) > 255:
            raise ValueError("HumanWare serial display cannot exceed 255 cells")
        return b"\x1b\x02" + bytes((len(cells),)) + bytes(cells)


class HimsSerialSink(_SerialSinkBase):
    transport = profiles.HIMS_SERIAL
    parity_name = "PARITY_NONE"

    def _initialize(self, device):
        for _attempt in range(3):
            device.reset_input_buffer()
            _write_all(device, hims_protocol.build_cell_count_request())
            if not _scan_for(device, b"\xfa", 64):
                continue
            packet = b"\xfa" + _read_exact(device, 9)
            if len(packet) != 10:
                continue
            checksum = sum(value for index, value in enumerate(packet) if index != 8) & 0xFF
            if packet[2] == 0x01 and packet[9] == 0xFB and packet[8] == checksum:
                if packet[1] == 0x02:
                    return packet[3]
        raise RuntimeError("display did not answer the HIMS cell-count request")

    @staticmethod
    def _frame(cells):
        return hims_protocol.build_display_packet(cells)


# --------------------------------------------------------------------------
# Bluetooth (SPP virtual COM ports)


# Warn once per port, not once per discovery pass — discovery re-runs every
# few seconds inside the startup/recovery retry loops.
_warned_nameless_ports = set()


def enumerate_bluetooth_displays(bt_ports_provider=None):
    """Paired Bluetooth SPP ports whose device NAME matches a registered
    Bluetooth profile. Passive: nothing is opened, so a paired-but-off
    display still enumerates (opening the port is what dials the radio)."""
    if bt_ports_provider is None:
        try:
            import bluetooth_ports
        except ImportError as error:
            raise RuntimeError("Bluetooth port discovery is unavailable") from error
        bt_ports_provider = bluetooth_ports.enumerate_spp_ports
    bt_profiles = profiles.bluetooth_profiles()
    matches = []
    for port in bt_ports_provider():
        if not port.name:
            # A port with no recorded name can never match — say so once,
            # or a genuinely paired display would be invisible with zero
            # appliance-side telemetry.
            key = (port.device, port.address)
            if key not in _warned_nameless_ports:
                _warned_nameless_ports.add(key)
                print(
                    f"[warn] paired Bluetooth serial port {port.device} has no "
                    "recorded device name and was skipped; if this is the "
                    "braille display, re-pair it (diagnostics: "
                    "device_inventory.ps1)",
                    file=sys.stderr, flush=True,
                )
            continue
        for profile in bt_profiles:
            if profile.matches_device_name(port.name):
                # Registry load validates that prefixes identify exactly one
                # profile, so the first match is the only match.
                matches.append((profile, port))
                break
    return tuple(matches)


class _BluetoothPortMixin:
    """Select the COM port by paired-device name instead of USB VID/PID.

    The protocol layer (init handshake, framing, in-write reconnect) is
    inherited unchanged from the USB serial sinks — HumanWare and HIMS speak
    the same serial protocol over RFCOMM that they speak over USB serial.
    """

    def __init__(self, *, bt_ports_provider=None,
                 reconnect_attempts=DEFAULT_RECONNECT_ATTEMPTS, **kwargs):
        # In-write reconnects hold the engine lock, and reopening a Bluetooth
        # port blocks for seconds while the radio dials. Cap them and let
        # run.py's recover_display own longer outages (such as the display
        # suspending itself).
        capped = min(reconnect_attempts, 2)
        if capped != reconnect_attempts:
            # Never silently override an explicit --reconnect-attempts.
            print(
                f"[info] Bluetooth caps in-write display reconnects at "
                f"{capped} (a radio reopen blocks braille output for "
                "seconds); display recovery keeps retrying beyond that",
                file=sys.stderr, flush=True,
            )
        # One radio dial per connect(): both callers already retry with their
        # own backoff.
        kwargs.setdefault("connect_attempts", 1)
        super().__init__(reconnect_attempts=capped, **kwargs)
        self._bt_ports_provider = bt_ports_provider
        self._paired_name = ""

    @property
    def display_name(self):
        if self._profile is None:
            return None
        if self._paired_name:
            return f"{self._profile.name}: {self._paired_name}"
        return self._profile.name

    @property
    def device_identity(self):
        # No cold-watch identity for Bluetooth: the virtual COM port
        # persists while the unit is off or out of range, so Windows never
        # raises a removal event for a BT power-off — the write path and
        # recover_display own those outages.
        return None

    def _runtime(self):
        # Bluetooth ports come from the registry (bluetooth_ports), not
        # pyserial's port list — only the Serial class itself is needed.
        if self._serial_module is None:
            serial_module, _ports = _serial_modules()
            self._serial_module = serial_module

    def _select(self, pin_profile_id=None):
        matches = [
            item
            for item in enumerate_bluetooth_displays(self._bt_ports_provider)
            if item[0].transport == self.transport
        ]
        pinned = pin_profile_id or self._requested_profile_id
        if pinned:
            requested = profiles.get_profile(pinned)
            if requested.transport != self.transport or not requested.native_supported:
                raise RuntimeError(f"{requested.name} is not a {self.transport} profile")
            matches = [item for item in matches if item[0].id == requested.id]
        if not matches:
            raise RuntimeError(
                "no paired Bluetooth braille display was found. Pair the "
                "display in Windows Settings > Bluetooth & devices and turn "
                "on its Bluetooth terminal/serial connection"
            )
        if len(matches) > 1:
            detail = ", ".join(
                f"{port.name} on {port.device}" for _profile, port in matches
            )
            addresses = {port.address for _profile, port in matches}
            if len(addresses) < len(matches):
                # Same remote address twice = one physical display owning two
                # SPP ports (a stale entry surviving a re-pair, or a second
                # service record). "Unpair the extras" cannot fix that — name
                # the actual remedy.
                raise RuntimeError(
                    f"the same paired display owns more than one Bluetooth "
                    f"serial port ({detail}); remove the stale 'Standard "
                    "Serial over Bluetooth link' port in Device Manager, or "
                    "unpair and re-pair the display"
                )
            # One profile covers a vendor family, so a profile pin cannot
            # split two paired units of the same family — being explicit
            # beats guessing which unit the reader's hands are on.
            raise RuntimeError(
                f"multiple paired Bluetooth braille displays match ({detail}); "
                "unpair the ones not in use"
            )
        profile, port = matches[0]
        self._paired_name = port.name
        return profile, port

    def _open(self, port):
        # Line settings (baud/parity) are advisory over RFCOMM — the BTHENUM
        # driver ignores them. Opening the port is what dials the radio
        # link, and reads/writes ride radio latency, so the timeouts are
        # looser than the USB serial sinks' 0.2 s.
        return self._serial_module.Serial(
            port.device,
            baudrate=115200,
            timeout=0.5,
            write_timeout=2.0,
        )


# HumanWare serial key events (Bluetooth terminal mode). Message ids follow
# BRLTTY's HumanWare driver and NVDA's brailliantB driver: unlike the HID
# transport's full-set report 0x04, the serial transport sends one key DOWN
# and one key UP message per key, with the same key numbering both transports
# share (windows_hid.HW_KEY_NAMES).
_HW_MSG_INIT_RESP = 0x01
_HW_MSG_KEY_DOWN = 0x05
_HW_MSG_KEY_UP = 0x06
# Serial message id per BRLTTY's brldefs-hw.h — NOT the HID report id 0x07,
# which means firmware-update in the serial message space.
_HW_MSG_POWERING_OFF = 0x10

_hw_key_table = None


def _humanware_key_name(code):
    global _hw_key_table
    if _hw_key_table is None:
        import windows_hid  # staged alongside this module; shares the table

        _hw_key_table = (windows_hid.HW_KEY_NAMES, windows_hid.HW_KEY_ROUTING_BASE)
    names, routing_base = _hw_key_table
    if code >= routing_base:
        return f"routing:{code - routing_base}"
    return names.get(code, f"key{code}")


class HumanWareBluetoothSink(_BluetoothPortMixin, HumanWareSerialSink):
    transport = profiles.HUMANWARE_BLUETOOTH

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._keys_changed = None
        self._held = set()
        self._reader_stop = threading.Event()
        self._reader_thread = None
        self._announced_first_event = False

    def _connect_once(self, pin_profile_id=None):
        # The init handshake reads synchronously, so it runs with the reader
        # stopped (_close_unlocked precedes every connect path); the reader
        # restarts only once the fresh port is fully initialized.
        width = super()._connect_once(pin_profile_id)
        if self._keys_changed is not None:
            self._held.clear()  # keys held across a disconnect never got an UP
            forget_held_chord(self._keys_changed)
            self._start_reader()
        return width

    def _close_unlocked(self):
        # Stop the reader WITHOUT joining: chord actions dispatch on the
        # reader thread and re-enter sink.write, which needs the very lock
        # every caller of this method holds — a join here could never succeed
        # mid-dispatch and would only burn its timeout under the engine lock.
        # Same discipline as the native HID sink's close(): signal and close;
        # the daemon thread exits on its own once the port dies under it.
        self._reader_stop.set()
        self._reader_thread = None
        super()._close_unlocked()  # closes the port; unblocks a pending read

    def start_key_listener(self, keys_changed):
        """Deliver display key events on a daemon reader thread.

        Same contract as the native HID sink: ``keys_changed(frozenset)``
        receives the FULL currently-pressed set (symbolic names from
        ``windows_hid.HW_KEY_NAMES``) on every change; the chord layer above
        accumulates and fires on the empty set at full release.
        """
        with self._lock:
            if self._keys_changed is not None:
                raise RuntimeError("display key listener is already running")
            # Remember the callback BEFORE the connected check: if the sink
            # is mid-outage right now (a write-path reconnect just failed),
            # the next successful reconnect auto-starts the reader with it
            # (_connect_once) instead of stranding display keys dead for the
            # session behind AutoDisplaySink's reuse-path early return.
            self._keys_changed = keys_changed
            self._held.clear()
            if self._device is None:
                raise RuntimeError(f"{self.transport} sink is not connected")
            self._start_reader()
            return self._reader_thread

    def close(self):
        super().close()
        # A closed sink is done listening: let a probe/embedder that reuses
        # one sink object attach a fresh listener after connect() instead of
        # hitting a false "already running" (and keep a stale callback from
        # silently resurrecting with the reader on the next connect).
        self._keys_changed = None

    def _start_reader(self):
        self._reader_stop = threading.Event()  # fresh stop per port session
        self._reader_thread = threading.Thread(
            target=self._reader_loop,
            args=(self._device, self._reader_stop),
            daemon=True,
        )
        self._reader_thread.start()

    def _reader_loop(self, device, stop):
        """Parse ESC-framed messages off the port until stopped.

        Exits (rather than reconnecting) on any port error: the write path
        and run.py's recover_display own reopening, and every successful
        reconnect starts a fresh reader against the fresh port.
        """

        def read_frame_bytes(length):
            # RFCOMM is a reliable in-order stream: a frame's remaining
            # bytes WILL arrive unless the link died, so a mid-frame read
            # timeout means "keep waiting", never "drop the frame" —
            # abandoning after one quiet 0.5 s window would drop key UPs and
            # leave phantom held keys poisoning every later chord. Returns
            # None on stop/port death.
            data = bytearray()
            while len(data) < length and not stop.is_set():
                try:
                    chunk = device.read(length - len(data))
                except Exception:
                    return None  # port closed under us or link died
                if chunk:
                    data.extend(chunk)
            return bytes(data) if len(data) == length else None

        while not stop.is_set():
            try:
                lead = device.read(1)
            except Exception:
                break  # port closed under us (teardown/reconnect) or link died
            if not lead:
                continue  # read timeout: idle link
            if lead != b"\x1b":
                continue  # resync: skip noise between frames
            header = read_frame_bytes(2)
            if header is None:
                break
            payload = read_frame_bytes(header[1])
            if payload is None:
                break
            if stop.is_set():
                break  # a teardown superseded this reader; do not dispatch
            try:
                self._handle_serial_message(header[0], payload)
            except Exception as error:
                # One bad frame or handler bug must not silently kill key
                # input for the session (mirrors the HID listener's guard).
                print(f"[keys:display] serial key handler error: {error}",
                      file=sys.stderr, flush=True)

    def _handle_serial_message(self, message, payload):
        if message in (_HW_MSG_KEY_DOWN, _HW_MSG_KEY_UP) and payload:
            code = payload[0]
            if message == _HW_MSG_KEY_DOWN:
                self._held.add(code)
            else:
                self._held.discard(code)
            keys_changed = self._keys_changed
            if keys_changed is None:
                return
            if not self._announced_first_event:
                # Same breadcrumb the HID listener leaves: distinguishes an
                # inert listener from an active one in hands-off logs.
                self._announced_first_event = True
                print("[keys:display] first Bluetooth key event received",
                      file=sys.stderr, flush=True)
            keys = frozenset(_humanware_key_name(code) for code in self._held)
            try:
                keys_changed(keys)
            except Exception as error:  # a bad mapping must not kill input
                print(f"[keys:display] handler error: {error}",
                      file=sys.stderr, flush=True)
        elif message == _HW_MSG_INIT_RESP:
            pass  # straggling init answer; the handshake already consumed its own
        elif message == _HW_MSG_POWERING_OFF:
            print("[keys:display] display is powering off",
                  file=sys.stderr, flush=True)
        else:
            # Hex-log unknown messages like the HID path does — any session
            # doubles as a protocol survey.
            print(f"[keys:display] raw serial message: {message:02x} "
                  f"{payload.hex(' ')}", file=sys.stderr, flush=True)


class HimsBluetoothSink(_BluetoothPortMixin, HimsSerialSink):
    """HIMS protocol over a paired Bluetooth SPP port. Output only: HIMS
    key packets are a different codec (not implemented), so display keys
    fall back to terminal keys via the standard downgrade message."""

    transport = profiles.HIMS_BLUETOOTH
