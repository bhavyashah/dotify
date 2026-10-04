from braille_engine.cells import BLANK, dots_to_pattern
from braille_engine.engine import BrailleEngine
from braille_engine.sinks.simulated import SimulatedSink
from braille_engine.translator import DevUebTranslator

H = dots_to_pattern("125")
I = dots_to_pattern("24")


def make(width=5, window=1):
    eng = BrailleEngine(DevUebTranslator(), SimulatedSink(width=width, echo=False),
                        window=window)
    eng.start()
    return eng


def test_no_write_before_tick():
    eng = make()
    eng.feed("hi ")
    assert eng.sink.frames == []
    assert eng.frame() == [BLANK] * 5


def test_first_tick_right_aligned():
    eng = make()
    eng.feed("hi ")
    assert eng.tick() == 1
    assert eng.frame() == [BLANK, BLANK, BLANK, BLANK, H]


def test_three_ticks_then_empty():
    eng = make()
    eng.feed("hi ")  # cells: H, I, BLANK
    eng.tick(); eng.tick(); eng.tick()
    assert eng.frame() == [BLANK, BLANK, H, I, BLANK]
    assert eng.has_pending() is False
    assert eng.tick() == 0


def test_pause_freezes_ticks_but_keeps_queueing():
    eng = make()
    eng.feed("hi ")
    eng.tick()
    frozen = eng.frame()
    eng.set_paused(True)
    assert eng.tick() == 0                 # nothing emitted while paused
    assert eng.frame() == frozen           # cells under the fingers unmoved
    eng.feed("more ")                      # sources keep feeding the backlog
    assert eng.tick() == 0
    assert eng.has_pending() is True
    eng.set_paused(False)
    assert eng.tick() == 1                 # resumes exactly where it left off


def test_rolling_window_drops_oldest():
    eng = make(width=3)
    for _ in range(5):
        eng.feed("a ")  # each -> a-cell then blank
    # drive enough ticks to overflow width 3
    while eng.tick():
        pass
    assert len(eng.frame()) == 3


def test_jump_to_live_shows_newest_n_and_clears_pending():
    eng = make(width=5)
    # 8 cells: 'abcdefgh' letters, no spaces
    eng.feed("abcdefgh")
    eng.flush_input()
    assert eng.has_pending() is True
    eng.jump_to_live()
    assert eng.has_pending() is False
    expected = [DevUebTranslator().translate(c)[0] for c in "defgh"]
    assert eng.frame() == expected
    assert eng.sink.frames[-1] == expected


# --- cell window (stream N cells per refresh) ---------------------------------

class UnitTranslator:
    """Fake translator emitting scripted units, to pin boundary behavior."""

    def __init__(self, units):
        self.units = units

    def translate_units(self, token):
        return [list(u) for u in self.units]

    def translate(self, token):
        return [c for u in self.units for c in u]


def test_window_emits_n_cells_in_one_frame():
    eng = make(width=8, window=4)
    eng.feed("hi hi ")            # cells: H I _ H I _
    assert eng.tick() == 4        # H I _ H in a single refresh
    assert len(eng.sink.frames) == 1
    assert eng.frame() == [BLANK] * 4 + [H, I, BLANK, H]


def test_window_returns_short_count_when_queue_runs_dry():
    eng = make(width=8, window=4)
    eng.feed("hi ")               # only 3 cells exist
    assert eng.tick() == 3


def test_window_never_splits_a_unit_across_refreshes():
    a, b, c, d = 1, 2, 3, 4
    eng = BrailleEngine(UnitTranslator([[a], [b, c], [d]]),
                        SimulatedSink(width=8, echo=False), window=2)
    eng.start()
    eng.feed("x ")
    assert eng.tick() == 1        # [a] alone: [b,c] must not straddle
    assert eng.tick() == 2        # [b,c] leads its own refresh, intact
    assert eng.tick() == 2        # [d] + the trailing space cell
    assert list(eng.frame())[-5:] == [a, b, c, d, BLANK]


def test_unit_wider_than_window_still_streams():
    a, b = 1, 2
    eng = BrailleEngine(UnitTranslator([[a, b]]),
                        SimulatedSink(width=8, echo=False), window=1)
    eng.start()
    eng.feed("x")
    eng.flush_input()
    assert eng.tick() == 1        # forced split: window 1 can never hold 2
    assert eng.tick() == 1
    assert eng.tick() == 0


def test_set_window_validates_and_applies_live():
    eng = make(width=8)
    assert eng.set_window(0) is False
    assert eng.set_window(4) is True
    eng.feed("hi hi ")
    assert eng.tick() == 4


def test_capital_indicator_stays_with_its_letter():
    eng = make(width=8, window=2)
    eng.feed("aHi ")              # units: [a] [cap,h] [i] [space]
    assert eng.tick() == 1        # 'a' alone — cap pair would straddle
    assert eng.tick() == 2        # capital indicator + h together
    assert eng.tick() == 2        # i + space


# --- jump-to-live summary primitives ------------------------------------------

def test_take_pending_captures_text_and_cells_and_clears():
    eng = make(width=5)
    eng.feed("hi there ")
    eng.tick()                    # 'hi' is now mid-scroll (H shown, I pending)
    text, cells = eng.take_pending()
    # The mid-scroll word is captured IN FULL — a summary must not lose it.
    assert text == "hi there"
    assert cells[0] == I          # the not-yet-shown remainder of 'hi'
    assert eng.has_pending() is False
    assert eng.tick() == 0        # nothing left to stream
    assert eng.sink.frames == [eng.frame()]   # capture itself wrote nothing


def test_show_frame_appends_to_the_rolling_buffer():
    eng = make(width=5)
    eng.show_frame([1, 2])
    assert eng.frame() == [BLANK, BLANK, BLANK, 1, 2]
    eng.show_frame([3])           # append keeps the rolling-newest contract
    assert eng.frame() == [BLANK, BLANK, 1, 2, 3]


def test_hold_blocks_ticks_independently_of_pause():
    eng = make()
    eng.feed("hi ")
    eng.set_hold(True)
    assert eng.tick() == 0
    assert eng.paused is False    # hold is machine state, not reader state
    eng.set_hold(False)
    assert eng.tick() == 1


def test_fit_cells_truncates_on_unit_boundary_and_reports_total():
    a, b, c = 1, 2, 3
    eng = BrailleEngine(UnitTranslator([[a, b], [c]]),
                        SimulatedSink(width=8, echo=False))
    eng.start()
    # Each word is 3 cells ([a,b] + [c]); "x y" totals 3 + 1 + 3 = 7.
    cells, total = eng.fit_cells("x y", 5)
    assert total == 7
    # [a,b] of the second word won't fit in the 1 remaining cell, and the
    # narrower [c] behind it must NOT leapfrog the skip; the dangling
    # separator blank is dropped too.
    assert cells == [a, b, c]


def test_fit_cells_within_budget_keeps_everything():
    eng = make(width=8)
    cells, total = eng.fit_cells("hi", 8)
    assert (cells, total) == ([H, I], 2)


def test_feed_summary_streams_ahead_of_queued_text_like_ordinary_cells():
    eng = make(width=5)
    eng.feed("later ", seg="s1", final=False)   # soft tail survives a jump
    eng.take_pending()                          # the jump's capture
    eng.feed_summary("hi")
    assert eng.summary_pending() is True
    eng.revise_segment("s1", "later ", final=True)   # tail hardens
    eng.tick()                                  # the leading separator blank
    eng.tick(); eng.tick()                      # H I — the recap first
    assert eng.frame()[-2:] == [H, I]
    while eng.tick():
        pass                                    # then the hardened tail
    assert eng.summary_pending() is False
    assert eng.frame()[-1] == BLANK             # 'later' followed the recap


def test_summary_marker_occupies_cell_2_while_recap_is_under_the_fingers():
    from braille_engine.engine import SUMMARY_MARKER, TYPED_MARKER
    from braille_engine.gauge import BacklogGauge
    eng = BrailleEngine(DevUebTranslator(),
                        SimulatedSink(width=6, echo=False),
                        gauge=BacklogGauge())   # content width 4
    eng.start()
    eng.feed_summary("hi")
    eng.feed("abcd")                            # live speech queues behind
    eng.flush_input()
    eng.tick()
    assert eng.frame()[1] == SUMMARY_MARKER     # recap cell on the display
    while eng.summary_pending():
        eng.tick()
    assert eng.frame()[1] == SUMMARY_MARKER     # recap cells still visible
    for _ in range(4):
        eng.tick()                              # live text pushes them off
    # Marker hands off to the live content's source tag (untagged = typed)
    # — s outranks it only while recap cells remain on the display.
    assert eng.frame()[1] == TYPED_MARKER
    assert eng.shown_source() == "abcd"         # live cells keep their source


def test_ambient_stream_renders_a_blank_marker_but_truthful_tags():
    # The native live stream — transcribed speech or captions —
    # earns NO cell-2 letter (a permanent letter over
    # the everyday stream carries zero information). The origin tags stay
    # truthful underneath: they drive commit policy and the highlight.
    from braille_engine.engine import (ORIGIN_CAPTIONS, ORIGIN_SPEECH,
                                       SUMMARY_MARKER, TYPED_MARKER)
    from braille_engine.gauge import BacklogGauge
    eng = BrailleEngine(DevUebTranslator(),
                        SimulatedSink(width=6, echo=False),
                        gauge=BacklogGauge())   # content width 4
    eng.start()
    assert eng.frame()[1] == BLANK              # nothing on display yet
    eng.feed("ab ", seg="s1")                   # transcribed speech (AI mode)
    while eng.tick():
        pass
    assert eng.frame()[1] == BLANK              # ambient speech: no letter
    assert ORIGIN_SPEECH in eng._origin         # ...but the tag is truthful
    # A caption stream tags captions at ORIGIN time — still no letter, and
    # the tag must say captions, not speech.
    eng.speech_is_stream = True
    eng.feed("cd ", seg="s2")
    while eng.tick():
        pass
    assert eng.frame()[1] == BLANK
    assert ORIGIN_CAPTIONS in eng._origin
    eng.speech_is_stream = False                # back to a local mic
    eng.feed("cdef")                            # typed input: no segment tag
    eng.flush_input()
    for _ in range(2):
        eng.tick()
    # Mixed window (captions draining, typed arriving): h is exceptional
    # CONTENT and outranks the blank ambient claim.
    assert eng.frame()[1] == TYPED_MARKER
    for _ in range(4):
        eng.tick()                              # typed text fills the window
    assert eng.frame()[1] == TYPED_MARKER
    # Summary outranks everything while any recap cell is visible
    # (priority s > h > blank).
    eng.feed_summary("hi")
    eng.tick()
    assert eng.frame()[1] == SUMMARY_MARKER


def test_restored_flip_space_keeps_the_stream_origin():
    # flip()'s shown-nothing restore re-queues the consumed separator with no
    # segment; it must inherit the flowing origin, not read as typed — one
    # 'typed' blank would pin a false h marker over the ambient stream.
    from braille_engine.engine import TYPED_MARKER
    from braille_engine.gauge import BacklogGauge
    eng = BrailleEngine(DevUebTranslator(),
                        SimulatedSink(width=6, echo=False),
                        gauge=BacklogGauge())   # content width 4
    eng.start()
    eng.feed("ab ", seg="s1")                   # hardened speech
    eng.tick(); eng.tick()                      # word shown; space still queued
    eng.feed("xy", seg="s2", final=False)       # soft head blocks rendering
    assert eng.flip() == 0                      # consumes + restores the space
    eng.feed("xy ", seg="s2", final=True)       # harden s2
    while eng.tick():
        pass
    # Everything on display is speech — the restored space must not flip h.
    assert eng.frame()[1] == BLANK
    assert eng.frame()[1] != TYPED_MARKER


def test_jump_snap_keeps_the_origin_tags():
    # A catch-up snap keeps each captured cell's origin: the ambient
    # stream renders cell 2 blank by design, but the tags
    # must stay truthful — they feed the speaker highlight and the s/h
    # priority scan.
    from braille_engine.engine import ORIGIN_SPEECH
    from braille_engine.gauge import BacklogGauge
    eng = BrailleEngine(DevUebTranslator(),
                        SimulatedSink(width=6, echo=False),
                        gauge=BacklogGauge())   # content width 4
    eng.start()
    eng.feed("ab cd ef ", seg="s1")
    eng.tick()                                  # streaming has begun
    eng.jump_to_live()
    assert eng.frame()[1] == BLANK              # ambient stream: no letter
    assert ORIGIN_SPEECH in eng._origin         # tags rode the snap
    assert eng.shown_source() != ""             # and its highlight sources


def test_jump_snap_keeps_the_caption_tags():
    # Same snap over a caption stream: the tag must say captions, not
    # speech, though neither renders.
    from braille_engine.engine import ORIGIN_CAPTIONS, ORIGIN_SPEECH
    from braille_engine.gauge import BacklogGauge
    eng = BrailleEngine(DevUebTranslator(),
                        SimulatedSink(width=6, echo=False),
                        gauge=BacklogGauge())   # content width 4
    eng.start()
    eng.speech_is_stream = True
    eng.feed("ab cd ef ", seg="s1")
    eng.tick()                                  # streaming has begun
    eng.jump_to_live()
    assert eng.frame()[1] == BLANK
    assert ORIGIN_CAPTIONS in eng._origin
    assert ORIGIN_SPEECH not in eng._origin
    assert eng.shown_source() != ""             # and their highlight sources


def test_show_frame_ends_the_hold_so_the_written_marker_is_honest():
    # If the snap frame were WRITTEN while the catch-up hold is still set
    # (callers releasing it only after), the SINK would latch the hold's s
    # marker over plain speech until the next organic write — indefinitely
    # in manual mode. frame() recomputed after the release would read the
    # right marker, so this test checks the written frame: the lie would
    # only ever exist on the physical display. (Ambient speech reads
    # BLANK — the assert is that the written cell is NOT the hold's s.)
    from braille_engine.engine import SUMMARY_MARKER
    from braille_engine.gauge import BacklogGauge
    eng = BrailleEngine(DevUebTranslator(),
                        SimulatedSink(width=6, echo=False),
                        gauge=BacklogGauge())   # content width 4
    eng.start()
    eng.feed("ab cd ef ", seg="s1")
    eng.tick()                                  # streaming has begun
    eng.set_hold(True)                          # a catch-up fetch in flight
    _text, cells = eng.take_pending()
    eng.show_frame(cells)                       # the snap IS the live edge
    assert not eng._hold                        # hold ended by the write...
    assert eng.sink.frames[-1][1] == BLANK      # ...as physically written
    assert eng.sink.frames[-1][1] != SUMMARY_MARKER


def test_owed_separator_after_a_typed_flush_does_not_pin_the_h_marker():
    # feed()'s owed-space payment after a typed flush: once the typed word
    # AND its paid separator roll off, pure speech must read blank — the
    # paid space must not linger as a 'typed' cell.
    from braille_engine.engine import TYPED_MARKER
    from braille_engine.gauge import BacklogGauge
    eng = BrailleEngine(DevUebTranslator(),
                        SimulatedSink(width=6, echo=False),
                        gauge=BacklogGauge())   # content width 4
    eng.start()
    eng.feed("hi")            # typed partial word (no trailing space)
    eng.flush_input()         # Tab out of Human mode: word committed, space owed
    eng.feed("abcdef ", seg="s1")   # speech resumes; the owed space is paid
    while eng.tick():
        pass
    # Typed word (2 cells) + paid space rolled off the 4-cell window; only
    # speech cells remain under the fingers.
    assert eng.frame()[1] == BLANK
    assert eng.frame()[1] != TYPED_MARKER


def test_summary_words_do_not_count_as_backlog():
    from braille_engine.gauge import BacklogGauge
    eng = BrailleEngine(DevUebTranslator(),
                        SimulatedSink(width=8, echo=False),
                        gauge=BacklogGauge())
    eng.start()
    eng.feed_summary("one two three four five six seven eight nine ten")
    assert eng.backlog_words() == 0             # the recap is not backlog
    assert eng.frame()[0] == BLANK              # gauge stays drained
    eng.feed("real words landing now more still coming on and on ")
    assert eng.backlog_words() == 10            # live speech is


def test_take_pending_discards_queued_summary_instead_of_capturing_it():
    eng = make(width=5)
    eng.feed_summary("hello")
    eng.feed("abc ")
    eng.tick()                                  # first recap cell mid-scroll
    text, cells = eng.take_pending()
    assert text == "abc"                        # only real speech captured
    assert eng.summary_pending() is False       # the rest of the recap died
    letters = [DevUebTranslator().translate(c)[0] for c in "abc"]
    assert cells == letters + [BLANK]


def test_summary_cells_carry_no_speaker_highlight_source():
    eng = make(width=5)
    eng.feed_summary("hi")
    eng.tick(); eng.tick()
    assert eng.shown_source() == ""             # recap text is not transcript


def test_recap_leads_with_a_separator_never_fusing_onto_shown_text():
    # The display still holds the pre-jump cells (take_pending never
    # rewrites the frame), so without a leading blank the recap's first
    # word would fuse onto them ("hel" + "meeting").
    eng = make(width=5)
    eng.feed("ab")
    eng.flush_input()
    eng.tick(); eng.tick()                      # 'ab' under the fingers
    eng.feed_summary("hi")
    assert eng.tick() == 1                      # the separator blank first
    ab = [DevUebTranslator().translate(c)[0] for c in "ab"]
    assert eng.frame() == [BLANK, BLANK] + ab + [BLANK]
    eng.tick()
    assert eng.frame()[-1] == H                 # then the recap's first cell


def test_fetch_hold_shows_the_s_marker():
    # Without a marker, the frozen fetch window would be
    # indistinguishable by touch from a wedged display.
    from braille_engine.engine import SUMMARY_MARKER
    from braille_engine.gauge import BacklogGauge
    eng = BrailleEngine(DevUebTranslator(),
                        SimulatedSink(width=6, echo=False),
                        gauge=BacklogGauge())
    eng.start()
    eng.set_hold(True)
    assert eng.frame()[1] == SUMMARY_MARKER     # catch-up owns the display
    eng.repaint()
    assert eng.sink.frames[-1][1] == SUMMARY_MARKER
    eng.set_hold(False)
    assert eng.frame()[1] == BLANK


def test_flash_keeps_the_summary_marker_mid_recap():
    from braille_engine.engine import SUMMARY_MARKER
    from braille_engine.gauge import BacklogGauge
    eng = BrailleEngine(DevUebTranslator(),
                        SimulatedSink(width=8, echo=False),
                        gauge=BacklogGauge())
    eng.start()
    eng.feed_summary("hi")
    eng.tick(); eng.tick()                      # recap cells on the display
    eng.flash("x")
    assert eng.sink.frames[-1][1] == SUMMARY_MARKER


def test_repaint_rewrites_the_frame_but_honors_pause_and_prestart():
    eng = BrailleEngine(DevUebTranslator(), SimulatedSink(width=5, echo=False))
    eng.repaint()                               # before start: silent no-op
    eng.start()
    eng.feed("a ")
    eng.tick()
    writes = len(eng.sink.frames)
    eng.set_paused(True)
    eng.repaint()
    assert len(eng.sink.frames) == writes       # pause: nothing changes
    eng.set_paused(False)
    eng.repaint()
    assert len(eng.sink.frames) == writes + 1
    assert eng.sink.frames[-1] == eng.frame()


def test_flash_renders_under_pause_and_paused_ok_repaint_restores():
    # Pause-safe flashes: a chord answer is a frame the
    # reader ASKED for (reply_frame's exemption), and the display is a
    # deaf-blind reader's only channel — so flash writes under pause.
    # A plain repaint keeps the stricter contract (no frame the reader
    # didn't ask about); the pacer's dwell-end restore passes paused_ok
    # and brings back EXACTLY the frozen frame, advancing nothing.
    eng = make()
    eng.feed("hi ")
    eng.tick()
    frozen = eng.sink.frames[-1]
    eng.set_paused(True)
    eng.flash("x")
    assert eng.sink.frames[-1] != frozen        # the answer rendered
    writes = len(eng.sink.frames)
    eng.repaint()                               # ordinary callers: still a
    assert len(eng.sink.frames) == writes       # no-op under pause
    eng.repaint(paused_ok=True)                 # the pacer's dwell-end restore
    assert eng.sink.frames[-1] == frozen        # exactly the frozen frame
    assert eng.tick() == 0                      # and nothing advanced


def test_paused_pan_restore_keeps_the_frozen_gauge_cell():
    # Pan, then pause, then a chord answer: the dwell restore lands in
    # the PAN branch, which normally re-measures the gauge (the backlog
    # keeps growing behind a panned view). Pause outranks pan — exactly
    # as the pan keys defer to pause — so the paused restore re-sends
    # the gauge as last shown instead.
    from braille_engine.gauge import BacklogGauge
    eng = BrailleEngine(DevUebTranslator(),
                        SimulatedSink(width=8, echo=False),
                        gauge=BacklogGauge())
    eng.start()
    eng.feed("aa bb cc dd ")
    while eng.tick():
        pass
    eng.pan_back(4)
    eng.set_paused(True)
    gauge_cell = eng.sink.frames[-1][0]
    pan_frame = eng.sink.frames[-1]
    eng.feed(" ".join(["word"] * 20) + " ")     # backlog grows under pause
    eng.flash("x")
    eng.repaint(paused_ok=True)
    assert eng.sink.frames[-1] == pan_frame     # view AND gauge unmoved
    assert eng.sink.frames[-1][0] == gauge_cell


def test_flash_under_pause_keeps_the_frozen_gauge_cell():
    # The reader asked about a setting, not the backlog: the gauge cell of
    # a paused flash — and of the dwell-end restore — is re-sent as last
    # shown, never re-measured, even though the backlog grew under pause
    # (refresh_gauge's "nothing changes at all", per uninvited cell).
    from braille_engine.gauge import BacklogGauge
    eng = BrailleEngine(DevUebTranslator(),
                        SimulatedSink(width=8, echo=False),
                        gauge=BacklogGauge())
    eng.start()
    eng.feed("a ")
    eng.tick()
    eng.set_paused(True)
    gauge_cell = eng.sink.frames[-1][0]
    eng.feed(" ".join(["word"] * 20) + " ")     # backlog grows under pause
    eng.flash("x")
    assert eng.sink.frames[-1][0] == gauge_cell
    eng.repaint(paused_ok=True)
    assert eng.sink.frames[-1][0] == gauge_cell


def test_flash_still_refused_during_a_catchup_hold():
    # A fetch hold is machine state, not a reader command: its frozen ack
    # frame (drained gauge + s marker) must stay put — the pause exemption
    # does not extend to it.
    eng = make()
    eng.feed("a ")
    eng.tick()
    eng.set_hold(True)
    writes = len(eng.sink.frames)
    eng.flash("x")
    assert len(eng.sink.frames) == writes       # held frame untouched
    eng.set_hold(False)
    eng.flash("x")
    assert len(eng.sink.frames) == writes + 1   # hold released: flash lands


def test_drained_recap_prunes_its_segment_bookkeeping():
    # The counter and prune keep long sessions from growing one summary
    # segment per jump (and keep summary_pending O(1)).
    eng = make(width=8)
    eng.feed_summary("hi")
    while eng.tick():
        pass
    assert eng.summary_pending() is False
    assert eng._summary_segs == set()
    assert not any(isinstance(key, tuple) and key and key[0] == "summary"
                   for key in eng._seg_info)


def test_late_revision_of_a_prejump_segment_lands_after_the_summary():
    # A pre-jump segment extended AFTER the jump contributes its new words
    # behind the recap (order 0 keeps the summary oldest), never in front.
    eng = make(width=12)
    eng.feed("old words ", seg="pre", final=True)
    eng.take_pending()                          # jump: captures 'old words'
    eng.feed_summary("sum")
    eng.revise_segment("pre", "old words fresh ", final=True)
    while eng.tick():
        pass
    # The recap streamed first: its cells sit LEFT of 'fresh' in the buffer.
    frame = eng.frame()
    s_cells = [DevUebTranslator().translate(c)[0] for c in "sum"]
    f_cells = [DevUebTranslator().translate(c)[0] for c in "fresh"]
    joined = list(frame)
    s_at = _find_run(joined, s_cells)
    f_at = _find_run(joined, f_cells)
    assert s_at != -1 and f_at != -1 and s_at < f_at


def _find_run(cells, run):
    for i in range(len(cells) - len(run) + 1):
        if cells[i:i + len(run)] == run:
            return i
    return -1


def test_hold_refreshes_gauge_cell_as_backlog_grows():
    from braille_engine.gauge import BacklogGauge
    eng = BrailleEngine(DevUebTranslator(),
                        SimulatedSink(width=6, echo=False),
                        gauge=BacklogGauge())
    eng.start()
    eng.feed("a ")
    eng.tick()
    baseline = list(eng.frame())
    eng.set_hold(True)
    assert eng.tick() == 0
    writes = len(eng.sink.frames)
    for _ in range(8):            # queue past the first gauge bucket (6)
        eng.feed("word ")
    assert eng.tick() == 0        # still no cells emitted...
    frame = eng.frame()
    assert len(eng.sink.frames) == writes + 1   # ...but the gauge cell moved
    assert frame[0] != BLANK                    # thermometer shows backlog
    assert frame[2:] == baseline[2:]            # content cells untouched
    assert eng.tick() == 0
    assert len(eng.sink.frames) == writes + 1   # same bucket: no rewrite


# --- full-page flip (full-display window / manual advance) --------------------
# flip() fills the whole content width with WHOLE words (word wrap, the
# screen-reader panning default), pads the rest with blanks so each flip is a
# clean left-aligned page, and leaves the word that did not fit to lead the
# next page.


def T(text):
    return DevUebTranslator().translate(text)


def test_flip_fills_a_page_of_whole_words_and_pads():
    eng = make(width=8)
    eng.feed("hi go bigword ")
    assert eng.flip() == 6                 # hi, space, go, space; bigword waits
    assert eng.frame() == T("hi") + [BLANK] + T("go") + [BLANK] * 3
    assert eng.shown_source() == "hi go"   # padding carries no source
    assert eng.flip() == 8                 # bigword led the page, whole
    assert eng.frame() == T("bigword") + [BLANK]
    assert eng.flip() == 0                 # drained: display untouched


def test_flip_word_wider_than_page_splits_as_it_must():
    eng = make(width=4)
    eng.feed("abcdefgh ")
    assert eng.flip() == 4
    assert eng.frame() == T("abcdefgh")[:4]
    assert eng.flip() == 4
    assert eng.frame() == T("abcdefgh")[4:]
    # Only the orphaned trailing space is left; a page never opens with a
    # blank, so there is nothing to show — and the display holds still.
    frames = len(eng.sink.frames)
    assert eng.flip() == 0
    assert len(eng.sink.frames) == frames


def test_flip_skips_the_separator_at_the_page_start():
    eng = make(width=4)
    eng.feed("abcd ef ")
    assert eng.flip() == 4                 # first word fills the page exactly
    assert eng.flip() == 3                 # space led the queue but is skipped
    assert eng.frame() == T("ef") + [BLANK] * 2


def test_flip_continues_a_mid_stream_word_before_new_ones():
    eng = make(width=6)
    eng.feed("abcd ef ")
    eng.tick()                             # ticker showed 'a'; 'bcd' is owed
    assert eng.flip() == 6                 # bcd + space + ef fill the page
    assert eng.frame() == T("abcd")[1:] + [BLANK] + T("ef")


def test_flip_respects_pause_and_never_blanks_an_idle_page():
    eng = make(width=4)
    eng.feed("ab ")
    eng.set_paused(True)
    assert eng.flip() == 0
    assert eng.sink.frames == []
    eng.set_paused(False)
    assert eng.flip() == 3
    frames = list(eng.sink.frames)
    assert eng.flip() == 0                 # queue empty: no write at all
    assert eng.sink.frames == frames


def test_flip_stops_at_a_soft_segment():
    eng = make(width=8)
    eng.feed("ab ", seg="s1", final=True)
    eng.feed("cd ", seg="s2", final=False)
    assert eng.flip() == 3                 # soft text queues but never renders
    assert eng.frame() == T("ab") + [BLANK] * 6
    assert eng.has_pending() is True


def test_flip_with_gauge_pages_the_content_width():
    from braille_engine.gauge import BacklogGauge
    eng = BrailleEngine(DevUebTranslator(),
                        SimulatedSink(width=6, echo=False),
                        gauge=BacklogGauge())
    eng.start()                            # 2 gauge cells + 4 content cells
    eng.feed("abcd ")
    assert eng.flip() == 4
    frame = eng.sink.frames[-1]
    assert len(frame) == 6
    assert frame[2:] == T("abcd")


def test_full_window_tick_is_a_word_wrapped_page_flip():
    # The window cycle's "full display" stop: a
    # tick at the full content width IS a flip — word wrap, padding, the
    # word that does not fit leading the next page.
    eng = make(width=8, window=8)
    eng.feed("hi go bigword ")
    assert eng.window_is_full
    assert eng.tick() == 6
    assert eng.frame() == T("hi") + [BLANK] + T("go") + [BLANK] * 3
    assert eng.tick() == 8
    assert eng.frame() == T("bigword") + [BLANK]
    assert eng.tick() == 0                 # drained: display untouched


def test_flip_prices_the_page_for_the_pacer():
    # The pacer sleeps on last_tick_cost after a full-window tick, so the
    # page dwell is its content priced by dwell class — spaces discounted
    # like any ticker refresh, padding blanks free.
    eng = make(width=8, window=8)
    eng.set_space_dwell(50)
    eng.feed("hi go ")
    assert eng.tick() == 6                 # hi, space, go, trailing space
    assert abs(eng.last_tick_cost - 5.0) < 1e-9   # 4 text + 2 half-spaces


def test_flip_restores_the_separator_when_nothing_renders():
    eng = make(width=4)
    eng.feed("abcd ", seg="s1", final=True)
    assert eng.flip() == 4                 # page ends exactly at the edge
    eng.feed("ef ", seg="s2", final=False)
    assert eng.flip() == 0                 # soft head: nothing renders...
    eng.harden_all()
    while eng.tick():
        pass
    # ...and the separator it skipped was restored: back in ticker mode the
    # words never fuse. Buffer holds [blank, e, f, trailing blank] — without
    # the restore it would read [d, e, f, blank] ("...def" fused).
    assert eng.frame() == [BLANK] + T("ef") + [BLANK]


def test_repaint_on_a_never_fed_session_is_blank():
    # A transient flash's dwell ending before any text ever flowed
    # restores an empty frame — there is nothing else to restore.
    eng = make()
    eng.flash("x")
    eng.repaint()
    assert eng.sink.frames[-1] == [BLANK] * 5


def test_set_window_full_clamps_now_or_at_start():
    # Connected: the full-display stop applies immediately.
    eng = make(width=6)
    eng.set_window_full()
    assert eng.window == 6 and eng.window_is_full
    # Not yet connected (run.py arms the default before start()):
    # the request is armed oversize and start() clamps it to the display.
    eng2 = BrailleEngine(DevUebTranslator(), SimulatedSink(width=5, echo=False))
    eng2.set_window_full()
    eng2.start()
    assert eng2.window == 5 and eng2.window_is_full


def test_tick_without_record_cost_leaves_the_pacers_price_alone():
    # The thumb-Right fast-forward ticks from the key thread; it must not
    # overwrite last_tick_cost, which the pacer reads after its OWN tick.
    eng = make()
    eng.feed("hi ")
    eng.tick()
    priced = eng.last_tick_cost
    assert eng.tick(record_cost=False) == 1
    assert eng.last_tick_cost == priced
