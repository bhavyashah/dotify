"""Transcript panning (thumb Left/Right): the shown-cell history ring, the
frozen panned view, and the mode-aware step in PacerControls.

The contract under test: every cell that reaches the display also enters a
bounded history ring; pan_back moves a reading view back through it (page
steps snap to word starts), and while the view is panned the engine freezes
exactly like a catch-up hold — speech queues invisibly, and the GAUGE
carries the position: pan depth counts toward the distance it shows, and
it never reads blank while the view is off the live edge. Cell 2 keeps
naming the content's KIND (h for the typed-class text these tests feed;
the ambient stream reads blank). Thumb Next (and every other whole-frame
catch-up) is the escape hatch back to live.
"""

from braille_engine.cells import BLANK
from braille_engine.controls import ADVANCE_MANUAL, PacerControls
from braille_engine.engine import BrailleEngine, TYPED_MARKER
from braille_engine.gauge import BacklogGauge
from braille_engine.sinks.simulated import SimulatedSink
from braille_engine.translator import DevUebTranslator


def make(width=5, window=1, gauge=None):
    eng = BrailleEngine(DevUebTranslator(), SimulatedSink(width=width,
                                                          echo=False),
                        gauge=gauge, window=window)
    eng.start()
    return eng


def cells(text):
    tr = DevUebTranslator()
    return [tr.translate(ch)[0] if ch != " " else BLANK for ch in text]


def drain(eng):
    while eng.tick():
        pass


# --- engine: the panned view ---------------------------------------------

def test_pan_back_shows_history_and_freezes_ticks():
    eng = make(width=5)
    eng.feed("abcdefgh")
    eng.flush_input()
    drain(eng)
    assert eng.sink.frames[-1] == cells("defgh")
    assert eng.pan_back(2) == 2
    assert eng.pan_offset == 2
    assert eng.sink.frames[-1] == cells("bcdef")
    # Streaming is frozen: new text queues invisibly, the view holds.
    eng.feed("xy")
    eng.flush_input()
    assert eng.tick() == 0
    assert eng.sink.frames[-1] == cells("bcdef")
    assert eng.has_pending()


def test_pan_clamps_at_the_start_of_history():
    eng = make(width=5)
    eng.feed("abcdefgh")          # 8 cells; the oldest whole view is a..e
    eng.flush_input()
    drain(eng)
    assert eng.pan_back(50) == 3  # limit: 8 - 5
    assert eng.sink.frames[-1] == cells("abcde")
    assert eng.pan_back(1) == 0   # at the start: the view must not move
    assert eng.sink.frames[-1] == cells("abcde")


def test_pan_forward_returns_to_live_and_unfreezes():
    eng = make(width=5)
    eng.feed("abcdefgh")
    eng.flush_input()
    drain(eng)
    eng.pan_back(3)
    eng.feed("xy")
    eng.flush_input()
    assert eng.pan_forward(1) == 1
    assert eng.pan_offset == 2
    assert eng.pan_forward(50) == 2
    assert eng.pan_offset == 0
    # Landing on live repaints the LIVE buffer, then streaming resumes.
    assert eng.sink.frames[-1] == cells("defgh")
    assert eng.tick() == 1


def test_pan_forward_from_live_is_a_no_op():
    eng = make(width=5)
    eng.feed("abc")
    eng.flush_input()
    drain(eng)
    frames = len(eng.sink.frames)
    assert eng.pan_forward(5) == 0
    assert len(eng.sink.frames) == frames


def test_page_pan_snaps_its_left_edge_to_a_word_start():
    eng = make(width=4)
    eng.feed("ab cdefgh ij ")    # 13 cells: ab_cdefgh_ij_
    eng.flush_input()
    drain(eng)
    # A raw page step (4) would open the view at 'e', mid-word; the snap
    # walks back to the word's own start instead.
    assert eng.pan_back(4) == 6
    assert eng.sink.frames[-1] == cells("cdef")


def test_small_window_pans_are_cell_exact():
    eng = make(width=4)
    eng.feed("ab cdefgh ij ")
    eng.flush_input()
    drain(eng)
    assert eng.pan_back(2) == 2   # below a page: no snap, exact cells
    assert eng.pan_offset == 2


def test_paused_display_refuses_pans():
    eng = make(width=5)
    eng.feed("abcdefgh")
    eng.flush_input()
    drain(eng)
    eng.set_paused(True)
    assert eng.pan_back(2) == 0
    assert eng.pan_offset == 0


def test_catchup_commands_clear_the_pan():
    eng = make(width=5)
    eng.feed("abcdefgh")
    eng.flush_input()
    drain(eng)
    eng.pan_back(3)
    eng.feed("newer words ")
    eng.jump_to_live()
    assert eng.pan_offset == 0
    assert eng.tick() == 0        # jump drained the queue; not frozen


def test_repaint_while_panned_restores_the_history_slice():
    eng = make(width=5)
    eng.feed("abcdefgh")
    eng.flush_input()
    drain(eng)
    eng.pan_back(3)
    eng.flash("hi")               # a transient covers the panned view
    eng.repaint()                 # the dwell-end restore
    assert eng.sink.frames[-1] == cells("abcde")


def test_pan_frame_marker_names_the_content_and_flash_keeps_it():
    # No pan marker: cell 2 keeps naming the KIND of the
    # cells under the fingers (untagged feed = typed-class, h) while the
    # gauge carries the position.
    eng = make(width=6, gauge=BacklogGauge())   # content width 4
    eng.feed("abcdefgh")
    eng.flush_input()
    drain(eng)
    eng.pan_back(2)
    assert eng.sink.frames[-1][1] == TYPED_MARKER
    eng.flash("hi")
    assert eng.sink.frames[-1][1] == TYPED_MARKER


def test_panned_gauge_never_reads_blank():
    # The hard invariant: blank gauge ⇔ at live and caught up.
    # A shallow pan in a lull — no backlog at all — still shows level 1;
    # returning to the live edge with nothing queued drains it to blank.
    eng = make(width=6, gauge=BacklogGauge())
    eng.feed("abcdefgh")
    eng.flush_input()
    drain(eng)
    assert eng.frame()[0] == BLANK            # at live, caught up
    eng.pan_back(2)
    assert eng.sink.frames[-1][0] != BLANK    # off the edge: min level 1
    eng.pan_forward(50)
    assert eng.sink.frames[-1][0] == BLANK    # back at live: blank again


def test_pan_depth_counts_toward_the_gauge_distance():
    # Panning back FILLS the gauge even when no new words arrive: the
    # depth in cells maps to words (PAN_CELLS_PER_WORD) and joins the
    # bucket walk. 40 cells back at 5 cells/word is 8 words — level 1
    # by count, not merely by the off-live floor.
    eng = make(width=6, gauge=BacklogGauge())
    eng.feed("x" * 60)
    eng.flush_input()
    drain(eng)
    eng.pan_back(40)
    assert eng._gauge_distance() >= 6         # a real level-1 count
    assert eng.sink.frames[-1][0] != BLANK


def test_gauge_keeps_moving_while_panned():
    eng = make(width=6, gauge=BacklogGauge())
    eng.feed("abcdefgh")
    eng.flush_input()
    drain(eng)
    eng.pan_back(2)
    panned_view = eng.sink.frames[-1][2:]
    eng.feed(" ".join(["word"] * 40) + " ")   # the backlog bucket moves
    assert eng.tick() == 0                    # frozen — but the gauge wrote
    frame = eng.sink.frames[-1]
    assert frame[0] != BLANK                  # backlog is feelable
    assert frame[1] == TYPED_MARKER
    assert frame[2:] == panned_view           # content never moved


def test_history_ring_is_capped():
    eng = BrailleEngine(DevUebTranslator(),
                        SimulatedSink(width=5, echo=False))
    eng.HISTORY_CELLS = 6
    eng.start()
    eng.feed("abcdefghij")
    eng.flush_input()
    drain(eng)
    assert len(eng._history) == 6
    assert eng.pan_back(50) == 1              # limit: 6 - 5
    assert eng.sink.frames[-1] == cells("efghi")


def test_pan_frames_mirror_as_kind_pan_with_their_own_words():
    eng = make(width=5)
    eng.feed("ab cd ef ")
    eng.flush_input()
    drain(eng)
    eng.pan_back(3)
    event = eng.frame_event()
    assert event["kind"] == "pan"
    assert "cd" in event["text"]


def test_manual_pages_replay_exactly_as_read():
    eng = make(width=5)
    eng.feed("ab cd ")
    eng.flush_input()
    eng.flip()
    first_page = eng.sink.frames[-1]
    eng.feed("ef ")
    eng.flip()
    assert eng.sink.frames[-1] != first_page
    eng.pan_back(5)
    assert eng.sink.frames[-1] == first_page  # padding blanks included


# --- controls: the mode-aware step ----------------------------------------

class FakePanEngine:
    WINDOW_SIZES = (1, 2, 4, 6, 8)

    def __init__(self):
        self.window = 2
        self.window_is_full = False
        self.content_width = 12
        self.paused = False
        self.pan_offset = 0
        self.pans = []
        self.flashes = []
        self.exits = 0
        self.ticks = 0
        self.tick_result = 0

    def pan_back(self, step):
        self.pans.append(("back", step))
        self.pan_offset += step
        return step

    def pan_forward(self, step):
        self.pans.append(("forward", step))
        moved = min(step, self.pan_offset)
        self.pan_offset -= moved
        return moved

    def tick(self, record_cost=True):
        self.ticks += 1
        return self.tick_result

    def exit_pan(self):
        self.exits += 1
        self.pan_offset = 0

    def flash(self, text):
        self.flashes.append(text)

    def reply_frame(self, cells):
        pass                      # begin_reply paints the echo


def make_controls():
    eng = FakePanEngine()
    c = PacerControls(eng, {"v": 0.15})
    # The mode-aware pan step is under test; set the paced (auto —
    # internally "ticker") mode explicitly so the window-step branch is
    # what the default rig hits.
    c.advance_mode = "ticker"
    return eng, c


def test_ticker_pans_by_the_cell_window():
    eng, c = make_controls()
    c.pan_back()
    assert eng.pans == [("back", 2)]


def test_manual_and_full_window_pan_by_a_content_page():
    eng, c = make_controls()
    c.advance_mode = ADVANCE_MANUAL
    c.pan_back()
    c.advance_mode = "ticker"
    eng.window_is_full = True
    c.pan_back()
    assert eng.pans == [("back", 12), ("back", 12)]


def test_pan_back_at_the_start_announces_the_edge():
    eng, c = make_controls()
    eng.pan_back = lambda step: 0         # the engine is at history's start
    c.pan_back()
    assert eng.flashes == ["at start"]


def test_pan_forward_at_live_in_ticker_fast_forwards_the_backlog():
    # Half-full gauge, window 1, thumb Right — the
    # reader is behind, so the press must pull the next step forward
    # NOW, not flash "live". One press = one tick = one window (a whole
    # page at the full-display window).
    eng, c = make_controls()
    eng.tick_result = 2
    c.pan_forward()
    assert eng.ticks == 1
    assert eng.pans == []
    assert eng.flashes == []
    eng.tick_result = 0                   # now truly caught up
    c.pan_forward()
    assert eng.flashes == ["live"]


def test_pan_forward_at_live_in_manual_flips_the_next_page():
    eng, c = make_controls()
    c.advance_mode = ADVANCE_MANUAL
    c.pan_forward()
    assert eng.pans == []
    assert c.take_advance() is True


def test_pan_forward_landing_on_live_announces_it():
    eng, c = make_controls()
    c.pan_back()                          # offset 2
    c.pan_forward()
    assert eng.pan_offset == 0
    assert eng.flashes == ["live"]


def test_paused_controls_refuse_pans_without_touching_the_engine():
    eng, c = make_controls()
    eng.paused = True
    c.pan_back()
    c.pan_forward()
    assert eng.pans == []
    # The refusal answers by touch — flashes render under pause
    # and the dwell-end repaint restores the frozen frame.
    assert eng.flashes == ["display paused", "display paused"]


def test_entering_a_reply_exits_the_pan():
    eng, c = make_controls()
    eng.pan_offset = 5
    c.begin_reply()
    assert eng.exits == 1
    assert eng.pan_offset == 0


class ExplodingSink:
    """Connects fine, then every write raises — a display mid-outage."""

    def __init__(self, width=5):
        self.width = width
        self.explode = False

    def connect(self):
        return self.width

    def write(self, cells):
        if self.explode:
            raise OSError("display gone")


def test_failed_pan_write_reverts_the_offset():
    # A pan whose frame never reached the display must not stay panned:
    # a panned engine returns 0 from tick() without touching the sink, so
    # the pacer would never hit the error and never run its reconnect.
    eng = BrailleEngine(DevUebTranslator(), ExplodingSink())
    eng.start()
    eng.feed("abcdefgh")
    eng.flush_input()
    drain(eng)
    eng.sink.explode = True
    try:
        eng.pan_back(2)
        assert False, "expected OSError"
    except OSError:
        pass
    assert eng.pan_offset == 0            # reverted: ticks keep flowing
    eng.sink.explode = False
    eng.feed("xy")
    eng.flush_input()
    assert eng.tick() == 1                # the pacer path still owns writes


def test_shown_source_follows_the_pan():
    # The transcript-anchor (and the literal mirror line) must name the
    # HISTORY words while panned — the highlight follows the reader back
    # instead of parking on the freeze-point words.
    eng = make(width=5)
    eng.feed("ab cd ef ")
    eng.flush_input()
    drain(eng)
    live = eng.shown_source()
    eng.pan_back(3)
    assert eng.shown_source() != live
    assert "ab" in eng.shown_source()
    assert "ab" in eng.shown_display()
    eng.pan_forward(50)
    assert eng.shown_source() == live     # back at live: anchor restored
