import pytest

from braille_engine.controls import ADVANCE_TICKER
from run import build_parser


def test_grade_defaults_to_2():
    args = build_parser().parse_args([])
    assert args.grade == 2


def test_window_defaults_to_the_full_display():
    # The paced (auto) mode defaults to full-width page flips —
    # None means "the display's content width, sized at start()"; run.py
    # arms engine.set_window_full() for it. One-cell ticker-tape scrolling
    # stays available as an explicit --window 1.
    args = build_parser().parse_args([])
    assert args.window is None


def test_window_accepts_multi_cell_sizes():
    args = build_parser().parse_args(["--window", "6"])
    assert args.window == 6


def test_advance_mode_defaults_to_auto():
    # Live captions must flow without a flip press, so fresh sessions
    # start paced. At the full-display default window that is discrete
    # page flips, not a ticker-tape crawl.
    args = build_parser().parse_args([])
    assert args.advance_mode == ADVANCE_TICKER


def test_advance_mode_auto_is_the_paced_mode_name():
    args = build_parser().parse_args(["--advance-mode", "auto"])
    assert args.advance_mode == "auto"   # normalized to ticker in main()


def test_advance_mode_accepts_the_internal_ticker_value():
    args = build_parser().parse_args(["--advance-mode", "ticker"])
    assert args.advance_mode == ADVANCE_TICKER


def test_advance_mode_rejects_unknown_values():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--advance-mode", "sideways"])


def test_reading_density_defaults():
    # Full-price boundary cells, capitals kept, numbers as numerals.
    args = build_parser().parse_args([])
    assert args.space_time == 100
    assert args.punct_time == 100
    assert args.lowercase is False
    assert args.no_digits is False


def test_reading_density_flags_parse():
    args = build_parser().parse_args(
        ["--space-time", "25", "--punct-time", "50",
         "--lowercase", "--no-digits"])
    assert args.space_time == 25
    assert args.punct_time == 50
    assert args.lowercase is True
    assert args.no_digits is True


def test_sigterm_and_sighup_quit_cleanly_on_posix(monkeypatch):
    # Default signal death would skip the atexit hook that restores the
    # terminal from raw mode; the handlers turn the signal into a quit.
    import signal
    import threading

    import run

    installed = {}
    monkeypatch.setattr(run.sys, "platform", "linux")
    monkeypatch.setattr(signal, "signal",
                        lambda signum, handler: installed.update(
                            {signum: handler}))
    monkeypatch.setattr(signal, "SIGHUP", 1, raising=False)
    stop = threading.Event()
    run.install_stop_signals(stop)
    assert set(installed) == {signal.SIGTERM, signal.SIGHUP}
    installed[signal.SIGTERM](signal.SIGTERM, None)
    assert stop.is_set()
