"""Inspect and exercise supported braille displays through Windows HID.

This tool deliberately has no third-party dependencies.  Its HumanWare write
path follows NVDA's brailliantB driver: open the usage-page 0x93 collection
with overlapped I/O, read capability report 0x01, then send report 0x05 with
HidD_SetOutputReport.
"""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sys
import time
from typing import Iterable, Sequence

# The source tool and staged ``windows_hid.py`` both keep the registry beside
# this module. Ensure importlib-based tests get the same layout as normal script
# execution without requiring a package install.
_MODULE_DIR = str(Path(__file__).resolve().parent)
if _MODULE_DIR not in sys.path:
    sys.path.insert(0, _MODULE_DIR)
import display_profiles as profiles  # noqa: E402

HUMANWARE_USAGE_PAGE = 0x93
STANDARD_BRAILLE_USAGE_PAGE = 0x41

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
FILE_FLAG_OVERLAPPED = 0x40000000
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

DIGCF_PRESENT = 0x00000002
DIGCF_DEVICEINTERFACE = 0x00000010
ERROR_INSUFFICIENT_BUFFER = 122
ERROR_NO_MORE_ITEMS = 259
ERROR_IO_PENDING = 997
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 258


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("InterfaceClassGuid", GUID),
        ("Flags", wintypes.DWORD),
        ("Reserved", ctypes.c_size_t),
    ]


class HIDD_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("Size", wintypes.ULONG),
        ("VendorID", wintypes.USHORT),
        ("ProductID", wintypes.USHORT),
        ("VersionNumber", wintypes.USHORT),
    ]


class HIDP_CAPS(ctypes.Structure):
    _fields_ = [
        ("Usage", wintypes.USHORT),
        ("UsagePage", wintypes.USHORT),
        ("InputReportByteLength", wintypes.USHORT),
        ("OutputReportByteLength", wintypes.USHORT),
        ("FeatureReportByteLength", wintypes.USHORT),
        ("Reserved", wintypes.USHORT * 17),
        ("NumberLinkCollectionNodes", wintypes.USHORT),
        ("NumberInputButtonCaps", wintypes.USHORT),
        ("NumberInputValueCaps", wintypes.USHORT),
        ("NumberInputDataIndices", wintypes.USHORT),
        ("NumberOutputButtonCaps", wintypes.USHORT),
        ("NumberOutputValueCaps", wintypes.USHORT),
        ("NumberOutputDataIndices", wintypes.USHORT),
        ("NumberFeatureButtonCaps", wintypes.USHORT),
        ("NumberFeatureValueCaps", wintypes.USHORT),
        ("NumberFeatureDataIndices", wintypes.USHORT),
    ]


class HIDP_VALUE_CAPS_RANGE(ctypes.Structure):
    _fields_ = [
        ("UsageMin", wintypes.USHORT),
        ("UsageMax", wintypes.USHORT),
        ("StringMin", wintypes.USHORT),
        ("StringMax", wintypes.USHORT),
        ("DesignatorMin", wintypes.USHORT),
        ("DesignatorMax", wintypes.USHORT),
        ("DataIndexMin", wintypes.USHORT),
        ("DataIndexMax", wintypes.USHORT),
    ]


class HIDP_VALUE_CAPS_NOT_RANGE(ctypes.Structure):
    _fields_ = [
        ("Usage", wintypes.USHORT),
        ("Reserved1", wintypes.USHORT),
        ("StringIndex", wintypes.USHORT),
        ("Reserved2", wintypes.USHORT),
        ("DesignatorIndex", wintypes.USHORT),
        ("Reserved3", wintypes.USHORT),
        ("DataIndex", wintypes.USHORT),
        ("Reserved4", wintypes.USHORT),
    ]


class HIDP_VALUE_CAPS_UNION(ctypes.Union):
    _fields_ = [
        ("Range", HIDP_VALUE_CAPS_RANGE),
        ("NotRange", HIDP_VALUE_CAPS_NOT_RANGE),
    ]


class HIDP_VALUE_CAPS(ctypes.Structure):
    _anonymous_ = ("values",)
    _fields_ = [
        ("UsagePage", wintypes.USHORT),
        ("ReportID", ctypes.c_ubyte),
        ("IsAlias", ctypes.c_ubyte),
        ("BitField", wintypes.USHORT),
        ("LinkCollection", wintypes.USHORT),
        ("LinkUsage", wintypes.USHORT),
        ("LinkUsagePage", wintypes.USHORT),
        ("IsRange", ctypes.c_ubyte),
        ("IsStringRange", ctypes.c_ubyte),
        ("IsDesignatorRange", ctypes.c_ubyte),
        ("IsAbsolute", ctypes.c_ubyte),
        ("HasNull", ctypes.c_ubyte),
        ("Reserved", ctypes.c_ubyte),
        ("BitSize", wintypes.USHORT),
        ("ReportCount", wintypes.USHORT),
        ("Reserved2", wintypes.USHORT * 5),
        ("UnitsExp", wintypes.ULONG),
        ("Units", wintypes.ULONG),
        ("LogicalMin", wintypes.LONG),
        ("LogicalMax", wintypes.LONG),
        ("PhysicalMin", wintypes.LONG),
        ("PhysicalMax", wintypes.LONG),
        ("values", HIDP_VALUE_CAPS_UNION),
    ]


class OVERLAPPED(ctypes.Structure):
    _fields_ = [
        ("Internal", ctypes.c_size_t),
        ("InternalHigh", ctypes.c_size_t),
        ("Offset", wintypes.DWORD),
        ("OffsetHigh", wintypes.DWORD),
        ("hEvent", wintypes.HANDLE),
    ]


@dataclass(frozen=True)
class ValueCap:
    report_id: int
    usage_page: int
    link_collection: int
    link_usage: int
    link_usage_page: int
    usage: int
    bit_size: int
    report_count: int
    is_range: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "report_id": f"0x{self.report_id:02X}",
            "usage_page": f"0x{self.usage_page:04X}",
            "link_collection": self.link_collection,
            "link_usage": f"0x{self.link_usage:04X}",
            "link_usage_page": f"0x{self.link_usage_page:04X}",
            "usage": f"0x{self.usage:04X}",
            "bit_size": self.bit_size,
            "report_count": self.report_count,
            "is_range": self.is_range,
        }


@dataclass(frozen=True)
class Collection:
    path: str
    vid: int
    pid: int
    version: int
    usage_page: int
    usage: int
    input_length: int
    output_length: int
    feature_length: int
    manufacturer: str | None
    product: str | None
    output_value_caps: tuple[ValueCap, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "vid": f"0x{self.vid:04X}",
            "pid": f"0x{self.pid:04X}",
            "version": f"0x{self.version:04X}",
            "usage_page": f"0x{self.usage_page:04X}",
            "usage": f"0x{self.usage:04X}",
            "input_length": self.input_length,
            "output_length": self.output_length,
            "feature_length": self.feature_length,
            "manufacturer": self.manufacturer,
            "product": self.product,
            "output_value_caps": [cap.as_dict() for cap in self.output_value_caps],
        }


@dataclass
class PendingRead:
    buffer: ctypes.Array
    operation: OVERLAPPED
    event: int


class WinApi:
    def __init__(self) -> None:
        if os.name != "nt":
            raise RuntimeError("hid_probe.py must run under native Windows Python")

        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
        self.hid = ctypes.WinDLL("hid", use_last_error=True)
        self._bind()

    def _bind(self) -> None:
        pvoid = ctypes.c_void_p
        pdword = ctypes.POINTER(wintypes.DWORD)

        self.hid.HidD_GetHidGuid.argtypes = [ctypes.POINTER(GUID)]
        self.hid.HidD_GetHidGuid.restype = None
        self.setupapi.SetupDiGetClassDevsW.argtypes = [
            ctypes.POINTER(GUID), wintypes.LPCWSTR, wintypes.HWND, wintypes.DWORD
        ]
        self.setupapi.SetupDiGetClassDevsW.restype = pvoid
        self.setupapi.SetupDiEnumDeviceInterfaces.argtypes = [
            pvoid, pvoid, ctypes.POINTER(GUID), wintypes.DWORD,
            ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
        ]
        self.setupapi.SetupDiEnumDeviceInterfaces.restype = wintypes.BOOL
        self.setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
            pvoid, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA), pvoid,
            wintypes.DWORD, pdword, pvoid,
        ]
        self.setupapi.SetupDiGetDeviceInterfaceDetailW.restype = wintypes.BOOL
        self.setupapi.SetupDiDestroyDeviceInfoList.argtypes = [pvoid]
        self.setupapi.SetupDiDestroyDeviceInfoList.restype = wintypes.BOOL

        self.kernel32.CreateFileW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, pvoid,
            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
        ]
        self.kernel32.CreateFileW.restype = wintypes.HANDLE
        self.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel32.CloseHandle.restype = wintypes.BOOL
        self.kernel32.CreateEventW.argtypes = [pvoid, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
        self.kernel32.CreateEventW.restype = wintypes.HANDLE
        self.kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.kernel32.WaitForSingleObject.restype = wintypes.DWORD
        self.kernel32.WriteFile.argtypes = [
            wintypes.HANDLE, pvoid, wintypes.DWORD, pdword, ctypes.POINTER(OVERLAPPED)
        ]
        self.kernel32.WriteFile.restype = wintypes.BOOL
        self.kernel32.ReadFile.argtypes = [
            wintypes.HANDLE, pvoid, wintypes.DWORD, pdword, ctypes.POINTER(OVERLAPPED)
        ]
        self.kernel32.ReadFile.restype = wintypes.BOOL
        self.kernel32.GetOverlappedResult.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(OVERLAPPED), pdword, wintypes.BOOL
        ]
        self.kernel32.GetOverlappedResult.restype = wintypes.BOOL
        self.kernel32.CancelIoEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(OVERLAPPED)]
        self.kernel32.CancelIoEx.restype = wintypes.BOOL

        self.hid.HidD_GetAttributes.argtypes = [wintypes.HANDLE, ctypes.POINTER(HIDD_ATTRIBUTES)]
        self.hid.HidD_GetAttributes.restype = wintypes.BOOL
        self.hid.HidD_GetPreparsedData.argtypes = [wintypes.HANDLE, ctypes.POINTER(pvoid)]
        self.hid.HidD_GetPreparsedData.restype = wintypes.BOOL
        self.hid.HidD_FreePreparsedData.argtypes = [pvoid]
        self.hid.HidD_FreePreparsedData.restype = wintypes.BOOL
        self.hid.HidP_GetCaps.argtypes = [pvoid, ctypes.POINTER(HIDP_CAPS)]
        self.hid.HidP_GetCaps.restype = ctypes.c_long
        self.hid.HidP_GetValueCaps.argtypes = [
            ctypes.c_int,
            ctypes.POINTER(HIDP_VALUE_CAPS),
            ctypes.POINTER(wintypes.USHORT),
            pvoid,
        ]
        self.hid.HidP_GetValueCaps.restype = ctypes.c_long
        self.hid.HidP_SetUsageValueArray.argtypes = [
            ctypes.c_int,
            wintypes.USHORT,
            wintypes.USHORT,
            wintypes.USHORT,
            pvoid,
            wintypes.USHORT,
            pvoid,
            pvoid,
            wintypes.ULONG,
        ]
        self.hid.HidP_SetUsageValueArray.restype = ctypes.c_long
        self.hid.HidD_GetManufacturerString.argtypes = [wintypes.HANDLE, pvoid, wintypes.ULONG]
        self.hid.HidD_GetManufacturerString.restype = wintypes.BOOL
        self.hid.HidD_GetProductString.argtypes = [wintypes.HANDLE, pvoid, wintypes.ULONG]
        self.hid.HidD_GetProductString.restype = wintypes.BOOL
        self.hid.HidD_GetFeature.argtypes = [wintypes.HANDLE, pvoid, wintypes.ULONG]
        self.hid.HidD_GetFeature.restype = wintypes.BOOL
        self.hid.HidD_SetOutputReport.argtypes = [wintypes.HANDLE, pvoid, wintypes.ULONG]
        self.hid.HidD_SetOutputReport.restype = wintypes.BOOL

    @staticmethod
    def error(operation: str, code: int | None = None) -> OSError:
        if code is None:
            code = ctypes.get_last_error()
        return OSError(code, f"{operation}: {ctypes.FormatError(code).strip()}")

    def open_handle(self, path: str, *, shared: bool, overlapped: bool) -> int:
        share = FILE_SHARE_READ | FILE_SHARE_WRITE if shared else 0
        flags = FILE_FLAG_OVERLAPPED if overlapped else 0
        handle = self.kernel32.CreateFileW(
            path, GENERIC_READ | GENERIC_WRITE, share, None, OPEN_EXISTING, flags, None
        )
        if handle == INVALID_HANDLE_VALUE:
            raise self.error("CreateFileW")
        return handle

    def close_handle(self, handle: int | None) -> None:
        if handle not in (None, INVALID_HANDLE_VALUE):
            self.kernel32.CloseHandle(handle)


def _device_path_from_detail(detail: ctypes.Array[ctypes.c_char]) -> str:
    # SP_DEVICE_INTERFACE_DETAIL_DATA_W.DevicePath begins immediately after the
    # DWORD cbSize, even though cbSize itself is 8 on 64-bit Windows.
    return ctypes.wstring_at(ctypes.addressof(detail) + ctypes.sizeof(wintypes.DWORD))


def enumerate_paths(api: WinApi) -> list[str]:
    hid_guid = GUID()
    api.hid.HidD_GetHidGuid(ctypes.byref(hid_guid))
    info_set = api.setupapi.SetupDiGetClassDevsW(
        ctypes.byref(hid_guid), None, None, DIGCF_PRESENT | DIGCF_DEVICEINTERFACE
    )
    if info_set == INVALID_HANDLE_VALUE:
        raise api.error("SetupDiGetClassDevsW")

    paths: list[str] = []
    try:
        index = 0
        while True:
            interface = SP_DEVICE_INTERFACE_DATA()
            interface.cbSize = ctypes.sizeof(interface)
            if not api.setupapi.SetupDiEnumDeviceInterfaces(
                info_set, None, ctypes.byref(hid_guid), index, ctypes.byref(interface)
            ):
                code = ctypes.get_last_error()
                if code == ERROR_NO_MORE_ITEMS:
                    break
                raise api.error("SetupDiEnumDeviceInterfaces", code)

            required = wintypes.DWORD()
            api.setupapi.SetupDiGetDeviceInterfaceDetailW(
                info_set, ctypes.byref(interface), None, 0, ctypes.byref(required), None
            )
            code = ctypes.get_last_error()
            if code != ERROR_INSUFFICIENT_BUFFER:
                raise api.error("SetupDiGetDeviceInterfaceDetailW(size)", code)

            detail = ctypes.create_string_buffer(required.value)
            ctypes.cast(detail, ctypes.POINTER(wintypes.DWORD))[0] = (
                8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6
            )
            if not api.setupapi.SetupDiGetDeviceInterfaceDetailW(
                info_set, ctypes.byref(interface), detail, required.value,
                ctypes.byref(required), None,
            ):
                raise api.error("SetupDiGetDeviceInterfaceDetailW(path)")
            paths.append(_device_path_from_detail(detail))
            index += 1
    finally:
        api.setupapi.SetupDiDestroyDeviceInfoList(info_set)
    return paths


def _hid_string(function: object, handle: int) -> str | None:
    buffer = ctypes.create_unicode_buffer(256)
    if function(handle, buffer, ctypes.sizeof(buffer)):
        return buffer.value
    return None


def inspect_path(api: WinApi, path: str) -> Collection | None:
    handle: int | None = None
    try:
        try:
            handle = api.open_handle(path, shared=True, overlapped=False)
        except OSError:
            return None
        return inspect_open_handle(api, handle, path)
    finally:
        api.close_handle(handle)


def _read_output_value_caps(
    api: WinApi, preparsed: ctypes.c_void_p, count: int
) -> tuple[ValueCap, ...]:
    if count <= 0:
        return ()
    buffer = (HIDP_VALUE_CAPS * count)()
    length = wintypes.USHORT(count)
    status = api.hid.HidP_GetValueCaps(1, buffer, ctypes.byref(length), preparsed)
    if status < 0:
        raise RuntimeError(
            f"HidP_GetValueCaps(output) returned NTSTATUS 0x{status & 0xFFFFFFFF:08X}"
        )
    result = []
    for item in buffer[: length.value]:
        usage = item.Range.UsageMin if item.IsRange else item.NotRange.Usage
        result.append(
            ValueCap(
                report_id=item.ReportID,
                usage_page=item.UsagePage,
                link_collection=item.LinkCollection,
                link_usage=item.LinkUsage,
                link_usage_page=item.LinkUsagePage,
                usage=usage,
                bit_size=item.BitSize,
                report_count=item.ReportCount,
                is_range=bool(item.IsRange),
            )
        )
    return tuple(result)


def inspect_open_handle(api: WinApi, handle: int, path: str) -> Collection | None:
    """Read identity and caps without closing the caller's existing handle."""
    preparsed = ctypes.c_void_p()
    try:
        attributes = HIDD_ATTRIBUTES()
        attributes.Size = ctypes.sizeof(attributes)
        if not api.hid.HidD_GetAttributes(handle, ctypes.byref(attributes)):
            return None
        if not api.hid.HidD_GetPreparsedData(handle, ctypes.byref(preparsed)):
            raise api.error("HidD_GetPreparsedData")
        caps = HIDP_CAPS()
        status = api.hid.HidP_GetCaps(preparsed, ctypes.byref(caps))
        if status < 0:
            raise RuntimeError(f"HidP_GetCaps returned NTSTATUS 0x{status & 0xFFFFFFFF:08X}")
        output_value_caps = _read_output_value_caps(
            api, preparsed, caps.NumberOutputValueCaps
        )

        return Collection(
            path=path,
            vid=attributes.VendorID,
            pid=attributes.ProductID,
            version=attributes.VersionNumber,
            usage_page=caps.UsagePage,
            usage=caps.Usage,
            input_length=caps.InputReportByteLength,
            output_length=caps.OutputReportByteLength,
            feature_length=caps.FeatureReportByteLength,
            manufacturer=_hid_string(api.hid.HidD_GetManufacturerString, handle),
            product=_hid_string(api.hid.HidD_GetProductString, handle),
            output_value_caps=output_value_caps,
        )
    finally:
        if preparsed.value:
            api.hid.HidD_FreePreparsedData(preparsed)


def enumerate_hid_collections(api: WinApi) -> list[Collection]:
    result: list[Collection] = []
    for path in enumerate_paths(api):
        collection = inspect_path(api, path)
        if collection is not None:
            result.append(collection)
    return sorted(result, key=lambda item: (item.vid, item.pid, item.usage_page, item.path))


def enumerate_known_displays(api: WinApi) -> list[Collection]:
    """Return HID collections whose VID/PID appears in the display registry.

    USB interface paths carry their identity ("...#vid_1c71&pid_c006..."),
    so every other USB device is skipped without being opened; reconnect
    paths run this repeatedly during an outage. A path without a USB
    identity is inspected as before.
    """
    identities = {(profile.vid, profile.pid) for profile in profiles.PROFILES}
    path_ids = {f"vid_{vid:04x}&pid_{pid:04x}" for vid, pid in identities}
    result: list[Collection] = []
    for path in enumerate_paths(api):
        lowered = path.lower()
        if "vid_" in lowered and not any(ident in lowered for ident in path_ids):
            continue
        collection = inspect_path(api, path)
        if collection is not None and (collection.vid, collection.pid) in identities:
            result.append(collection)
    return sorted(result, key=lambda item: (item.vid, item.pid, item.usage_page, item.path))


def select_profile_collection(
    api: WinApi,
    profile: profiles.DisplayProfile,
    *,
    collections: Sequence[Collection] | None = None,
    usage_page: int | None = None,
) -> Collection:
    """Select one fresh collection for a profile, rejecting ambiguity."""
    if collections is None:
        collections = enumerate_known_displays(api)
    identity_matches = [
        collection
        for collection in collections
        if profile.matches_identity(collection.vid, collection.pid)
    ]
    if usage_page is None:
        matches = list(profiles.matching_collections(profile, identity_matches))
    else:
        matches = [item for item in identity_matches if item.usage_page == usage_page]
    if not matches:
        expected = (
            f"usage page 0x{usage_page:04X}"
            if usage_page is not None
            else "a compatible HID collection"
        )
        raise RuntimeError(f"{profile.name} is present but does not expose {expected}")
    if len(matches) > 1:
        pages = ", ".join(f"0x{item.usage_page:04X}" for item in matches)
        raise RuntimeError(
            f"multiple compatible collections found for {profile.name} ({pages}); "
            "disconnect duplicate units or select one explicitly"
        )
    return matches[0]


def get_feature(api: WinApi, handle: int, size: int, report_id: int) -> bytes:
    if size <= 0:
        raise RuntimeError("collection advertises no feature report")
    buffer = ctypes.create_string_buffer(size)
    buffer[0] = bytes([report_id])
    if not api.hid.HidD_GetFeature(handle, buffer, size):
        raise api.error(f"HidD_GetFeature(report 0x{report_id:02X})")
    return buffer.raw


def acquire_preparsed_data(api: WinApi, handle: int) -> ctypes.c_void_p:
    preparsed = ctypes.c_void_p()
    if not api.hid.HidD_GetPreparsedData(handle, ctypes.byref(preparsed)):
        raise api.error("HidD_GetPreparsedData")
    return preparsed


def free_preparsed_data(api: WinApi, preparsed: ctypes.c_void_p | None) -> None:
    if preparsed is not None and preparsed.value:
        api.hid.HidD_FreePreparsedData(preparsed)


def queue_input_read(api: WinApi, handle: int, size: int) -> PendingRead:
    """Arm the interrupt-IN path as NVDA's hwIo.Hid does immediately on open."""
    if size <= 0:
        raise RuntimeError("collection advertises no input report")
    event = api.kernel32.CreateEventW(None, True, False, None)
    if not event:
        raise api.error("CreateEventW(input read)")
    operation = OVERLAPPED()
    operation.hEvent = event
    buffer = ctypes.create_string_buffer(size)
    if not api.kernel32.ReadFile(handle, buffer, size, None, ctypes.byref(operation)):
        code = ctypes.get_last_error()
        if code != ERROR_IO_PENDING:
            api.close_handle(event)
            raise api.error("ReadFile(queue input)", code)
    return PendingRead(buffer=buffer, operation=operation, event=event)


def cancel_input_read(api: WinApi, handle: int | None, pending: PendingRead | None) -> None:
    if pending is None:
        return
    if handle not in (None, INVALID_HANDLE_VALUE):
        api.kernel32.CancelIoEx(handle, ctypes.byref(pending.operation))
    # CancelIoEx is asynchronous: the kernel owns pending.operation and
    # pending.buffer until the IRP completes and writes its final status
    # into them. Wait on the event (signaled for completed, failed, AND
    # cancelled I/O) before anything here is freed, or the completion lands
    # in released CPython heap — an intermittent ticker crash on unplug.
    # Bounded: cancels complete promptly. A wedged driver that never
    # completes it leaves the read in _abandoned_reads instead of freed.
    if api.kernel32.WaitForSingleObject(pending.event, 2000) == WAIT_TIMEOUT:
        _abandoned_reads.append(pending)
        return
    api.close_handle(pending.event)


# Reads whose cancel never completed: the kernel may still write into their
# OVERLAPPED and buffer, so they are kept alive for the life of the process.
_abandoned_reads: list[PendingRead] = []


# HumanWare HID input reports (display key events). Report ids and key
# numbers follow BRLTTY's HumanWare driver (brldefs-hw.h), the reference
# implementation for this protocol: report 0x04 carries the FULL set of
# currently-pressed key numbers (zero-padded); report 0x07 announces
# power-off. Routing keys are numbered from HW_KEY_ROUTING_BASE up, one per
# cell.
HW_REPORT_PRESSED_KEYS = 0x04
HW_REPORT_POWERING_OFF = 0x07
HW_KEY_ROUTING_BASE = 80
HW_KEY_NAMES = {
    1: "reset",
    2: "dot1", 3: "dot2", 4: "dot3", 5: "dot4",
    6: "dot5", 7: "dot6", 8: "dot7", 9: "dot8",
    10: "space",
    11: "command1", 12: "command2", 13: "command3",
    14: "command4", 15: "command5", 16: "command6",
    17: "thumb_previous", 18: "thumb_left",
    19: "thumb_right", 20: "thumb_next",
    21: "up", 22: "down", 23: "left", 24: "right", 25: "action",
}


def parse_humanware_pressed_keys(report: bytes) -> frozenset | None:
    """Pressed-key set from a HumanWare input report, or ``None`` if the
    report is not a key report (callers log those raw for discovery).

    Key numbers outside the table come back as ``key<N>`` rather than being
    dropped, so a hand-test session surfaces every code the display sends.
    """
    if not report or report[0] != HW_REPORT_PRESSED_KEYS:
        return None
    keys = set()
    for code in report[1:]:
        if code == 0:
            continue  # zero padding after the pressed set
        if code >= HW_KEY_ROUTING_BASE:
            keys.add(f"routing:{code - HW_KEY_ROUTING_BASE}")
        else:
            keys.add(HW_KEY_NAMES.get(code, f"key{code}"))
    return frozenset(keys)


def build_humanware_report(cells: Sequence[int]) -> bytes:
    if len(cells) > 255:
        raise ValueError("HumanWare reports support at most 255 cells")
    return bytes((0x05, 0x01, 0x00, len(cells), *cells))


def standard_braille_rows(collection: Collection) -> tuple[ValueCap, ...]:
    rows = tuple(
        cap
        for cap in collection.output_value_caps
        if cap.link_usage_page == STANDARD_BRAILLE_USAGE_PAGE
        and cap.link_usage == 0x02
        and cap.usage in (0x03, 0x04)
        and cap.report_count > 0
    )
    if not rows:
        raise RuntimeError("standard HID collection has no braille-row output value array")
    if any(cap.is_range or cap.bit_size != 8 for cap in rows):
        raise RuntimeError("standard HID braille rows must be non-range eight-bit value arrays")
    return rows


def standard_braille_width(collection: Collection) -> int:
    rows = standard_braille_rows(collection)
    if len(rows) != 1:
        raise RuntimeError(
            f"standard HID display has {len(rows)} rows; the ticker currently supports one row"
        )
    return rows[0].report_count


def build_standard_output_reports(
    api: WinApi,
    handle: int,
    collection: Collection,
    cells: Sequence[int],
    *,
    preparsed_data: ctypes.c_void_p | None = None,
) -> tuple[bytes, ...]:
    rows = standard_braille_rows(collection)
    expected = sum(row.report_count for row in rows)
    if len(cells) != expected:
        raise ValueError(f"expected {expected} standard-HID cells, got {len(cells)}")

    owns_preparsed = preparsed_data is None
    preparsed = preparsed_data or acquire_preparsed_data(api, handle)
    try:
        reports: dict[int, ctypes.Array] = {}
        offset = 0
        for row in rows:
            report = reports.setdefault(
                row.report_id, ctypes.create_string_buffer(collection.output_length)
            )
            report[0] = bytes((row.report_id,))
            row_data = bytes(cells[offset : offset + row.report_count])
            offset += row.report_count
            values = ctypes.create_string_buffer(row_data, len(row_data))
            status = api.hid.HidP_SetUsageValueArray(
                1,
                row.usage_page,
                row.link_collection,
                row.usage,
                values,
                len(row_data),
                preparsed,
                report,
                collection.output_length,
            )
            if status < 0:
                raise RuntimeError(
                    "HidP_SetUsageValueArray returned NTSTATUS "
                    f"0x{status & 0xFFFFFFFF:08X}"
                )
        return tuple(report.raw for report in reports.values())
    finally:
        if owns_preparsed:
            free_preparsed_data(api, preparsed)


def make_cells(pattern: str, count: int) -> bytes:
    if not 1 <= count <= 255:
        raise ValueError(f"invalid cell count: {count}")
    if pattern == "blank":
        return bytes(count)
    if pattern == "full":
        return bytes([0xFF]) * count
    if pattern == "alternating":
        return bytes(0xFF if index % 2 == 0 else 0x00 for index in range(count))
    if pattern == "walking":
        return bytes(1 << (index % 8) for index in range(count))
    raise ValueError(f"unknown pattern: {pattern}")


def set_output_report(api: WinApi, handle: int, report: bytes) -> None:
    buffer = ctypes.create_string_buffer(report, len(report))
    if not api.hid.HidD_SetOutputReport(handle, buffer, len(report)):
        raise api.error("HidD_SetOutputReport")


def write_file(
    api: WinApi, handle: int, report: bytes, *, overlapped: bool, timeout_ms: int
) -> int:
    buffer = ctypes.create_string_buffer(report, len(report))
    written = wintypes.DWORD()
    if not overlapped:
        if not api.kernel32.WriteFile(handle, buffer, len(report), ctypes.byref(written), None):
            raise api.error("WriteFile(synchronous)")
        return written.value

    event = api.kernel32.CreateEventW(None, True, False, None)
    if not event:
        raise api.error("CreateEventW")
    operation = OVERLAPPED()
    operation.hEvent = event
    try:
        if api.kernel32.WriteFile(handle, buffer, len(report), None, ctypes.byref(operation)):
            if not api.kernel32.GetOverlappedResult(
                handle, ctypes.byref(operation), ctypes.byref(written), False
            ):
                raise api.error("GetOverlappedResult(immediate)")
            return written.value

        code = ctypes.get_last_error()
        if code != ERROR_IO_PENDING:
            raise api.error("WriteFile(overlapped)", code)
        wait = api.kernel32.WaitForSingleObject(event, timeout_ms)
        if wait == WAIT_TIMEOUT:
            api.kernel32.CancelIoEx(handle, ctypes.byref(operation))
            # Async cancel: the kernel still owns `operation`/`buffer` until
            # the IRP completes — wait for the event before the raise (and
            # the finally's CloseHandle) frees them under the kernel.
            api.kernel32.WaitForSingleObject(event, 2000)
            raise TimeoutError(f"overlapped WriteFile timed out after {timeout_ms} ms")
        if wait != WAIT_OBJECT_0:
            raise api.error("WaitForSingleObject", wait)
        if not api.kernel32.GetOverlappedResult(
            handle, ctypes.byref(operation), ctypes.byref(written), False
        ):
            raise api.error("GetOverlappedResult")
        return written.value
    finally:
        api.close_handle(event)


def print_snapshot(collections: Iterable[Collection], *, json_output: bool) -> None:
    items = list(collections)
    if json_output:
        print(json.dumps([item.as_dict() for item in items], indent=2))
        return
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  braille HID collections: {len(items)}")
    for item in items:
        matched = profiles.profiles_for_identity(item.vid, item.pid)
        device_name = matched[0].name if matched else "unregistered HID device"
        print(
            f"  {device_name} VID=0x{item.vid:04X} PID=0x{item.pid:04X} "
            f"usagePage=0x{item.usage_page:04X} usage=0x{item.usage:04X} "
            f"reports(in/out/feature)={item.input_length}/{item.output_length}/{item.feature_length}"
        )
        print(f"    product={item.product!r} manufacturer={item.manufacturer!r}")
        print(f"    path={item.path}")


def command_inspect(api: WinApi, args: argparse.Namespace) -> int:
    previous: tuple[str, ...] | None = None
    changed = False
    for snapshot in range(args.snapshots):
        if args.all_hid:
            collections = enumerate_hid_collections(api)
        else:
            collections = enumerate_known_displays(api)
            if args.device != "all":
                profile = profiles.get_profile(args.device)
                collections = [
                    item
                    for item in collections
                    if profile.matches_identity(item.vid, item.pid)
                ]
        current = tuple(f"{item.usage_page:04X}:{item.path}" for item in collections)
        if previous is not None and current != previous:
            changed = True
            if not args.json:
                print("COLLECTION SET CHANGED SINCE PREVIOUS SNAPSHOT")
        print_snapshot(collections, json_output=args.json)
        previous = current
        if snapshot + 1 < args.snapshots:
            time.sleep(args.interval)
    if not previous:
        return 2
    return 3 if changed else 0


def _selected_native_profile(args: argparse.Namespace) -> profiles.DisplayProfile:
    profile = profiles.get_profile(args.device)
    if not profile.native_supported or profile.transport != profiles.HUMANWARE_HID:
        raise RuntimeError(
            f"{profile.name} is discovery-only: its {profile.transport} transport "
            "is not available through the in-box HID sink"
        )
    return profile


def command_probe(api: WinApi, args: argparse.Namespace) -> int:
    profile = _selected_native_profile(args)
    selected = select_profile_collection(api, profile, usage_page=args.usage_page)
    path = selected.path
    handle: int | None = None
    pending: PendingRead | None = None
    try:
        handle = api.open_handle(
            path, shared=args.share == "shared", overlapped=args.io == "overlapped"
        )
        collection = inspect_open_handle(api, handle, path)
        if collection is None or collection.usage_page != args.usage_page:
            raise RuntimeError("opened path does not expose the requested display collection")
        print_snapshot([collection], json_output=False)
        if args.queue_read:
            pending = queue_input_read(api, handle, collection.input_length)
            print(f"queued {collection.input_length}-byte overlapped input read")
        print(f"opened ({args.share}, {args.io}); settling {args.settle_seconds:.1f}s")
        time.sleep(args.settle_seconds)
        last_error: OSError | None = None
        for attempt in range(1, args.attempts + 1):
            try:
                feature = get_feature(api, handle, collection.feature_length, args.report_id)
                print(f"feature attempt {attempt}: {feature.hex(' ')}")
                if args.report_id == 0x01 and len(feature) > 24:
                    print(f"HumanWare reported cell count: {feature[24]}")
                return 0
            except OSError as error:
                last_error = error
                print(f"feature attempt {attempt} failed: winerror={error.errno} {error.strerror}")
                if attempt < args.attempts:
                    time.sleep(args.retry_delay)
        assert last_error is not None
        return 1
    finally:
        cancel_input_read(api, handle, pending)
        api.close_handle(handle)


def _read_humanware_cell_count(
    api: WinApi,
    handle: int,
    collection: Collection,
    attempts: int,
    retry_delay: float,
    *,
    verbose: bool = True,
) -> int:
    last_error: OSError | None = None
    for attempt in range(1, attempts + 1):
        try:
            feature = get_feature(api, handle, collection.feature_length, 0x01)
            if len(feature) <= 24 or feature[24] == 0:
                raise RuntimeError(f"capability report has invalid cell count: {feature.hex(' ')}")
            if verbose:
                print(f"feature attempt {attempt}: cell count={feature[24]} data={feature.hex(' ')}")
            return feature[24]
        except OSError as error:
            last_error = error
            if verbose:
                print(f"feature attempt {attempt} failed: winerror={error.errno} {error.strerror}")
            if attempt < attempts:
                time.sleep(retry_delay)
    assert last_error is not None
    raise last_error


def command_write(api: WinApi, args: argparse.Namespace) -> int:
    profile = _selected_native_profile(args)
    usage_page = HUMANWARE_USAGE_PAGE if args.protocol == "humanware" else STANDARD_BRAILLE_USAGE_PAGE
    selected = select_profile_collection(api, profile, usage_page=usage_page)
    path = selected.path
    handle: int | None = None
    pending: PendingRead | None = None
    standard_preparsed: ctypes.c_void_p | None = None
    try:
        handle = api.open_handle(
            path, shared=args.share == "shared", overlapped=args.io == "overlapped"
        )
        collection = inspect_open_handle(api, handle, path)
        if collection is None or collection.usage_page != usage_page:
            raise RuntimeError("opened path does not expose the requested display collection")
        print_snapshot([collection], json_output=False)
        if args.protocol == "standard":
            # Cache parser data while the collection is freshly open. Some
            # composite displays stop serving control requests after the input
            # read is armed even though interrupt output remains available.
            standard_preparsed = acquire_preparsed_data(api, handle)
        if args.queue_read:
            pending = queue_input_read(api, handle, collection.input_length)
            print(f"queued {collection.input_length}-byte overlapped input read")
        print(f"opened ({args.share}, {args.io}); settling {args.settle_seconds:.1f}s")
        time.sleep(args.settle_seconds)

        if args.protocol == "humanware":
            if args.cell_count is None:
                count = _read_humanware_cell_count(
                    api, handle, collection, args.attempts, args.retry_delay
                )
            else:
                count = args.cell_count
                descriptor_count = collection.output_length - 4
                if count != descriptor_count:
                    raise RuntimeError(
                        f"--cell-count {count} conflicts with descriptor payload {descriptor_count}"
                    )
                print(f"bypassing capability read; descriptor implies {count} cells")
            report = build_humanware_report(make_cells(args.pattern, count))
            reports = (report,)
        else:
            count = standard_braille_width(collection)
            reports = build_standard_output_reports(
                api,
                handle,
                collection,
                make_cells(args.pattern, count),
                preparsed_data=standard_preparsed,
            )

        for report in reports:
            if len(report) != collection.output_length:
                raise RuntimeError(
                    f"report is {len(report)} bytes but collection requires {collection.output_length}"
                )
            print(f"sending {len(report)} bytes via {args.transport}: {report.hex(' ')}")
            if args.transport == "set-output":
                set_output_report(api, handle, report)
                print("HidD_SetOutputReport succeeded")
            else:
                written = write_file(
                    api,
                    handle,
                    report,
                    overlapped=args.io == "overlapped",
                    timeout_ms=args.timeout_ms,
                )
                print(f"WriteFile succeeded: {written} bytes")
        return 0
    except OSError as error:
        print(f"FAILED: winerror={error.errno} {error.strerror}", file=sys.stderr)
        return 1
    except (RuntimeError, TimeoutError, ValueError) as error:
        print(f"FAILED: {error}", file=sys.stderr)
        return 1
    finally:
        free_preparsed_data(api, standard_preparsed)
        cancel_input_read(api, handle, pending)
        api.close_handle(handle)


def auto_int(value: str) -> int:
    return int(value, 0)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    profile_choices = tuple(profile.id for profile in profiles.PROFILES)
    native_choices = tuple(profile.id for profile in profiles.native_hid_profiles())

    inspect_parser = subparsers.add_parser("inspect", help="enumerate fresh display HID paths")
    inspect_parser.add_argument("--snapshots", type=int, default=1)
    inspect_parser.add_argument("--interval", type=float, default=1.0)
    inspect_parser.add_argument("--json", action="store_true")
    inspect_parser.add_argument("--device", choices=("all", *profile_choices), default="all")
    inspect_parser.add_argument(
        "--all-hid", action="store_true", help="show every HID device, not only registry identities"
    )
    inspect_parser.set_defaults(function=command_inspect)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--share", choices=("exclusive", "shared"), default="exclusive")
    common.add_argument("--io", choices=("overlapped", "synchronous"), default="overlapped")
    common.add_argument(
        "--queue-read", action=argparse.BooleanOptionalAction, default=True,
        help="queue an overlapped input read before control/output operations (NVDA default)",
    )
    common.add_argument("--settle-seconds", type=float, default=1.0)
    common.add_argument("--attempts", type=int, default=3)
    common.add_argument("--retry-delay", type=float, default=0.2)
    common.add_argument("--device", choices=native_choices, default="nls-ereader")

    probe_parser = subparsers.add_parser(
        "probe", parents=[common], help="open one collection and read a feature report"
    )
    probe_parser.add_argument("--usage-page", type=auto_int, default=HUMANWARE_USAGE_PAGE)
    probe_parser.add_argument("--report-id", type=auto_int, default=0x01)
    probe_parser.set_defaults(function=command_probe)

    write_parser = subparsers.add_parser(
        "write", parents=[common], help="write a visible, non-persistent cell pattern"
    )
    write_parser.add_argument("--protocol", choices=("humanware", "standard"), default="humanware")
    write_parser.add_argument(
        "--transport", choices=("set-output", "writefile"), default="set-output"
    )
    write_parser.add_argument(
        "--pattern", choices=("blank", "full", "alternating", "walking"), default="alternating"
    )
    write_parser.add_argument(
        "--cell-count", type=int,
        help="bypass HumanWare feature report 0x01; must match the output descriptor",
    )
    write_parser.add_argument("--timeout-ms", type=int, default=2000)
    write_parser.set_defaults(function=command_write)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "snapshots", 1) < 1:
        raise SystemExit("--snapshots must be at least 1")
    if getattr(args, "attempts", 1) < 1:
        raise SystemExit("--attempts must be at least 1")
    try:
        api = WinApi()
        return args.function(api, args)
    except OSError as error:
        print(f"FAILED: winerror={error.errno} {error.strerror}", file=sys.stderr)
        return 1
    except RuntimeError as error:
        print(f"FAILED: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
