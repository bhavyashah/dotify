from types import SimpleNamespace
import unittest

import hid_probe


class ReportTests(unittest.TestCase):
    def test_humanware_twenty_cell_report_is_exactly_output_length(self):
        cells = bytes(range(20))
        report = hid_probe.build_humanware_report(cells)
        self.assertEqual(bytes((0x05, 0x01, 0x00, 0x14)), report[:4])
        self.assertEqual(cells, report[4:])
        self.assertEqual(24, len(report))

    def test_standard_width_comes_from_braille_row_value_cap(self):
        cap = hid_probe.ValueCap(
            report_id=0x37,
            usage_page=0x41,
            link_collection=1,
            link_usage=0x02,
            link_usage_page=0x41,
            usage=0x03,
            bit_size=8,
            report_count=32,
            is_range=False,
        )
        self.assertEqual(
            32, hid_probe.standard_braille_width(SimpleNamespace(output_value_caps=(cap,)))
        )

    def test_value_caps_ctypes_layout_matches_windows_hidpi(self):
        self.assertEqual(72, hid_probe.ctypes.sizeof(hid_probe.HIDP_VALUE_CAPS))

    def test_patterns_have_requested_width(self):
        for pattern in ("blank", "full", "alternating", "walking"):
            with self.subTest(pattern=pattern):
                self.assertEqual(20, len(hid_probe.make_cells(pattern, 20)))

    def test_alternating_pattern_starts_with_full_cell(self):
        self.assertEqual(bytes((0xFF, 0x00, 0xFF, 0x00)), hid_probe.make_cells("alternating", 4))

    def test_invalid_width_is_rejected(self):
        with self.assertRaises(ValueError):
            hid_probe.make_cells("blank", 0)


class PressedKeyParsingTests(unittest.TestCase):
    def test_non_key_report_returns_none(self):
        self.assertIsNone(hid_probe.parse_humanware_pressed_keys(b"\x05\x01\x00"))
        self.assertIsNone(hid_probe.parse_humanware_pressed_keys(b""))
        self.assertIsNone(hid_probe.parse_humanware_pressed_keys(b"\x07\x00"))

    def test_space_dot1_chord(self):
        report = bytes((0x04, 10, 2, 0, 0, 0))
        self.assertEqual(
            frozenset({"space", "dot1"}),
            hid_probe.parse_humanware_pressed_keys(report),
        )

    def test_empty_key_report_is_all_released(self):
        self.assertEqual(
            frozenset(),
            hid_probe.parse_humanware_pressed_keys(bytes((0x04, 0, 0, 0))),
        )

    def test_thumb_keys_map_to_roles(self):
        report = bytes((0x04, 17, 18, 19, 20))
        self.assertEqual(
            frozenset(
                {"thumb_previous", "thumb_left", "thumb_right", "thumb_next"}
            ),
            hid_probe.parse_humanware_pressed_keys(report),
        )

    def test_routing_keys_are_zero_indexed(self):
        report = bytes((0x04, 80, 99, 0))
        self.assertEqual(
            frozenset({"routing:0", "routing:19"}),
            hid_probe.parse_humanware_pressed_keys(report),
        )

    def test_unknown_key_codes_survive_for_discovery(self):
        report = bytes((0x04, 42, 0))
        self.assertEqual(
            frozenset({"key42"}),
            hid_probe.parse_humanware_pressed_keys(report),
        )


class FakeKernel:
    def __init__(self, wait_result):
        self.wait_result = wait_result
        self.cancelled = []

    def CancelIoEx(self, handle, operation):
        self.cancelled.append(handle)

    def WaitForSingleObject(self, event, timeout_ms):
        return self.wait_result


class FakeApi:
    def __init__(self, wait_result=0):
        self.kernel32 = FakeKernel(wait_result)
        self.closed = []

    def close_handle(self, handle):
        self.closed.append(handle)


class CancelInputReadTests(unittest.TestCase):
    def pending(self):
        return hid_probe.PendingRead(
            buffer=bytearray(8), operation=hid_probe.OVERLAPPED(), event=77)

    def test_completed_cancel_releases_the_read(self):
        api = FakeApi(wait_result=0)
        hid_probe.cancel_input_read(api, 5, self.pending())
        self.assertEqual([5], api.kernel32.cancelled)
        self.assertEqual([77], api.closed)

    def test_a_cancel_that_never_completes_keeps_the_read_alive(self):
        # The kernel may still write into the OVERLAPPED and buffer; freeing
        # them would let that completion land in released memory.
        api = FakeApi(wait_result=hid_probe.WAIT_TIMEOUT)
        pending = self.pending()
        hid_probe.cancel_input_read(api, 5, pending)
        self.assertEqual([], api.closed)
        self.assertIn(pending, hid_probe._abandoned_reads)
        hid_probe._abandoned_reads.remove(pending)


class KnownDisplayEnumerationTests(unittest.TestCase):
    def test_other_usb_devices_are_skipped_without_being_opened(self):
        nls = hid_probe.profiles.get_profile("nls-ereader")
        ours = rf"\\?\hid#vid_{nls.vid:04x}&pid_{nls.pid:04x}&mi_00#7&1&0000#{{x}}"
        paths = [
            r"\\?\hid#vid_046d&pid_c52b&mi_00#7&2&0000#{x}",  # a mouse
            ours.upper(),
            r"\\?\hid#{00001124-0000-1000-8000-00805f9b34fb}_dev#{x}",
        ]
        inspected = []

        def inspect(api, path):
            inspected.append(path)
            if path == ours.upper():
                return SimpleNamespace(vid=nls.vid, pid=nls.pid, usage_page=0x93,
                                       path=path)
            return None

        original = (hid_probe.enumerate_paths, hid_probe.inspect_path)
        hid_probe.enumerate_paths = lambda api: paths
        hid_probe.inspect_path = inspect
        try:
            found = hid_probe.enumerate_known_displays(object())
        finally:
            hid_probe.enumerate_paths, hid_probe.inspect_path = original
        self.assertEqual([ours.upper()], [item.path for item in found])
        self.assertEqual([ours.upper(), paths[2]], inspected)


if __name__ == "__main__":
    unittest.main()
