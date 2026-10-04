import threading
import time
import unittest

import display_cold_watch as cold_watch


def wait_until(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


class FakeSink:
    def __init__(self):
        self.connected = False
        self.device_identity = None
        self.drop_calls = 0
        self.connect_calls = 0
        self.connect_results = []   # widths or exceptions, consumed in order
        self.writes = []
        self.write_error = None

    def drop_cold(self):
        self.drop_calls += 1
        self.connected = False
        self.device_identity = None

    def connect(self):
        self.connect_calls += 1
        if self.connect_results:
            result = self.connect_results.pop(0)
        else:
            result = RuntimeError("no display")
        if isinstance(result, Exception):
            raise result
        self.connected = True
        return result

    def write(self, cells):
        if self.write_error is not None:
            raise self.write_error
        self.writes.append(cells)


class FakeEngine:
    def __init__(self, sink):
        self.sink = sink
        self.display_wait_reason = None
        self.width = 20

    def frame(self):
        return [0x2A] * self.width


class DisplayColdWatchTest(unittest.TestCase):
    def setUp(self):
        self.sink = FakeSink()
        self.engine = FakeEngine(self.sink)
        self.stop_event = threading.Event()
        self.announced = []
        self.ports = ["COM3", "COM7"]
        self.hid_paths = []
        self.watch = cold_watch.DisplayColdWatch(
            self.engine, self.stop_event,
            announce=self.announced.append,
            com_ports_provider=lambda: list(self.ports),
            hid_paths_provider=lambda: list(self.hid_paths),
            initial_delay=0.02, max_delay=0.2, arrival_grace=0.0,
            tick=0.02,
        ).start()

    def tearDown(self):
        self.watch.stop()

    def connect_sink(self, identity=("hid", "path-a")):
        self.sink.connected = True
        self.sink.device_identity = identity
        if identity and identity[0] == "hid":
            self.hid_paths = [identity[1]]
        elif identity:
            if identity[1] not in self.ports:
                self.ports.append(identity[1])

    # -- removal matching ------------------------------------------------

    def test_hid_removal_of_session_device_drops_announces_and_redials(self):
        self.connect_sink(("hid", "path-a"))
        self.hid_paths = []              # the device really left
        self.sink.connect_results = [RuntimeError("still gone"), 20]
        self.watch.on_device_removed("hid", "PATH-A")  # case-insensitive
        self.assertTrue(wait_until(lambda: self.sink.drop_calls == 1))
        self.assertEqual(cold_watch.DROP_REASON,
                         self.engine.display_wait_reason)
        self.assertTrue(wait_until(lambda: self.sink.connected))
        self.assertTrue(wait_until(
            lambda: self.engine.display_wait_reason is None))
        self.assertEqual([self.engine.frame()], self.sink.writes)
        self.assertTrue(wait_until(lambda: self.announced))
        self.assertGreaterEqual(self.sink.connect_calls, 2)

    def test_other_units_removal_never_touches_a_healthy_link(self):
        self.connect_sink(("hid", "path-a"))
        self.watch.on_device_removed("hid", "path-b")
        self.watch.on_device_removed("com", "")
        time.sleep(0.08)
        self.assertEqual(0, self.sink.drop_calls)
        self.assertTrue(self.sink.connected)
        self.assertIsNone(self.engine.display_wait_reason)

    def test_stale_hid_removal_is_ignored_when_device_still_enumerates(self):
        # A fast unplug/replug reconnects on the SAME path before the pump
        # drains its queue: the stale removal must not close the healthy
        # link (the in-write retries already won it back).
        self.connect_sink(("hid", "path-a"))
        self.watch.on_device_removed("hid", "path-a")
        time.sleep(0.08)
        self.assertEqual(0, self.sink.drop_calls)
        self.assertTrue(self.sink.connected)
        self.assertIsNone(self.engine.display_wait_reason)

    def test_com_removal_checks_port_presence(self):
        self.connect_sink(("com", "COM3"))
        # Another COM device left; ours still enumerates: no drop.
        self.watch.on_device_removed("com", "irrelevant")
        time.sleep(0.08)
        self.assertEqual(0, self.sink.drop_calls)
        # Now our port is gone.
        self.ports = ["COM7"]
        self.sink.connect_results = [20]
        self.watch.on_device_removed("com", "irrelevant")
        self.assertTrue(wait_until(lambda: self.sink.drop_calls == 1))
        self.assertTrue(wait_until(lambda: self.sink.connected))

    def test_presence_check_failure_counts_as_still_present(self):
        self.connect_sink(("com", "COM3"))
        self.watch._com_ports_provider = lambda: (_ for _ in ()).throw(
            OSError("registry unavailable"))
        self.watch.on_device_removed("com", "irrelevant")
        time.sleep(0.08)
        self.assertEqual(0, self.sink.drop_calls)
        self.assertTrue(self.sink.connected)

    def test_disconnected_sink_ignores_removals(self):
        self.watch.on_device_removed("hid", "path-a")
        time.sleep(0.06)
        self.assertEqual(0, self.sink.drop_calls)
        self.assertIsNone(self.engine.display_wait_reason)

    # -- one-dialer rule and startup ------------------------------------

    def test_stands_down_while_an_engine_dialer_owns_the_dial(self):
        self.connect_sink(("hid", "path-a"))
        self.hid_paths = []
        self.engine.display_wait_reason = "native HID connection failed"
        self.watch.on_device_removed("hid", "path-a")
        time.sleep(0.08)
        self.assertEqual(0, self.sink.drop_calls)
        self.assertEqual(0, self.sink.connect_calls)

    def test_never_dials_before_the_engine_first_starts(self):
        # start_controls runs BEFORE wait_for_display: engine.width is None
        # and engine.frame() does not exist yet — the initial claim belongs
        # to wait_for_display, and the watch must not touch the engine.
        self.engine.width = None
        self.sink.connected = False
        self.watch.on_device_arrived("hid", "path-a")
        time.sleep(0.08)
        self.assertEqual(0, self.sink.connect_calls)
        self.assertIsNone(self.engine.display_wait_reason)

    def test_engine_win_closes_the_outage_and_clears_stale_reason(self):
        self.connect_sink(("hid", "path-a"))
        self.hid_paths = []
        self.watch.on_device_removed("hid", "path-a")
        self.assertTrue(wait_until(
            lambda: self.engine.display_wait_reason
            == cold_watch.DROP_REASON))
        # recover_display wins the display back before the watch does.
        self.sink.connected = True
        self.assertTrue(wait_until(
            lambda: self.engine.display_wait_reason is None))
        self.assertEqual([], self.sink.writes)   # their recovery, not ours
        self.assertEqual([], self.announced)

    # -- arrivals --------------------------------------------------------

    def test_arrival_rescues_a_dialer_less_state(self):
        # Sink down, no outage open, engine idle: nobody is dialing.
        self.sink.connected = False
        self.sink.connect_results = [20]
        self.watch.on_device_arrived("hid", "path-a")
        self.assertTrue(wait_until(lambda: self.sink.connected))
        self.assertEqual([self.engine.frame()], self.sink.writes)

    def test_arrival_rescues_a_bound_sink_whose_device_left_unnoticed(self):
        # Unplug during system sleep: no removal broadcast was delivered,
        # the sink still holds a dead handle. The replug arrival must
        # verify the bound device and treat the absence as a cold drop.
        self.connect_sink(("hid", "path-a"))
        self.hid_paths = []              # bound device is NOT present
        self.sink.connect_results = [20]
        self.watch.on_device_arrived("hid", "path-b")
        self.assertTrue(wait_until(lambda: self.sink.drop_calls == 1))
        self.assertTrue(wait_until(
            lambda: self.sink.connect_calls >= 1 and self.sink.connected))

    def test_arrival_defers_to_an_engine_dialer(self):
        self.sink.connected = False
        self.engine.display_wait_reason = "still connecting"
        self.watch.on_device_arrived("hid", "path-a")
        time.sleep(0.08)
        self.assertEqual(0, self.sink.connect_calls)

    def test_lost_units_arrival_short_circuits_the_ladder(self):
        self.connect_sink(("hid", "path-a"))
        self.hid_paths = []
        self.watch.on_device_removed("hid", "path-a")
        self.assertTrue(wait_until(lambda: self.sink.connect_calls >= 1))
        calls = self.sink.connect_calls
        self.sink.connect_results = [20]
        self.watch.on_device_arrived("hid", "path-a")  # OUR path is back
        self.assertTrue(wait_until(
            lambda: self.sink.connect_calls > calls and self.sink.connected))

    def test_unknown_arrivals_do_not_reset_the_ladder(self):
        # A flapping unrelated device must not defeat the backoff: the
        # rung survives unknown arrivals (each may pull one dial in, but
        # never resets _delay to the initial rung).
        self.connect_sink(("hid", "path-a"))
        self.hid_paths = []
        self.watch.on_device_removed("hid", "path-a")
        self.assertTrue(wait_until(
            lambda: self.watch._delay >= 0.08))  # ladder has grown
        self.watch.on_device_arrived("hid", "some-other-device")
        time.sleep(0.05)
        self.assertGreaterEqual(self.watch._delay, 0.08)

    # -- width refusal ---------------------------------------------------

    def test_wrong_width_is_refused_honestly_and_torn_back_down(self):
        self.connect_sink(("hid", "path-a"))
        self.hid_paths = []
        self.sink.connect_results = [32]
        self.watch.on_device_removed("hid", "path-a")
        self.assertTrue(wait_until(lambda: self.sink.drop_calls >= 2))
        self.assertFalse(self.sink.connected)
        self.assertIn("32 cells", self.engine.display_wait_reason)
        self.assertIn("sized for 20", self.engine.display_wait_reason)
        # No blind re-ladder: without a fresh arrival, no more dials.
        calls = self.sink.connect_calls
        time.sleep(0.08)
        self.assertEqual(calls, self.sink.connect_calls)
        # The right unit returns: refusal lifts, session resumes.
        self.sink.connect_results = [20]
        self.watch.on_device_arrived("hid", "path-c")
        self.assertTrue(wait_until(lambda: self.sink.connected))
        self.assertTrue(wait_until(
            lambda: self.engine.display_wait_reason is None))

    # -- half-dead transports and internal robustness --------------------

    def test_failed_repaint_tears_the_sink_back_down_and_keeps_dialing(self):
        # connect() can succeed while the transport is unusable (a port
        # that opens with the unit detached). The outage must NOT close
        # over the half-dead sink — tear down and keep the ladder alive.
        self.connect_sink(("hid", "path-a"))
        self.hid_paths = []
        self.sink.connect_results = [20, 20]
        self.sink.write_error = OSError("transport dead")
        self.watch.on_device_removed("hid", "path-a")
        self.assertTrue(wait_until(lambda: self.sink.drop_calls >= 2))
        self.assertEqual(cold_watch.DROP_REASON,
                         self.engine.display_wait_reason)
        # The transport comes back for real: recovery completes.
        self.sink.write_error = None
        self.assertTrue(wait_until(
            lambda: self.sink.connected and self.sink.writes, timeout=5.0))
        self.assertTrue(wait_until(
            lambda: self.engine.display_wait_reason is None))

    def test_internal_errors_never_kill_the_watch_thread(self):
        self.connect_sink(("hid", "path-a"))
        self.hid_paths = []
        # frame() raising a surprise type must not end the watch.
        self.engine.frame = lambda: (_ for _ in ()).throw(
            TypeError("engine not ready"))
        self.sink.connect_results = [20, 20]
        self.watch.on_device_removed("hid", "path-a")
        self.assertTrue(wait_until(lambda: self.sink.connect_calls >= 1))
        self.assertTrue(self.watch._thread.is_alive())
        # Heal the engine: the still-alive watch finishes the recovery.
        del self.engine.frame
        self.assertTrue(wait_until(lambda: self.sink.connected, timeout=5.0))

    def test_keeps_dialing_until_the_display_is_back(self):
        self.connect_sink(("hid", "path-a"))
        self.hid_paths = []
        self.sink.connect_results = [
            RuntimeError("gone"), OSError("gone"), RuntimeError("gone"), 20]
        self.watch.on_device_removed("hid", "path-a")
        self.assertTrue(wait_until(lambda: self.sink.drop_calls == 1))
        self.assertTrue(wait_until(lambda: self.sink.connected, timeout=5.0))
        self.assertEqual(4, self.sink.connect_calls)
        self.assertTrue(wait_until(
            lambda: self.engine.display_wait_reason is None))

    # -- shutdown --------------------------------------------------------

    def test_events_after_stop_touch_nothing(self):
        self.connect_sink(("hid", "path-a"))
        self.hid_paths = []
        self.watch.stop()
        self.watch.on_device_removed("hid", "path-a")
        time.sleep(0.06)
        self.assertEqual(0, self.sink.drop_calls)
        self.assertIsNone(self.engine.display_wait_reason)

    def test_events_after_stop_event_touch_nothing(self):
        self.connect_sink(("hid", "path-a"))
        self.hid_paths = []
        self.stop_event.set()
        self.watch.on_device_removed("hid", "path-a")
        time.sleep(0.06)
        self.assertEqual(0, self.sink.drop_calls)
        self.assertIsNone(self.engine.display_wait_reason)


if __name__ == "__main__":
    unittest.main()
