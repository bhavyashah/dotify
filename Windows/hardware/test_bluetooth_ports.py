import unittest

import bluetooth_ports

ENUM = r"SYSTEM\CurrentControlSet\Enum\BTHENUM"
NAMES = r"SYSTEM\CurrentControlSet\Services\BTHPORT\Parameters\Devices"
SPP = "{00001101-0000-1000-8000-00805f9b34fb}_LOCALMFG&000a"


class FakeRegistry:
    """Read-only registry double keyed exactly like WinRegistry's API."""

    def __init__(self, subkeys, values):
        self._subkeys = subkeys
        self._values = values

    def subkeys(self, path):
        return self._subkeys.get(path, ())

    def value(self, path, name):
        return self._values.get((path, name))


def registry_with_paired_displays():
    ereader = "5&abc&0&AABBCCDDEE01_C00000000"
    brailliant = "5&abc&0&AABBCCDDEE02_C00000000"
    listener = "5&abc&0&000000000000_C00000000"
    portless = "5&abc&0&AABBCCDDEE03_C00000000"
    return FakeRegistry(
        subkeys={
            ENUM: (SPP, "{0000110e-0000-1000-8000-00805f9b34fb}_VID&0001",
                   "Dev_AABBCCDDEE01", "Dev_AABBCCDDEE02"),
            f"{ENUM}\\{SPP}": (ereader, brailliant, listener, portless),
            f"{ENUM}\\Dev_AABBCCDDEE02": ("5&abc&0&BluetoothDevice_AABBCCDDEE02",),
        },
        values={
            (f"{ENUM}\\{SPP}\\{ereader}\\Device Parameters", "PortName"): "COM5",
            (f"{ENUM}\\{SPP}\\{brailliant}\\Device Parameters", "PortName"): "COM7",
            (f"{ENUM}\\{SPP}\\{listener}\\Device Parameters", "PortName"): "COM3",
            # The pairing-time name is REG_BINARY UTF-8, NUL-terminated.
            (f"{NAMES}\\aabbccddee01", "Name"): b"NLS eReader H1234\x00",
            # No BTHPORT name for the second unit: the Dev_ node's
            # FriendlyName is the fallback source.
            (
                f"{ENUM}\\Dev_AABBCCDDEE02\\5&abc&0&BluetoothDevice_AABBCCDDEE02",
                "FriendlyName",
            ): "Brailliant BI 40X 456789",
        },
    )


class BluetoothPortTests(unittest.TestCase):
    def test_outgoing_spp_ports_enumerate_with_paired_names(self):
        ports = bluetooth_ports.enumerate_spp_ports(registry_with_paired_displays())
        self.assertEqual(
            [
                ("COM5", "aabbccddee01", "NLS eReader H1234"),
                ("COM7", "aabbccddee02", "Brailliant BI 40X 456789"),
            ],
            [(port.device, port.address, port.name) for port in ports],
        )

    def test_local_listener_and_portless_instances_are_skipped(self):
        ports = bluetooth_ports.enumerate_spp_ports(registry_with_paired_displays())
        self.assertNotIn("COM3", [port.device for port in ports])
        self.assertNotIn("aabbccddee03", [port.address for port in ports])

    def test_non_spp_services_are_ignored(self):
        audio = "{0000110e-0000-1000-8000-00805f9b34fb}_VID&0001"
        instance = "5&abc&0&AABBCCDDEE09_C00000000"
        registry = FakeRegistry(
            subkeys={ENUM: (audio,), f"{ENUM}\\{audio}": (instance,)},
            values={
                (f"{ENUM}\\{audio}\\{instance}\\Device Parameters", "PortName"): "COM9",
            },
        )
        self.assertEqual((), bluetooth_ports.enumerate_spp_ports(registry))

    def test_unresolvable_name_degrades_to_empty_not_error(self):
        instance = "5&abc&0&AABBCCDDEE04_C00000000"
        registry = FakeRegistry(
            subkeys={ENUM: (SPP,), f"{ENUM}\\{SPP}": (instance,)},
            values={
                (f"{ENUM}\\{SPP}\\{instance}\\Device Parameters", "PortName"): "COM4",
            },
        )
        (port,) = bluetooth_ports.enumerate_spp_ports(registry)
        self.assertEqual(("COM4", ""), (port.device, port.name))

    def test_missing_bthenum_tree_yields_nothing(self):
        self.assertEqual(
            (), bluetooth_ports.enumerate_spp_ports(FakeRegistry({}, {}))
        )


if __name__ == "__main__":
    unittest.main()
