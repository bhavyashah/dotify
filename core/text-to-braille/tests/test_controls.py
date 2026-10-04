import pytest

from braille_engine.controls import (
    ANNOUNCE_ACQUIRE, ANNOUNCE_CEILING, ANNOUNCE_CEILING_ALERT,
    ANNOUNCE_MARGIN, ANNOUNCE_SECONDS,
    PacerControls, MODE_LISTEN, MODE_TYPE, reading_wpm,
)


class FakeEngine:
    WINDOW_SIZES = (1, 2, 4, 6, 8)

    def __init__(self):
        self.jumps = 0
        self.fed = ""
        self.flushes = 0
        self.window = 1
        self.paused = False
        self.flashes = []         # set_grade announces its landing now
        self.events = []          # ordered log for transition-order asserts

    def flash(self, text):
        self.flashes.append(text)
        self.events.append(("flash", text))

    def jump_to_live(self):
        self.jumps += 1
        self.events.append("jump")

    def feed(self, text):
        self.fed += text
        self.events.append(("feed", text))

    def flush_input(self):
        self.flushes += 1
        self.events.append("flush")

    def discard_soft(self):
        self.events.append("discard_soft")
        return []

    def set_window(self, window):
        self.window = window
        return True

    def set_paused(self, paused):
        self.paused = bool(paused)
        self.events.append(("paused", self.paused))


def make():
    eng = FakeEngine()
    interval = {"v": 0.15}
    c = PacerControls(eng, interval)
    # Most tests below exercise the speed keys, which only change the pace
    # in the paced (auto — internally "ticker") mode. That is the
    # constructor's own default too (see its dedicated test); the
    # explicit set keeps these tests honest
    # about what they need rather than inheriting it.
    c.advance_mode = "ticker"
    return eng, interval, c


def test_new_controls_default_to_auto_advance():
    # Live captions must flow without a flip press, so auto (wire value
    # "ticker") is the default for every fresh session.
    eng = FakeEngine()
    c = PacerControls(eng, {"v": 0.15})
    assert c.advance_mode == "ticker"


def test_f_reads_faster():
    eng, interval, c = make()
    c.handle("f")
    assert interval["v"] < 0.15


def test_s_reads_slower():
    eng, interval, c = make()
    c.handle("s")
    assert interval["v"] > 0.15


def test_plus_minus_no_longer_commands():
    eng, interval, c = make()
    c.handle("+")
    assert interval["v"] == 0.15
    c.handle("-")
    assert interval["v"] == 0.15


def test_interval_floor():
    eng, interval, c = make()
    for _ in range(100):
        c.handle("f")
    assert interval["v"] >= c.min_interval


def test_space_and_l_jump_to_live():
    eng, interval, c = make()
    c.handle(" ")
    c.handle("l")
    assert eng.jumps == 2


def test_shift_s_summarizes_without_a_summarizer_snaps():
    # The terminal's summarize-on-demand key (the display chord is
    # Space+S). No summarizer configured -> degrades to the plain snap.
    eng, interval, c = make()
    c.handle("S")
    assert eng.jumps == 1
    assert interval["v"] == 0.15   # never the slower key ('s' is lowercase)


def test_q_stops():
    eng, interval, c = make()
    c.handle("q")
    assert c.stop is True


class FakeGradedTranslator:
    def __init__(self, grades=(1, 2, 3)):
        self.grade = 1
        self._grades = grades

    def set_grade(self, grade):
        if grade in self._grades:
            self.grade = grade
            return True
        return False


def test_g_toggles_grade_when_supported():
    eng, interval, c = make()
    eng.translator = FakeGradedTranslator()
    c.handle("g")
    assert eng.translator.grade == 2
    c.handle("g")
    assert eng.translator.grade == 3
    c.handle("g")
    assert eng.translator.grade == 1


def test_g_stays_grade_1_when_unsupported():
    eng, interval, c = make()
    eng.translator = FakeGradedTranslator(grades=(1,))  # dev-like
    c.handle("g")
    assert eng.translator.grade == 1
    # The refusal is felt on the display, not only logged — without the
    # flash a missing liblouis would make the grade key read dead.
    assert eng.flashes == ["no grade 2"]


def test_w_cycles_window_presets_and_wraps():
    eng, interval, c = make()
    seen = []
    for _ in range(6):
        c.handle("w")
        seen.append(eng.window)
    assert seen == [2, 4, 6, 8, 1, 2]


def test_w_cycle_announces_every_stop_on_the_display():
    # A display-only reader must feel every window change land — a stop
    # answered only on stderr reads as a dead key on the appliance.
    eng, interval, c = make()
    c.handle("w")
    assert eng.flashes == ["window: 2"]


def test_w_cycle_from_off_preset_size_snaps_to_next_larger():
    eng, interval, c = make()
    eng.window = 5                    # e.g. --window 5 from the CLI
    c.handle("w")
    assert eng.window == 6


def test_w_is_content_in_type_mode_but_ctrl_w_cycles():
    eng, interval, c = make()
    c.handle("\t")
    c.handle("w")
    assert eng.fed == "w"
    assert eng.window == 1
    c.handle("\x17")   # Ctrl+W
    assert eng.window == 2
    assert eng.fed == "w"


def test_p_toggles_pause_in_listen_mode():
    eng, interval, c = make()
    c.handle("p")
    assert eng.paused is True
    c.handle("p")
    assert eng.paused is False


def test_pause_is_silent_but_resume_announces_on_the_display():
    # Pause-safe flashes make a pause-time flash POSSIBLE —
    # this pins that it stays deliberately absent: the freeze itself is
    # the confirmation, and stamping text over the very cells the reader
    # just chose to hold would defeat the point of pausing. Resume DOES
    # flash: in manual mode nothing moves on resume, so without a tactile
    # answer the unpause reads as a dead key.
    eng, interval, c = make()
    c.handle("p")                    # pause
    assert eng.paused is True
    assert eng.flashes == []         # silent: the freeze is the answer
    c.handle("p")                    # resume
    assert eng.paused is False
    assert eng.flashes == ["resumed"]


def test_resume_unpauses_before_it_announces():
    # Order matters: the announce claims the dwell and sets the owed
    # marker, and the pacer repaints CONTENT when the dwell ends — the
    # engine must already be unpaused so that repaint (and the ticks after
    # it) find a moving display, not a still-frozen one.
    eng, interval, c = make()
    c.handle("\x10")                 # Ctrl+P: pause
    c.handle("\x10")                 # Ctrl+P: resume
    assert eng.events[-2:] == [("paused", False), ("flash", "resumed")]


def test_set_paused_direct_set_is_idempotent_with_chord_parity():
    # The shell's explicit-valued relay (the panel bridge)
    # rides the chord's own toggle on a real flip — silent pause, flashed
    # resume — and a stale press re-asking for the current state changes
    # nothing and never re-flashes. Returns whether it flipped,
    # like set_advance_mode/set_grade's changed contract.
    eng, interval, c = make()
    assert c.set_paused(True) is True
    assert eng.paused is True
    assert eng.flashes == []                 # silent: the freeze answers
    assert c.set_paused(True) is False       # idempotent: no double toggle
    assert eng.paused is True
    assert c.set_paused(False) is True
    assert eng.paused is False
    assert eng.flashes == ["resumed"]        # the chord's resume flash
    assert c.set_paused(False) is False
    assert eng.flashes == ["resumed"]        # no re-flash on the echo


def test_p_is_content_in_type_mode_but_ctrl_p_pauses():
    eng, interval, c = make()
    c.handle("\t")
    c.handle("p")
    assert eng.fed == "p"
    assert eng.paused is False
    c.handle("\x10")   # Ctrl+P
    assert eng.paused is True
    assert eng.fed == "p"


def test_starts_in_listen_mode():
    eng, interval, c = make()
    assert c.mode == MODE_LISTEN


def test_tab_enters_type_flush_discard_then_jump():
    eng, interval, c = make()
    c.handle("\t")
    assert c.mode == MODE_TYPE
    # flush BEFORE jump drops the tail; the soft discard
    # sits between them so the flushed partial joins the committed queue
    # while every still-soft segment is withdrawn AND forgotten before the
    # jump snaps what remains.
    assert eng.events == ["flush", "discard_soft", "jump"]


def test_tab_back_to_listen_flushes_partial_word():
    eng, interval, c = make()
    c.handle("\t")
    c.handle("h")
    c.handle("i")
    c.handle("\t")
    assert c.mode == MODE_LISTEN
    assert eng.flushes == 2          # enter + exit
    assert eng.jumps == 1            # enter only


def test_type_mode_chars_feed_engine():
    eng, interval, c = make()
    c.handle("\t")
    for ch in "hi bob":
        c.handle(ch)
    assert eng.fed == "hi bob"


def test_enter_maps_to_space():
    eng, interval, c = make()
    c.handle("\t")
    c.handle("h")
    c.handle("\r")
    c.handle("\n")
    assert eng.fed == "h  "


def test_backspace_and_escape_ignored_in_type():
    eng, interval, c = make()
    c.handle("\t")
    c.handle("\x08")   # backspace
    c.handle("\x1b")   # escape
    assert eng.fed == ""


def test_command_letters_are_content_in_type():
    eng, interval, c = make()
    eng.translator = FakeGradedTranslator()
    c.handle("\t")
    for ch in "gfsq":
        c.handle(ch)
    assert eng.fed == "gfsq"
    assert interval["v"] == 0.15
    assert eng.translator.grade == 1
    assert c.stop is False


def test_ctrl_chords_work_in_type_mode():
    eng, interval, c = make()
    eng.translator = FakeGradedTranslator()
    c.handle("\t")
    c.handle("\x07")   # Ctrl+G
    c.handle("\x06")   # Ctrl+F
    assert eng.translator.grade == 2
    assert interval["v"] < 0.15
    c.handle("\x13")   # Ctrl+S
    assert eng.fed == ""


def test_ctrl_chords_work_in_listen_mode():
    eng, interval, c = make()
    eng.translator = FakeGradedTranslator()
    c.handle("\x07")
    c.handle("\x06")
    assert eng.translator.grade == 2
    assert interval["v"] < 0.15


def test_ctrl_c_quits_in_both_modes():
    eng, interval, c = make()
    c.handle("\t")
    c.handle("\x03")
    assert c.stop is True


# --- Space+S status flash -----------------------------------------------------


class StatusEngine(FakeEngine):
    """fit_cells counts one cell per character: the composition rules are
    under test here, not UEB arithmetic — the real engine measures real
    cells (number signs, contractions) through liblouis."""

    def __init__(self, content_width, window=4):
        super().__init__()
        self.content_width = content_width
        self.window = window
        self.translator = FakeGradedTranslator()
        self.translator.grade = 2
        self.flashes = []

    def fit_cells(self, text, budget):
        return list(text[:budget]), len(text)

    def flash(self, text):
        self.flashes.append(text)


def make_status(width, mode="ticker", window=4, interval=0.15):
    eng = StatusEngine(width, window)
    c = PacerControls(eng, {"v": interval})
    c.advance_mode = mode
    return eng, c


def test_reading_wpm_is_the_user_facing_speed_unit():
    # 60 s / (interval * cells-per-word), rounded; grade-aware because an
    # uncontracted word costs more cells (6.0 vs 4.5). 0.6 s/cell reads
    # as about 22 wpm in grade 2, 17 in grade 1 (the shipped default, the
    # 5 s page, is ~278 ms/cell, ~48 wpm, on the eReader). Floored at 1 —
    # never "0 wpm".
    assert reading_wpm(0.6) == 22
    assert reading_wpm(0.6, grade=1) == 17
    assert reading_wpm(0.15) == 89
    assert reading_wpm(0.15, grade=1) == 67
    assert reading_wpm(0.3) == 44
    assert reading_wpm(99999.0) == 1


def test_status_speed_follows_the_grade():
    # Same interval, grade 1: fewer estimated wpm (uncontracted words cost
    # more cells), and the g1 tag rides along.
    eng, c = make_status(38)
    eng.translator.grade = 1
    c.show_status()
    assert eng.flashes == ["auto c4 67wpm g1"]


def test_status_wide_display_gets_every_item_with_long_labels():
    eng, c = make_status(38)         # 0.15 s/cell -> "89wpm"
    c.show_status()
    assert eng.flashes == ["auto c4 89wpm g2"]


def test_status_short_label_buys_back_the_speed():
    # "auto c4 89wpm" is 13 and "auto c4 89" is 10 — neither fits 9, but
    # the one-letter label carries the (unitless) speed: more core items win.
    eng, c = make_status(9)
    c.show_status()
    assert eng.flashes == ["a c4 89"]


def test_status_speed_drops_its_unit_before_dropping_entirely():
    # "auto c4 89wpm" is 13; the bare number keeps the long label complete.
    eng, c = make_status(10)
    c.show_status()
    assert eng.flashes == ["auto c4 89"]


def test_status_manual_has_no_speed_and_grade_rides_when_it_fits():
    eng, c = make_status(12, mode="manual")
    c.show_status()
    assert eng.flashes == ["man g2"]
    eng_tight, c_tight = make_status(5, mode="manual")
    c_tight.show_status()
    assert eng_tight.flashes == ["man"]        # grade would overflow: drops


def test_status_manual_never_shows_the_window():
    # Manual always flips the full display (word-wrapped pages), so the
    # window item belongs to ticker alone — even on a display wide enough
    # to carry it.
    eng, c = make_status(38, mode="manual")
    c.show_status()
    assert "c4" not in eng.flashes[0]


def test_status_full_window_reads_full():
    # At the full-display window the ticker flips word-wrapped pages; the
    # status names the state instead of a cell count, and the speed item
    # speaks the unit the speed keys step there: seconds per page
    # (0.15 s/cell x 20 cells = 3 s), not the wpm estimate.
    eng, c = make_status(20, window=20)
    c.show_status()
    assert eng.flashes == ["auto full 3s g2"]


def test_status_full_window_speed_degrades_to_bare_seconds():
    # "auto full 2s" is 12 cells and misses an 11-cell display; the bare
    # number keeps the item (0.15 x 11 = 1.65 -> "2").
    eng, c = make_status(11, window=20)
    c.show_status()
    assert eng.flashes == ["auto full 2"]


def test_status_grade_never_rides_past_a_dropped_source():
    # Grade rides only when every prior item made it, the input-source
    # item included: on a 20-cell budget "auto c4 89wpm" (13) fits, the
    # mic fits in neither spelling ("conference hall mic" -> 33,
    # "conference" -> 24), and grade (16) WOULD fit — but showing it after
    # a higher-priority drop would misrepresent what's missing.
    eng, c = make_status(20)
    c.mic_label = "conference hall mic"
    c.show_status()
    assert eng.flashes == ["auto c4 89wpm"]


# --- speed keys: seconds-per-page at the full window --------------------------


def test_full_window_speed_press_steps_half_a_second_per_page():
    # 0.5 s/cell x 20 content cells = a 10 s page. One press = half a
    # second (the TalkBack/JAWS adjustment grain), so the interval moves
    # by exactly 0.5/content_width per press.
    eng, c = make_status(20, window=20, interval=0.5)
    c.handle("s")
    assert c.interval["v"] == pytest.approx(10.5 / 20)
    c.handle("f")
    assert c.interval["v"] == pytest.approx(10.0 / 20)
    c.handle("f")
    assert c.interval["v"] == pytest.approx(9.5 / 20)


def test_full_window_speed_clamps_at_1_and_30_seconds_per_page():
    eng, c = make_status(20, window=20, interval=1.5 / 20)   # 1.5 s page
    c.handle("f")
    assert c.interval["v"] == pytest.approx(1.0 / 20)        # floored
    c.handle("f")
    assert c.interval["v"] == pytest.approx(1.0 / 20)        # stays floored
    c.interval["v"] = 29.5 / 20                              # 29.5 s page
    c.handle("s")
    assert c.interval["v"] == pytest.approx(30.0 / 20)       # ceilinged
    c.handle("s")
    assert c.interval["v"] == pytest.approx(30.0 / 20)


def test_full_window_speed_press_snaps_onto_the_half_second_grid():
    # An arbitrary --pace (9.74 s page) joins the grid on its first press
    # instead of stepping forever between the marks: 9.74 - 0.5 = 9.24
    # snaps to 9.0, and the announce shows the snapped value.
    eng, c = make_status(20, window=20, interval=9.74 / 20)
    c.handle("f")
    assert c.interval["v"] == pytest.approx(9.0 / 20)


def test_full_window_speed_press_announces_the_page_duration():
    # The press must be perceptible by touch: the resulting page duration
    # flashes, whole seconds bare ("10s") and real halves kept ("9.5s").
    eng, c = make_status(20, window=20, interval=0.5)        # 10 s page
    c.handle("f")
    assert eng.flashes == ["9.5s per page"]
    c.handle("s")
    assert eng.flashes == ["9.5s per page", "10s per page"]


def test_full_window_per_cell_interval_clamps_on_a_narrow_display():
    # A narrow display (4 content cells) at the 30 s page ceiling would put
    # 7.5 s on each cell — past the 5 s/cell max_interval the small-window
    # speed paths respect. The page clamp must not smuggle that through.
    eng, c = make_status(4, window=4, interval=29.5 / 4)
    c.handle("s")                                   # -> 30 s page
    assert c.interval["v"] == pytest.approx(c.max_interval)
    assert c.interval["v"] <= c.max_interval


def test_narrow_display_flash_reports_the_clamped_page_duration():
    # When the per-cell clamp bites, the flash must report the seconds the
    # display will actually take (5 s/cell x 4 cells = 20 s), not the
    # pre-clamp 30 s request — otherwise it disagrees with the control
    # panel, which derives its pace from the clamped interval.
    eng, c = make_status(4, window=4, interval=29.5 / 4)
    c.handle("s")                                   # requests a 30 s page
    assert eng.flashes == ["20s per page"]


def test_full_window_status_does_not_render_zero_seconds_for_a_fast_page():
    # A sub-second page (~0.4 s here) must not read as "0s": the full-window
    # status keeps a decimal so a fast pace stays perceptible.
    eng, c = make_status(40, window=40, interval=0.4 / 40)
    c.show_status()
    assert eng.flashes
    flash = eng.flashes[-1]
    assert "0s" not in flash
    assert "0.4s" in flash


def test_shipped_pace_default_is_5_seconds_per_page():
    # The shipped auto-advance default, guarded by value: a full page
    # lasts 5 s whatever the display width — the
    # JAWS default; TalkBack ships 3 s with the same scale-by-length
    # behavior — and the speed keys step it on the peers' 0.5 s grain
    # between 1 s and 30 s. On the eReader's 18 content cells the derived
    # interval is ~278 ms/cell (~48 wpm in grade 2).
    from braille_engine.controls import (
        DEFAULT_PAGE_SECONDS, default_interval_s)
    assert DEFAULT_PAGE_SECONDS == 5.0
    assert PacerControls.PAGE_SECONDS_STEP == 0.5
    assert PacerControls.PAGE_SECONDS_MIN == 1.0
    assert PacerControls.PAGE_SECONDS_MAX == 30.0
    assert default_interval_s(18) == pytest.approx(5.0 / 18)
    assert default_interval_s(38) == pytest.approx(5.0 / 38)
    # Degenerate widths never divide by zero or go negative.
    assert default_interval_s(0) == 5.0
    assert default_interval_s(None) == 5.0


def test_small_window_speed_press_keeps_the_multiplicative_step():
    # A 4-cell streaming window has no meaningful page duration: the
    # classic 0.8x/1.25x per-cell step stays, and the press announces the
    # wpm readout instead.
    eng, c = make_status(20, window=4, interval=0.15)
    c.handle("f")
    assert c.interval["v"] == pytest.approx(0.12)
    wpm = reading_wpm(0.12, grade=2, engine=eng)
    assert eng.flashes == [f"about {wpm} wpm"]
    c.handle("s")
    assert c.interval["v"] == pytest.approx(0.15)


def test_manual_s_announces_no_pace():
    # Manual has no pace, and 's' must never read as a dead key by touch:
    # the display says so instead of a stderr-only line.
    eng, c = make_status(20, mode="manual")
    c.handle("s")
    assert eng.flashes == ["manual, no pace"]
    assert c.interval["v"] == pytest.approx(0.15)            # untouched
    assert c.take_advance() is False                         # never a flip


def test_manual_f_still_advances_without_announcing():
    # 'f' keeps its manual meaning — the page flip itself is the tactile
    # answer, so no flash competes with the fresh page.
    eng, c = make_status(20, mode="manual")
    c.handle("f")
    assert c.take_advance() is True
    assert eng.flashes == []


def test_w_refuses_outside_ticker_and_flashes_why():
    eng, c = make_status(20, mode="manual")
    c.handle("w")
    assert eng.window == 4                 # untouched
    assert eng.flashes == ["window: auto only"]


def test_w_cycle_ends_at_the_full_display_then_wraps():
    # With a connected display the preset ladder tops out at the full
    # content width — word-wrapped pages — and the
    # next press wraps back to 1 cell.
    eng, c = make_status(20, window=8)
    c.handle("w")
    assert eng.window == 20                # the full-display stop
    assert eng.flashes == ["window: full display"]
    c.handle("w")
    assert eng.window == 1                 # wrapped


def test_status_mic_rides_between_speed_and_grade():
    # The shell-reported mic source outranks grade:
    # grade is inferable from the braille itself, the live mic is not.
    eng, c = make_status(38)
    c.mic_label = "usb mic"
    c.show_status()
    assert eng.flashes == ["auto c4 89wpm usb mic g2"]


def test_status_mic_degrades_to_its_first_word():
    # "auto c4 89wpm usb mic" is 21 and doesn't fit 17; "usb" alone does.
    # Grade then overflows (" g2" would make 20) and drops.
    eng, c = make_status(17)
    c.mic_label = "usb mic"
    c.show_status()
    assert eng.flashes == ["auto c4 89wpm usb"]
    # One cell wider at 20: the degraded mic leaves room for grade again.
    eng20, c20 = make_status(20)
    c20.mic_label = "usb mic"
    c20.show_status()
    assert eng20.flashes == ["auto c4 89wpm usb g2"]


def test_status_whitespace_mic_label_reads_as_no_mic():
    # The label is shell-reported free text: a whitespace-only value is
    # truthy but has no first word to degrade to — it must behave exactly
    # like "no mic reported", never crash the status chord.
    eng, c = make_status(38)
    c.mic_label = "   "
    c.show_status()
    assert eng.flashes == ["auto c4 89wpm g2"]


def test_status_no_mic_reported_shows_no_mic_item():
    # The default: shells that don't own capture (Windows today, the phone
    # recognizer) leave mic_label empty — the flash reads as before.
    eng, c = make_status(38)
    c.show_status()
    assert eng.flashes == ["auto c4 89wpm g2"]


class FakeFeeder:
    """Stands in for DemoStream: a streaming feeder with a fixed label."""

    def __init__(self, label):
        self.source_label = label
        self.active = bool(label)


def test_status_feeder_label_replaces_the_mic_while_demo_streams():
    # While the demo feeder streams, IT
    # is the live text source under the reader's fingers, so the status
    # flash names it instead of the shell-reported mic — which returns as
    # the fallback the moment the feeder goes idle (the tests above, which
    # all run with the real, idle DemoStream).
    eng, c = make_status(38)
    c.mic_label = "usb mic"
    c.demo = FakeFeeder("demo")
    c.show_status()
    assert eng.flashes == ["auto c4 89wpm demo g2"]


def test_status_caption_replay_reads_captions():
    # The timed caption replay names itself distinctly: "captions", not
    # "demo" — a different source, same slot.
    eng, c = make_status(38)
    c.demo = FakeFeeder("captions")
    c.show_status()
    assert eng.flashes == ["auto c4 89wpm captions g2"]


def test_status_feeder_label_never_leapfrogs_a_dropped_speed():
    # Same rule as the mic and grade riders: the source item only rides
    # when every core item made it.
    eng, c = make_status(11, window=1, interval=0.000001)
    c.demo = FakeFeeder("demo")
    c.show_status()
    assert eng.flashes == ["auto c1"]


def test_status_mic_never_leapfrogs_a_dropped_speed():
    # Speed can't fit in any spelling (0.000001 s/cell -> "13333333wpm" /
    # "13333333"), so the mic rider stays off the frame even though
    # "auto c1 usb" would fit — same rule as grade below.
    eng, c = make_status(11, window=1, interval=0.000001)
    c.mic_label = "usb mic"
    c.show_status()
    assert eng.flashes == ["auto c1"]


def test_status_grade_never_leapfrogs_a_dropped_speed():
    # Speed can't fit in any spelling (0.0001 s/cell -> "133333wpm" /
    # "133333"), so grade must stay off the frame even though "auto c1 g2"
    # would fit: showing a lower-priority item after dropping a higher one
    # would misrepresent what's missing.
    eng, c = make_status(10, window=1, interval=0.0001)
    c.show_status()
    assert eng.flashes == ["auto c1"]


def test_status_before_the_display_starts_is_a_log_line_only(capsys):
    eng, c = make_status(0)          # content_width unset: not started
    c.show_status()
    assert eng.flashes == []
    assert "status unavailable" in capsys.readouterr().err


# --- summarize-on-demand (Space+S / terminal shift+S) ------------------------
# The recap machinery lives behind its own command, separate from the
# jump: jump_to_live is the plain snap everywhere, summarize_now
# owns the fetch/stream state machine, and each degrades into the other's
# snap on cancel/skip/failure.

import threading
import time

from braille_engine.cells import BLANK
from braille_engine.engine import BrailleEngine
from braille_engine.sinks.simulated import SimulatedSink
from braille_engine.translator import DevUebTranslator


def _wait_until(cond, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(0.01)
    return False


class FakeSummarizer:
    """Scripted summarizer: records calls; optionally blocks until released."""

    def __init__(self, reply="hi", gate=None):
        self.reply = reply
        self.gate = gate          # threading.Event: wait before answering
        self.calls = []

    def summarize(self, text, chars, grade):
        self.calls.append((text, chars, grade))
        if self.gate is not None:
            self.gate.wait(2.0)
        return self.reply


def make_live(width=5, summarizer=None, gauge=False):
    # ``gauge`` keeps the CONTENT width at ``width`` (the gauge takes two
    # extra cells), so the same _letters frames fit either way; cell 2 is
    # then the content-kind marker the marker tests assert on (s/h; the
    # ambient stream reads blank).
    from braille_engine.gauge import BacklogGauge
    eng = BrailleEngine(DevUebTranslator(),
                        SimulatedSink(width=width + 2 if gauge else width,
                                      echo=False),
                        gauge=BacklogGauge() if gauge else None)
    eng.start()
    interval = {"v": 0.02}
    return eng, PacerControls(eng, interval, summarizer=summarizer)


def _letters(text):
    tr = DevUebTranslator()
    return [tr.translate(ch)[0] for ch in text]


def test_jump_to_live_is_always_the_plain_snap():
    # However big the backlog and whatever summarizer is
    # configured, the jump key never fetches — a single press, no hold.
    summ = FakeSummarizer(reply="hi")
    eng, c = make_live(summarizer=summ)
    eng.feed("abcdefgh")          # 8 cells > width 5: the snap drops 3
    eng.flush_input()
    c.jump_to_live()
    assert summ.calls == []
    assert eng.frame() == _letters("defgh")    # exactly the classic jump
    assert c.catchup is None
    assert not eng._hold
    assert eng.tick() == 0        # nothing queued behind the snap


def test_backlog_that_fits_snaps_instantly_without_the_llm():
    summ = FakeSummarizer()
    eng, c = make_live(summarizer=summ)
    eng.feed("abc")
    eng.flush_input()             # 3 cells < width 5: nothing would be lost
    c.summarize_now()
    assert summ.calls == []
    assert eng.frame() == [BLANK, BLANK] + _letters("abc")
    assert c.catchup is None      # the pre-claimed state was released...
    assert not eng._hold          # ...and the hold healed on the snap path


def test_summarize_without_a_summarizer_degrades_to_the_plain_snap():
    eng, c = make_live(summarizer=None)
    eng.feed("abcdefgh")
    eng.flush_input()
    c.summarize_now()
    assert eng.frame() == _letters("defgh")
    assert c.catchup is None
    assert not eng._hold


def test_summarize_without_a_summarizer_flashes_the_refusal():
    # With no summarize path (file replay, --no-summary) the
    # refusal must be FELT on the display (flash + dwell), never only in
    # the log — a silent snap is indistinguishable from a lost chord.
    eng, c = make_live(summarizer=None)
    eng.feed("abcdefgh")
    eng.flush_input()
    c.summarize_now()
    event = eng.frame_event()
    assert event["kind"] == "flash"
    assert "no summary" in event["text"]
    assert c.announce_until > 0            # the flash claimed a dwell


def test_failed_fetch_flashes_the_refusal():
    # The fetch-failure fallback snap must announce too: without it a dead
    # server reads exactly like a recap that never existed.
    summ = FakeSummarizer(reply=None)
    eng, c = make_live(summarizer=summ)
    eng.feed("abcdefgh")
    eng.flush_input()
    c.summarize_now()
    assert _wait_until(lambda: c._catchup is None and not eng._hold)
    assert _wait_until(
        lambda: (eng.frame_event() or {}).get("kind") == "flash")
    assert "no summary" in eng.frame_event()["text"]


def test_jump_when_already_live_flashes_instead_of_a_silent_repaint():
    # Jump-to-live with nothing pending must not just repaint an
    # identical frame — "command worked, nothing to do" would feel exactly
    # like "command lost". The press answers by touch.
    eng, c = make_live()
    eng.feed("abc")
    eng.flush_input()
    c.jump_to_live()                       # the snap: real display change
    assert eng.frame_event()["kind"] != "flash"
    c.jump_to_live()                       # already live: nothing pending
    event = eng.frame_event()
    assert event["kind"] == "flash"
    assert "already live" in event["text"]
    assert c.announce_until > 0            # the flash claimed a dwell


def test_summarize_when_already_live_flashes_nothing_to_recap():
    # The summary twin of the jump's "already live": no backlog, no pan —
    # the snap repaints an identical frame, so the press owes its answer.
    summ = FakeSummarizer(reply="hi")
    eng, c = make_live(summarizer=summ)
    c.summarize_now()
    assert summ.calls == []                # nothing was worth fetching
    event = eng.frame_event()
    assert event["kind"] == "flash"
    assert "nothing to recap" in event["text"]
    assert c.catchup is None
    assert not eng._hold                   # the hold healed on the way out


def test_catchup_state_and_hold_precede_the_queue_drain():
    # With the state/hold set only AFTER
    # take_pending, the pacer could slip into the drained-queue gap and
    # either exit for good (source done, catchup still None — the recap
    # would land on a dead loop) or emit a just-hardened newer word ahead
    # of the recap of older speech.
    gate = threading.Event()
    summ = FakeSummarizer(reply="hi", gate=gate)
    eng, c = make_live(summarizer=summ)
    eng.feed("abcdefgh")
    eng.flush_input()
    seen = {}
    original = eng.take_pending

    def spying_take_pending():
        seen["phase"] = c.catchup
        seen["hold"] = eng._hold
        return original()

    eng.take_pending = spying_take_pending
    c.summarize_now()
    assert seen == {"phase": "fetching", "hold": True}
    gate.set()


def test_big_backlog_streams_as_a_summary_that_drains_to_live():
    summ = FakeSummarizer(reply="hi")
    eng, c = make_live(summarizer=summ)
    eng.feed("abcdefgh")          # 8 cells > width 5: the snap would drop 3
    eng.flush_input()
    c.summarize_now()
    assert _wait_until(lambda: c.catchup == "streaming")
    assert summ.calls == [("abcdefgh", 8, 1)]   # floor: one display's worth
    assert c.last_summary == "hi"
    eng.feed("wxy ")              # live speech QUEUES behind the recap
    assert eng.tick() == 1        # the recap's leading separator blank
    assert eng.tick() == 1        # then the summary streams like text
    assert eng.tick() == 1
    assert eng.frame()[-2:] == _letters("hi")
    assert eng.tick() == 1        # the recap's trailing separator space
    assert c.catchup is None      # drained: the catch-up ended by itself
    for _ in range(4):            # the flushed word's owed space, then w x y
        assert eng.tick() == 1    # ...live speech follows on the same clock
    assert eng.frame()[-3:] == _letters("wxy")


def test_jump_mid_recap_skips_the_rest_and_snaps_to_live():
    # Thumb Next mid-recap: it is "jump to live", so it ends the recap and
    # snaps — the reader never has to wait a streaming summary out.
    summ = FakeSummarizer(reply="hello")
    eng, c = make_live(summarizer=summ)
    eng.feed("abcdefgh")
    eng.flush_input()
    c.summarize_now()
    assert _wait_until(lambda: c.catchup == "streaming")
    eng.feed("wxy ")              # live speech queues behind the recap
    assert eng.tick() == 1        # reader starts the recap (its separator)...
    c.jump_to_live()              # ...and bails out mid-summary
    assert c.catchup is None
    assert not eng.summary_pending()          # the rest of the recap died
    # The snap: the flushed word's owed separator, then the live text.
    assert eng.frame() == [BLANK] + _letters("wxy") + [BLANK]
    eng.feed("z ")                # streaming is live again
    assert eng.tick() == 1


def test_summarize_mid_recap_also_skips_to_live():
    # Space+S mid-recap skips: the second press
    # ends the recap and snaps, it never stacks a second summary.
    summ = FakeSummarizer(reply="hello")
    eng, c = make_live(summarizer=summ)
    eng.feed("abcdefgh")
    eng.flush_input()
    c.summarize_now()
    assert _wait_until(lambda: c.catchup == "streaming")
    eng.feed("wxy ")
    assert eng.tick() == 1
    c.summarize_now()             # press again mid-recap: skip to live
    assert c.catchup is None
    assert not eng.summary_pending()
    assert summ.calls != [] and len(summ.calls) == 1   # no second fetch
    assert eng.frame() == [BLANK] + _letters("wxy") + [BLANK]


def test_summary_budget_scales_with_the_backlog_and_caps():
    long_text = ("alpha beta gamma delta " * 25).strip()   # ~570 chars
    summ = FakeSummarizer(reply="ok")
    eng, c = make_live(summarizer=summ)
    eng.feed(long_text + " ")
    c.summarize_now()
    assert _wait_until(lambda: summ.calls != [])
    _text, chars, _grade = summ.calls[0]
    assert chars == len(long_text) // 5        # ~1 char per 5 of backlog

    huge_text = ("word " * 800).strip()        # ~4000 chars
    summ2 = FakeSummarizer(reply="ok")
    eng2, c2 = make_live(summarizer=summ2)
    eng2.feed(huge_text + " ")
    c2.summarize_now()
    assert _wait_until(lambda: summ2.calls != [])
    assert summ2.calls[0][1] == 700            # hard cap on the recap


def test_summary_failure_falls_back_to_the_plain_snap():
    summ = FakeSummarizer(reply=None)
    eng, c = make_live(summarizer=summ)
    eng.feed("abcdefgh")
    eng.flush_input()
    c.summarize_now()
    assert _wait_until(lambda: c._catchup is None and not eng._hold)
    assert eng.frame() == _letters("defgh")     # exactly the classic jump
    assert eng.tick() == 0


def test_second_press_cancels_the_summary_to_a_snap():
    # Space+S mid-fetch cancels: down to the plain snap.
    gate = threading.Event()
    summ = FakeSummarizer(reply="hi", gate=gate)
    eng, c = make_live(summarizer=summ)
    eng.feed("abcdefgh")
    eng.flush_input()
    c.summarize_now()
    assert _wait_until(lambda: summ.calls != [])
    c.summarize_now()             # impatience: cancel back to the plain snap
    assert eng.frame() == _letters("defgh")
    eng.feed("z ")
    assert eng.tick() == 1        # hold already released by the cancel
    frame_after_cancel = eng.frame()
    gate.set()                    # the late summary must be discarded
    time.sleep(0.1)
    assert eng.frame() == frame_after_cancel
    assert not eng.summary_pending()   # ...not queued behind the snap either


def test_cancel_snap_does_not_latch_a_stale_s():
    # Two quick thumb-Next presses — the second cancels the fetch to a
    # plain snap — must not WRITE the snap frame while the hold is still
    # set, or the sink latches the s marker over plain speech until the
    # next organic write (indefinitely in manual mode). Asserting on
    # frame(), recomputed after the release, would miss this: the test
    # checks what the display saw. (The snap's ambient speech reads
    # BLANK — the honest non-s cell.)
    from braille_engine.engine import ORIGIN_SPEECH, SUMMARY_MARKER
    gate = threading.Event()
    summ = FakeSummarizer(reply="hi", gate=gate)
    eng, c = make_live(summarizer=summ, gauge=True)
    eng.feed("abcdefgh ", seg="s1")
    c.summarize_now()
    assert _wait_until(lambda: summ.calls != [])
    assert eng.sink.frames[-1][1] == SUMMARY_MARKER  # fetch ack: s, honest
    c.jump_to_live()              # thumb Next mid-fetch: cancel to the snap
    assert eng.sink.frames[-1][1] == BLANK           # as physically written
    assert eng.sink.frames[-1][1] != SUMMARY_MARKER
    assert ORIGIN_SPEECH in eng._origin              # tags rode the snap
    assert not eng._hold
    gate.set()


def test_fitting_snap_does_not_latch_the_hold_s():
    # The single-press instant snap had the same latch: show_frame ran
    # inside the hold, then the release never repainted.
    from braille_engine.engine import SUMMARY_MARKER
    eng, c = make_live(summarizer=FakeSummarizer(), gauge=True)
    eng.feed("abc ", seg="s1")    # 4 cells < width 5: fits, no LLM
    c.summarize_now()
    assert eng.sink.frames[-1][1] == BLANK
    assert eng.sink.frames[-1][1] != SUMMARY_MARKER
    assert not eng._hold


def test_fallback_snap_does_not_latch_the_hold_s():
    # ...and so did the fetch-failure fallback snap in _fetch_summary.
    from braille_engine.engine import SUMMARY_MARKER
    summ = FakeSummarizer(reply=None)
    eng, c = make_live(summarizer=summ, gauge=True)
    eng.feed("abcdefgh ", seg="s1")
    c.summarize_now()
    assert _wait_until(lambda: c._catchup is None and not eng._hold)
    assert eng.sink.frames[-1][1] != SUMMARY_MARKER


def test_cancel_snap_keeps_caption_tags_over_a_stream():
    # The cancel snap over a caption stream must tag captions, not speech
    # (truthful tags; neither renders a letter).
    from braille_engine.engine import (ORIGIN_CAPTIONS, ORIGIN_SPEECH,
                                       SUMMARY_MARKER)
    gate = threading.Event()
    summ = FakeSummarizer(reply="hi", gate=gate)
    eng, c = make_live(summarizer=summ, gauge=True)
    eng.speech_is_stream = True      # a caption replay is feeding
    eng.feed("abcdefgh ", seg="s1")
    c.summarize_now()
    assert _wait_until(lambda: summ.calls != [])
    assert eng.sink.frames[-1][1] == SUMMARY_MARKER  # fetch ack: still s
    c.jump_to_live()
    assert eng.sink.frames[-1][1] == BLANK
    assert ORIGIN_CAPTIONS in eng._origin
    assert ORIGIN_SPEECH not in eng._origin
    assert not eng._hold
    gate.set()


def test_fitting_snap_keeps_caption_tags_over_a_stream():
    from braille_engine.engine import ORIGIN_CAPTIONS
    eng, c = make_live(summarizer=FakeSummarizer(), gauge=True)
    eng.speech_is_stream = True      # a caption replay is feeding
    eng.feed("abc ", seg="s1")    # 4 cells < width 5: fits, no LLM
    c.summarize_now()
    assert eng.sink.frames[-1][1] == BLANK
    assert ORIGIN_CAPTIONS in eng._origin
    assert not eng._hold


def test_fallback_snap_keeps_caption_tags_over_a_stream():
    from braille_engine.engine import ORIGIN_CAPTIONS
    summ = FakeSummarizer(reply=None)
    eng, c = make_live(summarizer=summ, gauge=True)
    eng.speech_is_stream = True      # a caption replay is feeding
    eng.feed("abcdefgh ", seg="s1")
    c.summarize_now()
    assert _wait_until(lambda: c._catchup is None and not eng._hold)
    assert ORIGIN_CAPTIONS in eng._origin


def test_tab_to_type_abandons_an_inflight_summary_without_a_snap():
    gate = threading.Event()
    summ = FakeSummarizer(reply="hi", gate=gate)
    eng, c = make_live(summarizer=summ)
    eng.feed("abcdefgh")
    eng.flush_input()
    c.summarize_now()
    assert _wait_until(lambda: summ.calls != [])
    c.handle("\t")                # into HUMAN mode: clean slate for typing
    assert c.mode == MODE_TYPE
    c.handle("z")
    c.handle("\r")                # commit the typed word (Enter = space)
    assert eng.tick() == 1        # hold released; typed content streams
    frame_typing = eng.frame()
    gate.set()                    # the late summary must not stomp typing
    time.sleep(0.1)
    assert eng.frame() == frame_typing
    assert not eng.summary_pending()   # ...nor sneak into the typing queue


def test_tab_to_type_drops_a_streaming_summary():
    summ = FakeSummarizer(reply="hi")
    eng, c = make_live(summarizer=summ)
    eng.feed("abcdefgh")
    eng.flush_input()
    c.summarize_now()
    assert _wait_until(lambda: c.catchup == "streaming")
    c.handle("\t")                # into HUMAN mode: the recap is abandoned
    assert c.catchup is None
    assert not eng.summary_pending()
    c.handle("z")
    c.handle("\r")
    assert eng.tick() == 1


def _dwell(controls, text):
    """Seconds the announcement claimed, measured at the claim."""
    start = time.monotonic()
    controls.announce(text)
    return controls.announce_until - start


def test_announce_dwell_is_acquisition_plus_scaled_reading():
    # ACQUIRE + cells x interval x MARGIN. The two terms pay for
    # different things: finding the message is a FIXED cost — the same
    # work for six cells as for a full line — so it is an addend, not a
    # multiplier. Reading is the per-cell term, billed BELOW
    # the streaming pace: these are mostly confirmations where one token
    # carries the news, and every second of dwell is a second of live
    # conversation queueing behind them.
    eng, c = make_status(20, interval=0.5)
    short = _dwell(c, "manual")                   # 6 shown cells
    long_ = _dwell(c, "auto c4 89wpm usb g2")     # 20 cells fill the line
    assert abs(short - (ANNOUNCE_ACQUIRE + 6 * 0.5 * ANNOUNCE_MARGIN)) < 0.2
    assert abs(long_ - (ANNOUNCE_ACQUIRE + 20 * 0.5 * ANNOUNCE_MARGIN)) < 0.2
    # The 14 extra cells buy exactly their reading time and nothing else:
    # acquisition is charged once, not stretched by length.
    assert abs((long_ - short) - 14 * 0.5 * ANNOUNCE_MARGIN) < 0.2


def test_shipped_constants_at_the_shipped_pace():
    # Absolute seconds, deliberately hard-coded: every other dwell test
    # derives its expectation from the constants, so it would follow a
    # retune silently. This one is the guard on the VALUES — the shipped
    # default pace (a 5 s full page, screen-reader-aligned:
    # 5/18 s ~ 278 ms/cell) on an 18-content-cell display, which is the
    # NLS eReader.
    eng, c = make_status(18, interval=5.0 / 18)
    # A confirmation: long enough to find and read one changed token,
    # short enough that a live conversation barely gaps. Banded tightly
    # — a loose band would let a 33% drift in ANNOUNCE_ACQUIRE pass
    # unnoticed. "grade 2" is 7 cells in the fake:
    # 0.75 + 7 * (5/18) * 0.35 = 1.43 s.
    assert 1.3 < _dwell(c, "grade 2") < 1.6
    # The status line: the densest frame, and the one that pays most in
    # queued speech — it must stay at or under the cap.
    line = _dwell(c, "auto c4 89wpm usb g2")
    assert 2.3 < line <= ANNOUNCE_CEILING


def test_unsolicited_alerts_get_the_higher_ceiling():
    # The ordinary ceiling is affordable because the message was ASKED
    # for — a capped status line can just be requested again. The idle
    # watchdog's attention check can't: it is timer-issued and costs the
    # mic if missed, so capping it like a confirmation would hand a slow
    # reader the first few cells of a question nobody repeats.
    from braille_engine.idle_watchdog import WARN_S
    # 4 s/cell: 0.75 + 18 * 4 * 0.35 = 25.95 s of honest read time, so
    # BOTH caps bind and the two classes are distinguishable.
    eng, c = make_status(18, interval=4.0)
    warn = "still reading? press any key"
    assert abs(_dwell(c, warn) - ANNOUNCE_CEILING) < 0.2
    c.announce_until = 0.0
    start = time.monotonic()
    c.announce(warn, important=True)
    assert abs(c.announce_until - start - ANNOUNCE_CEILING_ALERT) < 0.2
    # Both caps stay well inside the window the reader has to answer in.
    assert ANNOUNCE_CEILING < ANNOUNCE_CEILING_ALERT < WARN_S
    # The alert cap's VALUE, pinned to what it exists to cover: the warn's
    # 28 shown cells on a 40-cell display — the width where the whole
    # sentence actually lands — at a plausible slow 1.5 s/cell. Every
    # assertion above derives from the constant and would follow a
    # retune silently.
    assert ANNOUNCE_CEILING_ALERT >= (
        ANNOUNCE_ACQUIRE + 28 * 1.5 * ANNOUNCE_MARGIN)


def test_the_alert_ceiling_covers_the_warn_at_realistic_slow_paces():
    # The point of the higher cap is that the question is READABLE, not
    # merely longer. At 1.5 s/cell — a plausible slow braille pace — the
    # 18 shown cells bill 0.75 + 18 * 1.5 * 0.35 = 10.2 s, and the alert
    # cap must not cut that down.
    eng, c = make_status(18, interval=1.5)
    start = time.monotonic()
    c.announce("still reading? press any key", important=True)
    assert abs(c.announce_until - start - 10.2) < 0.3


def test_announce_reading_is_billed_below_the_streaming_pace():
    # MARGIN < 1 is the point, not an accident: a confirmation is scanned
    # for its one changed token, and the dwell is paid for in queued
    # speech. The read term must stay under what the same cells would
    # cost as streamed content.
    eng, c = make_status(20, interval=0.5)
    reading = _dwell(c, "auto c4 89wpm usb g2") - ANNOUNCE_ACQUIRE
    assert reading < 20 * 0.5           # cheaper than streaming the line
    assert ANNOUNCE_MARGIN < 1.0


def test_short_message_at_a_fast_pace_still_gets_the_floor():
    # The floor outranks the formula: at a fast pace a two-cell answer
    # bills well under 2 s, and a flash that brief is the "it vanished
    # before I found it" failure the whole model exists to prevent.
    eng, c = make_status(20, interval=0.05)
    assert abs(_dwell(c, "hi") - ANNOUNCE_SECONDS) < 0.2


def test_announce_dwell_charges_only_the_shown_cells():
    # A message longer than the display is truncated by fit_cells — the
    # cells that never landed must not buy dwell time.
    eng, c = make_status(8, interval=0.5)
    assert abs(_dwell(c, "a message far wider than eight cells")
               - (ANNOUNCE_ACQUIRE + 8 * 0.5 * ANNOUNCE_MARGIN)) < 0.2


class UnmeasurableEngine(FakeEngine):
    """No display to measure against: content_width is None before one
    connects, and a minimal shell may not expose fit_cells at all."""
    content_width = None


def test_announce_dwell_falls_back_to_the_floor_without_a_display():
    # Nothing to measure: use the floor rather than guessing from
    # characters. Must exercise the GUARD, not reach the floor by
    # arithmetic — a real engine over a 5-cell sink would pass through
    # the formula and leave the guard uncovered.
    eng = UnmeasurableEngine()
    assert not hasattr(eng, "fit_cells")
    c = PacerControls(eng, {"v": 4.0})    # a pace that would bill ~10 s+
    assert abs(_dwell(c, "whatever length this is, it cannot be measured")
               - ANNOUNCE_SECONDS) < 0.2


def test_mode_switch_announcement_uses_the_scaled_dwell():
    # Both claim sites share _announce_dwell — a regression could silently
    # revert one. "manual" is 6 shown cells at the one-cell-per-char fake.
    eng, c = make_status(20, interval=0.5)
    start = time.monotonic()
    c.set_advance_mode("manual")
    assert abs(c.announce_until - start
               - (ANNOUNCE_ACQUIRE + 6 * 0.5 * ANNOUNCE_MARGIN)) < 0.2


def test_announce_dwell_is_ceilinged_under_the_watchdog_window():
    # The ceiling is the conversation-cost backstop: the reader falls
    # behind live speech for the whole dwell, so no message holds the
    # display past it however slow the pace (a solicited one can just be
    # asked for again). It also keeps every dwell far inside the idle
    # watchdog's answer window — the warn flash is itself an announce,
    # and a dwell outliving that window would let the mic cut off the
    # slowest readers mid-"still reading?".
    from braille_engine.idle_watchdog import WARN_S
    eng, c = make_status(20, interval=5.0)
    # 1.5 + 20 * 5 * 0.7 = 71.5 s of "honest" read time -> capped.
    assert abs(_dwell(c, "auto c4 89wpm usb g2") - ANNOUNCE_CEILING) < 0.2
    assert ANNOUNCE_CEILING < WARN_S
    # The cap's VALUE, in absolute seconds (every assertion above follows
    # the constant, so none of them would notice a retune): a single
    # confirmation that costs more than a quarter-minute of live
    # conversation is the failure this cap exists to prevent.
    assert ANNOUNCE_CEILING <= 15.0
    # And at a slow pace the cap really is what binds — 1.5 + 18 * 2 *
    # 0.7 = 26.7 s of "honest" read time, cut to the cap.
    eng2, c2 = make_status(18, interval=2.0)
    assert _dwell(c2, "auto c4 89wpm usb g2") <= 15.0


def test_manual_mode_announce_charges_the_last_expressed_pace():
    # Deliberate (pinned): manual has no live pace, but interval is the
    # reader's last-expressed one and nothing waits behind the dwell in
    # manual — every exit is reader-initiated and dismisses.
    eng, c = make_status(20, mode="manual", interval=0.5)
    assert abs(_dwell(c, "auto c4 89wpm usb g2")
               - (ANNOUNCE_ACQUIRE + 20 * 0.5 * ANNOUNCE_MARGIN)) < 0.2


def test_terminal_command_keys_dismiss_the_dwell():
    # Dismiss-and-execute, terminal edition: with pace-scaled dwells the
    # display-only escape hatch left terminal readers frozen for the
    # remainder. Command keys dismiss; typed Human-mode content never.
    eng, c = make_status(20)
    deadline = time.monotonic() + 30.0
    c.announce_until = deadline
    c.handle("f")                        # listen-mode command: dismisses
    # The speed press announces its own result now, so the stale dwell is
    # replaced by a fresh, shorter one rather than zeroed.
    assert 0.0 < c.announce_until < deadline
    c.mode = MODE_TYPE
    c.announce_until = deadline
    c.handle("z")                        # typed content: never dismisses
    assert c.announce_until == deadline


def test_jump_to_live_dismisses_the_dwell():
    # "Take me to live" moots any announcement mid-dwell — the snap used
    # to land and then streaming sat frozen for the dwell remainder.
    eng, c = make_status(20)
    c.announce_until = time.monotonic() + 30.0
    c.jump_to_live()
    assert c.announce_until == 0.0
    assert eng.jumps == 1


def test_summarize_now_dismisses_the_dwell():
    # Same mid-dwell rule for the summary command. The stale dwell goes;
    # with no summarizer the refusal flash then claims its own, shorter
    # one (dismiss-and-execute: the command's own answer replaces the
    # dismissed message).
    eng, c = make_status(20)
    deadline = time.monotonic() + 30.0
    c.announce_until = deadline
    c.summarize_now()
    assert 0.0 < c.announce_until < deadline
    assert eng.jumps == 1         # no summarizer here: degraded to the snap


def test_announce_failure_resets_the_dwell():
    # announce() claims the dwell BEFORE flashing (same race-avoidance as
    # set_advance_mode); a failed flash must release it, or the pacer waits
    # out a dwell for a message that never landed.
    eng, c = make_status(20)

    def boom(_text):
        raise RuntimeError("display gone")

    eng.flash = boom
    c.announce("hi")
    assert c.announce_until == 0.0


# --- display ownership (reply echo) ------------------------------------------
# One definition — display_owner — answers "does another mode own the
# display?" for the pace loop, the dwell gauge loop, announce(), and the
# key dispatch. These pin the owner name and the getattr-safety run.py's
# loops rely on (minimal fakes don't define the flag).

def test_display_owner_names_the_owning_mode():
    from types import SimpleNamespace
    from braille_engine.controls import OWNER_REPLY, display_owner
    eng, _, c = make()
    assert display_owner(c) is None
    c.reply = SimpleNamespace(active=True)
    assert display_owner(c) == OWNER_REPLY
    # getattr-safe on minimal fakes: run.py's loops pass test doubles
    # that don't define the flag.
    class Bare:
        pass
    assert display_owner(Bare()) is None
    assert display_owner(SimpleNamespace(reply_active=True)) == OWNER_REPLY


def test_announce_suppression_string_while_a_reply_owns_the_display(capsys):
    # The flash is dropped, the dwell is never claimed, and the status
    # line explains WHY — byte-identical to the pre-owner-refactor copy.
    from types import SimpleNamespace
    eng, _, c = make()
    c.reply = SimpleNamespace(active=True)
    c.announce("grade 2")
    assert eng.flashes == []
    assert c.announce_until == 0.0
    err = capsys.readouterr().err
    assert ("[keys] not shown on the display "
            "(reply in progress): grade 2\n") in err


@pytest.mark.parametrize("with_gauge", [True, False])
def test_human_mode_promises_the_h_marker_only_with_a_gauge(capsys,
                                                            with_gauge):
    # The h marker lives in the gauge's separator cell: without a gauge
    # the status line must not promise it.
    from braille_engine.engine import BrailleEngine
    from braille_engine.gauge import BacklogGauge
    from braille_engine.sinks.simulated import SimulatedSink
    from braille_engine.translator import DevUebTranslator

    engine = BrailleEngine(DevUebTranslator(),
                           SimulatedSink(width=12, echo=False),
                           gauge=BacklogGauge() if with_gauge else None)
    engine.start()
    PacerControls(engine, {"v": 0.1}).handle("\t")
    assert ("h in cell 2" in capsys.readouterr().err) is with_gauge
