import threading
import time

import bluetooth_ports
import serial_sinks


EREADER = bluetooth_ports.BluetoothSerialPort(
    device="COM9", address="aabbccddee01", name="NLS eReader H1234"
)
BRAILLIANT = bluetooth_ports.BluetoothSerialPort(
    device="COM7", address="aabbccddee02", name="Brailliant BI 40X 456789"
)
EDGE = bluetooth_ports.BluetoothSerialPort(
    device="COM6", address="aabbccddee03", name="Braille EDGE 40 111"
)


class FakeSerialException(Exception):
    pass


class BtDevice:
    """Thread-safe fake port: the reader thread pulls while tests feed."""

    def __init__(self, protocol="humanware", width=20):
        self.protocol = protocol
        self.width = width
        self.writes = []
        self.closed = False
        self._buf = bytearray()
        self._lock = threading.Lock()

    def feed(self, data):
        with self._lock:
            self._buf.extend(data)

    def reset_input_buffer(self):
        with self._lock:
            self._buf.clear()

    def read(self, length):
        with self._lock:
            data = bytes(self._buf[:length])
            del self._buf[: len(data)]
        if not data:
            time.sleep(0.005)  # emulate the real port's read timeout pause
        return data

    def write(self, data):
        if self.closed:
            raise FakeSerialException("port is closed")
        self.writes.append(bytes(data))
        if self.protocol == "humanware" and bytes(data) == b"\x1b\x00\x00":
            self.feed(b"\x1b\x01\x03\x00\x00" + bytes((self.width,)))
        return len(data)

    def close(self):
        self.closed = True


class SerialModule:
    PARITY_EVEN = "even"
    PARITY_NONE = "none"
    SerialException = FakeSerialException

    def __init__(self, device):
        self.device = device
        self.calls = []

    def Serial(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.device


def humanware_sink(device, ports=(EREADER,), **kwargs):
    module = SerialModule(device)
    sink = serial_sinks.HumanWareBluetoothSink(
        serial_module=module,
        bt_ports_provider=lambda: tuple(ports),
        settle_seconds=0,
        connect_attempts=1,
        **kwargs,
    )
    return sink, module


def wait_until(condition, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.005)
    return condition()


def test_humanware_bluetooth_matches_by_paired_name_and_writes_frames():
    device = BtDevice()
    sink, module = humanware_sink(device)
    try:
        assert sink.connect() == 20
        assert sink.profile_id == "humanware-bluetooth"
        assert sink.display_name == (
            "HumanWare / APH braille display (Bluetooth): NLS eReader H1234"
        )
        sink.write([3] * 20)
        assert device.writes[-1] == b"\x1b\x02\x14" + bytes([3] * 20)
        # RFCOMM ignores line settings; the open must not die on a parity
        # kwarg and must use the registry-discovered COM port.
        assert module.calls[0][0][0] == "COM9"
        assert "parity" not in module.calls[0][1]
    finally:
        sink.close()


def test_bluetooth_connect_demands_a_paired_match():
    device = BtDevice()
    sink, _module = humanware_sink(device, ports=())
    try:
        sink.connect()
        raise AssertionError("connect succeeded with nothing paired")
    except RuntimeError as error:
        assert "Pair the display" in str(error)


def test_two_paired_family_units_are_refused_not_guessed():
    device = BtDevice()
    sink, _module = humanware_sink(device, ports=(EREADER, BRAILLIANT))
    try:
        sink.connect()
        raise AssertionError("ambiguous pairing was accepted")
    except RuntimeError as error:
        assert "unpair" in str(error)


def test_other_family_ports_do_not_collide():
    device = BtDevice()
    # A HIMS unit paired alongside the eReader belongs to the other profile;
    # the HumanWare sink must still see exactly one candidate.
    sink, _module = humanware_sink(device, ports=(EREADER, EDGE))
    try:
        assert sink.connect() == 20
    finally:
        sink.close()


def test_in_write_reconnect_attempts_are_capped_for_bluetooth():
    device = BtDevice()
    sink, _module = humanware_sink(device, reconnect_attempts=8)
    assert sink._reconnect_attempts == 2  # radio reopens block the engine lock


def test_connect_dials_once_by_default():
    """Both callers (startup wait, recovery) retry forever with backoff, and
    every extra connect attempt is another multi-second radio dial."""
    device = BtDevice()
    module = SerialModule(device)
    sink = serial_sinks.HumanWareBluetoothSink(
        serial_module=module, bt_ports_provider=lambda: (EREADER,)
    )
    assert sink._connect_attempts == 1


def test_same_address_ghost_ports_name_the_real_remedy():
    ghost = bluetooth_ports.BluetoothSerialPort(
        device="COM10", address=EREADER.address, name=EREADER.name
    )
    device = BtDevice()
    sink, _module = humanware_sink(device, ports=(EREADER, ghost))
    try:
        sink.connect()
        raise AssertionError("ghost twin port was accepted")
    except RuntimeError as error:
        assert "Device Manager" in str(error)  # unpairing extras cannot fix this


def test_key_events_accumulate_and_release_like_the_hid_listener():
    device = BtDevice()
    sink, _module = humanware_sink(device)
    seen = []
    try:
        sink.connect()
        sink.start_key_listener(lambda keys: seen.append(keys))
        device.feed(b"\x1b\x05\x01\x0a")  # space down
        device.feed(b"\x1b\x05\x01\x02")  # dot1 down
        device.feed(b"\x1b\x06\x01\x0a")  # space up
        device.feed(b"\x1b\x06\x01\x02")  # dot1 up -> empty set fires chords
        assert wait_until(lambda: len(seen) == 4)
        assert seen == [
            frozenset({"space"}),
            frozenset({"space", "dot1"}),
            frozenset({"dot1"}),
            frozenset(),
        ]
    finally:
        sink.close()


def test_split_frame_waits_instead_of_dropping_the_key_event():
    """A frame interrupted by a read timeout must complete, not vanish: a
    dropped key-UP would leave a phantom held key poisoning every later
    chord (the fake's read returns empty immediately, mimicking a timeout
    between the ESC lead and the frame body)."""
    device = BtDevice()
    sink, _module = humanware_sink(device)
    seen = []
    try:
        sink.connect()
        sink.start_key_listener(lambda keys: seen.append(keys))
        device.feed(b"\x1b\x05")  # lead + message id only; body delayed
        time.sleep(0.05)          # reader sees several empty reads meanwhile
        device.feed(b"\x01\x0a")  # length + key code arrive late
        assert wait_until(lambda: seen == [frozenset({"space"})])
    finally:
        sink.close()


def test_close_allows_a_fresh_listener_on_reconnect():
    devices = []

    class FreshPortModule(SerialModule):
        # Real pyserial constructs a new port object per open; reusing the
        # closed fake would refuse the reconnect for the wrong reason.
        def Serial(self, *args, **kwargs):
            devices.append(BtDevice())
            return devices[-1]

    sink = serial_sinks.HumanWareBluetoothSink(
        serial_module=FreshPortModule(None),
        bt_ports_provider=lambda: (EREADER,),
        settle_seconds=0,
        connect_attempts=1,
    )
    sink.connect()
    sink.start_key_listener(lambda keys: None)
    sink.close()
    seen = []
    sink.connect()
    sink.start_key_listener(lambda keys: seen.append(keys))  # no false "already running"
    try:
        devices[-1].feed(b"\x1b\x05\x01\x0a")
        assert wait_until(lambda: seen == [frozenset({"space"})])
    finally:
        sink.close()


def test_failed_listener_attach_self_heals_on_reconnect():
    """start_key_listener on a disconnected sink raises, but the callback is
    remembered — the next successful connect must start delivering keys
    instead of stranding them dead behind the reuse-path early return."""
    device = BtDevice()
    sink, _module = humanware_sink(device)
    seen = []
    try:
        sink.start_key_listener(lambda keys: seen.append(keys))
        raise AssertionError("listener attached while disconnected")
    except RuntimeError as error:
        assert "not connected" in str(error)
    try:
        sink.connect()  # revives the remembered listener
        device.feed(b"\x1b\x05\x01\x0a")
        assert wait_until(lambda: seen == [frozenset({"space"})])
    finally:
        sink.close()


def test_routing_keys_and_noise_do_not_break_the_reader():
    device = BtDevice()
    sink, _module = humanware_sink(device)
    seen = []
    try:
        sink.connect()
        sink.start_key_listener(lambda keys: seen.append(keys))
        device.feed(b"\x00\xff")  # line noise between frames: resync
        device.feed(b"\x1b\x05\x01" + bytes((80 + 3,)))  # routing key 3 down
        assert wait_until(lambda: len(seen) == 1)
        assert seen == [frozenset({"routing:3"})]
    finally:
        sink.close()


def test_close_stops_the_reader_thread():
    device = BtDevice()
    sink, _module = humanware_sink(device)
    sink.connect()
    thread = sink.start_key_listener(lambda keys: None)
    sink.close()
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert device.closed


def test_second_key_listener_is_refused():
    device = BtDevice()
    sink, _module = humanware_sink(device)
    try:
        sink.connect()
        sink.start_key_listener(lambda keys: None)
        try:
            sink.start_key_listener(lambda keys: None)
            raise AssertionError("second listener was accepted")
        except RuntimeError as error:
            assert "already running" in str(error)
    finally:
        sink.close()


def test_enumerate_bluetooth_displays_pairs_ports_with_profiles():
    found = serial_sinks.enumerate_bluetooth_displays(
        lambda: (EREADER, EDGE, bluetooth_ports.BluetoothSerialPort("COM2", "aa", ""))
    )
    assert [(profile.id, port.device) for profile, port in found] == [
        ("humanware-bluetooth", "COM9"),
        ("hims-bluetooth", "COM6"),
    ]
