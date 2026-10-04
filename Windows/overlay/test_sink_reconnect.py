from unittest import mock

import pytest

import sink_reconnect
from sink_reconnect import reconnect_and_resend


class Link:
    """A display that comes back on the attempt-th reconnect."""

    def __init__(self, back_on=None, width=40):
        self.back_on = back_on
        self.width = width
        self.connects = 0
        self.resent = 0
        self.closes = 0

    def connect(self):
        self.connects += 1
        if self.back_on is None or self.connects < self.back_on:
            raise OSError("device not present")
        return self.width

    def resend(self):
        self.resent += 1

    def close(self):
        self.closes += 1


def run(link, attempts=2, expected_width=40):
    with mock.patch.object(sink_reconnect.time, "sleep") as sleep:
        reconnect_and_resend(
            "test display", OSError("write failed"),
            attempts=attempts, delay=0.5, connect=link.connect,
            resend=link.resend, close=link.close,
            expected_width=expected_width, errors=(OSError, RuntimeError))
    return [call.args[0] for call in sleep.call_args_list]


def test_resends_once_the_display_is_back():
    link = Link(back_on=2)
    assert run(link) == [0.5, 1.0]
    assert (link.connects, link.resent, link.closes) == (2, 1, 1)


def test_gives_up_after_the_attempts_with_the_last_error():
    link = Link()
    with pytest.raises(RuntimeError) as raised:
        run(link, attempts=3)
    assert link.connects == 3 and link.resent == 0
    assert "test display could not reconnect after 3 attempts" in str(raised.value)
    assert isinstance(raised.value.__cause__, OSError)


def test_a_different_width_is_a_failed_attempt():
    link = Link(back_on=1, width=20)
    with pytest.raises(RuntimeError, match="width changed from 40 to 20"):
        run(link, attempts=1)
    assert link.resent == 0 and link.closes == 1


def test_backoff_is_capped():
    link = Link()
    with pytest.raises(RuntimeError):
        sleeps = None
        with mock.patch.object(sink_reconnect.time, "sleep") as sleep:
            try:
                reconnect_and_resend(
                    "test display", OSError("x"), attempts=5, delay=1.0,
                    connect=link.connect, resend=link.resend,
                    close=link.close, expected_width=40, errors=(OSError,),
                    max_delay=3.0)
            finally:
                sleeps = [call.args[0] for call in sleep.call_args_list]
    assert sleeps == [1.0, 2.0, 3.0, 3.0, 3.0]


def test_in_write_retries_stay_short_by_default():
    # They run under the engine lock; long outages belong to recover_display.
    assert sink_reconnect.DEFAULT_RECONNECT_ATTEMPTS == 2


class Chords:
    def __init__(self):
        self.resets = 0

    def keys_changed(self, keys):
        pass

    def reset(self):
        self.resets += 1


def test_forget_held_chord_resets_the_chord_layer():
    chords = Chords()
    sink_reconnect.forget_held_chord(chords.keys_changed)
    assert chords.resets == 1


def test_forget_held_chord_ignores_plain_callbacks():
    sink_reconnect.forget_held_chord(lambda keys: None)
