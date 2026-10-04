from braille_engine.cells import dots_to_pattern
from braille_engine.translator import DevUebTranslator, get_translator


def test_single_letter():
    t = DevUebTranslator()
    assert t.translate("a") == [dots_to_pattern("1")]


def test_capital_indicator():
    t = DevUebTranslator()
    assert t.translate("Hi") == [
        dots_to_pattern("6"),      # capital indicator
        dots_to_pattern("125"),    # h
        dots_to_pattern("24"),     # i
    ]


def test_numeric_indicator_once_per_run():
    t = DevUebTranslator()
    assert t.translate("12") == [
        dots_to_pattern("3456"),   # numeric indicator
        dots_to_pattern("1"),      # 1 -> a
        dots_to_pattern("12"),     # 2 -> b
    ]


def test_punctuation():
    t = DevUebTranslator()
    assert t.translate(".") == [dots_to_pattern("256")]


def test_unknown_char_is_blank():
    t = DevUebTranslator()
    assert t.translate("~") == [0]


def test_get_translator_dev():
    t, name = get_translator("dev")
    assert name == "dev"
    assert isinstance(t, DevUebTranslator)


def test_dev_refuses_grade_2():
    t = DevUebTranslator()
    assert t.grade == 1
    assert t.set_grade(2) is False
    assert t.grade == 1            # unchanged after refusal
    assert t.set_grade(1) is True


def test_units_capital_indicator_grouped_with_letter():
    t = DevUebTranslator()
    assert t.translate_units("Hi") == [
        [dots_to_pattern("6"), dots_to_pattern("125")],   # cap + h, one unit
        [dots_to_pattern("24")],                          # i
    ]


def test_units_numeric_indicator_grouped_with_first_digit():
    t = DevUebTranslator()
    assert t.translate_units("12") == [
        [dots_to_pattern("3456"), dots_to_pattern("1")],  # num + 1, one unit
        [dots_to_pattern("12")],                          # 2
    ]


def test_units_flatten_to_translate():
    t = DevUebTranslator()
    for token in ("Hi", "12", "a.", "He110"):
        flat = [c for u in t.translate_units(token) for c in u]
        assert flat == t.translate(token)


def test_units_from_positions_groups_equal_positions():
    from braille_engine.translator import _units_from_positions
    # cells 1,2 share input pos 0 (a two-cell sign); 3 stands alone.
    # Each unit carries the source character its position names.
    assert _units_from_positions([1, 2, 3], [0, 0, 1], "a.") \
        == [([1, 2], "a"), ([3], ".")]


def test_units_from_positions_bad_map_degrades_to_singles():
    from braille_engine.translator import _units_from_positions
    assert _units_from_positions([1, 2, 3], [0, 0]) \
        == [([1], None), ([2], None), ([3], None)]


def test_units_from_positions_out_of_range_position_tags_none():
    from braille_engine.translator import _units_from_positions
    assert _units_from_positions([1], [7], "ab") == [([1], None)]


def test_liblouis_grade_tables_defined():
    # Table selection is data, verifiable without liblouis installed.
    from braille_engine.translator import LiblouisTranslator
    assert LiblouisTranslator.GRADE_TABLES[1][-1] == "en-ueb-g1.ctb"
    assert LiblouisTranslator.GRADE_TABLES[2][-1] == "en-ueb-g2.ctb"
    assert LiblouisTranslator.GRADE_TABLES[3][-1] == "en-g3.ctb"


def test_liblouis_grade_switch_swaps_tables():
    # Bypass __init__ (which imports louis) to unit-test the switch logic.
    from braille_engine.translator import LiblouisTranslator
    t = LiblouisTranslator.__new__(LiblouisTranslator)
    t.grade, t.tables = 1, LiblouisTranslator.GRADE_TABLES[1]
    assert t.set_grade(2) is True
    assert t.grade == 2 and t.tables[-1] == "en-ueb-g2.ctb"
    assert t.set_grade(3) is True
    assert t.grade == 3 and t.tables[-1] == "en-g3.ctb"
    assert t.set_grade(7) is False      # unknown grade refused
    assert t.grade == 3                 # state intact after refusal
    assert t.set_grade(1) is True
    assert t.tables[-1] == "en-ueb-g1.ctb"


def test_grade3_reply_input_uses_grade2_because_grade3_is_forward_only():
    from braille_engine.translator import LiblouisTranslator

    class FakeLouis:
        def __init__(self):
            self.tables = None

        def backTranslateString(self, tables, _braille):
            self.tables = tables
            return "reply"

    t = LiblouisTranslator.__new__(LiblouisTranslator)
    t._louis = FakeLouis()
    t.grade = 3
    t.tables = LiblouisTranslator.GRADE_TABLES[3]
    assert t.back_translate([1]) == "reply"
    assert t._louis.tables == LiblouisTranslator.GRADE_TABLES[2]
    assert t.grade == 3
