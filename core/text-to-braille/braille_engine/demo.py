"""Demo stream: canned text through the live transcription pipeline.

Space+T (dots 2-3-4-5) or the Windows panel's play button streams text
through the same soft/revise/final segment protocol the speech pipeline
uses, so everything downstream behaves as in a live session: words arrive at
partial-transcript cadence, segments harden sentence by sentence, the gauge
fills and the latency presets apply. Useful when there is no microphone or
network, for checking a display, or for learning to read the stream.

The stream is a toggle. Stopping it withdraws every still-queued demo word
(the same empty final revision Human-mode entry uses); cells already shown
stay. A run that reaches the end just stops feeding, and once its tail has
drained the pace loop leaves a "demo ended" / "captions ended" frame up
(``take_ended``); a stop press or Human-mode entry never sets that.

Live speech may interleave (both are just segments to the engine), pause
and hold freeze the display while the feeder keeps queueing, and Human-mode
entry makes the feeder notice its segment vanished and stop.

Timed caption replay (``toggle(text, timed=True)``) takes SRT/WebVTT content
(``braille_engine.captions``) and replays it on the wall clock: each cue's
text arrives at its start offset, as one soft emission (no per-word timing
is invented). Cues routinely split sentences, so consecutive cues
accumulate into one segment, which hardens when the text ends a sentence,
when the gap to the next cue exceeds ``CAPTION_GAP_S``, or at the end of
the track. A replay that falls behind feeds late cues immediately (delays
are measured from the track start, so it catches up rather than drifts).
Because sentence-ending cues harden at once, a Human-mode entry in the gap
before the next cue leaves no soft segment to notice, so the replay also
watches the engine's ``soft_discards`` counter.
"""

import re
import threading
import time

from .captions import parse_captions
from .reply import _SENTENCE_END

# Provider-partial cadence: one growing soft partial per beat, a couple of
# new words each — ~150 wpm, conversational pace. The sentence hardens (and
# in the default non-eager presets, first renders) when its final lands,
# the same sentence-burst rhythm real engines deliver.
BEAT_S = 0.8
WORDS_PER_BEAT = 2

# Timed replay: a silence gap this long between cues hardens the open
# segment even without a sentence terminator — a pause that long is a
# spoken boundary, and hardening lets the text render in the default
# non-eager presets instead of waiting soft through the silence.
CAPTION_GAP_S = 2.0

DEMO_TEXT = (
    "Welcome to Dotify. "
    "This is the demo stream: preset text playing through the live "
    "pipeline, with no microphone or internet needed. "
    "Words arrive in small groups, the way live speech does. "
    "Try the reading controls while it plays: speed, grade, reading mode, "
    "jump to live. "
    "Press space with dots two three four five, or the panel's demo "
    "button, to stop. "
    "Dotify turns live speech into braille the moment it is spoken."
)


class DemoStream:
    """Feeds the demo passage to a ``BrailleEngine`` on its own thread.

    One instance per ``PacerControls``; ``toggle`` is safe from any thread
    (display-key transports, the bridge's HTTP threads, the terminal key
    thread). ``status`` is the [keys]-style stderr line writer.
    """

    def __init__(self, engine, status=None, beat_s=BEAT_S,
                 clock=time.monotonic, wait=None, gap_s=CAPTION_GAP_S):
        self.engine = engine
        self._status = status or (lambda msg: None)
        self.beat_s = beat_s
        self.gap_s = gap_s
        # Injectable time for the tests' fake clock: ``clock`` reads the
        # wall clock, ``wait(stop, seconds)`` blocks like ``stop.wait``
        # (True means the stop event fired). The defaults are the real
        # thing.
        self._clock = clock
        self._wait = wait or (lambda stop, seconds: stop.wait(seconds))
        self._lock = threading.Lock()
        self._thread = None
        self._stop = None
        self._timed = False   # the RUNNING stream's mode, set on start
        self._runs = 0   # run id, so a restarted demo can never collide
                         # with a prior run's segment ids
        self._ended_source = ""   # natural-completion notice: the source
                         # label once a run feeds its WHOLE track (never on
                         # a stop-press or a Human-mode bow-out, where "off"
                         # already answers). Consumed by take_ended

    @property
    def active(self) -> bool:
        """True while the feeder is streaming (surfaced to the panel)."""
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    @property
    def source_label(self) -> str:
        """The streaming source's status-flash name while the feeder runs:
        "captions" for a timed caption replay, "demo" for preset text, ""
        when idle. ``show_status`` prefers it over the mic label: while the
        feeder streams, it is the input source."""
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                return ""
            return "captions" if self._timed else "demo"

    def toggle(self, text=None, timed=False) -> bool:
        """Start the demo stream, or stop the one running.

        ``text`` (optional) substitutes a custom passage for the built-in
        one — the bridge's demo command forwards it. With ``timed`` True,
        ``text`` is SRT/WebVTT caption-file CONTENT and the track replays
        on the wall clock (see the module docstring); content with no
        usable cues raises ValueError so the bridge can answer the post
        instead of starting a silent stream. Returns True when this call
        STARTED a stream, False when it stopped one.
        """
        if timed:
            # Parse OUTSIDE the lock: the asyncio pace loop polls ``active``
            # (same lock) every iteration, and a large SRT parse measured
            # 100-170 ms — under the lock it stalled pacing, flips, and
            # ingest right as the reader pressed play. Parsing first also
            # keeps the contract the bridge depends on: unusable content
            # raises ValueError before anything is announced or started.
            cues = parse_captions(text or "", status=self._status)
            if not cues:
                raise ValueError(
                    "no usable caption cues found — expected SRT or "
                    "WebVTT caption file content")
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                # Stop-toggle: a stream is already running. Any cues just
                # parsed are simply discarded.
                self._stop.set()
                return False
            if timed:
                target, args = self._run_timed, (cues,)
            else:
                target, args = self._run, (text or DEMO_TEXT,)
            self._timed = bool(timed)
            self._ended_source = ""   # a fresh stream reopens the story
            self._runs += 1
            self._stop = threading.Event()
            self._thread = threading.Thread(
                target=target, args=args + (self._runs, self._stop),
                name="dotify-demo-stream", daemon=True)
            self._thread.start()
            return True

    def _set_ended(self, label) -> None:
        with self._lock:
            self._ended_source = label

    def take_ended(self) -> str:
        """Consume the completion notice: the source label once the last
        run fed its whole track and nothing restarted since, else "". The
        pace loop calls this once the queued tail has drained."""
        with self._lock:
            label, self._ended_source = self._ended_source, ""
            return label

    def _run(self, text, run_id, stop) -> None:
        sentences = [s for s in re.split(r"(?<=[.!?])\s+", text.strip())
                     if s]
        fed = []
        for index, sentence in enumerate(sentences):
            seg = ("demo", run_id, index)
            fed.append(seg)
            if not self._feed_sentence(sentence, seg, stop):
                # Stopped (or discarded by Human-mode entry): withdraw
                # every still-queued demo word so "off" means off. The
                # empty final revision is the established withdrawal —
                # cells already shown stay (the frozen rule), fully-
                # streamed segments are a no-op, unknown ones return
                # False harmlessly.
                for queued in fed:
                    self.engine.revise_segment(queued, "", final=True)
                self._status("demo stopped — queued demo text withdrawn")
                return
        self._status("demo passage fully fed — the queued tail streams "
                     "at reading pace (jump-to-live skips it)")
        self._set_ended("demo")

    def _feed_sentence(self, sentence, seg, stop) -> bool:
        """One sentence as one segment: growing soft partials at beat
        cadence, then the hardening final. Returns False when the run
        should end (reader stopped it, or the segment was discarded)."""
        words = sentence.split()
        count = 0
        while count < len(words):
            count = min(len(words), count + WORDS_PER_BEAT)
            # Trailing space so the FIRST feed leaves the engine's shared
            # assembler clean (the unknown-segment registration path feeds
            # it directly — run.py does the same); growth feeds route to
            # revise_segment, which tokenizes with a fresh assembler.
            self.engine.feed(" ".join(words[:count]) + " ",
                             seg=seg, final=False)
            if self._wait(stop, self.beat_s):
                return False
            if not self.engine.knows_segment(seg):
                # Human-mode entry withdrew and FORGOT every soft segment,
                # ours included: feeding on would re-register it and land
                # demo text on top of the reader's typing.
                return False
        self.engine.feed(" ".join(words), seg=seg, final=True)
        return True

    # ---- timed caption replay ----

    @staticmethod
    def _ends_sentence(text) -> bool:
        # The reply composer's sentence-end rule, shared: terminal
        # punctuation, optionally wrapped in closing quotes/brackets.
        # Caption text may carry trailing whitespace the composer's
        # committed words never do, hence the rstrip.
        return bool(_SENTENCE_END.search(text.rstrip()))

    def _run_timed(self, cues, run_id, stop) -> None:
        """Replay parsed cues on the wall clock: each cue's text feeds as
        one soft emission at the cue's start offset; segments accumulate
        across cues and harden per the module-docstring rule. Late cues
        (slow engine) feed immediately — delays are computed against the
        track's absolute start, so the replay catches up, never drifts.

        The engine's ``speech_is_stream`` flag is raised for the run, so
        its text is tagged ORIGIN_CAPTIONS rather than ORIGIN_SPEECH."""
        prior = getattr(self.engine, "speech_is_stream", False)
        self.engine.speech_is_stream = True
        try:
            self._replay_cues(cues, run_id, stop)
        finally:
            self.engine.speech_is_stream = prior

    def _replay_cues(self, cues, run_id, stop) -> None:
        origin = self._clock()
        fed = []        # every segment key this run touched, for withdrawal
        index = 0       # current segment index within the run
        acc = ""        # the open segment's accumulated cue text ("" when
                        # the last segment hardened and none is open)
        # Human-mode entry bumps this counter even when it finds nothing
        # soft to discard — the only trace an entry leaves when it lands
        # in an inter-cue gap AFTER our segment hardened. Capture it now;
        # any advance means the reader walked away from streamed text.
        discards = self.engine.soft_discards

        def withdraw():
            # Same contract as the preset mode: the empty final revision
            # withdraws every still-queued word; cells already shown stay
            # (the frozen rule), forgotten segments answer False harmlessly.
            for queued in fed:
                self.engine.revise_segment(queued, "", final=True)
            self._status("captions stopped — queued caption text withdrawn")

        for position, cue in enumerate(cues):
            delay = cue.start - (self._clock() - origin)
            if delay > 0:
                if self._wait(stop, delay):
                    withdraw()
                    return
            elif stop.is_set():
                # A catch-up burst never waits — honor the stop anyway.
                withdraw()
                return
            seg = ("captions", run_id, index)
            if self.engine.soft_discards != discards \
                    or (acc and not self.engine.knows_segment(seg)):
                # Human-mode entry (the counter moved), or something else
                # forgot our open soft segment (a stale-orphan drop):
                # feeding on would (re-)register the segment and land
                # caption text on top of the reader's typing — worse, an
                # unknown segment's first feed routes through the engine's
                # SHARED assembler, which in Human mode holds the reader's
                # half-typed word, so the cue's first word could fuse onto
                # it. Bow out.
                withdraw()
                return
            if not fed or fed[-1] != seg:
                fed.append(seg)
            acc = (acc + " " + cue.text) if acc else cue.text
            last = position + 1 == len(cues)
            gap = None if last else cues[position + 1].start - cue.end
            if last or self._ends_sentence(acc) \
                    or (gap is not None and gap > self.gap_s):
                # Trailing space here too, unlike the preset mode's finals:
                # a single-cue sentence makes this the segment's FIRST feed,
                # which routes through the engine's shared assembler — the
                # space flushes the last word and leaves the assembler
                # clean. On an already-known segment the feed becomes a
                # revision, which strips it harmlessly.
                self.engine.feed(acc + " ", seg=seg, final=True)
                index += 1
                acc = ""
            else:
                # Trailing space for the same assembler-cleanliness reason
                # as the preset mode's soft feeds.
                self.engine.feed(acc + " ", seg=seg, final=False)
        self._status("captions fully fed — the queued tail streams at "
                     "reading pace (jump-to-live skips it)")
        self._set_ended("captions")
