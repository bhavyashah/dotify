"""LiblouisDllTranslator — the ctypes twin of LiblouisTranslator (Windows).

Unit tests fake the DLL surface; the live tests (skipped when the vendored
DLL is absent) assert cell-for-cell parity with the WSL liblouis reference.
"""

import json
import sys
from pathlib import Path

import pytest

from braille_engine.translator import (
    LiblouisDllTranslator,
    get_translator,
)

VENDOR = Path(__file__).resolve().parents[3] / "Windows" / "installer" / "vendor"
DLL = VENDOR / "liblouis" / "bin" / "liblouis.dll"
REF = VENDOR / "reference_g2.json"


class FakeLib:
    def lou_charSize(self):
        return 2


def make(grade=1):
    return LiblouisDllTranslator(grade=grade, lib=FakeLib(), tables_dir=Path("T"))


def test_tables_follow_grade():
    t = make(grade=1)
    assert "en-ueb-g1.ctb" in t.tables and "unicode.dis" in t.tables
    assert t.set_grade(2) is True
    assert "en-ueb-g2.ctb" in t.tables and t.grade == 2
    assert t.set_grade(3) is True
    assert "en-g3.ctb" in t.tables and t.grade == 3
    assert t.set_grade(7) is False and t.grade == 3


def test_grade3_reply_input_uses_grade2_because_grade3_is_forward_only():
    class BackLib(FakeLib):
        def __init__(self):
            self.tables = None

        def lou_backTranslateString(self, tables, *_args):
            self.tables = tables.decode("utf-8")
            return 0

    lib = BackLib()
    t = LiblouisDllTranslator(grade=3, lib=lib, tables_dir=Path("T"))
    assert t.back_translate([1]) == ""
    assert "en-ueb-g2.ctb" in lib.tables
    assert "en-g3.ctb" not in lib.tables
    assert t.grade == 3


def test_translate_decodes_unicode_braille(monkeypatch):
    t = make()
    monkeypatch.setattr(t, "_call", lambda *a, **k: ("⠁⠃", None))
    assert t.translate("ab") == [0x01, 0x03]


def test_translate_maps_non_braille_to_blank(monkeypatch):
    t = make()
    monkeypatch.setattr(t, "_call", lambda *a, **k: ("x", None))
    assert t.translate("x") == [0]


@pytest.mark.skipif(
    sys.platform != "win32" or not (DLL.exists() and REF.exists()),
    reason="windows + vendored DLL only",
)
def test_live_dll_matches_wsl_reference():
    t = LiblouisDllTranslator(grade=2)
    ref = json.loads(REF.read_text())
    for word, cells in ref.items():
        assert t.translate(word) == cells, word


@pytest.mark.skipif(
    sys.platform != "win32" or not DLL.exists(), reason="windows+dll only"
)
def test_auto_prefers_dll_over_dev_on_windows():
    t, name = get_translator("auto", grade=2)
    assert name == "liblouis" and t.grade == 2


@pytest.mark.skipif(
    sys.platform != "win32" or not DLL.exists(), reason="windows+dll only"
)
def test_live_dll_units_flatten_to_translate_and_group_two_cell_signs():
    t = LiblouisDllTranslator(grade=2)
    # Flattened units must reproduce translate() exactly.
    for word in ("about", "ounce", "Nation", "lesson", "123", "the"):
        units = t.translate_units(word)
        assert [c for u in units for c in u] == t.translate(word), word
    # Grade-2 "nation" uses the two-cell "ation" groupsign (dot 6 + n),
    # which must come back as a single unit.
    units = t.translate_units("nation")
    assert any(len(u) > 1 for u in units), units
