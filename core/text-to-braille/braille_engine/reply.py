"""Reply channel: the reader types braille on the display.

A deaf-blind reader who cannot speak answers through Dotify. Space+R (dots
1-2-3-5) enters and leaves reply mode. While it is active:

* Braille streaming freezes (speech keeps queueing and the gauge keeps
  measuring, as in a pause) and the content line echoes the typed cells,
  left-aligned, with ``r`` in the marker cell.
* Each completed word (Space commits it) is back-translated to print at the
  current grade and appears in the platform apps' state poll, which print
  it in the transcript.
* Each completed sentence (a word ending in ``. ! ?``, or dot-8, the
  Perkins Enter) is spoken aloud by the platform app.
* Dot-7 (Perkins backspace) erases the last cell of the current word.
* Exiting commits the partial word, speaks what remains, restores the
  frozen reading frame, and streaming resumes where it stopped.

This module owns only the composer state machine; key routing lives in
``display_keys``, the pacer freeze in ``run.py``, and printing and speaking
in the platform shells, which poll ``state()``.

Reply is not Human mode: Human mode discards speech and clears the queue
for a typing partner, while a reply is an interjection inside a listening
session and discards nothing. The platforms pause the microphone during a
reply all the same, so the synthetic voice isn't transcribed back.
"""

import re
import threading

from .cells import BLANK, frame_to_str

# A committed word that ends a sentence: terminal punctuation — including a
# trailing-off ellipsis, "…" or "..." (the latter via its final ".") —
# optionally wrapped in closing quotes/brackets ("he said.'" still ends it).
# Shared with the timed caption replay (demo.py), so replies and caption
# segments end sentences on the same boundaries.
_SENTENCE_END = re.compile(r'(?:[.!?]|…)["\'’”)\]]*$')


class ReplyComposer:
    """Accumulates typed dot patterns into words, sentences, and echo frames.

    Thread-safe: typing arrives on the display transport's reader thread
    while ``state()`` is polled from the platform bridge thread. Engine
    calls (echo frames) happen inside the composer lock; the engine's own
    RLock nests below it and never calls back up, so there is no inversion.
    """

    # How long a refused-chord hint owns the line before the typing echo
    # returns on its own (typing sooner also returns it).
    HINT_SECONDS = 2.0
    # What a refused chord shows on the display: the typist's only channel
    # needs to name the way out. Plain words, since it is translated at the
    # current grade.
    HINT_TEXT = "space r to exit"

    def __init__(self, engine, status=None):
        self.engine = engine
        self._status = status or (lambda msg: None)
        self._lock = threading.Lock()
        self.active = False
        self.session = 0        # bumps on every entry — platforms reset
                                # their insert/speak cursors on a new value
        self.seq = 0            # bumps on every visible change (cheap
                                # change detection for 1 s pollers)
        self._cells = []        # the current WORD's typed cell patterns
                                # (back-translated whole, so grade-2
                                # contractions see the full word)
        self._echo = []         # this session's cell history for the
                                # display echo (words + blank separators)
        self._text = ""         # committed print text this session
        self._sent_mark = 0     # offset into _text where closed sentences
                                # end; the tail past it is the open sentence
        self._sentences = []    # closed sentences, in order — platforms
                                # speak entries beyond their own cursor
        self._hint_seq = 0      # claims the hint's delayed restore: any
                                # newer paint (typing, a fresh hint, exit)
                                # invalidates an older timer's repaint

    # -- session -----------------------------------------------------------

    def enter(self) -> bool:
        with self._lock:
            if self.active:
                return False
            self.active = True
            self.session += 1
            self.seq += 1
            self._cells = []
            self._echo = []
            self._text = ""
            self._sent_mark = 0
            self._sentences = []
            self._paint()
        return True

    def exit(self) -> bool:
        """Commit the partial word, close the open sentence (it speaks even
        without terminal punctuation — ending the reply IS the boundary),
        and deactivate. The session's text/sentences stay readable in
        ``state()`` until the next entry, so a 1 s poller never misses the
        final flush."""
        with self._lock:
            if not self.active:
                return False
            self._commit_word_locked()
            self._close_sentence_locked()
            self.active = False
            self.seq += 1
        return True

    # -- typing (display key thread) ---------------------------------------

    def add_pattern(self, pattern: int) -> None:
        with self._lock:
            if not self.active:
                return
            self._cells.append(int(pattern) & 0xFF)
            self._echo.append(int(pattern) & 0xFF)
            self._paint()

    def backspace(self) -> None:
        """Dot-7, the Perkins backspace: erase the last cell of the word
        being typed. Committed words are frozen — same discipline as every
        other emitted cell in the system."""
        with self._lock:
            if not self.active:
                return
            if self._cells:
                self._cells.pop()
                self._echo.pop()
            self._paint()

    def space(self) -> None:
        with self._lock:
            if not self.active:
                return
            self._commit_word_locked()
            self._paint()

    def end_sentence(self) -> None:
        """Dot-8, the Perkins Enter: speak what I have now — commits the
        word and closes the sentence regardless of punctuation."""
        with self._lock:
            if not self.active:
                return
            self._commit_word_locked()
            self._close_sentence_locked()
            self._paint()

    def refuse(self) -> None:
        """A non-typing chord landed mid-reply. The refusal must be
        FEELABLE: the display line briefly answers with the exit hint
        ("space r to exit") in place of the echo, then the typing echo
        returns — after HINT_SECONDS on its own, or immediately on the next
        keypress. The status line still logs the specific chord (the
        display line is for the way out, not the diagnosis)."""
        with self._lock:
            if not self.active:
                return
            self._hint_seq += 1
            seq = self._hint_seq
            try:
                width = getattr(self.engine, "content_width", None) or 0
                cells, _ = self.engine.fit_cells(self.HINT_TEXT, width)
                self.engine.reply_frame(cells)
            except (OSError, RuntimeError) as error:
                self._status(f"reply hint not shown (display write failed: "
                             f"{error})")
                return
        timer = threading.Timer(self.HINT_SECONDS, self._end_hint, (seq,))
        timer.daemon = True
        timer.start()

    def _end_hint(self, seq: int) -> None:
        """Timer callback: put the typing echo back, unless something newer
        already owns the line (typing repainted it, a fresh hint claimed
        it, or the reply ended and reply_restore took over)."""
        with self._lock:
            if not self.active or seq != self._hint_seq:
                return
            self._paint()

    # -- state (platform poll) ---------------------------------------------

    def state(self) -> dict:
        with self._lock:
            return {
                "active": self.active,
                "session": self.session,
                "seq": self.seq,
                "text": self._text,
                "sentences": list(self._sentences),
            }

    # -- internals (hold self._lock) ---------------------------------------

    def _commit_word_locked(self) -> None:
        if not self._cells:
            return
        word = self._back_translate(self._cells).strip()
        self._cells = []
        if not self._echo or self._echo[-1] != BLANK:
            self._echo.append(BLANK)
        if not word:
            return
        self._text = f"{self._text} {word}" if self._text else word
        self.seq += 1
        if _SENTENCE_END.search(word):
            self._close_sentence_locked()

    def _close_sentence_locked(self) -> None:
        sentence = self._text[self._sent_mark:].strip()
        if not sentence:
            return
        self._sentences.append(sentence)
        self._sent_mark = len(self._text)
        self.seq += 1

    def _back_translate(self, cells) -> str:
        back = getattr(self.engine.translator, "back_translate", None)
        if back is not None:
            try:
                word = back(list(cells))
            except Exception:
                word = ""
            if word:
                return word
        # No back-translation available (or it produced nothing): show the
        # typed patterns as Unicode braille glyphs rather than lose the
        # reader's words — visible on screen, honest about what was typed.
        return frame_to_str(c & 0xFF for c in cells)

    def _paint(self) -> None:
        """Best-effort echo frame, like every flash: a display outage never
        propagates past a status line — reconnect is deferred to the pacer,
        which resumes when the reply ends."""
        try:
            self.engine.reply_frame(self._echo)
        except (OSError, RuntimeError) as error:
            self._status(f"reply echo not shown (display write failed: "
                         f"{error})")
