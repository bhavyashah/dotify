"""Bluetooth SPP diagnostics for paired braille displays.

Mirrors hid_probe's role for the Bluetooth transport: ``list`` is read-only;
``write`` and ``keys`` open the port (which dials the radio link) and drive
the same sinks the appliance uses, so a passing probe is evidence for the
shipping path, not a parallel implementation.

Run with a Python that has pyserial (the staged runtime does):

    runtime\\python\\python.exe Windows\\hardware\\bluetooth_probe.py list
    ...\\python.exe Windows\\hardware\\bluetooth_probe.py write --pattern alternating
    ...\\python.exe Windows\\hardware\\bluetooth_probe.py keys --seconds 30
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
# The sinks live in ../overlay in the repo; in a flat copy everything sits
# beside this file. hid_probe is the module the installed image calls
# windows_hid, and serial_sinks imports it under that name.
sys.path[:0] = [str(HERE), str(HERE.parent / "overlay")]
try:
    import hid_probe as windows_hid
except ImportError:
    import windows_hid
sys.modules.setdefault("windows_hid", windows_hid)

import bluetooth_ports  # noqa: E402
import display_profiles as profiles  # noqa: E402
import serial_sinks  # noqa: E402


def cmd_list(_args):
    ports = bluetooth_ports.enumerate_spp_ports()
    if not ports:
        print("no outgoing Bluetooth SPP ports. Pair the display in Windows "
              "Settings > Bluetooth & devices and enable its Bluetooth "
              "terminal connection.")
        return 2
    # Match through the shipping discovery, not a re-implementation — the
    # whole point of `list` is to explain what the appliance will do.
    matched = {
        port.device: profile.id
        for profile, port in serial_sinks.enumerate_bluetooth_displays(
            lambda: ports
        )
    }
    for port in ports:
        print(f"{port.device}  address={port.address}  "
              f"name={port.name!r}  profile={matched.get(port.device, '-')}")
    return 0 if matched else 2


def _sink():
    found = serial_sinks.enumerate_bluetooth_displays()
    if not found:
        raise SystemExit("no paired Bluetooth braille display matched a "
                         "registered profile; run the list subcommand first")
    transports = {profile.transport for profile, _port in found}
    if len(transports) > 1:
        # The appliance refuses this ambiguity; the probe must not guess a
        # different answer than the thing it diagnoses.
        raise SystemExit(
            "multiple paired Bluetooth braille display families match ("
            + ", ".join(f"{port.name} on {port.device}" for _p, port in found)
            + "); unpair the ones not in use"
        )
    if profiles.HUMANWARE_BLUETOOTH in transports:
        return serial_sinks.HumanWareBluetoothSink()
    return serial_sinks.HimsBluetoothSink()


def cmd_write(args):
    sink = _sink()
    print("connecting (the radio link may take a few seconds)...")
    width = sink.connect()
    print(f"connected: {sink.display_name} ({width} cells)")
    try:
        # Same pattern vocabulary as hid_probe, so a cross-transport visual
        # comparison on one display paints identical cells.
        sink.write(list(windows_hid.make_cells(args.pattern, width)))
        print(f"pattern {args.pattern} shown; holding {args.hold_seconds}s")
        time.sleep(args.hold_seconds)
        sink.write([0] * width)
        print("cleared")
    finally:
        sink.close()
    return 0


def cmd_keys(args):
    sink = _sink()
    starter = getattr(sink, "start_key_listener", None)
    if starter is None:
        print(f"{type(sink).__name__} is output-only; display key input is "
              "not implemented for this family yet")
        return 2
    print("connecting (the radio link may take a few seconds)...")
    width = sink.connect()
    print(f"connected: {sink.display_name} ({width} cells)")
    try:
        sink.write([0] * width)
        starter(
            lambda keys: print(f"keys: {sorted(keys) or '(released)'}",
                               flush=True)
        )
        print(f"press display keys for {args.seconds}s; every event prints")
        time.sleep(args.seconds)
    finally:
        sink.close()
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="read-only: paired SPP ports + matches")
    write = commands.add_parser("write", help="connect and show a test pattern")
    write.add_argument("--pattern",
                       choices=("blank", "full", "alternating", "walking"),
                       default="alternating")
    write.add_argument("--hold-seconds", type=float, default=5.0)
    keys = commands.add_parser("keys", help="connect and print key events")
    keys.add_argument("--seconds", type=float, default=30.0)
    args = parser.parse_args(argv)
    return {"list": cmd_list, "write": cmd_write, "keys": cmd_keys}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
