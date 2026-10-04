"""The Windows ticker: the braille engine's run.py plus the Windows pieces.

Adds the Windows display sinks and --display/--reconnect-attempts to the
command line, and starts the browser control bridge and the display cold
watch next to the engine's own controls.
"""

import os
import sys

import run as ticker

import display_profiles as profiles
from auto_display_sink import AutoDisplaySink
from control_bridge import ControlBridge
from hims_hid_sink import HimsHidSink
from native_hid_sink import NativeHidSink
from sink_reconnect import DEFAULT_RECONNECT_ATTEMPTS
from serial_sinks import (
    HimsBluetoothSink,
    HimsSerialSink,
    HumanWareBluetoothSink,
    HumanWareSerialSink,
)
from standard_hid_sink import StandardHidSink


_original_build_parser = ticker.build_parser
_original_build_sink = ticker.build_sink
_original_start_controls = ticker.start_controls
_display_profile_id = None
_reconnect_attempts = DEFAULT_RECONNECT_ATTEMPTS
# close() callables for the loopback services start_controls brings up,
# run at shutdown so their ports are released with the session.
_shutdown = []

# --sink names whose sink takes the --display profile id.
_PROFILE_SINKS = {
    "auto-display": AutoDisplaySink,
    "native-hid": NativeHidSink,
    "hims-hid": HimsHidSink,
    "humanware-serial": HumanWareSerialSink,
    "hims-serial": HimsSerialSink,
    "humanware-bluetooth": HumanWareBluetoothSink,
    "hims-bluetooth": HimsBluetoothSink,
}


def build_parser():
    parser = _original_build_parser()
    for action in parser._actions:
        if action.dest == "sink":
            action.choices = tuple(action.choices) + tuple(_PROFILE_SINKS) + (
                "standard-hid",
            )
            break
    native_choices = tuple(profile.id for profile in profiles.native_profiles())
    native_choices += tuple(profile.id for profile in profiles.bluetooth_profiles())
    parser.add_argument(
        "--display",
        choices=("auto", *native_choices),
        default=os.environ.get("DOTIFY_DISPLAY", "auto"),
        help="display profile, USB or Bluetooth (default: auto-detect "
             "exactly one; USB wins when both are available)",
    )
    # A malformed value must not stop the launch before the panel exists to
    # say why: warn and use the default.
    try:
        reconnect_default = int(
            os.environ.get("DOTIFY_RECONNECT_ATTEMPTS",
                           str(DEFAULT_RECONNECT_ATTEMPTS)).strip())
    except ValueError:
        print(
            "[warn] DOTIFY_RECONNECT_ATTEMPTS is not a whole number; "
            f"using the default of {DEFAULT_RECONNECT_ATTEMPTS}",
            file=sys.stderr, flush=True,
        )
        reconnect_default = DEFAULT_RECONNECT_ATTEMPTS
    parser.add_argument(
        "--reconnect-attempts",
        type=int,
        default=reconnect_default,
        help="quick reconnect attempts inside a failed display write "
             "before the longer recovery takes over (default: "
             f"{DEFAULT_RECONNECT_ATTEMPTS})",
    )
    original_parse_args = parser.parse_args

    def parse_args(args=None, namespace=None):
        parsed = original_parse_args(args, namespace)
        if parsed.reconnect_attempts < 1:
            parser.error("--reconnect-attempts must be at least 1")
        global _display_profile_id, _reconnect_attempts
        _display_profile_id = None if parsed.display == "auto" else parsed.display
        _reconnect_attempts = parsed.reconnect_attempts
        return parsed

    parser.parse_args = parse_args
    return parser


def build_sink(name, width):
    if name in _PROFILE_SINKS:
        return _PROFILE_SINKS[name](
            profile_id=_display_profile_id,
            reconnect_attempts=_reconnect_attempts,
        )
    if name == "standard-hid":
        # StandardHidSink pins a (vid, pid), not a profile id.
        identity = None
        if _display_profile_id:
            profile = profiles.get_profile(_display_profile_id)
            if profile.transport in profiles.BLUETOOTH_TRANSPORTS:
                raise RuntimeError(
                    f"{profile.name} is a Bluetooth profile; it has no USB "
                    "identity for the standard-hid sink"
                )
            identity = (profile.vid, profile.pid)
        return StandardHidSink(
            identity=identity,
            reconnect_attempts=_reconnect_attempts,
        )
    return _original_build_sink(name, width)


def start_controls(controls, stop_event):
    """Start the Windows services, then the engine's terminal controls."""
    bridge = ControlBridge(controls, stop_event).start()
    _shutdown.append(bridge.close)
    # Notices an unplugged display while nothing is being written. Optional:
    # without it a disconnect is still caught by the next write.
    engine_sink = getattr(controls.engine, "sink", None)
    if hasattr(engine_sink, "drop_cold"):
        try:
            from display_cold_watch import start_cold_watch
            watch, listener = start_cold_watch(
                controls.engine, stop_event, announce=controls.announce)
            _shutdown.extend((listener.stop, watch.stop))
        except Exception as error:
            print(f"[warn] display cold watch failed to start ({error}); "
                  "reconnect still works after the next write",
                  file=sys.stderr, flush=True)
    return _original_start_controls(controls, stop_event)


ticker.build_parser = build_parser
ticker.build_sink = build_sink
ticker.start_controls = start_controls


def close_services():
    while _shutdown:
        close = _shutdown.pop()
        try:
            close()
        except Exception as error:
            print(f"[warn] shutdown: {error}", file=sys.stderr, flush=True)


def main(argv=None):
    try:
        ticker.main(argv)
    finally:
        close_services()


if __name__ == "__main__":
    main()
