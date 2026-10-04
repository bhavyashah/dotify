"""ctypes BrlAPI client for Windows.

BRLTTY for Windows installs the BrlAPI service and ``brlapi.dll`` but not the
``brlapi`` Python module, so ``BrlapiSink`` falls back to this client when the
module import fails. It speaks the default-handle C API and implements only
the surface the sink uses. Linux/WSL never loads this module (the ``brlapi``
import succeeds there).
"""

import ctypes
import os
from pathlib import Path


class _ConnectionSettings(ctypes.Structure):
    _fields_ = [("auth", ctypes.c_char_p), ("host", ctypes.c_char_p)]


def _declare_signatures(lib):
    """Pin arg/return types on the real DLL so 64-bit ctypes never truncates a
    pointer or the returned fd. For the canonical BrlAPI signatures these match
    how the call sites already pass args (byref/bytes/None/arrays), so behavior
    is unchanged — this only removes the implicit-``c_int`` guesswork. Skipped
    when a Python fake lib is injected (tests), which needs no ctypes typing.
    """
    settings_p = ctypes.POINTER(_ConnectionSettings)
    uint_p = ctypes.POINTER(ctypes.c_uint)
    int_p = ctypes.POINTER(ctypes.c_int)
    lib.brlapi_openConnection.argtypes = [settings_p, settings_p]
    lib.brlapi_openConnection.restype = ctypes.c_int
    lib.brlapi_getDisplaySize.argtypes = [uint_p, uint_p]
    lib.brlapi_getDisplaySize.restype = ctypes.c_int
    lib.brlapi_enterTtyModeWithPath.argtypes = [int_p, ctypes.c_int, ctypes.c_char_p]
    lib.brlapi_enterTtyModeWithPath.restype = ctypes.c_int
    lib.brlapi_writeDots.argtypes = [ctypes.c_char_p]
    lib.brlapi_writeDots.restype = ctypes.c_int
    lib.brlapi_leaveTtyMode.argtypes = []
    lib.brlapi_leaveTtyMode.restype = ctypes.c_int
    lib.brlapi_closeConnection.argtypes = []
    lib.brlapi_closeConnection.restype = None


def _find_dll() -> Path:
    env = os.environ.get("DOTIFY_BRLAPI_DLL")
    if env:
        return Path(env)
    for base in (
        r"C:\Program Files (x86)\BRLTTY\bin",
        r"C:\Program Files\BRLTTY\bin",
    ):
        p = Path(base) / "brlapi.dll"
        if p.exists():
            return p
    raise FileNotFoundError(
        "brlapi.dll not found — is BRLTTY for Windows installed? "
        "(set DOTIFY_BRLAPI_DLL to override the search path)"
    )


class DllConnection:
    """Mirror of the ``brlapi.Connection`` surface ``BrlapiSink`` uses."""

    def __init__(self, lib=None):
        if lib is not None:
            self._lib = lib
        else:
            self._lib = ctypes.CDLL(str(_find_dll()))
            _declare_signatures(self._lib)
        # NULL auth/host = library defaults (BRLAPI_AUTH / BRLAPI_HOST envs,
        # then the standard local service + key file).
        settings = _ConnectionSettings(None, None)
        fd = self._lib.brlapi_openConnection(ctypes.byref(settings), None)
        if fd < 0:
            raise RuntimeError(
                "BrlAPI connection failed — is the BRLTTY service running?"
            )

    @property
    def displaySize(self):
        x = ctypes.c_uint(0)
        y = ctypes.c_uint(0)
        if self._lib.brlapi_getDisplaySize(ctypes.byref(x), ctypes.byref(y)) < 0:
            raise RuntimeError("brlapi_getDisplaySize failed")
        return (x.value, y.value)

    def enterTtyModeWithPath(self, path):
        n = len(path)
        arr = (ctypes.c_int * n)(*path) if n else None
        if self._lib.brlapi_enterTtyModeWithPath(arr, n, None) < 0:
            raise RuntimeError("brlapi_enterTtyModeWithPath failed")

    def writeDots(self, dots):
        if self._lib.brlapi_writeDots(bytes(dots)) < 0:
            raise RuntimeError("brlapi_writeDots failed")

    def leaveTtyMode(self):
        self._lib.brlapi_leaveTtyMode()

    def closeConnection(self):
        self._lib.brlapi_closeConnection()
