import unittest
from unittest import mock

import standard_hid_sink as standard_sink
import windows_hid


def collection(path="standard-path", width=20):
    cap = windows_hid.ValueCap(0x20, 0x41, 1, 2, 0x41, 3, 8, width, False)
    return windows_hid.Collection(
        path, 0x1234, 0x5678, 1, 0x41, 1, 12, width + 1, 16,
        "Example", "Standard Display", (cap,)
    )


class FakeApi:
    def __init__(self):
        self.closed = []

    def open_handle(self, path, *, shared, overlapped):
        self.opened = (path, shared, overlapped)
        return 44

    def close_handle(self, handle):
        self.closed.append(handle)


class StandardHidSinkTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeApi()
        self.collection = collection()
        self.sink = standard_sink.StandardHidSink(
            api=self.api,
            warmup_delay=0,
            connect_attempts=1,
            reconnect_attempts=1,
            reconnect_delay=0,
        )
        self.patches = [
            mock.patch.object(
                windows_hid, "enumerate_hid_collections", return_value=[self.collection]
            ),
            mock.patch.object(
                windows_hid, "inspect_open_handle", return_value=self.collection
            ),
            mock.patch.object(windows_hid, "queue_input_read", return_value="pending"),
            mock.patch.object(windows_hid, "cancel_input_read"),
            mock.patch.object(windows_hid, "acquire_preparsed_data", return_value="preparsed"),
            mock.patch.object(windows_hid, "free_preparsed_data"),
            mock.patch.object(
                windows_hid,
                "build_standard_output_reports",
                return_value=(bytes(21),),
            ),
            mock.patch.object(windows_hid, "write_file", return_value=21),
        ]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in reversed(self.patches):
            patch.stop()

    def test_connect_discovers_width_from_value_caps(self):
        self.assertEqual(20, self.sink.connect())
        self.assertEqual(("standard-path", False, True), self.api.opened)

    def test_write_uses_descriptor_built_report_and_writefile(self):
        self.sink.connect()
        cells = list(range(20))
        self.sink.write(cells)
        windows_hid.build_standard_output_reports.assert_called_with(
            self.api, 44, self.collection, cells, preparsed_data="preparsed"
        )
        windows_hid.write_file.assert_called_with(
            self.api, 44, bytes(21), overlapped=True, timeout_ms=2000
        )

    def test_write_reconnects_and_retries(self):
        self.sink.connect()
        windows_hid.write_file.side_effect = [OSError(1167, "gone"), 21]
        self.sink.write([0] * 20)
        self.assertEqual(2, windows_hid.write_file.call_count)

    def test_rejects_multiple_displays(self):
        windows_hid.enumerate_hid_collections.return_value = [
            self.collection,
            collection(path="second"),
        ]
        with self.assertRaisesRegex(RuntimeError, "multiple standard"):
            self.sink.connect()


if __name__ == "__main__":
    unittest.main()
