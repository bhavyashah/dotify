"""Reading density: space/punctuation dwell, lowercase filter, digits filter.

Four levers that raise words-per-minute throughput on a one-cell ticker
without dropping a single word — a braille reader takes in ~50-80 wpm
against speech at up to ~150, and every discounted boundary cell narrows
that gap while keeping the transcript raw.
"""

from braille_engine.controls import PacerControls, reading_wpm
from braille_engine.engine import BrailleEngine, DWELL_PUNCT, DWELL_TEXT
from braille_engine.filters import digits
from braille_engine.sinks.simulated import SimulatedSink
from braille_engine.translator import DevUebTranslator


def make_engine(width=40, window=1):
    engine = BrailleEngine(DevUebTranslator(),
                           SimulatedSink(width=width, echo=False),
                           window=window)
    engine.start()
    return engine


def drain_costs(engine):
    """Tick to empty, returning last_tick_cost per tick."""
    costs = []
    while engine.tick():
        costs.append(engine.last_tick_cost)
    return costs


# ---- digits filter (pure function) -----------------------------------------

def test_digits_basic_cardinals():
    assert digits("we have twenty five people") == "we have 25 people"
    assert digits("three hundred thousand") == "300000"
    assert digits("two thousand five hundred") == "2500"
    assert digits("seven hundred and fifty dollars") == "750 dollars"
    assert digits("zero chance") == "0 chance"


def test_digits_standalone_one_is_left_alone():
    # "one" is usually a pronoun; converting it misreads "one of them".
    assert digits("one of them said no") == "one of them said no"
    assert digits("no one was there") == "no one was there"
    # Multi-word groups starting with one still convert.
    assert digits("one hundred and one") == "101"


def test_digits_years_fuse_across_group_breaks():
    assert digits("twenty twenty six was the year") == "2026 was the year"
    assert digits("nineteen eighty four") == "1984"
    assert digits("ten sixty six battle") == "1066 battle"
    # CB slang is not the year 1004: the second half must be two digits.
    assert digits("ten four good buddy") == "10 4 good buddy"


def test_digits_big_scales_stay_words():
    # "5 million" costs fewer cells than "5000000".
    assert digits("five million dollars") == "5 million dollars"
    assert digits("about three billion stars") == "about 3 billion stars"
    # An article is not a count: untouched.
    assert digits("a million reasons") == "a million reasons"


def test_digits_keeps_punctuation_and_case_context():
    assert digits("Twenty-five, then thirty.") == "25, then 30."
    assert digits("One of us") == "One of us"          # guard keeps case
    assert digits("She said, 'forty two'") == "She said, '42'"


def test_digits_and_before_a_quoted_number_is_kept():
    # The quoted number cannot join the group, so "and" must survive as a
    # word instead of being swallowed as glue.
    assert digits('one hundred and "five" people') ==         '100 and "5" people'
    assert digits("one hundred and (five)") == "100 and (5)"


def test_digits_lists_do_not_sum():
    assert digits("one and two") == "one and 2"
    assert digits("call five five five") == "call 5 5 5"


def test_digits_plain_text_untouched():
    text = "plain text with no numbers at all"
    assert digits(text) == text
    assert digits("") == ""


def test_digits_is_idempotent():
    once = digits("twenty five people and one hundred dogs")
    assert digits(once) == once


# ---- engine ingest: digits filter on speech only ----------------------------

def test_engine_rewrites_speech_numbers_by_default():
    engine = make_engine()
    engine.feed("twenty five people ", seg="s1")
    assert engine.segment_text("s1") == "25 people"


def test_engine_digits_filter_can_be_disabled():
    engine = make_engine()
    engine.set_digits_filter(False)
    engine.feed("twenty five people ", seg="s1")
    assert engine.segment_text("s1") == "twenty five people"


def test_typed_text_is_never_rewritten():
    # Untagged feeds are the reader's own typing (Human mode) or file
    # replay — their author chose the form.
    engine = make_engine()
    engine.feed("twenty five ")
    words = [t[1] for t in engine._tokens if t[0] == "word"]
    assert words == ["twenty", "five"]


def test_revision_applies_the_same_rewrite_as_feed():
    # Partial and final must splice in the same shape.
    engine = make_engine()
    engine.feed("twenty five ", seg="s1", final=False)
    engine.revise_segment("s1", "twenty five people", final=True)
    assert engine.segment_text("s1") == "25 people"


# ---- dwell pricing ----------------------------------------------------------

def test_space_cell_costs_the_space_dwell():
    engine = make_engine()
    assert engine.set_space_dwell(25) is True
    engine.feed("ab cd ")
    costs = drain_costs(engine)
    # a, b, space, c, d, space -> the two space ticks cost 0.25.
    assert costs == [1.0, 1.0, 0.25, 1.0, 1.0, 0.25]


def test_punctuation_cell_costs_the_punct_dwell():
    engine = make_engine()
    assert engine.set_punct_dwell(50) is True
    engine.feed("ab. ")
    costs = drain_costs(engine)
    # a, b, ., space -> the period tick costs 0.5, the space full price.
    assert costs == [1.0, 1.0, 0.5, 1.0]


def test_dwell_defaults_are_full_price():
    engine = make_engine()
    engine.feed("a. b ")
    assert all(cost == 1.0 for cost in drain_costs(engine))


def test_dwell_validation_rejects_out_of_range():
    engine = make_engine()
    for bad in (0, 101, -5, None, "fast"):
        assert engine.set_space_dwell(bad) is False
        assert engine.set_punct_dwell(bad) is False
    assert engine.space_dwell == 1.0
    assert engine.punct_dwell == 1.0
    assert engine.set_space_dwell("40") is True      # panel sends strings
    assert engine.space_dwell == 0.4


def test_windowed_tick_sums_mixed_dwell():
    engine = make_engine(window=4)
    engine.set_space_dwell(50)
    engine.feed("ab cd ")
    engine.tick()          # a, b, space, c in one refresh
    assert engine.last_tick_cost == 1.0 + 1.0 + 0.5 + 1.0
    engine.tick()          # d, space
    assert engine.last_tick_cost == 1.0 + 0.5


def test_paused_tick_reports_zero_cost():
    engine = make_engine()
    engine.feed("ab ")
    engine.set_paused(True)
    assert engine.tick() == 0
    assert engine.last_tick_cost == 0.0


# ---- lowercase filter --------------------------------------------------------

def cap_indicator_count(engine, text):
    engine.feed(text)
    total = 0
    while engine.tick():
        total += 1
    return total


def test_lowercase_drops_capital_indicator_cells():
    plain = make_engine()
    lowered = make_engine()
    lowered.set_lowercase(True)
    # Dev translator: "Hi" is cap-indicator + h + i (3 cells) uppercase,
    # h + i (2 cells) lowered. Plus the trailing space cell.
    assert cap_indicator_count(plain, "Hi ") == 4
    assert cap_indicator_count(lowered, "Hi ") == 3


def test_lowercase_is_display_only():
    # The transcript (segment_text) keeps its capitals; only cells change.
    engine = make_engine()
    engine.set_lowercase(True)
    engine.feed("Hello There ", seg="s1")
    assert engine.segment_text("s1") == "Hello There"


def test_lowercase_default_off():
    assert make_engine().lowercase is False


# ---- units keep their dwell class through the emit queue ---------------------

def test_translate_units_tags_punctuation():
    engine = make_engine()
    kinds = [kind for _, kind in engine._translate_units("a.")]
    assert kinds == [DWELL_TEXT, DWELL_PUNCT]


def test_dev_translator_tagged_units_flatten_to_translate():
    t = DevUebTranslator()
    for token in ("Hi", "12", "a.", "He110"):
        flat = [c for u, _ in t.translate_units_tagged(token) for c in u]
        assert flat == t.translate(token)


# ---- PacerControls relays ----------------------------------------------------

def test_controls_setters_relay_to_engine():
    engine = make_engine()
    controls = PacerControls(engine, {"v": 0.1})
    assert controls.set_space_time(30) is True
    assert controls.set_punct_time(60) is True
    controls.set_lowercase(True)
    assert engine.space_dwell == 0.3
    assert engine.punct_dwell == 0.6
    assert engine.lowercase is True
    assert controls.set_space_time(0) is False
    assert engine.space_dwell == 0.3


# ---- measured wpm estimate ---------------------------------------------------
#
# The on-screen number must price a word the way the pace loop actually
# sleeps: measured cells of the real stream (contractions, indicators,
# punctuation) at the live dwell discounts — not just the grade constant.

def stream(engine, text):
    engine.feed(text)
    while engine.tick():
        pass


def test_wpm_falls_back_to_grade_constant_before_enough_words():
    engine = make_engine()
    # Nothing streamed yet: static estimate (dev translator is grade 1).
    assert engine.reading_cost_per_word() is None
    assert reading_wpm(0.6, 1, engine=engine) == 17          # 60/(0.6*6.0)
    stream(engine, "ab ab ab ")                # 3 words < DENSITY_MIN_WORDS
    assert engine.reading_cost_per_word() is None
    assert reading_wpm(0.6, 1, engine=engine) == 17


def test_wpm_measures_the_real_stream():
    engine = make_engine()
    stream(engine, "ab ab ab ab ab ab ab ab ")   # 2 text cells + 1 space each
    assert engine.reading_cost_per_word() == 3.0
    assert reading_wpm(0.1, 1, engine=engine) == 200         # 60/(0.1*3.0)


def test_measured_wpm_reprices_on_a_live_dwell_change():
    engine = make_engine()
    stream(engine, "ab ab ab ab ab ab ab ab ")
    engine.set_space_dwell(50)      # history re-priced at read time
    assert engine.reading_cost_per_word() == 2.5
    assert reading_wpm(0.1, 1, engine=engine) == 240


def test_fallback_estimate_honours_the_space_dwell():
    engine = make_engine()
    engine.set_space_dwell(50)      # nothing streamed: 5 letters + 0.5 space
    assert reading_wpm(0.1, 1, engine=engine) == round(60 / (0.1 * 5.5))


def test_measured_punct_cells_price_at_the_punct_dwell():
    engine = make_engine()
    engine.set_punct_dwell(25)
    stream(engine, "ab. ab. ab. ab. ab. ab. ab. ab. ")
    # 2 text + 1 punct (at 0.25) + 1 space per word.
    assert engine.reading_cost_per_word() == 3.25


def test_lowercase_toggle_rebases_the_measurement():
    engine = make_engine()
    stream(engine, "Ab Ab Ab Ab Ab Ab Ab Ab ")   # cap indicator: 3 text cells
    assert engine.reading_cost_per_word() == 4.0
    engine.set_lowercase(True)
    assert engine.reading_cost_per_word() is None   # history cleared
    stream(engine, "Ab Ab Ab Ab Ab Ab Ab Ab ")      # no indicator now
    assert engine.reading_cost_per_word() == 3.0


def test_digits_filter_toggle_rebases_the_measurement():
    engine = make_engine()                    # digits filter defaults on
    stream(engine, "ab ab ab ab ab ab ab ab ")
    assert engine.reading_cost_per_word() == 3.0
    engine.set_digits_filter(True)            # no change: history kept
    assert engine.reading_cost_per_word() == 3.0
    engine.set_digits_filter(False)           # numeral form changes: rebased
    assert engine.reading_cost_per_word() is None   # history cleared


def test_grade_change_rebases_the_measurement():
    engine = make_engine()
    controls = PacerControls(engine, {"v": 0.1})
    stream(engine, "ab ab ab ab ab ab ab ab ")
    assert engine.reading_cost_per_word() is not None
    engine.translator.set_grade = lambda target: True
    controls.set_grade(2)
    assert engine.reading_cost_per_word() is None
