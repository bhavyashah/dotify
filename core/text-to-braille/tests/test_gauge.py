"""Backlog gauge: cell 1 fills like a thermometer as the queue grows.

UX contract (ambient backlog gauge):
    caught up (<=5 words)   blank
    ~a sentence (6-15)      dots 7-8
    ~a paragraph (16-40)    dots 3-6-7-8
    far behind (41-100)     dots 2-3-5-6-7-8
    very far (>100)         all 8 dots
Levels change only at bucket boundaries, with hysteresis on the way down.
The gauge shows the distance between the text
under the fingers and the LIVE EDGE (pan depth counts; off the live edge it
floors at level 1 — see test_panning for the engine integration).
Cell 2 is the content-kind marker (s/h per the engine's marker contract;
these tests feed untagged text, which reads as typed — h); content uses the
remaining cells.
"""

from braille_engine.cells import BLANK, dots_to_pattern
from braille_engine.engine import TYPED_MARKER, BrailleEngine
from braille_engine.gauge import BacklogGauge
from braille_engine.sinks.simulated import SimulatedSink
from braille_engine.translator import DevUebTranslator

L1 = dots_to_pattern("78")
L2 = dots_to_pattern("3678")
L3 = dots_to_pattern("235678")
L4 = dots_to_pattern("12345678")


# --- unit: bucket mapping ---------------------------------------------------

def test_caught_up_is_blank():
    g = BacklogGauge()
    assert g.update(0) == BLANK
    assert g.update(5) == BLANK


def test_bucket_boundaries_rising():
    for words, pattern in [(6, L1), (15, L1), (16, L2), (40, L2),
                           (41, L3), (100, L3), (101, L4), (500, L4)]:
        g = BacklogGauge()
        assert g.update(words) == pattern, f"{words} words"


def test_semantic_level_uses_the_same_state_as_the_cell_pattern():
    g = BacklogGauge()
    expected = [(0, 0), (6, 1), (16, 2), (41, 3), (101, 4)]
    for words, level in expected:
        g.update(words)
        assert g.level == level

    # The exported level carries the tactile gauge's falling hysteresis too.
    g.update(100)
    assert g.level == 4
    g.update(98)
    assert g.level == 3


# --- unit: hysteresis -------------------------------------------------------

def test_small_dip_below_boundary_keeps_level():
    g = BacklogGauge()
    g.update(10)                 # level 1 (boundary at 6)
    assert g.update(5) == L1     # dip of 1 below boundary: no change
    assert g.update(4) == L1     # still within the hysteresis band
    assert g.update(3) == BLANK  # clearly below: drop


def test_rising_boundary_is_exact():
    g = BacklogGauge()
    assert g.update(5) == BLANK
    assert g.update(6) == L1     # rising uses the exact boundary


def test_catch_up_drains_multiple_levels_at_once():
    g = BacklogGauge()
    g.update(150)                # level 4
    assert g.update(0) == BLANK  # jump_to_live empties the queue instantly


# --- unit: the off-live floor ----------------------------------------------

def test_floor_holds_level_one_at_zero_words():
    # A panned view with no backlog at all must still read level 1: blank
    # gauge ⇔ at live and caught up is the hard invariant.
    g = BacklogGauge()
    assert g.update(0, floor=1) == L1
    assert g.level == 1
    assert g.update(0) == BLANK      # floor dropped (back at live): drains


def test_floor_never_lowers_a_real_count():
    g = BacklogGauge()
    assert g.update(20, floor=1) == L2   # the real count outranks the floor


def test_floor_clamps_to_the_top_level():
    g = BacklogGauge()
    assert g.update(0, floor=99) == L4


def test_peek_floor_does_not_mutate():
    g = BacklogGauge()
    assert g.peek(0, floor=1) == 1
    assert g.level == 0                  # nothing absorbed


# --- unit: read-only peek ---------------------------------------------------

def test_peek_does_not_mutate():
    g = BacklogGauge()
    assert g.peek(150) == 4      # would be level 4...
    assert g.level == 0          # ...but nothing was absorbed
    assert g.pattern == BLANK
    assert g.update(0) == BLANK  # and the next update starts from 0


def test_peek_applies_rising_and_hysteresis_rules():
    g = BacklogGauge()
    g.update(6)                  # absorb to level 1
    assert g.peek(5) == 1        # dip within the hysteresis band: holds
    assert g.peek(4) == 1        # still within the band
    assert g.peek(3) == 0        # clearly below: would drop
    assert g.peek(16) == 2       # rising uses the exact boundary
    assert g.level == 1          # none of those peeks moved the gauge


# --- engine integration -----------------------------------------------------

H = dots_to_pattern("125")
I = dots_to_pattern("24")


def make(width=7):
    eng = BrailleEngine(DevUebTranslator(),
                        SimulatedSink(width=width, echo=False),
                        gauge=BacklogGauge())
    eng.start()
    return eng


def test_frame_reserves_gauge_and_marker_cells():
    eng = make(width=7)
    eng.feed("hi ")
    eng.tick()
    # cells 1-2 reserved, content right-aligned in the remaining 5; the
    # marker cell reads t (untagged feed = typed).
    assert eng.frame() == [BLANK, TYPED_MARKER, BLANK, BLANK, BLANK, BLANK, H]
    assert len(eng.frame()) == 7


def test_gauge_cell_shows_backlog_level():
    eng = make(width=7)
    eng.feed("w " * 12)          # 12 words queued
    eng.tick()                   # one word loads for emission; ~11 remain
    frame = eng.sink.frames[-1]
    assert frame[0] == L1           # a sentence behind
    assert frame[1] == TYPED_MARKER  # marker names the content's source


def test_content_window_is_width_minus_two():
    eng = make(width=4)          # content window of 2
    eng.feed("hi ")
    eng.tick(); eng.tick()
    assert eng.frame() == [BLANK, TYPED_MARKER, H, I]


def test_jump_to_live_drains_gauge_to_blank():
    # A still-soft tail survives the jump (take_pending leaves hypothesis
    # queued) but must NOT hold the gauge above blank: soft words don't
    # count in backlog_words, and the snap resets the falling hysteresis.
    eng = make(width=7)
    eng.feed("w " * 30, seg="s1", final=True)
    eng.feed("s " * 5, seg="s2", final=False)
    eng.tick()
    assert eng.sink.frames[-1][0] == L2   # a paragraph behind
    eng.jump_to_live()
    assert eng.sink.frames[-1][0] == BLANK
    assert eng.backlog_words() == 0       # soft tail queued, not counted
    # ...and the tail re-enters the count the moment it hardens.
    eng.revise_segment("s2", "s " * 5, final=True)
    assert eng.backlog_words() == 5


def test_all_soft_queue_jump_still_blanks_gauge():
    # With ONLY soft text queued the jump captures nothing — but the gauge
    # must never have risen for unrenderable hypothesis, and the jump must
    # still read as caught up.
    eng = make(width=7)
    eng.feed("w " * 30, seg="s1", final=False)
    eng.tick()                            # nothing may render...
    assert eng.backlog_words() == 0       # ...and nothing counts
    eng.jump_to_live()
    assert eng.sink.frames[-1][0] == BLANK
    assert eng.backlog_words() == 0


def test_eager_mode_counts_confirmed_soft_words():
    # Eager renders soft words once a later same-segment word confirms
    # them (never the frontier word), so eager's honest backlog is
    # words-minus-frontier per soft run — and a jump still blanks the
    # gauge because the hysteresis reset lets the true count rule.
    eng = make(width=7)
    eng.set_eager(True)
    eng.feed("w " * 8, seg="s1", final=False)
    assert eng.backlog_words() == 7       # frontier word unconfirmed
    eng.revise_segment("s1", "w " * 8, final=True)
    assert eng.backlog_words() == 8       # hardened: all count


def test_engine_drops_gauge_when_display_too_narrow():
    # brlapi only reports width at connect time, so the engine itself must
    # refuse to spend 2 of e.g. 3 cells on the gauge
    eng = BrailleEngine(DevUebTranslator(),
                        SimulatedSink(width=3, echo=False),
                        gauge=BacklogGauge())
    eng.start()
    assert eng.gauge is None
    eng.feed("hi ")
    eng.tick()
    assert eng.frame() == [BLANK, BLANK, H]


# --- memoization ------------------------------------------------------------
# backlog_words is polled ~20x/s by the manual-mode hold loop, so it caches
# its count keyed on the engine's queue revision (_tokens_rev). A stale
# cached count would freeze the gauge, so these tests pin both directions:
# the cache is USED when nothing changed, and DROPPED on every mutation
# that can move the count.


def _count_origin_calls(eng):
    """Monkeypatch _origin_of on the instance to count invocations — a
    proxy for 'backlog_words walked the queue' (it calls _origin_of once
    per queued word token during a walk)."""
    calls = {"n": 0}
    real = eng._origin_of

    def counting(seg):
        calls["n"] += 1
        return real(seg)

    eng._origin_of = counting
    return calls


def test_backlog_memo_second_call_does_not_rewalk():
    eng = make(width=7)
    eng.feed("w " * 12)
    calls = _count_origin_calls(eng)
    first = eng.backlog_words()
    walked = calls["n"]
    assert walked >= 12                   # the first call really walked
    assert eng.backlog_words() == first   # unchanged queue, same answer...
    assert calls["n"] == walked           # ...from the cache, no re-walk


def test_backlog_unchanged_across_unrelated_calls():
    eng = make(width=7)
    eng.feed("w " * 12)
    before = eng.backlog_words()
    # Reads and non-queue state changes must not move the count.
    eng.frame()
    eng.shown_source()
    eng.pending_text()
    eng.has_pending()
    eng.set_paused(True)
    eng.set_paused(False)
    eng.set_hold(True)
    eng.set_hold(False)
    assert eng.backlog_words() == before


def test_backlog_changes_after_feed():
    eng = make(width=7)
    eng.feed("w " * 3)
    assert eng.backlog_words() == 3
    eng.feed("w " * 2)
    assert eng.backlog_words() == 5       # cache dropped by the feed


def test_backlog_changes_as_ticks_consume_words():
    eng = make(width=7)
    eng.feed("w " * 6)
    assert eng.backlog_words() == 6
    eng.tick()                            # loads a word for emission
    assert eng.backlog_words() == 5       # cache dropped by the pop
    for _ in range(40):
        eng.tick()
    assert eng.backlog_words() == 0       # drained queue reads caught up


def test_backlog_changes_when_eager_flips():
    # Eager changes WHICH soft words count (confirmed ones do, minus the
    # frontier word), so set_eager must invalidate the memo both ways.
    eng = make(width=7)
    eng.feed("w " * 8, seg="s1", final=False)
    assert eng.backlog_words() == 0       # soft: not backlog
    eng.set_eager(True)
    assert eng.backlog_words() == 7       # all but the frontier word
    eng.set_eager(False)
    assert eng.backlog_words() == 0       # gate restored, cache dropped


# --- CLI wiring ---------------------------------------------------------

def test_build_gauge_default_on():
    from run import build_gauge
    assert isinstance(build_gauge(no_gauge=False, width=20), BacklogGauge)


def test_build_gauge_flag_disables():
    from run import build_gauge
    assert build_gauge(no_gauge=True, width=20) is None


def test_build_gauge_skipped_on_tiny_display():
    from run import build_gauge
    # a display too narrow to give up 2 cells keeps full width for content
    assert build_gauge(no_gauge=False, width=3) is None


def test_engine_without_gauge_is_unchanged():
    eng = BrailleEngine(DevUebTranslator(),
                        SimulatedSink(width=5, echo=False))
    eng.start()
    eng.feed("hi ")
    eng.tick()
    assert eng.frame() == [BLANK, BLANK, BLANK, BLANK, H]
