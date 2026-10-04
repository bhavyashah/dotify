from types import SimpleNamespace
from unittest import mock

import auto_display_sink as auto
import display_profiles as profiles
import windows_hid


def profile_collection(profile_id="nls-ereader"):
    profile = profiles.get_profile(profile_id)
    usage = profile.usage_pages[0] if profile.usage_pages else 0xFF00
    width = 40 if profile.transport == profiles.HIMS_HID else 20
    feature = 16 if profile.transport == profiles.HIMS_HID else 39
    overhead = 1 if profile.transport == profiles.HIMS_HID else 4
    return windows_hid.Collection(
        profile.id, profile.vid, profile.pid, 1, usage, 1,
        12, width + overhead, feature, "Vendor", profile.name,
    )


def standard_collection():
    cap = windows_hid.ValueCap(0x20, 0x41, 1, 2, 0x41, 3, 8, 32, False)
    return windows_hid.Collection(
        "standard", 0x9999, 0x0001, 1, 0x41, 1,
        12, 33, 16, "Vendor", "Universal Braille", (cap,),
    )


class FakeSink:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.display_name = "Fake display"
        self.profile_id = kwargs.get("profile_id")

    def connect(self):
        return 20

    def write(self, cells):
        self.last = list(cells)

    def close(self):
        self.closed = True


def test_auto_prefers_registered_humanware_over_duplicate_standard_collection():
    nls = profile_collection()
    duplicate_standard = windows_hid.Collection(
        "nls-standard", nls.vid, nls.pid, 1, 0x41, 1, 12, 21, 16,
        "HumanWare", "NLS", (windows_hid.ValueCap(0x20, 0x41, 1, 2, 0x41, 3, 8, 20, False),),
    )
    with mock.patch.object(windows_hid, "enumerate_hid_collections", return_value=[nls, duplicate_standard]), \
         mock.patch.object(auto, "enumerate_serial_displays", return_value=()), \
         mock.patch.object(auto, "NativeHidSink", FakeSink):
        sink = auto.AutoDisplaySink(api=object())
        assert sink.connect() == 20
        assert sink.profile_id == "nls-ereader"


def test_unregistered_standard_hid_participates_in_normal_auto_detection():
    with mock.patch.object(windows_hid, "enumerate_hid_collections", return_value=[standard_collection()]), \
         mock.patch.object(auto, "enumerate_serial_displays", return_value=()), \
         mock.patch.object(auto, "StandardHidSink", FakeSink):
        sink = auto.AutoDisplaySink(api=object())
        assert sink.connect() == 20
        assert sink._sink.kwargs["identity"] == (0x9999, 0x0001)


def test_hims_hid_and_serial_profiles_route_to_their_protocol_sinks():
    hims_hid = profile_collection("hims-braille-edge-3s")
    with mock.patch.object(windows_hid, "enumerate_hid_collections", return_value=[hims_hid]), \
         mock.patch.object(auto, "enumerate_serial_displays", return_value=()), \
         mock.patch.object(auto, "HimsHidSink", FakeSink):
        sink = auto.AutoDisplaySink(api=object())
        sink.connect()
        assert sink.profile_id == "hims-braille-edge-3s"

    serial_profile = profiles.get_profile("hims-syncbraille-serial")
    with mock.patch.object(windows_hid, "enumerate_hid_collections", return_value=[]), \
         mock.patch.object(auto, "enumerate_serial_displays", return_value=((serial_profile, object()),)), \
         mock.patch.object(auto, "HimsSerialSink", FakeSink):
        sink = auto.AutoDisplaySink(api=object())
        sink.connect()
        assert sink.profile_id == "hims-syncbraille-serial"


def test_auto_rejects_multiple_physical_displays():
    with mock.patch.object(
        windows_hid,
        "enumerate_hid_collections",
        return_value=[profile_collection(), standard_collection()],
    ), mock.patch.object(auto, "enumerate_serial_displays", return_value=()), \
         mock.patch.object(auto, "NativeHidSink", FakeSink), \
         mock.patch.object(auto, "StandardHidSink", FakeSink):
        sink = auto.AutoDisplaySink(api=object())
        try:
            sink.connect()
            raise AssertionError("multiple displays were accepted")
        except RuntimeError as error:
            assert "multiple compatible" in str(error)


def bt_port(name="NLS eReader H1234", device="COM9"):
    return SimpleNamespace(device=device, name=name, address="aabbccddee01")


def test_usb_wins_over_paired_bluetooth():
    """A paired display's COM port exists even while the unit is off, so a
    Bluetooth candidate must never tie with (and block) a live USB unit."""
    serial_profile = profiles.get_profile("hims-syncbraille-serial")
    bt_profile = profiles.get_profile("humanware-bluetooth")
    with mock.patch.object(windows_hid, "enumerate_hid_collections", return_value=[]), \
         mock.patch.object(auto, "enumerate_serial_displays",
                           return_value=((serial_profile, object()),)), \
         mock.patch.object(auto, "enumerate_bluetooth_displays",
                           return_value=((bt_profile, bt_port()),)) as bluetooth, \
         mock.patch.object(auto, "HimsSerialSink", FakeSink):
        sink = auto.AutoDisplaySink(api=object())
        assert sink.connect() == 20
        assert sink.profile_id == "hims-syncbraille-serial"
        bluetooth.assert_not_called()


def test_bluetooth_is_discovered_when_no_usb_display_is_present():
    bt_profile = profiles.get_profile("humanware-bluetooth")
    with mock.patch.object(windows_hid, "enumerate_hid_collections", return_value=[]), \
         mock.patch.object(auto, "enumerate_serial_displays", return_value=()), \
         mock.patch.object(auto, "enumerate_bluetooth_displays",
                           return_value=((bt_profile, bt_port()),)), \
         mock.patch.object(auto, "HumanWareBluetoothSink", FakeSink):
        sink = auto.AutoDisplaySink(api=object())
        assert sink.connect() == 20
        assert sink.profile_id == "humanware-bluetooth"


def test_bluetooth_discovery_failure_degrades_to_the_no_display_error():
    with mock.patch.object(windows_hid, "enumerate_hid_collections", return_value=[]), \
         mock.patch.object(auto, "enumerate_serial_displays", return_value=()), \
         mock.patch.object(auto, "enumerate_bluetooth_displays",
                           side_effect=RuntimeError("registry unavailable")):
        sink = auto.AutoDisplaySink(api=object())
        try:
            sink.connect()
            raise AssertionError("a failed Bluetooth scan crashed discovery")
        except RuntimeError as error:
            # Action first (the panel speaks only the first sentence), the
            # transport rundown behind it for Connection details.
            message = str(error)
            assert message.startswith("Connect a braille display by USB")
            assert "No compatible display was found" in message


def test_two_paired_bluetooth_families_get_the_unpair_advice():
    """'Select one explicitly' would be a dead end for Bluetooth pairings —
    a family profile pin cannot split two paired units."""
    humanware = profiles.get_profile("humanware-bluetooth")
    hims = profiles.get_profile("hims-bluetooth")
    with mock.patch.object(windows_hid, "enumerate_hid_collections", return_value=[]), \
         mock.patch.object(auto, "enumerate_serial_displays", return_value=()), \
         mock.patch.object(auto, "enumerate_bluetooth_displays", return_value=(
             (humanware, bt_port()),
             (hims, bt_port(name="Braille EDGE 40 111", device="COM6")),
         )), \
         mock.patch.object(auto, "HumanWareBluetoothSink", FakeSink), \
         mock.patch.object(auto, "HimsBluetoothSink", FakeSink):
        sink = auto.AutoDisplaySink(api=object())
        try:
            sink.connect()
            raise AssertionError("two Bluetooth families were accepted")
        except RuntimeError as error:
            assert "unpair the ones not in use" in str(error)


def test_usb_session_never_falls_back_to_bluetooth_in_recovery():
    """Pre-Bluetooth recovery semantics are preserved for USB sessions: a
    USB blip must keep retrying for the USB display, never bind a
    merely-paired Bluetooth unit (its port exists even while it is off)."""
    serial_profile = profiles.get_profile("hims-syncbraille-serial")
    with mock.patch.object(windows_hid, "enumerate_hid_collections", return_value=[]), \
         mock.patch.object(auto, "enumerate_serial_displays",
                           return_value=((serial_profile, object()),)), \
         mock.patch.object(auto, "HimsSerialSink", FakeSink):
        sink = auto.AutoDisplaySink(api=object())
        assert sink.connect() == 20
        assert sink._usb_session is True

    # The USB display blips away; a paired Bluetooth unit is discoverable.
    sink._sink = None  # force the rebuild path (reuse would just reconnect)
    bt_profile = profiles.get_profile("humanware-bluetooth")
    with mock.patch.object(windows_hid, "enumerate_hid_collections", return_value=[]), \
         mock.patch.object(auto, "enumerate_serial_displays", return_value=()), \
         mock.patch.object(auto, "enumerate_bluetooth_displays",
                           return_value=((bt_profile, bt_port()),)) as bluetooth:
        try:
            sink.connect()
            raise AssertionError("a USB session hopped to Bluetooth")
        except RuntimeError as error:
            assert "restart Dotify to switch to Bluetooth" in str(error)
        bluetooth.assert_not_called()


def test_bluetooth_session_may_rediscover_bluetooth():
    bt_profile = profiles.get_profile("humanware-bluetooth")
    with mock.patch.object(windows_hid, "enumerate_hid_collections", return_value=[]), \
         mock.patch.object(auto, "enumerate_serial_displays", return_value=()), \
         mock.patch.object(auto, "enumerate_bluetooth_displays",
                           return_value=((bt_profile, bt_port()),)), \
         mock.patch.object(auto, "HumanWareBluetoothSink", FakeSink):
        sink = auto.AutoDisplaySink(api=object())
        assert sink.connect() == 20
        assert sink._usb_session is False  # Bluetooth sessions keep the fallback tier


def test_custom_bulk_profile_remains_blocked_without_signed_driver():
    sink = auto.AutoDisplaySink(profile_id="braillesense-legacy-usb", api=object())
    try:
        sink.connect()
        raise AssertionError("custom-bulk BrailleSense was claimed as native")
    except RuntimeError as error:
        assert "signed Windows driver" in str(error)


def test_key_listener_forwards_to_detected_sink():
    sink = auto.AutoDisplaySink()

    class Inner:
        def start_key_listener(self, keys_changed):
            self.handler = keys_changed
            return "thread"

    inner = Inner()
    sink._sink = inner
    seen = []
    assert sink.start_key_listener(seen.append) == "thread"
    # The detected sink receives the original callback unchanged.
    inner.handler({"dot1"})
    assert seen == [{"dot1"}]


def test_key_listener_names_unsupported_transport():
    sink = auto.AutoDisplaySink()
    sink._sink = object()
    try:
        sink.start_key_listener(lambda keys: None)
        raise AssertionError("transport without key input was accepted")
    except RuntimeError as error:
        assert "not supported" in str(error)


def test_key_listener_requires_connection():
    sink = auto.AutoDisplaySink()
    try:
        sink.start_key_listener(lambda keys: None)
        raise AssertionError("unconnected sink was accepted")
    except RuntimeError as error:
        assert "not connected" in str(error)


class ReconnectableInner:
    """Inner sink double for the display-recovery reconnect paths."""

    def __init__(self, fail_connects=0):
        self.fail_connects = fail_connects
        self.connects = 0
        self.closed = False
        self.listeners = []

    def connect(self):
        self.connects += 1
        if self.fail_connects > 0:
            self.fail_connects -= 1
            raise OSError(22, "display absent")
        return 20

    def start_key_listener(self, keys_changed):
        self.listeners.append(keys_changed)
        return "thread"

    def close(self):
        self.closed = True


def test_reconnect_reuses_the_inner_sink_and_its_key_listener():
    """Display recovery re-runs connect(); the inner sink must be REUSED so
    its key-listener thread survives; a rebuild would strand the listener
    on the abandoned instance and kill the display keys."""
    sink = auto.AutoDisplaySink(api=object())
    inner = ReconnectableInner()
    sink._sink = inner
    sink.start_key_listener(lambda keys: None)
    attached = inner.listeners[0]        # the filter-wrapped callback

    assert sink.connect() == 20          # recovery path: sink already exists
    assert inner.connects == 1           # same instance reconnected
    assert sink._sink is inner           # not rebuilt
    assert inner.listeners == [attached]  # listener untouched (still running)


def test_reconnect_rebuild_reattaches_the_key_listener():
    """If the reused sink cannot reconnect (display came back as a different
    transport), the rebuilt sink must get the remembered key callback."""
    sink = auto.AutoDisplaySink(profile_id="nls-ereader", api=object())
    old_inner = ReconnectableInner(fail_connects=10)
    sink._sink = old_inner
    sink.start_key_listener(lambda keys: None)
    attached = old_inner.listeners[0]         # the filter-wrapped callback

    new_inner = ReconnectableInner()
    with mock.patch.object(auto, "NativeHidSink", return_value=new_inner):
        assert sink.connect() == 20

    assert old_inner.closed is True           # zombie listener stopped
    assert sink._sink is new_inner            # rebuilt
    assert new_inner.listeners == [attached]  # callback re-attached


def test_reconnect_rebuild_without_prior_listener_attaches_nothing():
    sink = auto.AutoDisplaySink(profile_id="nls-ereader", api=object())
    sink._sink = ReconnectableInner(fail_connects=10)

    new_inner = ReconnectableInner()
    with mock.patch.object(auto, "NativeHidSink", return_value=new_inner):
        assert sink.connect() == 20

    assert new_inner.listeners == []


def test_failed_rebuild_then_recovery_reattaches_the_key_listener():
    """A rebuild whose first connect() fails must not park a listener-less
    sink in self._sink: the next recovery attempt would REUSE it and return
    early — braille resumed but every display key was dead for the session
   ."""
    sink = auto.AutoDisplaySink(profile_id="nls-ereader", api=object())
    old_inner = ReconnectableInner(fail_connects=10)   # reuse keeps failing
    sink._sink = old_inner
    sink.start_key_listener(lambda keys: None)
    attached = old_inner.listeners[0]  # the filter-wrapped callback

    new_inner = ReconnectableInner(fail_connects=1)    # rebuild fails once
    with mock.patch.object(auto, "NativeHidSink", return_value=new_inner):
        try:
            sink.connect()
            raise AssertionError("first recovery attempt should have failed")
        except OSError:
            pass
        assert sink._sink is None      # no half-built sink parked for reuse
        assert sink.connect() == 20    # next recovery attempt succeeds

    assert sink._sink is new_inner
    assert new_inner.listeners == [attached]  # display keys survived


def test_reconnect_rebuild_forgets_keys_held_across_the_outage():
    """Keys down when the old transport died never report release; the
    rebuilt sink must start from a clean chord."""

    class Chords:
        resets = 0

        def keys_changed(self, keys):
            pass

        def reset(self):
            self.resets += 1

    chords = Chords()
    sink = auto.AutoDisplaySink(profile_id="nls-ereader", api=object())
    sink._sink = ReconnectableInner(fail_connects=10)
    sink.start_key_listener(chords.keys_changed)

    with mock.patch.object(auto, "NativeHidSink", return_value=ReconnectableInner()):
        assert sink.connect() == 20

    assert chords.resets == 1
