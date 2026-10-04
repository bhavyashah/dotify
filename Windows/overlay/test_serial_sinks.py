

import display_profiles as profiles
import hims_protocol
import serial_sinks


class Port:
    def __init__(self, vid, pid, device="COM7"):
        self.vid = vid
        self.pid = pid
        self.device = device


class FakeSerialException(Exception):
    pass


class Device:
    def __init__(self, protocol):
        self.protocol = protocol
        self.incoming = bytearray()
        self.writes = []
        self.closed = False

    def reset_input_buffer(self):
        self.incoming.clear()

    def write(self, data):
        self.writes.append(data)
        if self.protocol == "humanware" and data == b"\x1b\x00\x00":
            self.incoming.extend(b"\x1b\x01\x03\x00\x00\x20")
        elif self.protocol == "hims" and data == hims_protocol.build_cell_count_request():
            packet = bytearray(b"\xfa\x02\x01\x20\x00\x00\x00\x00\x00\xfb")
            packet[8] = sum(value for index, value in enumerate(packet) if index != 8) & 0xFF
            self.incoming.extend(packet)
        return len(data)

    def read(self, length):
        data = bytes(self.incoming[:length])
        del self.incoming[:length]
        return data

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


def test_humanware_serial_initializes_and_writes_exact_frame():
    profile = profiles.get_profile("brailliant-bi-b-serial")
    device = Device("humanware")
    module = SerialModule(device)
    sink = serial_sinks.HumanWareSerialSink(
        profile_id=profile.id,
        serial_module=module,
        ports_provider=lambda: [Port(profile.vid, profile.pid)],
        settle_seconds=0,
        connect_attempts=1,
    )
    assert sink.connect() == 32
    sink.write([1] * 32)
    assert device.writes[-1] == b"\x1b\x02\x20" + bytes([1] * 32)
    assert module.calls[0][1]["parity"] == "even"


def test_hims_serial_requests_width_and_uses_checked_packet_codec():
    profile = profiles.get_profile("hims-syncbraille-serial")
    device = Device("hims")
    module = SerialModule(device)
    sink = serial_sinks.HimsSerialSink(
        profile_id=profile.id,
        serial_module=module,
        ports_provider=lambda: [Port(profile.vid, profile.pid)],
        settle_seconds=0,
        connect_attempts=1,
    )
    assert sink.connect() == 32
    sink.write(range(32))
    assert device.writes[-1] == hims_protocol.build_display_packet(range(32))
    assert hims_protocol.checksum_is_valid(device.writes[-1])
    assert module.calls[0][1]["parity"] == "none"


def test_serial_discovery_uses_exact_vid_pid_only():
    target = profiles.get_profile("brailliant-bi14-serial")
    found = serial_sinks.enumerate_serial_displays(
        lambda: [Port(0x9999, 1, "COM1"), Port(target.vid, target.pid, "COM2")]
    )
    assert [(profile.id, port.device) for profile, port in found] == [
        ("brailliant-bi14-serial", "COM2")
    ]
