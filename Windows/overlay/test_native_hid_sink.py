import unittest
from unittest import mock

import display_profiles
import native_hid_sink
import windows_hid


def collection(profile_id="nls-ereader", *, path="test-path", usage_page=None, width=20):
    profile = display_profiles.get_profile(profile_id)
    if usage_page is None:
        usage_page = profile.usage_pages[0] if profile.usage_pages else 0xFF00
    return windows_hid.Collection(
        path=path,
        vid=profile.vid,
        pid=profile.pid,
        version=1,
        usage_page=usage_page,
        usage=1,
        input_length=46,
        output_length=width + 4,
        feature_length=39,
        manufacturer="HumanWare",
        product=profile.name,
    )


class FakeApi:
    def __init__(self):
        self.closed = []
        self.open_args = []

    def open_handle(self, path, *, shared, overlapped):
        self.open_args.append((path, shared, overlapped))
        return 42 + len(self.open_args)

    def close_handle(self, handle):
        self.closed.append(handle)


class NativeHidSinkTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeApi()
        self.nls = collection()
        self.sink = native_hid_sink.NativeHidSink(
            api=self.api,
            settle_seconds=0,
            warmup_delay=0,
            connect_attempts=1,
            reconnect_attempts=1,
            reconnect_delay=0,
        )
        self.patches = [
            mock.patch.object(
                windows_hid, "enumerate_known_displays", return_value=[self.nls]
            ),
            mock.patch.object(windows_hid, "inspect_open_handle", return_value=self.nls),
            mock.patch.object(windows_hid, "queue_input_read", return_value="pending"),
            mock.patch.object(windows_hid, "cancel_input_read"),
            mock.patch.object(windows_hid, "_read_humanware_cell_count", return_value=20),
            mock.patch.object(windows_hid, "set_output_report"),
        ]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in reversed(self.patches):
            patch.stop()

    def test_connect_auto_detects_profile_and_uses_exclusive_overlapped_handle(self):
        self.assertEqual(20, self.sink.connect())
        self.assertEqual("nls-ereader", self.sink.profile_id)
        self.assertEqual("HumanWare NLS eReader", self.sink.display_name)
        self.assertEqual(("test-path", False, True), self.api.open_args[-1])
        self.assertEqual(2, windows_hid.enumerate_known_displays.call_count)

    def test_explicit_brailliant_profile_uses_same_descriptor_driven_protocol(self):
        bi20x = collection("brailliant-bi-20x", path="bi20x-path")
        windows_hid.enumerate_known_displays.return_value = [bi20x]
        windows_hid.inspect_open_handle.return_value = bi20x
        selected = native_hid_sink.NativeHidSink(
            api=self.api,
            profile_id="brailliant-bi-20x",
            settle_seconds=0,
            warmup_delay=0,
            connect_attempts=1,
        )
        self.assertEqual(20, selected.connect())
        self.assertEqual("brailliant-bi-20x", selected.profile_id)

    def test_touch_descriptor_fallback_accepts_nonstandard_usage_page(self):
        touch = collection("braillenote-touch", path="touch-path", usage_page=0xFF00, width=32)
        windows_hid.enumerate_known_displays.return_value = [touch]
        windows_hid.inspect_open_handle.return_value = touch
        windows_hid._read_humanware_cell_count.return_value = 32
        selected = native_hid_sink.NativeHidSink(
            api=self.api,
            profile_id="braillenote-touch",
            settle_seconds=0,
            warmup_delay=0,
            connect_attempts=1,
        )
        self.assertEqual(32, selected.connect())

    def test_auto_detect_rejects_multiple_supported_displays(self):
        windows_hid.enumerate_known_displays.return_value = [
            self.nls,
            collection("brailliant-bi-20x", path="second-path"),
        ]
        with self.assertRaisesRegex(RuntimeError, "multiple supported displays"):
            self.sink.connect()

    def test_discovery_only_braillesense_cannot_be_selected_as_humanware_hid(self):
        selected = native_hid_sink.NativeHidSink(
            api=self.api,
            profile_id="braillesense-legacy-usb",
            settle_seconds=0,
            warmup_delay=0,
            connect_attempts=1,
        )
        with self.assertRaisesRegex(RuntimeError, "discovery-only"):
            selected.connect()

    def test_write_frames_humanware_report(self):
        self.sink.connect()
        cells = list(range(20))
        self.sink.write(cells)
        report = windows_hid.set_output_report.call_args.args[2]
        self.assertEqual(bytes((0x05, 0x01, 0x00, 0x14)), report[:4])
        self.assertEqual(bytes(cells), report[4:])

    def test_write_reconnects_to_same_profile_and_retries_same_frame(self):
        self.sink.connect()
        failure = OSError(1167, "device not connected")
        windows_hid.set_output_report.side_effect = [failure, None]
        cells = list(range(20))

        self.sink.write(cells)

        self.assertEqual(2, windows_hid.set_output_report.call_count)
        reports = [call.args[2] for call in windows_hid.set_output_report.call_args_list]
        self.assertEqual(reports[0], reports[1])
        self.assertEqual("nls-ereader", self.sink.profile_id)
        self.assertEqual(4, windows_hid.enumerate_known_displays.call_count)

    def test_reconnect_rejects_width_change(self):
        self.sink.connect()
        wider = collection(width=32)
        windows_hid.enumerate_known_displays.return_value = [wider]
        windows_hid.inspect_open_handle.return_value = wider
        windows_hid._read_humanware_cell_count.return_value = 32
        windows_hid.set_output_report.side_effect = OSError(1167, "gone")
        with self.assertRaisesRegex(RuntimeError, "width changed from 20 to 32"):
            self.sink.write([0] * 20)

    def test_access_denied_names_competing_screen_readers(self):
        # The process scan is patched so the result doesn't depend on what
        # runs on the test machine.
        self.api.open_handle = mock.Mock(side_effect=OSError(5, "access denied"))
        with mock.patch.object(native_hid_sink, "_process_listing",
                               lambda: ""):
            with self.assertRaisesRegex(RuntimeError,
                                        "Close NVDA, JAWS, BRLTTY"):
                self.sink.connect()

    def test_access_denied_names_the_running_screen_reader(self):
        self.api.open_handle = mock.Mock(side_effect=OSError(5, "access denied"))
        with mock.patch.object(native_hid_sink, "_process_listing",
                               lambda: '"nvda.exe","4242","console"'):
            with self.assertRaisesRegex(
                    RuntimeError,
                    "NVDA appears to be using the braille display"):
                self.sink.connect()

    def test_write_rejects_wrong_width(self):
        self.sink.connect()
        with self.assertRaises(ValueError):
            self.sink.write([0] * 19)

    def test_close_cancels_read_and_clears_identity(self):
        self.sink.connect()
        handle = self.sink._handle
        self.sink.close()
        windows_hid.cancel_input_read.assert_called_with(self.api, handle, "pending")
        self.assertIn(handle, self.api.closed)
        self.assertIsNone(self.sink.profile_id)


class _ListenerKernel32:
    """One signaled read, then timeouts forever."""

    _WAIT_TIMEOUT = 0x0102

    def __init__(self, report_length):
        self.report_length = report_length
        self.waits = 0

    def WaitForSingleObject(self, event, timeout_ms):
        self.waits += 1
        return 0 if self.waits == 1 else self._WAIT_TIMEOUT

    def GetOverlappedResult(self, handle, operation_ref, transferred_ref, wait):
        transferred_ref._obj.value = self.report_length
        return 1


class KeyListenerTests(unittest.TestCase):
    """Same connect scaffolding as NativeHidSinkTests; exercises the
    input-report listener loop."""

    setUp = NativeHidSinkTests.setUp
    tearDown = NativeHidSinkTests.tearDown

    def _armed_pending(self, report: bytes):
        import ctypes

        return windows_hid.PendingRead(
            buffer=ctypes.create_string_buffer(bytes(report), 46),
            operation=windows_hid.OVERLAPPED(),
            event=111,
        )

    def test_key_report_reaches_handler_as_symbolic_set(self):
        self.sink.connect()
        report = bytes((0x04, 10, 2))          # space + dot1
        self.api.kernel32 = _ListenerKernel32(len(report))
        self.sink._pending = self._armed_pending(report)
        # Re-arm fails benignly; the loop parks on pending=None until stop.
        windows_hid.queue_input_read.side_effect = OSError(5, "gone")

        received = []

        def handler(keys):
            received.append(keys)
            self.sink._keys_stop.set()

        thread = self.sink.start_key_listener(handler)
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertEqual([frozenset({"space", "dot1"})], received)

    def test_listener_requires_connection_and_single_start(self):
        with self.assertRaisesRegex(RuntimeError, "not connected"):
            self.sink.start_key_listener(lambda keys: None)
        self.sink.connect()
        self.api.kernel32 = _ListenerKernel32(0)
        self.sink._pending = None              # park immediately
        windows_hid.queue_input_read.side_effect = OSError(5, "gone")
        self.sink.start_key_listener(lambda keys: None)
        try:
            with self.assertRaisesRegex(RuntimeError, "already running"):
                self.sink.start_key_listener(lambda keys: None)
        finally:
            self.sink._keys_stop.set()


class OwnedDisplayHelpTests(unittest.TestCase):
    """Culprit naming for a startup collision: the owned-
    display help names the running screen reader when it can, with a
    product-specific fix, and degrades to the generic advice otherwise."""

    def _help(self, listing, error=None):
        if error is None:
            error = OSError(5, "access denied")
        with mock.patch.object(native_hid_sink, "_process_listing",
                               lambda: listing):
            return native_hid_sink.NativeHidSink._help_for_error(error)

    def test_names_nvda_with_the_no_braille_fix(self):
        help_text = self._help('"nvda.exe","4242","Console","1","250,000 K"')
        self.assertIn("NVDA appears to be using the braille display",
                      help_text)
        self.assertIn("no braille", help_text)
        self.assertIn("connect automatically", help_text)
        # Heuristic honesty: NVDA is named as the likely culprit, not proven.
        self.assertNotIn("Close NVDA, JAWS", help_text)

    def test_names_jaws_when_jaws_runs_instead(self):
        help_text = self._help('"jfw.exe","99","Console"')
        self.assertIn("JAWS appears to be using", help_text)
        self.assertNotIn("NVDA appears", help_text)

    def test_generic_when_no_known_reader_is_running(self):
        help_text = self._help('"chrome.exe","1","Console"')
        self.assertIn("Another program owns the display", help_text)
        self.assertIn("connect automatically", help_text)
        self.assertNotIn("appears to be using", help_text)

    def test_generic_when_the_process_scan_fails(self):
        help_text = self._help("")
        self.assertIn("Another program owns the display", help_text)

    def test_other_errors_keep_their_existing_help(self):
        with mock.patch.object(
                native_hid_sink, "_process_listing",
                lambda: self.fail("scan must not run for other errors")):
            self.assertEqual(
                native_hid_sink.NativeHidSink._help_for_error(None), "")
            self.assertIn(
                "usbipd",
                native_hid_sink.NativeHidSink._help_for_error(
                    OSError(1167, "device not connected")))


if __name__ == "__main__":
    unittest.main()
