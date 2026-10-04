"""Idle watchdog: the attention-check state machine that
asks "is anyone still here?" before the shell turns the microphone off,
and wakes it on the first input after."""

from braille_engine.idle_watchdog import (
    IdleWatchdog, STATE_ACTIVE, STATE_PAUSED, STATE_WARNED)


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def make(idle_s=1800.0, warn_s=120.0):
    clock = FakeClock()
    return IdleWatchdog(idle_s=idle_s, warn_s=warn_s, clock=clock), clock


def test_quiet_before_the_idle_window_stays_active():
    dog, clock = make()
    clock.now += 1799.0
    assert dog.poll() is None
    assert dog.state == STATE_ACTIVE


def test_idle_window_elapsing_warns_exactly_once():
    dog, clock = make()
    clock.now += 1800.0
    assert dog.poll() == "warn"
    assert dog.state == STATE_WARNED
    clock.now += 1.0
    assert dog.poll() is None  # edge-triggered, not level


def test_any_touch_during_the_warning_answers_the_check():
    dog, clock = make()
    clock.now += 1800.0
    assert dog.poll() == "warn"
    assert dog.touch() is None  # no resume: nothing was paused
    assert dog.state == STATE_ACTIVE
    clock.now += 1799.0
    assert dog.poll() is None   # the idle clock restarted at the touch


def test_unanswered_warning_pauses_after_the_answer_window():
    dog, clock = make()
    clock.now += 1800.0
    assert dog.poll() == "warn"
    clock.now += 119.0
    assert dog.poll() is None
    clock.now += 1.0
    assert dog.poll() == "pause"
    assert dog.state == STATE_PAUSED
    clock.now += 10000.0
    assert dog.poll() is None   # paused is terminal until a touch


def test_touch_while_paused_reports_resume_and_rearms():
    dog, clock = make()
    clock.now += 1800.0
    dog.poll()
    clock.now += 120.0
    dog.poll()
    assert dog.touch() == "resume"
    assert dog.state == STATE_ACTIVE
    clock.now += 1800.0
    assert dog.poll() == "warn"  # the cycle repeats


def test_steady_activity_never_warns():
    dog, clock = make()
    for _ in range(100):
        clock.now += 900.0
        dog.touch()
        assert dog.poll() is None


def test_touch_activity_relays_resume_through_the_controls_counter():
    # The PacerControls wiring: handle() counts as input; a touch that wakes
    # a watchdog pause bumps the resume relay for the shell.
    from braille_engine.controls import PacerControls

    class FakeEngine:
        paused = False

        def flash(self, text):
            pass

        def fit_cells(self, text):
            return []

    clock = FakeClock()
    controls = PacerControls(FakeEngine(), {"v": 0.1})
    controls.idle_watchdog = IdleWatchdog(clock=clock)
    clock.now += 1800.0
    assert controls.idle_watchdog.poll() == "warn"
    clock.now += 120.0
    assert controls.idle_watchdog.poll() == "pause"
    assert controls.idle_resume_requests == 0
    controls.handle("f")  # any key: answers the pause
    assert controls.idle_resume_requests == 1
    controls.handle("f")  # already active: no second relay
    assert controls.idle_resume_requests == 1


def test_resume_still_relays_while_the_demo_feeder_streams():
    # The demo-feeder gate suppresses only the ATTENTION CHECK (run.py skips
    # poll() while demo_active); the resume side is untouched. A mic the
    # watchdog paused BEFORE the feeder started must still come back on
    # the first touch, feeder streaming or not.
    from braille_engine.controls import PacerControls

    class FakeEngine:
        paused = False

        def flash(self, text):
            pass

        def fit_cells(self, text):
            return []

    class Feeder:
        active = True
        source_label = "demo"

    clock = FakeClock()
    controls = PacerControls(FakeEngine(), {"v": 0.1})
    controls.idle_watchdog = IdleWatchdog(clock=clock)
    clock.now += 1800.0
    assert controls.idle_watchdog.poll() == "warn"
    clock.now += 120.0
    assert controls.idle_watchdog.poll() == "pause"
    controls.demo = Feeder()      # the feeder starts after the pause
    controls.handle("f")          # first touch: the mic must come back
    assert controls.idle_resume_requests == 1
