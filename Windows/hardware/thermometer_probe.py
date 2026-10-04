"""Timed physical acceptance sequence for the two reserved status cells.

Note: this probe drives the sink directly and writes a blank cell 2 in every
frame — that is the probe's own simplification so the gauge levels in cell 1
stand alone under a finger. In the shipping app cell 2 is the source marker
(`s` summary, `h` typed text; blank over live speech).
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time


HERE = Path(__file__).resolve().parent
if (HERE.name == "diagnostics"):
    APP = HERE.parent / "Text to Braille"
    HARDWARE = APP
    RUNTIME = APP
else:
    REPO_ROOT = HERE.parents[1]
    APP = REPO_ROOT / "core" / "text-to-braille"
    HARDWARE = HERE
    RUNTIME = HERE.parent / "overlay"
for path in (APP, HARDWARE, RUNTIME):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

if HERE.name != "diagnostics":
    # Source layout names the probe module hid_probe.py; production imports the
    # staged name windows_hid.py.
    import hid_probe

    sys.modules["windows_hid"] = hid_probe

from braille_engine.cells import BLANK  # noqa: E402
from braille_engine.gauge import BacklogGauge  # noqa: E402
from native_hid_sink import NativeHidSink  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--display", default="nls-ereader")
    parser.add_argument("--hold-seconds", type=float, default=4.0)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.hold_seconds <= 0:
        raise SystemExit("--hold-seconds must be positive")
    sink = NativeHidSink(profile_id=args.display, reconnect_attempts=12)
    width = sink.connect()
    if width < 4:
        raise RuntimeError("display is too narrow for two reserved status cells")
    gauge = BacklogGauge()
    cases = (
        (0, "blank"),
        (6, "dots 7-8"),
        (16, "dots 3-6-7-8"),
        (41, "dots 2-3-5-6-7-8"),
        (101, "all eight dots"),
        (0, "blank after jump-to-live"),
    )
    try:
        for words, label in cases:
            status = gauge.update(words)
            # A walking pattern after the two status cells makes the boundary
            # unmistakable by touch.
            content = [1 << (index % 8) for index in range(width - 2)]
            sink.write([status, BLANK, *content])
            print(
                f"words={words:3d}: cell 1 {label}; "
                f"cell 2 blank (probe only; the app shows s/h/a there); "
                f"holding {args.hold_seconds:.1f}s",
                flush=True,
            )
            time.sleep(args.hold_seconds)
        sink.write([BLANK] * width)
    finally:
        sink.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
