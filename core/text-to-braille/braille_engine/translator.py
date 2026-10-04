"""Text -> braille cell translation.

Three implementations behind one ``translate(token) -> list[int]`` interface:

* ``LiblouisTranslator`` — UEB grades 1 and 2 plus experimental English
  grade 3 via the ``louis`` Python module, switchable at runtime with
  ``set_grade`` (Linux/WSL).
* ``LiblouisDllTranslator`` — the same real liblouis on Windows, loaded from
  the bundled ``liblouis.dll`` via ctypes when the ``louis`` module is absent.
* ``DevUebTranslator`` — a pure-Python grade-1 UEB stand-in (letters,
  capital and numeric indicators, common punctuation), used only when
  liblouis is unavailable so the engine runs and is testable anywhere.

Translation is word-at-a-time, at emission: a runtime grade switch affects
every word translated after it, and cells already shown never change.

Every translator also offers ``translate_units(token) -> list[list[int]]``: the
same cells, grouped into *units* the windowed ticker should not split across a
refresh — a two-cell contraction, or an indicator (capital, numeric, grade-1)
with the cell it governs. liblouis exposes this via its output-to-input
position map: consecutive output cells that map to the same input position
belong to one sign. Flattening the units always reproduces ``translate``.

``back_translate(cells) -> str`` is the reverse direction, for the reply
channel: cell patterns the reader TYPED on the display's own dot keys become
print text. The experimental grade-3 table is forward-only, so reply input
uses grade 2 while grade 3 is selected for output.

A liblouis failure raises ``TranslationError``. It deliberately does not
subclass ``RuntimeError``: the sink contract reserves ``OSError`` and
``RuntimeError`` for display failures, which the pacer answers by
reconnecting the display.
"""

from .cells import BLANK, BRAILLE_BASE, dots_to_pattern


class TranslationError(Exception):
    """liblouis could not translate a token (damaged table, bad input)."""


def _cells_from_unicode(braille: str) -> list:
    """Unicode braille (liblouis's ``unicode.dis`` output) -> cell patterns;
    anything outside the braille block renders blank."""
    cells = []
    for ch in braille:
        pattern = ord(ch) - BRAILLE_BASE
        cells.append(pattern if 0 <= pattern <= 0xFF else BLANK)
    return cells


def _unicode_from_cells(cells) -> str:
    return "".join(chr(BRAILLE_BASE + (c & 0xFF)) for c in cells)


# liblouis table list per grade. unicode.dis forces braille-Unicode output so
# the dot patterns can be read back.
_GRADE_TABLES = {
    1: ["unicode.dis", "en-ueb-g1.ctb"],
    2: ["unicode.dis", "en-ueb-g2.ctb"],
    3: ["unicode.dis", "en-g3.ctb"],
}

# Grade-1 braille letter patterns.
_LETTERS = {
    "a": "1", "b": "12", "c": "14", "d": "145", "e": "15",
    "f": "124", "g": "1245", "h": "125", "i": "24", "j": "245",
    "k": "13", "l": "123", "m": "134", "n": "1345", "o": "135",
    "p": "1234", "q": "12345", "r": "1235", "s": "234", "t": "2345",
    "u": "136", "v": "1236", "w": "2456", "x": "1346", "y": "13456", "z": "1356",
}
_LETTER_CELLS = {c: dots_to_pattern(d) for c, d in _LETTERS.items()}

# Digits 1..9,0 reuse the a..j patterns, preceded by the numeric indicator.
_DIGITS = {
    "1": "a", "2": "b", "3": "c", "4": "d", "5": "e",
    "6": "f", "7": "g", "8": "h", "9": "i", "0": "j",
}

_CAP_INDICATOR = dots_to_pattern("6")       # dot 6
_NUM_INDICATOR = dots_to_pattern("3456")    # dots 3-4-5-6

# Single-cell UEB punctuation the dev fallback supports. Multi-cell marks
# (brackets, quotes) are left to liblouis; unknown chars render blank.
_PUNCT = {
    ".": dots_to_pattern("256"),
    ",": dots_to_pattern("2"),
    ";": dots_to_pattern("23"),
    ":": dots_to_pattern("25"),
    "?": dots_to_pattern("236"),
    "!": dots_to_pattern("235"),
    "'": dots_to_pattern("3"),
    "-": dots_to_pattern("36"),
}

# Reverse maps for the dev translator's back_translate (reply channel).
_CELL_TO_LETTER = {v: k for k, v in _LETTER_CELLS.items()}
_CELL_TO_PUNCT = {v: k for k, v in _PUNCT.items()}
_LETTER_TO_DIGIT = {v: k for k, v in _DIGITS.items()}


def _units_from_positions(cells, positions, token=""):
    """Group cells into units by liblouis's output->input position map:
    consecutive output cells mapping to the same input position are one sign
    (a multi-cell contraction, or an indicator + the cell it governs). A
    malformed map degrades to single-cell units, never to lost cells.

    Returns ``[(unit, source_char)]`` — the input character each unit came
    from (None when the map is malformed or a position is out of range).
    The source char lets the pacer give punctuation cells their own dwell
    (reading density) without guessing from dot patterns."""
    if len(positions) != len(cells):
        return [([c], None) for c in cells]
    units = []
    for cell, pos in zip(cells, positions):
        if units and pos == units[-1][1]:
            units[-1][0].append(cell)
        else:
            units.append(([cell], pos))
    return [(unit, token[pos] if 0 <= pos < len(token) else None)
            for unit, pos in units]


class DevUebTranslator:
    """Pure-Python grade-1 UEB stand-in. No native dependencies."""

    def __init__(self):
        self.grade = 1

    def set_grade(self, grade: int) -> bool:
        """Only grade 1 is implemented; anything else is refused."""
        return grade == 1

    def translate_units_tagged(self, token: str) -> list:
        """``[(unit, source_char)]`` — same units as ``translate_units``,
        each tagged with the input character it renders."""
        units = []
        in_number = False
        for ch in token:
            if ch in _DIGITS:
                if not in_number:
                    # Indicator + first digit is one unit; later digits stand
                    # alone, matching the position-map grouping liblouis gives.
                    units.append(([_NUM_INDICATOR, _LETTER_CELLS[_DIGITS[ch]]],
                                  ch))
                    in_number = True
                else:
                    units.append(([_LETTER_CELLS[_DIGITS[ch]]], ch))
                continue
            in_number = False
            if ch.isupper() and ch.lower() in _LETTER_CELLS:
                units.append(([_CAP_INDICATOR, _LETTER_CELLS[ch.lower()]], ch))
            elif ch in _LETTER_CELLS:
                units.append(([_LETTER_CELLS[ch]], ch))
            elif ch in _PUNCT:
                units.append(([_PUNCT[ch]], ch))
            else:
                units.append(([BLANK], ch))
        return units

    def translate_units(self, token: str) -> list:
        return [unit for unit, _ in self.translate_units_tagged(token)]

    def translate(self, token: str) -> list:
        return [cell for unit in self.translate_units(token) for cell in unit]

    def back_translate(self, cells) -> str:
        """Typed cells -> print text (grade 1). Inverts the same maps
        translate_units_tagged builds from, with the two indicator state
        machines run in reverse; unknown patterns are dropped rather than
        guessed."""
        out = []
        number = False
        capital = False
        for cell in cells:
            if cell == BLANK:
                out.append(" ")
                number = capital = False
                continue
            if cell == _NUM_INDICATOR:
                number = True
                continue
            if cell == _CAP_INDICATOR:
                capital = True
                number = False
                continue
            letter = _CELL_TO_LETTER.get(cell)
            if number and letter in _LETTER_TO_DIGIT:
                out.append(_LETTER_TO_DIGIT[letter])
                continue
            number = False
            if letter is not None:
                out.append(letter.upper() if capital else letter)
            elif cell in _CELL_TO_PUNCT:
                out.append(_CELL_TO_PUNCT[cell])
            capital = False
        return "".join(out)


class LiblouisTranslator:
    """Real English translation via liblouis, runtime-switchable.

    Grades 1 and 2 are UEB. Grade 3 uses liblouis' experimental,
    non-official, forward-only English table, which is based on older
    English Grade 2 rather than UEB.
    """

    GRADE_TABLES = _GRADE_TABLES

    def __init__(self, grade: int = 1):
        import louis  # raises if liblouis is unavailable
        self._louis = louis
        self.grade = grade
        self.tables = self.GRADE_TABLES[grade]

    def set_grade(self, grade: int) -> bool:
        """Switch tables; affects only words translated from now on."""
        if grade not in self.GRADE_TABLES:
            return False
        self.grade = grade
        self.tables = self.GRADE_TABLES[grade]
        return True

    def translate(self, token: str) -> list:
        try:
            out = self._louis.translateString(self.tables, token)
        except Exception as error:
            raise TranslationError(
                f"liblouis translation failed for {token!r}: {error}"
            ) from error
        return _cells_from_unicode(out)

    def translate_units(self, token: str) -> list:
        """Cells grouped into unsplittable signs via the position map that
        ``louis.translate`` returns alongside the translation."""
        return [unit for unit, _ in self.translate_units_tagged(token)]

    def translate_units_tagged(self, token: str) -> list:
        """``[(unit, source_char)]`` — units plus the input character each
        one renders, from the same position map."""
        try:
            out, braille_to_text, _, _ = self._louis.translate(
                self.tables, token)
        except Exception:
            return [([c], None) for c in self.translate(token)]
        return _units_from_positions(_cells_from_unicode(out),
                                     list(braille_to_text), token)

    def back_translate(self, cells) -> str:
        """Typed cells -> print text for the reply channel.

        Grade 3 is explicitly forward-only upstream, so use Grade 2 for
        input while keeping Grade 3 selected for all forward translation.
        A failure returns "" and the caller falls back.
        """
        braille = _unicode_from_cells(cells)
        if not braille:
            return ""
        try:
            tables = self.GRADE_TABLES[2] if self.grade == 3 else self.tables
            return self._louis.backTranslateString(tables, braille)
        except Exception:
            return ""


def _default_liblouis_dir():
    """Bundled liblouis location: env override, else the dev-checkout vendor dir."""
    import os
    from pathlib import Path
    env = os.environ.get("DOTIFY_LIBLOUIS_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[3] / "Windows" / "installer" / "vendor" / "liblouis"


class LiblouisDllTranslator:
    """UEB via the bundled liblouis.dll (Windows) — the ctypes twin of
    LiblouisTranslator, used when the ``louis`` Python module is absent."""

    GRADE_TABLES = _GRADE_TABLES

    def __init__(self, grade: int = 1, lib=None, tables_dir=None):
        if lib is None:
            import ctypes
            base = _default_liblouis_dir()
            dll = base / "bin" / "liblouis.dll"
            if not dll.exists():
                raise FileNotFoundError(dll)
            lib = ctypes.CDLL(str(dll))
            tables_dir = base / "share" / "liblouis" / "tables"
        self._lib = lib
        self._tables_dir = tables_dir
        # liblouis widechar is UTF-16 or UTF-32 depending on build.
        self._charsize = int(lib.lou_charSize())
        self.grade = grade
        self.tables = self._table_list(grade)

    def _table_list(self, grade: int) -> str:
        from pathlib import Path
        return ",".join(
            str(Path(self._tables_dir) / n) for n in self.GRADE_TABLES[grade]
        )

    def set_grade(self, grade: int) -> bool:
        """Switch tables; affects only words translated from now on."""
        if grade not in self.GRADE_TABLES:
            return False
        self.grade = grade
        self.tables = self._table_list(grade)
        return True

    def _call(self, name, tables: str, text: str, with_positions=False):
        """One liblouis string call (forward or back): returns ``(output,
        positions)``, positions being None unless asked for."""
        import ctypes
        enc = "utf-16-le" if self._charsize == 2 else "utf-32-le"
        inbuf = text.encode(enc)
        # inlen counts widechars (UTF-16/32 code units), not code points: an
        # astral character is two UTF-16 units.
        inlen = ctypes.c_int(len(inbuf) // self._charsize)
        # Back-translation expands (one grade-2 cell can be a whole word);
        # 8x headroom covers both directions.
        cap = len(text) * 8 + 8
        outbuf = ctypes.create_string_buffer(cap * self._charsize)
        outlen = ctypes.c_int(cap)
        args = [tables.encode("utf-8"), inbuf, ctypes.byref(inlen),
                outbuf, ctypes.byref(outlen), None, None]
        inpos = None
        if with_positions:
            inpos = (ctypes.c_int * cap)()
            args += [None, inpos, None]
        if not getattr(self._lib, name)(*args, 0):
            raise TranslationError(
                f"liblouis DLL translation failed for {text!r}")
        out = outbuf.raw[: outlen.value * self._charsize].decode(enc)
        positions = list(inpos[: outlen.value]) if with_positions else None
        return out, positions

    def translate(self, token: str) -> list:
        out, _ = self._call("lou_translateString", self.tables, token)
        return _cells_from_unicode(out)

    def translate_units(self, token: str) -> list:
        return [unit for unit, _ in self.translate_units_tagged(token)]

    def translate_units_tagged(self, token: str) -> list:
        """``[(unit, source_char)]`` — see LiblouisTranslator."""
        try:
            out, positions = self._call("lou_translate",
                                        self.tables, token,
                                        with_positions=True)
        except Exception:
            return [([c], None) for c in self.translate(token)]
        return _units_from_positions(_cells_from_unicode(out), positions,
                                     token)

    def back_translate(self, cells) -> str:
        """Typed cells -> print text for the reply channel; "" on failure.
        Grade 3 reads input with the grade-2 table (grade 3 is
        forward-only upstream)."""
        braille = _unicode_from_cells(cells)
        if not braille:
            return ""
        tables = self._table_list(2) if self.grade == 3 else self.tables
        try:
            out, _ = self._call("lou_backTranslateString", tables,
                                braille)
        except Exception:
            return ""
        return out


def get_translator(prefer: str = "auto", grade: int = 1):
    """Return ``(translator, name)``.

    ``auto`` tries the liblouis Python module, then the bundled Windows DLL,
    then the dev fallback. ``liblouis`` / ``dev`` force a backend (liblouis
    tries module then DLL). ``grade`` sets the starting grade (grades 2 and
    3 require one of the liblouis backends).
    """
    if prefer == "dev":
        return DevUebTranslator(), "dev"
    if prefer == "liblouis":
        try:
            return LiblouisTranslator(grade=grade), "liblouis"
        except Exception:
            return LiblouisDllTranslator(grade=grade), "liblouis"
    # auto
    try:
        return LiblouisTranslator(grade=grade), "liblouis"
    except Exception:
        pass
    try:
        return LiblouisDllTranslator(grade=grade), "liblouis"
    except Exception:
        return DevUebTranslator(), "dev"
