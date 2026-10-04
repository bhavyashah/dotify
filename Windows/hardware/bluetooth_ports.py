"""Passive discovery of paired Bluetooth serial-port (SPP) devices.

Windows exposes a paired device's Serial Port Profile as a virtual COM port
under the in-box BTHENUM bus driver. Everything here is read-only registry
inspection — no COM port is ever opened, so discovery cannot trigger a radio
connection attempt or its multi-second timeout. The registry layout, as seen
on Windows 11:

  HKLM\\SYSTEM\\CurrentControlSet\\Enum\\BTHENUM\\
      {00001101-...}_LOCALMFG&...\\        one key per SPP service source
          <bus>&<session>&<n>&<ADDR12>_C00000000\\
              Device Parameters\\PortName = COM5
  HKLM\\SYSTEM\\CurrentControlSet\\Services\\BTHPORT\\Parameters\\Devices\\
      <addr12, lowercase>\\Name = REG_BINARY (UTF-8 pairing-time device name)
  HKLM\\SYSTEM\\CurrentControlSet\\Enum\\BTHENUM\\Dev_<ADDR12>\\<instance>\\
      FriendlyName                          (fallback name source)

Outgoing ports (the PC initiates the link — the kind a braille display's
terminal mode needs) embed the remote device's address in the instance key.
Local listener ports use address 000000000000 and are skipped.
"""

from __future__ import annotations

from dataclasses import dataclass


SPP_SERVICE_CLASS = "{00001101-0000-1000-8000-00805f9b34fb}"
_ENUM_ROOT = r"SYSTEM\CurrentControlSet\Enum\BTHENUM"
_NAMES_ROOT = r"SYSTEM\CurrentControlSet\Services\BTHPORT\Parameters\Devices"


@dataclass(frozen=True)
class BluetoothSerialPort:
    device: str   # "COM5" — field name matches pyserial's ListPortInfo
    address: str  # remote Bluetooth address, 12 lowercase hex digits
    name: str     # paired device name, "" when unresolvable


class WinRegistry:
    """Minimal read-only HKLM view; injectable for tests."""

    def __init__(self):
        import winreg

        self._winreg = winreg

    def subkeys(self, path: str) -> tuple[str, ...]:
        winreg = self._winreg
        try:
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path)
        except OSError:
            return ()
        names = []
        with key:
            index = 0
            while True:
                try:
                    names.append(winreg.EnumKey(key, index))
                except OSError:
                    break
                index += 1
        return tuple(names)

    def value(self, path: str, name: str):
        winreg = self._winreg
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as key:
                data, _kind = winreg.QueryValueEx(key, name)
                return data
        except OSError:
            return None


def _instance_address(instance_key: str) -> str | None:
    """Remote address from an instance key like
    ``5&1a2b3c4&0&AABBCCDDEE01_C00000000``; None when the shape is foreign."""
    tail = instance_key.rsplit("&", 1)[-1]
    address = tail.split("_", 1)[0].lower()
    if len(address) != 12 or any(c not in "0123456789abcdef" for c in address):
        return None
    if address == "000000000000":
        return None  # local listener port, not a paired device
    return address


def _device_name(registry, address: str) -> str:
    raw = registry.value(f"{_NAMES_ROOT}\\{address}", "Name")
    if isinstance(raw, (bytes, bytearray)):
        try:
            name = bytes(raw).decode("utf-8", errors="replace").strip("\x00").strip()
        except Exception:
            name = ""
        if name:
            return name
    # Fallback: the BTHENUM device node's FriendlyName is the same paired name.
    dev_key = f"{_ENUM_ROOT}\\Dev_{address.upper()}"
    for instance in registry.subkeys(dev_key):
        friendly = registry.value(f"{dev_key}\\{instance}", "FriendlyName")
        if isinstance(friendly, str) and friendly.strip():
            return friendly.strip()
    return ""


def enumerate_spp_ports(registry=None) -> tuple[BluetoothSerialPort, ...]:
    """Every outgoing Bluetooth SPP COM port, with its paired device's name.

    Purely registry-based: safe to call on every discovery pass. The port
    existing does NOT mean the device is on or in range — opening the port
    is what starts the radio connection.
    """
    if registry is None:
        registry = WinRegistry()
    found = {}
    for service_key in registry.subkeys(_ENUM_ROOT):
        if not service_key.lower().startswith(SPP_SERVICE_CLASS):
            continue
        for instance in registry.subkeys(f"{_ENUM_ROOT}\\{service_key}"):
            address = _instance_address(instance)
            if address is None:
                continue
            port = registry.value(
                f"{_ENUM_ROOT}\\{service_key}\\{instance}\\Device Parameters",
                "PortName",
            )
            if not isinstance(port, str) or not port.strip():
                continue
            port = port.strip()
            if port not in found:
                found[port] = BluetoothSerialPort(
                    device=port,
                    address=address,
                    name=_device_name(registry, address),
                )
    return tuple(sorted(found.values(), key=lambda item: item.device))
