"""Core reading engine.

Owns the queue of not-yet-shown words, the rolling display buffer, the paced
tick, and the reader's catch-up commands. Hardware-agnostic: it talks only to
the translator, assembler and sink interfaces.

Invariants:

* Append-only. A cell that has been shown is never rewritten. ``tick``
  appends up to ``window`` cells per refresh. The only whole-frame
  replacement is reader catch-up (``jump_to_live``, and the
  ``take_pending``/``show_frame`` pair the summary is built from). ``flip``
  repaints the whole line, but only by appending a full page plus blank
  padding that pushes the old page out of the rolling buffer.
* With ``window`` > 1 a refresh never ends mid-sign: cells stream in
  translator *units* (a two-cell contraction, or an indicator plus the cell
  it governs), and a unit that would straddle the boundary leads the next
  refresh. Only a unit wider than the whole window splits.
* Translation happens at emission. Queued words are plain text until they
  reach the front, so a grade switch applies to every word not yet started;
  only the word mid-scroll finishes in its original grade.
* Revisable segments. Text may arrive tagged with a segment id;
  ``revise_segment`` replaces that segment's still-queued words. Words
  already emitted, including the one mid-display, are frozen. Segments fed
  with ``final=False`` are soft: they queue (backlog, gauge and catch-up see
  them) but do not render until hardened, so the display only ever shows
  text the transcript also shows.
* Eager mode (``set_eager``) relaxes the render gate only: soft text may
  stream, except each soft segment's last queued word (the unstable
  frontier of a growing partial), which waits for a newer partial, the
  final, or ``harden_all``. Streamed words stay frozen even if the provider
  later disagrees; ``segment_text`` reports what was actually shown.
"""

import re
import sys
import threading
import time
from collections import deque
from itertools import islice

from .assembler import WordAssembler
from .cells import BLANK, dots_to_pattern
from .filters import digits as _digits_filter
from .translator import TranslationError

_SURROGATE = re.compile("[\ud800-\udfff]")

# Cell 2 (the gauge's separator cell) marks EXCEPTIONAL content only. The
# everyday live stream (speech or captions) renders blank: a letter that is
# always there carries no information.
#   s (dots 2-3-4)   on-demand summary, or a summary fetch holding the display
#   h (dots 1-2-5)   typed text (Human mode; also file/stdin replay)
# s outranks h when a window briefly holds both.
SUMMARY_MARKER = dots_to_pattern("234")
TYPED_MARKER = dots_to_pattern("125")
# r (dots 1-2-3-5): reply-mode echo frames only (reply_frame), which bypass
# the rolling buffer.
REPLY_MARKER = dots_to_pattern("1235")

# Per-cell origin tags. They drive the marker, the speaker highlight and
# catch-up bookkeeping; speech and captions both render a blank marker.
ORIGIN_SUMMARY = "summary"
ORIGIN_TYPED = "typed"
ORIGIN_SPEECH = "speech"
ORIGIN_CAPTIONS = "captions"

# Dwell classes: every emitted unit carries one, and a tick reports its cells
# priced by class so the pacer can dwell a space or punctuation cell for a
# fraction of the per-cell interval (reading density).
DWELL_TEXT = "text"
DWELL_SPACE = "space"
DWELL_PUNCT = "punct"

# Source characters whose cells take the punctuation dwell, mid-word ones
# included: they are boundary marks, not content.
_PUNCT_CHARS = set(".,;:!?\"'()[]-*/–—‘’“”…")


class BrailleEngine:
    # Reader-selectable windows. The cycle's last stop is the full content
    # width, where tick() flips word-wrapped pages (see window_is_full).
    WINDOW_SIZES = (1, 2, 4, 6, 8)

    # Shown-cell history kept for panning: ~50 pages of a 40-cell display.
    HISTORY_CELLS = 2000

    # Pan depth is in cells, the gauge in words. A rough conversion is
    # enough: the gauge buckets are coarse.
    PAN_CELLS_PER_WORD = 5

    # The wpm readout measures cells-per-word over this many recent words
    # (under a minute of reading), and returns None below DENSITY_MIN_WORDS,
    # where one long word would swing the average.
    DENSITY_WORDS = 40
    DENSITY_MIN_WORDS = 8

    # A soft segment normally hardens within seconds. One silent far longer
    # is an orphan (its final was lost, e.g. in a /finalized disconnect,
    # which has no replay) and would block every later segment forever.
    SOFT_ORPHAN_S = 15.0

    # Word cap on the screen mirror's pending band, so a reader hours behind
    # never makes the per-frame mirror walk O(session).
    _PENDING_MAX_WORDS = 40

    def __init__(self, translator, sink, assembler=None, gauge=None, window=1):
        self.translator = translator
        self.sink = sink
        self.assembler = assembler or WordAssembler()
        self.gauge = gauge         # optional BacklogGauge; reserves 2 cells
        self.window = max(1, int(window))  # cells per refresh
        self.paused = False        # reader pause: display frozen, input queues
        self.eager = False         # render soft text (see _head_renderable)
        self._hold = False         # catch-up in flight: machine-side freeze
        # Untranslated queue: ("word", text, seg) / ("space", seg); seg is
        # the segment id (None for typed or file input).
        self._tokens = deque()
        # (unit, dwell class) pairs of the word currently streaming out.
        self._emit = deque()
        self._emit_src = None      # (token id, word) of the word in _emit
        self._emit_origin = None   # origin tag of the token in _emit
        self._token_seq = 0        # token ids keep repeats ("had had") apart

        # Reading density, all runtime-switchable.
        self.lowercase = False     # translate lowercased: no capital signs
        self.digits_filter = True  # "twenty five" -> "25" for fed speech
        self.space_dwell = 1.0     # fraction of the interval a space dwells
        self.punct_dwell = 1.0     # same for punctuation cells
        self.last_tick_cost = 0.0  # last tick's cells priced by dwell class
        # Per recently streamed word: [text cells, punct cells, space cells].
        self._density = deque(maxlen=self.DENSITY_WORDS)

        self.width = None
        self._content_width = None
        # The rolling window and its per-cell annotations, kept in lockstep:
        # _src holds (token id, word) or None, _origin the origin tag.
        self._buf = None
        self._src = None
        self._origin = None
        # True while a timed caption replay feeds: seg-tagged text is then
        # tagged ORIGIN_CAPTIONS instead of ORIGIN_SPEECH (set by the demo
        # feeder).
        self.speech_is_stream = False
        self._summary_segs = set() # segment ids carrying summary text
        self._summary_seq = 0
        self._summary_tokens = 0   # queued summary tokens (O(1) pending test)
        # (cells, sources, origins) captured by take_pending, claimed by a
        # show_frame of those same cells.
        self._pending_sources = None
        # Gauge cell as last written. A never-written display reads blank,
        # so a caught-up refresh_gauge writes nothing.
        self._shown_gauge = BLANK
        # segment id -> [feed order, words emitted, hardened, last activity
        # (monotonic), emitted word texts]. Ordinary entries are kept for
        # the session: reconnect dedupe (feed), frozen counts after a jump
        # and revise_segment's alignment all depend on them.
        self._seg_info = {}
        self._seg_counter = 0
        # Bumped by every discard_soft (one per Human-mode entry), even when
        # nothing was soft, so a feeder can notice an entry that landed after
        # its segments hardened.
        self._soft_discards = 0
        # Memo key for backlog_words. Every method that changes what it
        # counts bumps this; a spurious bump costs one recount, a missed one
        # freezes the gauge, so mutators bump unconditionally.
        # speech_is_stream is exempt: it cannot change the count.
        self._tokens_rev = 0
        self._backlog_cache = None # (rev, count)
        # A flushed partial word sits in the queue without its trailing
        # space; the next word fed owes one first.
        self._flush_owes_space = False
        # Why the display is unavailable (set by the runner while waiting or
        # reconnecting); None when connected. Platform UIs announce it.
        self.display_wait_reason = None
        # Screen mirror: every physical frame write publishes an event dict,
        # which frame_event() serves to pollers.
        self._frame_seq = 0
        self._frame_event = None
        # Panning: every shown cell also enters a bounded history ring, and
        # pan_offset is how far back from the live edge the view sits. While
        # panned, streaming freezes like a catch-up hold.
        self._history = None       # deque of (cell, src, origin)
        self.pan_offset = 0
        # Trailing padding blanks of the last flipped/topped-up page: the
        # room top_up may fill. Any other append zeroes it.
        self._page_pad = 0
        # The pacer and the key/bridge threads all mutate the queue and
        # buffer; every public method takes this lock. Reentrant because
        # public methods call each other.
        self._lock = threading.RLock()

    def start(self) -> int:
        """Open the display and size the rolling window to it. With a
        gauge, cells 1-2 are the gauge and the content-kind marker."""
        self.width = self.sink.connect()
        if self.gauge and self.width < 4:
            self.gauge = None  # too narrow to give up 2 cells
        self._content_width = self.width - 2 if self.gauge else self.width
        # A window wider than the buffer would push cells through unseen.
        self.window = min(self.window, self._content_width)
        self._buf = deque(maxlen=self._content_width)
        self._src = deque(maxlen=self._content_width)
        self._origin = deque(maxlen=self._content_width)
        self._history = deque(maxlen=self.HISTORY_CELLS)
        self.pan_offset = 0
        self._page_pad = 0
        return self.width

    def _append_cells(self, cells, sources, origins) -> None:
        """The one way cells enter the rolling buffer and the pan history
        (pages replay exactly as read, padding included). Callers hold the
        lock."""
        self._page_pad = 0   # flip/top_up re-assert it after their padding
        self._buf.extend(cells)
        self._src.extend(sources)
        self._origin.extend(origins)
        if self._history is not None:
            self._history.extend(zip(cells, sources, origins))

    @property
    def _emit_text(self):
        """Source text of the word in _emit (None for a space)."""
        return self._emit_src[1] if self._emit_src else None

    @property
    def has_shown_content(self) -> bool:
        """True once any content has entered the rolling buffer. Flashes and
        reply echoes don't count. Manual mode uses this to deliver a fresh
        session's first page unasked."""
        return bool(self._history)

    def feed(self, text: str, seg=None, final=True) -> None:
        """Queue text as untranslated tokens.

        ``seg`` tags the tokens with a segment id so ``revise_segment`` can
        correct them later. Feeding an id the engine already knows is a
        revision, not new text: that is what a final re-delivered after a
        stream reconnect looks like.

        ``final=False`` marks the segment soft: it queues but does not
        render, and does not count toward the backlog, until it hardens (or
        eager mode confirms it)."""
        with self._lock:
            if seg is not None:
                if seg in self._seg_info:
                    self.revise_segment(seg, text, final=final)
                    return
                if self.digits_filter:
                    # Speech only: typed/file text keeps its author's form.
                    text = _digits_filter(text)
                self._seg_counter += 1
                self._seg_info[seg] = [self._seg_counter, 0, bool(final),
                                       time.monotonic(), []]
            events = self.assembler.feed(text)
            if events and self._flush_owes_space:
                if events[0][0] == "word":
                    self._tokens.append(("space", None))
                self._flush_owes_space = False
            for event in events:
                self._tokens.append(event + (seg,))
            self._tokens_rev += 1

    def flush_input(self) -> None:
        """Queue any buffered incomplete word (end of input)."""
        with self._lock:
            events = self.assembler.flush()
            for event in events:
                self._tokens.append(event + (None,))
            self._tokens_rev += 1
            if events:
                # No trailing space now (it would only waste a cell at the
                # end of input), but the next word fed must not fuse onto
                # this one ("hal" + "hello").
                self._flush_owes_space = True

    def harden_all(self) -> None:
        """Mark every known segment hardened: the source has ended, so the
        text on hand is final."""
        with self._lock:
            for info in self._seg_info.values():
                info[2] = True
            self._tokens_rev += 1

    def feed_summary(self, text: str) -> None:
        """Queue an on-demand summary AHEAD of everything already queued.

        It streams like ordinary text, with the s marker in cell 2 while its
        cells are under the reader's fingers. Prepended because it recaps
        older speech than whatever is still queued (the soft tail that
        survived the jump, and speech fed since). Its segment registers at
        feed order 0, so a late revision of an older segment can never
        splice ahead of it, and it is excluded from backlog_words.

        The recap leads with a space: the display may still hold a word cut
        off mid-scroll by the jump, and the recap must not fuse onto it."""
        stripped = text.strip()
        if not stripped:
            return
        with self._lock:
            self._summary_seq += 1
            seg = ("summary", self._summary_seq)
            self._summary_segs.add(seg)
            self._seg_info[seg] = [0, 0, True, time.monotonic(), []]
            # A fresh assembler: the shared one may hold someone's
            # half-finished word.
            events = [("space",)] \
                + self.assembler.__class__().feed(stripped + " ")
            self._tokens.extendleft(reversed(
                [event + (seg,) for event in events]))
            self._summary_tokens += len(events)
            self._tokens_rev += 1

    def _origin_of(self, seg) -> str:
        """Origin tag for a token's segment: summary segments come from
        feed_summary, untagged tokens are typed (or file replay), anything
        else is speech or captions."""
        if seg in self._summary_segs:
            return ORIGIN_SUMMARY
        if seg is None:
            return ORIGIN_TYPED
        return ORIGIN_CAPTIONS if self.speech_is_stream else ORIGIN_SPEECH

    def summary_pending(self) -> bool:
        """True while summary text is queued or mid-emission."""
        with self._lock:
            return bool(self._emit and self._emit_origin == ORIGIN_SUMMARY) \
                or self._summary_tokens > 0

    def _prune_summary(self) -> None:
        """Forget drained summary segments so they don't accumulate one
        entry per summary. Shown cells keep their tags in _origin."""
        for seg in self._summary_segs:
            self._seg_info.pop(seg, None)
        self._summary_segs.clear()
        self._summary_tokens = 0
        self._tokens_rev += 1

    def _renderable(self, token) -> bool:
        """May this token render under the confirmed gate? Untagged tokens
        always may; tagged ones once their segment hardened."""
        seg = token[-1]
        if seg is None:
            return True
        info = self._seg_info.get(seg)
        return info is None or info[2]

    def _peek_next_word(self) -> tuple:
        """The queue head if it is a word that may render now, else None.
        Callers hold the lock."""
        if not self._tokens or not self._head_renderable():
            return None
        head = self._tokens[0]
        return head if head[0] == "word" else None

    def _head_renderable(self) -> bool:
        """May the queue head render now? In eager mode a soft word may,
        unless it is its segment's last queued word: a growing partial may
        still change that word ("wor" -> "world"), and once streamed it is
        frozen. A same-segment word queued behind it confirms it. Callers
        hold the lock."""
        head = self._tokens[0]
        if self._renderable(head):
            return True
        if not self.eager:
            return False
        if head[0] != "word":
            return True            # a soft run's separator space is safe
        seg = head[-1]
        for token in islice(self._tokens, 1, None):
            if token[-1] != seg:
                break              # a segment's queued run is contiguous
            if token[0] == "word":
                return True
        return False

    def has_pending(self) -> bool:
        """True while any cell remains to be shown (mid-word or queued)."""
        with self._lock:
            return bool(self._emit or self._tokens)

    def knows_segment(self, seg) -> bool:
        """True if this segment id has been fed and is still tracked."""
        with self._lock:
            return seg in self._seg_info

    @property
    def soft_discards(self) -> int:
        """How many times ``discard_soft`` has run (see __init__)."""
        with self._lock:
            return self._soft_discards

    def drop_stale_soft(self, max_age_s=SOFT_ORPHAN_S):
        """Withdraw the soft segment blocking the queue head if its stream
        has been silent for ``max_age_s``. Returns the dropped segment ids
        (empty when nothing is stale)."""
        with self._lock:
            if self._emit or not self._tokens:
                return []
            head = self._tokens[0]
            if self._renderable(head):
                return []
            seg = head[-1]
            info = self._seg_info.get(seg)
            if info is None or time.monotonic() - info[3] < max_age_s:
                return []
            # The empty final revision removes the queued run. Forgetting the
            # segment then makes a genuinely late final arrive as unknown, so
            # it is fed at the queue tail instead of spliced into its old
            # slot ahead of text that is already rendering.
            self.revise_segment(seg, "", final=True)
            self._seg_info.pop(seg, None)
            self._tokens_rev += 1
            return [seg]

    def discard_soft(self):
        """Withdraw and forget every soft segment (Human-mode entry). The
        closing speech session's flush would otherwise harden them moments
        later and stream stale speech into the typing. Forgotten, their late
        finals arrive as unknown segments, which the pipeline drops while
        typing. Returns the dropped segment ids, oldest first."""
        with self._lock:
            self._soft_discards += 1
            dropped = [seg for seg, info in self._seg_info.items()
                       if not info[2]]
            for seg in dropped:
                self.revise_segment(seg, "", final=True)
                self._seg_info.pop(seg, None)
            self._tokens_rev += 1
            return dropped

    def set_window(self, window: int) -> bool:
        """Set cells per refresh for future ticks. The pacer sleeps per cell
        emitted, so reading speed is unchanged; at the full content width
        ticks become page flips."""
        if not isinstance(window, int) or window < 1:
            return False
        with self._lock:
            limit = self._content_width
            self.window = min(window, limit) if limit else window
        return True

    def set_window_full(self) -> None:
        """Select the full-display window without knowing the width yet:
        clamps now if connected, else start() clamps the oversize value."""
        with self._lock:
            self.window = self._content_width or 10 ** 6

    def set_space_dwell(self, percent) -> bool:
        """Percent (1..100) of the per-cell interval a space cell dwells.
        Timing only: no cell is ever dropped."""
        return self._set_dwell("space_dwell", percent)

    def set_punct_dwell(self, percent) -> bool:
        """Same as set_space_dwell, for punctuation cells."""
        return self._set_dwell("punct_dwell", percent)

    def _set_dwell(self, attr, percent) -> bool:
        try:
            value = int(percent)
        except (TypeError, ValueError):
            return False
        if not 1 <= value <= 100:
            return False
        with self._lock:
            setattr(self, attr, value / 100.0)
        return True

    def _dwell_cost(self, kind) -> float:
        if kind == DWELL_SPACE:
            return self.space_dwell
        if kind == DWELL_PUNCT:
            return self.punct_dwell
        return 1.0

    def set_lowercase(self, on) -> None:
        """Toggle the lowercase display filter for words translated from
        now on. The transcript keeps its capitals either way."""
        with self._lock:
            on = bool(on)
            if on != self.lowercase:
                self.lowercase = on
                self.reset_reading_density()

    def set_digits_filter(self, on) -> None:
        """Toggle the number-word-to-numeral rewrite for speech fed from now
        on."""
        with self._lock:
            on = bool(on)
            if on != self.digits_filter:
                self.digits_filter = on
                self.reset_reading_density()

    def reset_reading_density(self) -> None:
        """Forget the measured cells-per-word, for changes that reshape
        every future word (grade, lowercase, digits). Dwell changes need no
        reset: prices are applied at read time."""
        with self._lock:
            self._density.clear()

    def reading_cost_per_word(self):
        """Average pacer cost of a recently streamed word, in cell-intervals,
        priced at the current dwell settings: the measured basis of the wpm
        readout (``controls.reading_wpm``). None until DENSITY_MIN_WORDS
        words have streamed."""
        with self._lock:
            words = len(self._density)
            if words < self.DENSITY_MIN_WORDS:
                return None
            text = sum(row[0] for row in self._density)
            punct = sum(row[1] for row in self._density)
            space = sum(row[2] for row in self._density)
            return (text + punct * self.punct_dwell
                    + space * self.space_dwell) / words

    def set_eager(self, eager) -> None:
        """Toggle eager soft rendering for everything not yet streamed."""
        with self._lock:
            self.eager = bool(eager)
            self._tokens_rev += 1

    def segment_text(self, seg):
        """The segment's effective text: the words already streamed (as
        shown) followed by its still-queued words. Can differ from the
        provider's final when a correction arrived after a word streamed or
        was captured by a jump; the shells print this so the transcript
        matches what the reader got. None for an unknown segment."""
        with self._lock:
            info = self._seg_info.get(seg)
            if info is None:
                return None
            words = list(info[4])
            words.extend(token[1] for token in self._tokens
                         if token[-1] == seg and token[0] == "word")
            return " ".join(words)

    @property
    def window_is_full(self):
        """True when the window spans the whole content width: ticks then
        flip word-wrapped pages. False until start()."""
        return (self._content_width is not None
                and self.window >= self._content_width)

    @property
    def content_width(self):
        """Cells available to content (display width minus the gauge's two
        cells); None until start()."""
        return self._content_width

    def set_paused(self, paused: bool) -> None:
        """Freeze or unfreeze the display. While paused nothing streams and
        input keeps queueing. Frames the reader asks for (flashes, reply
        echo) still render; the pacer restores the frozen frame after."""
        with self._lock:
            self.paused = bool(paused)

    def set_hold(self, hold: bool) -> None:
        """Block ticks while a catch-up is in flight. Separate from
        ``paused`` so it can never clobber the reader's own pause."""
        with self._lock:
            self._hold = bool(hold)

    def _display_form(self, token: str) -> str:
        """The text actually translated to cells. The lowercase filter
        applies here, not at ingest, so the transcript keeps its capitals.
        Lone surrogates (a Windows console delivers an astral character
        typed in Human mode as two separate keys) become U+FFFD: they
        cannot be encoded for liblouis."""
        if _SURROGATE.search(token):
            token = token.encode("utf-16", "surrogatepass").decode(
                "utf-16", "replace")
        return token.lower() if self.lowercase else token

    def _translate_units(self, token: str) -> list:
        """Token -> list of ``(unit, dwell class)`` pairs, each unit an
        unsplittable list of cells. Units whose source character is a
        punctuation mark carry DWELL_PUNCT so the pacer can discount them.
        Translators without unit support degrade to one cell per unit.

        A word the translator cannot handle is skipped (logged, no cells):
        it is a content problem, and letting it escape would either kill
        the pacer or be mistaken for a display outage."""
        token = self._display_form(token)
        try:
            tagged = getattr(self.translator, "translate_units_tagged", None)
            if tagged is not None:
                return [(unit,
                         DWELL_PUNCT if ch in _PUNCT_CHARS else DWELL_TEXT)
                        for unit, ch in tagged(token) if unit]
            translate_units = getattr(self.translator, "translate_units",
                                      None)
            if translate_units is not None:
                return [(unit, DWELL_TEXT)
                        for unit in translate_units(token) if unit]
            return [([cell], DWELL_TEXT)
                    for cell in self.translator.translate(token)]
        except (TranslationError, UnicodeError) as error:
            sys.stderr.write(f"[warn] skipped a word braille translation "
                             f"could not handle ({error})\n")
            return []

    def _load_next(self) -> bool:
        """Translate the next renderable token into the emit buffer at the
        current grade. Returns True once the emit buffer holds a unit; stops
        at a token that may not render yet."""
        while not self._emit and self._tokens:
            if not self._head_renderable():
                return False
            kind, *rest = self._tokens.popleft()
            self._tokens_rev += 1
            seg = rest[-1]
            if kind == "word":
                units = self._translate_units(rest[0])
                self._emit.extend(units)
                if units:
                    self._density.append([
                        sum(len(u) for u, k in units if k != DWELL_PUNCT),
                        sum(len(u) for u, k in units if k == DWELL_PUNCT),
                        0])
                self._token_seq += 1
                self._emit_origin = self._origin_of(seg)
                self._emit_src = (self._token_seq, rest[0])
                # The word is now under the reader's fingers: frozen against
                # revision, and part of the segment's effective text.
                if seg is not None and seg in self._seg_info:
                    self._seg_info[seg][1] += 1
                    self._seg_info[seg][4].append(rest[0])
            elif kind == "space":
                self._emit.append(([BLANK], DWELL_SPACE))
                if self._density:
                    self._density[-1][2] += 1   # billed to the word before
                self._emit_src = None
                # Spaces take their segment's origin so the marker doesn't
                # flicker across word gaps. An unattributed space (an owed
                # separator) keeps the origin already flowing: tagging it
                # typed would pin a false h into a speech stream.
                if seg is not None:
                    self._emit_origin = self._origin_of(seg)
            # Keyed on the token's own seg, not the inherited origin, so an
            # unattributed space can never double-count.
            if seg in self._summary_segs:
                self._summary_tokens -= 1
                if self._summary_tokens <= 0:
                    self._prune_summary()
        return bool(self._emit)

    def _emit_unit(self, room: int, started: bool) -> tuple:
        """Move the next unit from _emit onto the display buffer. A unit
        wider than ``room`` waits for the next refresh, unless nothing has
        been placed yet (``started`` False) and it must split. Returns
        ``(cells placed, dwell cost)``; (0, 0.0) means it waits. Callers
        hold the lock and ensure _emit is non-empty."""
        unit, kind = self._emit[0]
        if len(unit) > room:
            if started:
                return 0, 0.0
            self._emit[0] = (unit[room:], kind)
            unit = unit[:room]
        else:
            self._emit.popleft()
        self._append_cells(unit, [self._emit_src] * len(unit),
                           [self._emit_origin] * len(unit))
        return len(unit), self._dwell_cost(kind) * len(unit)

    def _frozen(self) -> bool:
        """True when nothing may stream: a reader pause, a catch-up hold or
        a panned view. A hold or pan still refreshes the gauge so the
        growing backlog stays feelable; a pause changes nothing at all.
        Callers hold the lock."""
        if not (self.paused or self._hold or self.pan_offset):
            return False
        if self._hold or self.pan_offset:
            self.refresh_gauge()
        return True

    @staticmethod
    def _norm_word(word: str) -> str:
        """Comparison form for frozen-prefix alignment: lowercase,
        alphanumerics only."""
        return "".join(ch.lower() for ch in word if ch.isalnum())

    def _align_past_frozen(self, events, frozen_words, seg) -> list:
        """Split a revision's assembled ``events`` at the frozen barrier and
        return the seg-tagged tail beyond it.

        The events are walked character-wise (in _norm_word form) along the
        concatenated text of ``frozen_words``, ignoring word boundaries, so a
        final that retokenizes ("icecream" -> "ice cream", "can not" ->
        "cannot") consumes exactly the frozen prefix. Where the text really
        changed:

        * a revision word that disagrees with the frozen text stands for the
          current frozen word one-for-one and is dropped (frozen cells never
          change);
        * a word straddling the barrier splices its unshown suffix only when
          that suffix matches the segment's queued text ("can" frozen +
          "not" queued, final "cannot"); otherwise it is a word that grew
          on a frozen stem ("the" -> "then") and is dropped.

        Callers hold the lock."""
        norms = [self._norm_word(word) for word in frozen_words]
        total = len(norms)

        def settle(i, c):
            # Step past exhausted (or punctuation-only, hence empty) frozen
            # words so (i, c) always indexes a real next character.
            while i < total and c >= len(norms[i]):
                i, c = i + 1, 0
            return i, c

        # Trailing punctuation-only frozen words are invisible to the walk.
        # Each one lets one punctuation-only revision word past the barrier
        # count as covered; otherwise the mark would be appended again.
        punct_credit = 0
        for norm in reversed(norms):
            if norm:
                break
            punct_credit += 1

        i, c = settle(0, 0)
        tail = []
        held_seps = []  # separators pending while trailing credits remain
        for event in events:
            if i >= total:
                if punct_credit:
                    if event[0] != "word":
                        held_seps.append(event)
                        continue
                    if not self._norm_word(event[1]):
                        punct_credit -= 1   # stands for a trailing frozen
                        held_seps.clear()   # mark, separator and all
                        continue
                    # A real word: the revision dropped the trailing marks.
                    punct_credit = 0
                    tail.extend(sep + (seg,) for sep in held_seps)
                    held_seps.clear()
                tail.append(event + (seg,))
                continue
            if event[0] != "word":
                continue        # separators inside the frozen prefix
            word = event[1]
            j, cc = i, c        # tentative walk; committed only on a match
            matched = True
            k = 0
            while k < len(word):
                ch = word[k]
                if not ch.isalnum():
                    k += 1      # punctuation never consumes frozen text
                    continue
                if j >= total:
                    break       # the word extends past the frozen text
                if norms[j][cc] != ch.lower():
                    matched = False
                    break
                k += 1
                j, cc = settle(j, cc + 1)
            if not matched:
                i, c = settle(i + 1, 0)
                continue
            if k >= len(word):
                i, c = j, cc    # inside (or exactly ending) the frozen text
                continue
            # Straddles the barrier: splice the suffix only if it is queued
            # text merging across.
            i, c = total, 0
            suffix = word[k:]
            suffix_norm = self._norm_word(suffix)
            queued = "".join(self._norm_word(token[1])
                             for token in self._tokens
                             if token[-1] == seg and token[0] == "word")
            if suffix_norm and queued and (
                    queued.startswith(suffix_norm)
                    or suffix_norm.startswith(queued)):
                tail.append(("word", suffix, seg))
        return tail

    def revise_segment(self, seg, text: str, final=False) -> bool:
        """Replace segment ``seg``'s still-queued words with ``text``;
        ``final=True`` also hardens it.

        Words already emitted (including the one mid-display) stay as shown:
        ``text`` is aligned against them textually (see
        ``_align_past_frozen``) and only the remainder is spliced. A
        revision that extends a fully consumed segment is inserted where the
        segment sat in stream order, ahead of any later segment's text. A
        catch-up jump advances the frozen count past everything it captured
        (see take_pending), so a later revision contributes only words newer
        than the jump.

        Returns True when the queue changed."""
        if seg is None:
            return False
        with self._lock:
            if self.digits_filter and text:
                # Same rewrite feed() applied, so alignment sees the same
                # shape that was queued.
                text = _digits_filter(text)
            info = self._seg_info.get(seg)
            if info is None:
                return False
            info[3] = time.monotonic()
            if final:
                info[2] = True
            order = info[0]
            stripped = text.strip()
            events = self.assembler.__class__().feed(stripped + " ") \
                if stripped else []
            tail = self._align_past_frozen(events, info[4], seg)
            # Separator bookkeeping at the barrier: if the frozen word's
            # trailing space was already emitted, a leading space in the
            # tail would double the blank; if the run still owed that space
            # and the tail is empty, keep one so segments don't fuse.
            run_first = next((token for token in self._tokens
                              if token[-1] == seg), None)
            run_owes_space = run_first is not None and run_first[0] == "space"
            if tail and tail[0][0] == "space" and not run_owes_space:
                tail.pop(0)
            if not tail and run_owes_space:
                tail = [("space", seg)]
            # Replace the segment's queued run in place; with no run left,
            # insert before the first token of any later segment, else
            # append.
            out = deque()
            spliced = False
            saw_run = False
            for token in self._tokens:
                token_seg = token[-1]
                if token_seg == seg:
                    saw_run = True
                    if not spliced:
                        out.extend(tail)
                        spliced = True
                    continue
                if not spliced and token_seg is not None:
                    later = self._seg_info.get(token_seg)
                    if later is not None and later[0] > order:
                        out.extend(tail)
                        spliced = True
                out.append(token)
            if not spliced:
                out.extend(tail)
            self._tokens = out
            self._tokens_rev += 1   # even a no-op splice may have hardened
            return saw_run or bool(tail)

    def tick(self, record_cost: bool = True) -> int:
        """Emit up to ``window`` cells as one refresh, never ending mid-unit
        (unless a single unit is wider than the window). Returns the number
        of cells emitted, 0 when nothing could be shown.

        ``last_tick_cost`` holds the same cells priced by dwell class, which
        is what the pacer sleeps on. At the full-display window the tick is
        a page ``flip``.

        ``record_cost=False`` leaves ``last_tick_cost`` alone: the thumb
        fast-forward ticks from the key thread and must not steer the
        pacer's next sleep."""
        with self._lock:
            if self.window_is_full:
                return self.flip(record_cost=record_cost)
            if record_cost:
                self.last_tick_cost = 0.0
            if self._frozen():
                return 0
            emitted = 0
            cost = 0.0
            while emitted < self.window:
                if not self._emit and not self._load_next():
                    break
                placed, unit_cost = self._emit_unit(self.window - emitted,
                                                    started=emitted > 0)
                if not placed:
                    break
                emitted += placed
                cost += unit_cost
            if emitted:
                self._write_frame()
            if record_cost:
                self.last_tick_cost = cost
            return emitted

    def flip(self, record_cost: bool = True) -> int:
        """Page advance for the full-display window and manual mode: fill
        the content width with as many whole words as fit and pad the rest
        with blanks, so each flip is a clean page. The word that doesn't fit
        leads the next page (word wrap, as screen readers pan); a word wider
        than the page splits. Spaces are skipped at the page start and fold
        into the padding at the end.

        Returns the content cells shown (padding excluded). With nothing
        ready it returns 0, leaves the display untouched, and restores any
        separator it consumed so words can't fuse across an empty flip.
        ``last_tick_cost`` prices the content cells as tick() does; padding
        costs nothing."""
        with self._lock:
            if record_cost:
                self.last_tick_cost = 0.0
            if self._frozen():
                return 0
            width = self._content_width
            emitted, cost, skipped_space = self._fill_page(width, 0)
            if emitted:
                pad = width - emitted
                self._append_cells([BLANK] * pad, [None] * pad, [None] * pad)
                self._page_pad = pad
                self._write_frame()
            elif skipped_space:
                self._tokens.appendleft(("space", None))
                self._tokens_rev += 1
            if record_cost:
                self.last_tick_cost = cost
            return emitted

    def _fill_page(self, width, emitted) -> tuple:
        """The page fill shared by ``flip`` (``emitted=0``) and ``top_up``
        (``emitted`` = cells already on the page): render whole words up to
        ``width`` cells. Returns ``(emitted, cost, skipped_space)``: the
        cells now on the page, the cost of those added here, and whether a
        leading separator was consumed. Callers hold the lock and own the
        padding."""
        cost = 0.0
        skipped_space = False
        while emitted < width:
            if not self._emit:
                if emitted:
                    # Measure the next word without consuming it, so a word
                    # that wraps stays queued: still revisable, still counted
                    # by the gauge, and translated at the next flip's grade.
                    head = self._peek_next_word()
                    if head is not None:
                        need = sum(
                            len(unit) for unit, _ in
                            self._translate_units(head[1]))
                        if need > width - emitted:
                            break
                if not self._load_next():
                    break
                if self._emit_text is None and emitted == 0:
                    self._emit.clear()   # no leading gap on a page
                    skipped_space = True
                    continue
            # A unit can only overflow inside a word wider than the page
            # (fitting words were measured above).
            placed, unit_cost = self._emit_unit(width - emitted,
                                                started=emitted > 0)
            if not placed:
                break
            emitted += placed
            cost += unit_cost
        return emitted, cost, skipped_space

    def top_up(self) -> int:
        """Manual mode: fill the blank tail of the current page in place with
        newly renderable text. The padding was never reading material, so
        text that arrives while the page has room renders there instead of
        waiting for an advance press. Cells already shown don't move; a word
        that doesn't fit waits to lead the next page.

        Returns the new content cells shown; 0 (display untouched) when there
        is no room, nothing renderable, or a pause/hold/pan owns the display.
        Never touches ``last_tick_cost``: manual mode has no pacer sleep."""
        with self._lock:
            pad = self._page_pad
            if not pad or self._buf is None or self.paused or self._hold \
                    or self.pan_offset:
                return 0
            if not self._emit and not (self._tokens
                                       and self._head_renderable()):
                return 0
            # Lift the padding off the buffer and the history, so the pan
            # history replays the page as finally read.
            for _ in range(pad):
                self._buf.pop()
                self._src.pop()
                self._origin.pop()
                if self._history:
                    self._history.pop()
            already = self._content_width - pad
            # A flipped page is never empty, so no separator restore is owed.
            emitted, _cost, _skipped = self._fill_page(
                self._content_width, already)
            new = emitted - already
            pad = self._content_width - emitted
            self._append_cells([BLANK] * pad, [None] * pad, [None] * pad)
            self._page_pad = pad
            if new:
                self._write_frame()
            return new

    def _gauge_distance(self) -> int:
        """Words between the text under the fingers and the live edge: the
        queued backlog plus, while panned, the pan depth converted to words.
        Callers hold the lock."""
        words = self.backlog_words()
        if self.pan_offset:
            words += self.pan_offset // self.PAN_CELLS_PER_WORD
        return words

    def _gauge_cell(self) -> int:
        """The gauge cell for the current view; the one place that steps the
        gauge's hysteresis (pollers use gauge.peek). Off the live edge the
        level floors at 1, so a blank gauge always means live and caught up.
        Callers hold the lock."""
        return self.gauge.update(self._gauge_distance(),
                                 floor=1 if self.pan_offset else 0)

    def refresh_gauge(self) -> None:
        """Rewrite the frame when the gauge bucket moved while content holds
        still (catch-up hold, panned view, manual mode between pages). No-op
        under a reader pause."""
        with self._lock:
            if self.paused or not self.gauge or self._buf is None:
                return
            if self._gauge_cell() != self._shown_gauge:
                if self.pan_offset:
                    self._write_pan_frame()
                else:
                    self._write_frame()

    def pan_back(self, step: int) -> int:
        """Move the view ``step`` cells back into the shown-cell history
        (thumb Left). Streaming freezes while panned and the gauge counts
        the pan depth. A page-sized step snaps the left edge to a word start;
        smaller steps are cell-exact. Returns how far the view moved: 0 at
        the start of history, while paused, or before start()."""
        with self._lock:
            if self._buf is None or self.paused or step < 1:
                return 0
            limit = max(0, len(self._history) - self._content_width)
            old = self.pan_offset
            target = min(old + step, limit)
            if step >= self._content_width:
                target = self._snap_to_word(target, limit)
            if target <= old:
                return 0
            self.pan_offset = target
            try:
                self._write_pan_frame()
            except (OSError, RuntimeError):
                # Revert: a panned engine never ticks, so the pacer would
                # never touch the dead sink and never reconnect.
                self.pan_offset = old
                raise
            return target - old

    def pan_forward(self, step: int) -> int:
        """Move a panned view ``step`` cells toward live (thumb Right).
        Reaching offset 0 repaints the live buffer and streaming resumes.
        Page-sized steps word-snap like pan_back, but never so far that the
        press stops making progress. Returns how far the view moved."""
        with self._lock:
            if self._buf is None or self.paused or step < 1 \
                    or not self.pan_offset:
                return 0
            old = self.pan_offset
            target = max(0, old - step)
            if target and step >= self._content_width:
                limit = max(0, len(self._history) - self._content_width)
                snapped = self._snap_to_word(target, limit)
                if snapped < old:
                    target = snapped
            self.pan_offset = target
            try:
                if target:
                    self._write_pan_frame()
                else:
                    self._write_frame()
            except (OSError, RuntimeError):
                if target:
                    # Same revert as pan_back. A failed landing at live keeps
                    # offset 0: the pacer's next tick owns the outage.
                    self.pan_offset = old
                raise
            return old - target

    def exit_pan(self) -> None:
        """Drop any pan without repainting, for commands whose own frame
        replaces the view (the reply echo)."""
        with self._lock:
            self.pan_offset = 0

    def _snap_to_word(self, offset: int, limit: int) -> int:
        """Grow ``offset`` until the view's left edge sits on a word start
        (or padding), bounded by ``limit``. Callers hold the lock."""
        history = self._history
        width = self._content_width
        while offset < limit:
            left = len(history) - offset - width
            if left <= 0:
                break
            src = history[left][1]
            if src is None or history[left - 1][1] != src:
                break
            offset += 1
        return offset

    def _pan_view(self):
        """The history slice the current pan_offset shows. Callers hold the
        lock."""
        end = len(self._history) - self.pan_offset
        start = max(0, end - self._content_width)
        return list(islice(self._history, start, end))

    def _write_pan_frame(self, frozen_gauge: bool = False) -> None:
        """Send the panned view: the history slice, right-aligned, with the
        live gauge (which carries the pan depth) and the slice's own marker.
        ``frozen_gauge`` re-sends the gauge as last shown, for the restore
        under a reader pause. Callers hold the lock."""
        view = self._pan_view()
        cells = [cell for cell, _src, _origin in view]
        frame = [BLANK] * (self._content_width - len(cells)) + cells
        if self.gauge:
            gauge_cell = (self._shown_gauge if frozen_gauge
                          else self._gauge_cell())
            marker = self._separator(origin for _c, _s, origin in view)
            frame = [gauge_cell, marker] + frame
            self._shown_gauge = gauge_cell
        self.sink.write(frame)
        pairs = [(src, origin) for _cell, src, origin in view]
        self._mirror("pan", self._words_from(pairs, include_summary=True),
                     source=self._words_from(pairs, include_summary=False))

    def _separator(self, origins=None) -> int:
        """Cell 2, the content-kind marker: s while summary cells are shown
        or a summary fetch holds the display (otherwise the frozen fetch
        window feels like a wedged display), else h while typed cells are
        shown, else blank. ``origins`` overrides the live buffer's tags
        (a pan frame's slice)."""
        if self._hold:
            return SUMMARY_MARKER
        has_typed = False
        for origin in (self._origin if origins is None else origins):
            if origin == ORIGIN_SUMMARY:
                return SUMMARY_MARKER
            if origin == ORIGIN_TYPED:
                has_typed = True
        return TYPED_MARKER if has_typed else BLANK

    def repaint(self, paused_ok: bool = False) -> None:
        """Rewrite the current frame as-is: restores content after a flash's
        dwell, and acknowledges a catch-up press immediately. No-op before
        start().

        Under a reader pause only ``paused_ok`` callers write (the pacer's
        dwell-end restore and display recovery): flashes render over a
        paused frame, and this is how the frozen cells come back. The gauge
        is re-sent as last shown rather than re-measured."""
        with self._lock:
            if self._buf is None or (self.paused and not paused_ok):
                return
            if self.pan_offset:
                # Restore the panned view, not the live buffer.
                self._write_pan_frame(frozen_gauge=self.paused)
                return
            if self.paused:
                cells = list(self._buf)
                content = [BLANK] * (self._content_width - len(cells)) \
                    + cells
                frame = ([self._shown_gauge, self._separator()] + content
                         if self.gauge else content)
                self.sink.write(frame)
                self._mirror("content")
                return
            self._write_frame()

    def flash(self, text: str) -> None:
        """Transient whole-frame message, written straight to the sink
        without entering the rolling buffer; the next frame write (the
        pacer's dwell-end repaint at the latest) replaces it. Right-aligned
        like content, and the marker keeps naming the content it covers.

        Renders under a reader pause (it answers something the reader just
        did), with the gauge frozen as last shown. Refused during a catch-up
        hold, whose frozen acknowledgement frame must stay."""
        with self._lock:
            if self._buf is None or self._hold:
                return
            cells, _total = self.fit_cells(text, self._content_width)
            frame = [BLANK] * (self._content_width - len(cells)) + cells
            if self.gauge:
                marker = (self._separator(
                              origin for _c, _s, origin in self._pan_view())
                          if self.pan_offset else self._separator())
                gauge_cell = self._shown_gauge if self.paused \
                    else self._gauge_cell()
                frame = [gauge_cell, marker] + frame
                self._shown_gauge = frame[0]
            self.sink.write(frame)
            self._mirror("flash", text)

    def reply_frame(self, cells) -> None:
        """Reply-mode echo: the cells the reader is typing on the display's
        keys, left-aligned, newest kept on overflow, with r in cell 2.
        Written straight to the sink; ``reply_restore`` brings the frozen
        reading frame back. Not gated on pause or hold: the reader asked
        for it by typing."""
        with self._lock:
            if self._buf is None:
                return
            shown = [c & 0xFF for c in cells][-self._content_width:]
            frame = shown + [BLANK] * (self._content_width - len(shown))
            if self.gauge:
                frame = [self._gauge_cell(), REPLY_MARKER] + frame
                self._shown_gauge = frame[0]
            self.sink.write(frame)
            # No print text at this layer: the shell renders its own echo.
            self._mirror("reply", "")

    def reply_restore(self) -> None:
        """Put the frozen reading frame back after a reply (bypassing
        repaint's pause guard: restoring the paused frame is the point)."""
        with self._lock:
            if self._buf is not None:
                self._write_frame()

    def _write_frame(self) -> None:
        frame = self.frame()
        if self.gauge:
            self._shown_gauge = frame[0]
        self.sink.write(frame)
        self._mirror("content")

    def _mirror(self, kind: str, text: str = None, source: str = None) -> None:
        """Publish what the display now shows. Called only after a
        successful sink write. ``kind`` is 'content', 'flash', 'reply' or
        'pan'; ``text`` is the literal display text and ``source`` the
        transcript anchor, both derived from the live buffer by default.
        Runs under the engine lock."""
        self._frame_seq += 1
        event = {
            "seq": self._frame_seq,
            "kind": kind,
            "text": self._shown_words(True) if text is None else text,
            "source": self._shown_words(False) if source is None else source,
            "pending": self._pending_words(),
        }
        self._frame_event = event

    def frame_event(self):
        """Latest mirror event, for pollers; None before the first frame.
        ``seq`` is monotonic so a poller can tell missed frames apart."""
        with self._lock:
            return self._frame_event

    def _pending_words(self, max_words=_PENDING_MAX_WORDS) -> str:
        """The head of the queue as print text (post-revision, soft tail
        and summary included), capped at ``max_words``. Callers hold the
        lock."""
        words = []
        for token in self._tokens:
            if token[0] != "word":
                continue
            if len(words) >= max_words:
                words.append("…")
                break
            words.append(token[1])
        return " ".join(words)

    def pending_text(self, max_words=_PENDING_MAX_WORDS) -> str:
        """Locked _pending_words, for state polls between frame writes."""
        with self._lock:
            return self._pending_words(max_words)

    def _remaining_cells(self, tokens):
        """Cells for the emit buffer plus ``tokens`` at the current grade,
        with per-cell source annotations and origin tags, so a catch-up snap
        keeps the highlight and marker accurate. Only take_pending needs the
        whole tail translated at once."""
        cells = []
        sources = []
        origins = []
        for unit, _ in self._emit:
            cells.extend(unit)
            sources.extend([self._emit_src] * len(unit))
            origins.extend([self._emit_origin] * len(unit))
        for kind, *rest in tokens:
            if kind == "word":
                self._token_seq += 1
                word_cells = [cell for unit, _ in
                              self._translate_units(rest[0])
                              for cell in unit]
                cells.extend(word_cells)
                sources.extend([(self._token_seq, rest[0])] * len(word_cells))
                origins.extend([self._origin_of(rest[-1])] * len(word_cells))
            elif kind == "space":
                cells.append(BLANK)
                sources.append(None)
                origins.append(self._origin_of(rest[0])
                               if rest[0] is not None else None)
        return cells, sources, origins

    def take_pending(self):
        """Atomically capture and discard the pending committed text.

        Returns ``(text, cells)``: the pending speech as plain text (the
        word mid-scroll included in full) and the same tail as cells at the
        current grade. The caller snaps to it or summarizes it; the queue
        and gauge drain either way.

        Soft tokens at the end of the queue are not captured: they stay
        queued and stream once they harden. Queued summary tokens are
        discarded, never captured, so a jump mid-summary lands on real
        text."""
        with self._lock:
            # A catch-up command is also the way out of a panned view.
            self.pan_offset = 0
            # Split at the first soft token: everything before it is
            # captured, everything from it on survives the jump.
            prefix = []
            consumed = 0
            for token in self._tokens:
                if not self._renderable(token):
                    break
                consumed += 1
                if self._origin_of(token[-1]) == ORIGIN_SUMMARY:
                    continue
                prefix.append(token)
            # All summary tokens precede the soft tail, so none survive.
            self._prune_summary()
            soft_tail = deque(list(self._tokens)[consumed:])
            if self._emit and self._emit_origin == ORIGIN_SUMMARY:
                self._emit.clear()
                self._emit_src = None
            cells, sources, origins = self._remaining_cells(prefix)
            words = []
            if self._emit and self._emit_text:
                words.append(self._emit_text)
            words.extend(rest[0] for kind, *rest in prefix
                         if kind == "word")
            self._emit.clear()
            self._emit_src = None
            self._emit_origin = None
            # The captured words reached the reader (snapped or summarized):
            # freeze them, so a later revision only adds newer words.
            for token in prefix:
                if token[0] == "word" and token[-1] is not None:
                    seg_info = self._seg_info.get(token[-1])
                    if seg_info is not None:
                        seg_info[1] += 1
                        seg_info[4].append(token[1])
            self._tokens = soft_tail
            self._tokens_rev += 1
            # A deliberate catch-up skips the gauge's falling-edge
            # hysteresis: the drained gauge is the reader's confirmation.
            if self.gauge:
                self.gauge.reset()
            self._pending_sources = (cells, sources, origins)
            return " ".join(words), cells

    def show_frame(self, cells) -> None:
        """Whole-frame write, for explicit reader catch-up only: appends
        ``cells`` to the rolling buffer."""
        with self._lock:
            if self._buf is None:
                return
            self.pan_offset = 0
            # The hold ends inside this write, so the snap carries its own
            # cells' marker rather than the hold's s, and no word hardened
            # meanwhile can tick out ahead of the snap.
            self._hold = False
            # Cells captured by take_pending keep their annotations. The
            # match is by list identity, so any other cells read as unknown
            # and the highlight goes dark instead of lying.
            sources = None
            origins = None
            if self._pending_sources and self._pending_sources[0] is cells:
                sources = self._pending_sources[1]
                origins = self._pending_sources[2]
            self._pending_sources = None
            self._append_cells(
                cells,
                sources if sources is not None else [None] * len(cells),
                origins if origins is not None else [None] * len(cells))
            self._write_frame()

    def fit_cells(self, text: str, budget: int):
        """Translate ``text`` at the current grade and fit it to ``budget``
        cells, truncating on a unit boundary. Returns ``(cells, total)``,
        ``total`` being the untruncated cell count. Takes the engine lock
        because liblouis isn't re-entrant."""
        with self._lock:
            cells, total, full = [], 0, False
            for index, token in enumerate(text.split(" ")):
                if index:
                    total += 1                  # the separating space
                    if not full and len(cells) + 1 <= budget and cells:
                        cells.append(BLANK)
                for unit, _ in self._translate_units(token):
                    total += len(unit)
                    if full or len(cells) + len(unit) > budget:
                        full = True             # nothing may leapfrog a skip
                    else:
                        cells.extend(unit)
            if cells and cells[-1] == BLANK:
                cells.pop()                     # a truncation ate the word
            return cells, total

    def jump_to_live(self) -> bool:
        """Reader catch-up: discard the queue and snap to the newest cells
        (the gauge drains on the same frame).

        Returns True when the press changed what is under the fingers
        (backlog captured, or a pan returned to live). False means the
        reader was already live with nothing pending, so the caller should
        acknowledge the press itself."""
        with self._lock:
            panned = bool(self.pan_offset)
            text, cells = self.take_pending()
            self.show_frame(cells)
            return bool(text) or panned

    def shown_source(self) -> str:
        """Print text of the words whose cells are on the display: the
        anchor for the speaker-facing highlight. A partly visible word
        counts whole. Summary cells contribute nothing (that text was never
        in the transcript). While panned, this is the history slice."""
        with self._lock:
            if self.pan_offset:
                return self._words_from(
                    [(src, origin) for _c, src, origin in self._pan_view()],
                    include_summary=False)
        return self._shown_words(include_summary=False)

    def shown_display(self) -> str:
        """Print text of everything on the display, summary included: the
        literal screen-mirror line. Pan-aware like shown_source."""
        with self._lock:
            if self.pan_offset:
                return self._words_from(
                    [(src, origin) for _c, src, origin in self._pan_view()],
                    include_summary=True)
        return self._shown_words(include_summary=True)

    def _shown_words(self, include_summary: bool) -> str:
        with self._lock:
            if not self._src:
                return ""
            return self._words_from(zip(self._src, self._origin),
                                    include_summary)

    @staticmethod
    def _words_from(pairs, include_summary: bool) -> str:
        """Print text of the words a run of (src, origin) annotations
        covers."""
        words = []
        last_id = None
        for entry, origin in pairs:
            if entry is None:
                continue
            if not include_summary and origin == ORIGIN_SUMMARY:
                continue
            token_id, word = entry
            if token_id != last_id:
                words.append(word)
                last_id = token_id
        return " ".join(words)

    def backlog_words(self) -> int:
        """Whole words queued behind the one streaming: the gauge's measure
        of distance from live speech. Summary words don't count (they are
        the catching-up), nor do soft words that cannot render yet; in
        eager mode soft words count except each run's unconfirmed frontier.

        Memoized on _tokens_rev: manual mode polls the gauge 20 times a
        second, and a long backlog would otherwise be walked each time."""
        with self._lock:
            if self._backlog_cache is not None \
                    and self._backlog_cache[0] == self._tokens_rev:
                return self._backlog_cache[1]
            count = 0
            pending_seg = _NO_SEG = object()  # eager: unconfirmed frontier
            for token in self._tokens:
                seg = token[-1]
                if pending_seg is not _NO_SEG and seg != pending_seg:
                    pending_seg = _NO_SEG  # run ended: never confirmed
                if token[0] != "word":
                    continue
                if self._origin_of(seg) == ORIGIN_SUMMARY:
                    continue
                if self._renderable(token):
                    count += 1
                elif self.eager:
                    if seg == pending_seg:
                        count += 1  # a later word confirms the pending one
                    pending_seg = seg
            self._backlog_cache = (self._tokens_rev, count)
            return count

    def frame(self) -> list:
        """The current display frame: content right-aligned, and with a
        gauge, cell 1 the gauge and cell 2 the content-kind marker."""
        with self._lock:
            cells = list(self._buf)
            pad = self._content_width - len(cells)
            content = [BLANK] * pad + cells
            if not self.gauge:
                return content
            return [self._gauge_cell(), self._separator()] + content
