import unittest
from unittest import mock

import display_profiles as profiles
import hims_hid_sink as hims_sink
import windows_hid


def collection(output_length=41):
    profile = profiles.get_profile("hims-braille-edge-3s")
    return windows_hid.Collection(
        "hims-path", profile.vid, profile.pid, 1, 0xFF00, 1,
        12, output_length, 16, "HIMS", profile.name,
    )


class FakeApi:
    def __init__(self):
        self.closed = []

    def open_handle(self, path, *, shared, overlapped):
        self.opened = (path, shared, overlapped)
        return 88

    def close_handle(self, handle):
        self.closed.append(handle)


class HimsHidSinkTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeApi()
        self.collection = collection()
        self.sink = hims_sink.HimsHidSink(
            api=self.api, settle_seconds=0, warmup_delay=0,
            connect_attempts=1, reconnect_attempts=1, reconnect_delay=0,
        )
        caps = bytearray(16)
        caps[9] = 40
        self.patches = [
            mock.patch.object(windows_hid, "enumerate_known_displays", return_value=[self.collection]),
            mock.patch.object(windows_hid, "inspect_open_handle", return_value=self.collection),
            mock.patch.object(windows_hid, "queue_input_read", return_value="pending"),
            mock.patch.object(windows_hid, "cancel_input_read"),
            mock.patch.object(windows_hid, "get_feature", return_value=bytes(caps)),
            mock.patch.object(windows_hid, "set_output_report"),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()

    def test_connect_reads_descriptor_cell_count(self):
        self.assertEqual(40, self.sink.connect())
        self.assertEqual("HIMS Braille Edge 3S", self.sink.display_name)
        self.assertEqual(("hims-path", False, True), self.api.opened)
        windows_hid.get_feature.assert_called_with(self.api, 88, 16, 1)

    def test_write_uses_hims_hid_length_report(self):
        self.sink.connect()
        cells = list(range(40))
        self.sink.write(cells)
        windows_hid.set_output_report.assert_called_with(
            self.api, 88, bytes((40,)) + bytes(cells)
        )

    def test_rejects_descriptor_width_mismatch(self):
        bad = collection(output_length=40)
        windows_hid.enumerate_known_displays.return_value = [bad]
        windows_hid.inspect_open_handle.return_value = bad
        with self.assertRaisesRegex(RuntimeError, "output length"):
            self.sink.connect()


if __name__ == "__main__":
    unittest.main()
