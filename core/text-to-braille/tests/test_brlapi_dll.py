"""ctypes BrlAPI client (Windows fallback) + BrlapiSink fallback wiring."""

import sys

import pytest

from braille_engine.sinks.brlapi_dll import DllConnection
from braille_engine.sinks.brlapi_sink import BrlapiSink


class FakeBrlapiLib:
    def __init__(self, fd=3):
        self.fd = fd
        self.calls = []

    def brlapi_openConnection(self, settings, used):
        self.calls.append("open")
        return self.fd

    def brlapi_getDisplaySize(self, x, y):
        x._obj.value = 20
        y._obj.value = 1
        return 0

    def brlapi_enterTtyModeWithPath(self, arr, n, driver):
        self.calls.append(("tty", n))
        return 0

    def brlapi_writeDots(self, dots):
        self.calls.append(("dots", bytes(dots)))
        return 0

    def brlapi_leaveTtyMode(self):
        self.calls.append("leave")
        return 0

    def brlapi_closeConnection(self):
        self.calls.append("close")
        return 0


def test_open_failure_raises():
    with pytest.raises(RuntimeError):
        DllConnection(lib=FakeBrlapiLib(fd=-1))


def test_display_size_and_global_tty():
    lib = FakeBrlapiLib()
    c = DllConnection(lib=lib)
    assert c.displaySize == (20, 1)
    c.enterTtyModeWithPath([])
    assert ("tty", 0) in lib.calls


def test_write_dots_passes_bytes_through():
    lib = FakeBrlapiLib()
    c = DllConnection(lib=lib)
    c.writeDots(bytes([1, 2, 3]))
    assert ("dots", bytes([1, 2, 3])) in lib.calls


def test_leave_and_close_forwarded():
    lib = FakeBrlapiLib()
    c = DllConnection(lib=lib)
    c.leaveTtyMode()
    c.closeConnection()
    assert "leave" in lib.calls and "close" in lib.calls


def test_sink_falls_back_to_dll_client(monkeypatch):
    # Force `import brlapi` to fail even where the module exists (WSL).
    monkeypatch.setitem(sys.modules, "brlapi", None)
    made = {}

    class FakeConn:
        displaySize = (20, 1)

        def enterTtyModeWithPath(self, path):
            made["tty"] = path

    monkeypatch.setattr(
        "braille_engine.sinks.brlapi_dll.DllConnection", lambda: FakeConn()
    )
    sink = BrlapiSink()
    assert sink.connect() == 20
    assert made["tty"] == []
