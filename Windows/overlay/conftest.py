"""Reproduce the installed image's import layout for the overlay tests.

Installed, every module sits in one "Text to Braille" folder: the overlay,
the hardware modules (hid_probe.py staged as windows_hid.py) and the braille
engine. The repo keeps them in three folders, so they all go on sys.path
here, with windows_hid as an alias of hid_probe.
"""

from pathlib import Path
import sys

HERE = Path(__file__).parent
REPO = HERE.parents[1]

for _path in (HERE, REPO / "Windows" / "hardware", REPO / "core" / "text-to-braille"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import hid_probe  # noqa: E402

sys.modules.setdefault("windows_hid", hid_probe)
