"""Revisable transcription buffer.

The engine half of the contract: text still queued is fair game for
correction, text already emitted — including the word currently mid-display —
is frozen. Plus the ws_source protocol interpreter and the run_pipeline
dispatch that carry revisions from the wire to the engine.
"""

import asyncio
import threading

from braille_engine.cells import BLANK
from braille_engine.controls import PacerControls, MODE_LISTEN, MODE_TYPE
from braille_engine.engine import BrailleEngine
from braille_engine.sinks.simulated import SimulatedSink
from braille_engine.sources.ws_source import interpret_message
from braille_engine.translator import DevUebTranslator
from run import run_pipeline


WIDTH = 40


def make_engine(width=WIDTH):
    translator = DevUebTranslator()
    sink = SimulatedSink(width=width, echo=False)
    engine = BrailleEngine(translator, sink)
    # These tests use number words as convenient tokens ("one two three");
    # keep them words — the digits filter has its own tests.
    engine.set_digits_filter(False)
    engine.start()
    return engine, translator, sink


def drain(engine):
    while engine.tick():
        pass


def frame_for(translator, text, width=WIDTH):
    cells = []
    for index, word in enumerate(text.split(" ")):
        if index:
            cells.append(BLANK)
        cells.extend(translator.translate(word))
    cells = cells[-width:]
    return [BLANK] * (width - len(cells)) + cells


# ---- engine.revise_segment ------------------------------------------------

def test_revision_before_streaming_replaces_the_whole_segment():
    engine, translator, sink = make_engine()
    engine.feed("helo wrld ", seg="s1")
    assert engine.revise_segment("s1", "hello world") is True
    drain(engine)
    assert sink.frames[-1] == frame_for(translator, "hello world ")


def test_emitted_words_are_frozen_only_the_queued_tail_changes():
    engine, translator, sink = make_engine()
    engine.feed("alpha beta gamma ", seg="s1")
    engine.tick()   # "alpha" is now loaded and its first cell shown: frozen
    assert engine.revise_segment("s1", "omega bravo charlie") is True
    drain(engine)
    # The revision's first word stands for the frozen "alpha" and is skipped.
    assert sink.frames[-1] == frame_for(translator, "alpha bravo charlie ")


def test_mid_display_word_is_frozen_too():
    engine, translator, sink = make_engine()
    engine.feed("abcdef xyz ", seg="s1")
    engine.tick()   # one cell of "abcdef" on the display: the word is frozen
    assert engine.revise_segment("s1", "qqq uvw") is True
    drain(engine)
    assert sink.frames[-1] == frame_for(translator, "abcdef uvw ")


def test_extension_of_a_fully_read_segment_appends():
    engine, translator, sink = make_engine()
    engine.feed("hi ", seg="s1")
    drain(engine)
    assert engine.revise_segment("s1", "hi there friend") is True
    drain(engine)
    assert sink.frames[-1] == frame_for(translator, "hi there friend ")


def test_hardening_lands_in_stream_order_not_at_the_end():
    engine, translator, sink = make_engine()
    engine.feed("one two ", seg="s1")
    engine.feed("three four ", seg="s2")
    assert engine.revise_segment("s1", "one two extra") is True
    drain(engine)
    assert sink.frames[-1] == frame_for(translator, "one two extra three four ")


def test_extension_after_full_consumption_precedes_later_segments():
    engine, translator, sink = make_engine()
    engine.feed("hi ", seg="s1")
    drain(engine)                       # s1 fully read
    engine.feed("bye ", seg="s2")       # next segment queued, unread
    assert engine.revise_segment("s1", "hi again") is True
    drain(engine)
    assert sink.frames[-1] == frame_for(translator, "hi again bye ")


def test_revision_can_shrink_or_withdraw_a_segment():
    engine, translator, sink = make_engine()
    engine.feed("aaa bbb ccc ", seg="s1")
    engine.tick()   # freeze "aaa"
    assert engine.revise_segment("s1", "") is True
    drain(engine)
    # Only the frozen word (and its owed separator) remains.
    assert sink.frames[-1] == frame_for(translator, "aaa ")
    assert engine.backlog_words() == 0


def test_unknown_segment_revision_is_a_noop():
    engine, _translator, _sink = make_engine()
    engine.feed("hello ", seg="s1")
    assert engine.revise_segment("nope", "anything") is False
    assert engine.revise_segment(None, "anything") is False


def test_corrections_of_jumped_past_words_are_dropped():
    # The jump captured (snapped/summarized) these words as heard; a revision
    # that merely re-spells them arrives too late to matter.
    engine, _translator, _sink = make_engine()
    engine.feed("one two three ", seg="s1")
    engine.jump_to_live()
    assert engine.revise_segment("s1", "won too tree") is False
    assert engine.has_pending() is False


def test_speech_after_a_jump_still_streams():
    # The in-flight segment keeps growing after the jump: only the words
    # NEWER than the jump appear — nothing jumped past is replayed.
    engine, translator, sink = make_engine()
    engine.feed("one two three ", seg="s1")
    engine.jump_to_live()                     # snap shows "one two three"
    assert engine.revise_segment("s1", "won too tree four five") is True
    drain(engine)
    assert sink.frames[-1] == frame_for(translator, "one two three four five ")


def test_post_jump_tail_precedes_newer_segments():
    engine, translator, sink = make_engine()
    engine.feed("one two ", seg="s1")
    engine.jump_to_live()
    engine.feed("later words ", seg="s2")     # next segment, spoken after
    assert engine.revise_segment("s1", "one two tail") is True
    drain(engine)
    assert sink.frames[-1] == frame_for(translator,
                                        "one two tail later words ")


def test_refeeding_a_known_segment_is_a_revision_not_a_duplicate():
    # A hardened final re-delivered after a stream reconnect arrives as a
    # plain feed with an id the engine already knows.
    engine, translator, sink = make_engine()
    engine.feed("helo wrld ", seg="s1")
    engine.feed("hello world ", seg="s1")
    drain(engine)
    assert sink.frames[-1] == frame_for(translator, "hello world ")


def test_backlog_gauge_counts_follow_revisions():
    engine, _translator, _sink = make_engine()
    engine.feed("a b c ", seg="s1")
    assert engine.backlog_words() == 3
    engine.revise_segment("s1", "a b c d e")
    assert engine.backlog_words() == 5
    engine.revise_segment("s1", "a")
    assert engine.backlog_words() == 1


def test_untagged_feeds_are_untouched_by_revisions():
    engine, translator, sink = make_engine()
    engine.feed("typed ")              # no segment id (Human mode, file source)
    engine.feed("spoken ", seg="s1")
    engine.revise_segment("s1", "revised")
    drain(engine)
    assert sink.frames[-1] == frame_for(translator, "typed revised ")


# ---- retokenization across the frozen barrier ---------------------------
# The splice is anchored TEXTUALLY against the words actually streamed, so a
# final that tokenizes differently from the interim it corrects cannot land
# the splice a word early (duplicating a read word) or late (dropping a new
# one).

def test_token_split_across_the_barrier_duplicates_nothing():
    # The issue's verbatim case: "icecream" streamed, the final says
    # "ice cream". Word-count skipping would skip only "ice" and re-queue
    # "cream" — a word the reader already has under their fingers.
    engine, translator, sink = make_engine()
    engine.feed("icecream sundae ", seg="s1")
    engine.tick()   # freeze "icecream"
    assert engine.revise_segment("s1", "ice cream sundae") is True
    drain(engine)
    assert sink.frames[-1] == frame_for(translator, "icecream sundae ")


def test_pure_split_retokenization_of_the_frozen_word_is_a_noop():
    engine, translator, sink = make_engine()
    engine.feed("icecream ", seg="s1")
    engine.tick()   # freeze "icecream"
    engine.revise_segment("s1", "ice cream", final=True)
    drain(engine)
    assert sink.frames[-1] == frame_for(translator, "icecream ")


def test_token_merge_across_frozen_words_drops_nothing():
    # "can not" streamed as two words; the final merges them. Word-count
    # skipping would count "cannot" and "stop" as the three frozen words
    # plus one — silently dropping the genuinely new "please".
    engine, translator, sink = make_engine()
    engine.feed("can not stop ", seg="s1")
    drain(engine)   # all three words read
    assert engine.revise_segment("s1", "cannot stop please") is True
    drain(engine)
    assert sink.frames[-1] == frame_for(translator, "can not stop please ")


def test_merge_at_the_barrier_keeps_the_queued_half():
    # "can" is frozen, "not" still queued; the final "cannot" merges across
    # the barrier. The unshown half must survive the splice — dropping the
    # revision whole would drop "not" with the replaced queued run.
    engine, translator, sink = make_engine()
    engine.feed("can not ", seg="s1")
    engine.tick()   # freeze "can"
    assert engine.revise_segment("s1", "cannot", final=True) is True
    drain(engine)
    # The owed separator folds away with the merge: the display reads the
    # provider's merged form.
    assert sink.frames[-1] == frame_for(translator, "cannot ")


def test_punctuation_token_in_the_frozen_prefix_does_not_shift_the_splice():
    # A punctuation mark that streamed as its own token disappears from the
    # final's tokenization: by word count that misaligns every later word.
    engine, translator, sink = make_engine()
    engine.feed("hello , world ", seg="s1")
    drain(engine)   # "hello", ",", "world" all read
    assert engine.revise_segment("s1", "hello, world again") is True
    drain(engine)
    assert sink.frames[-1] == frame_for(translator, "hello , world again ")


def test_trailing_punctuation_token_at_the_barrier_is_not_duplicated():
    # The frozen prefix ENDS on a standalone "," token. The character walk
    # can't see punctuation, so settle() steps over it to i == total — and
    # the revision's own "," must not then hit the append branch, which
    # would show the mark twice ("hello , , world again"). Caption tracks stream spaced
    # punctuation tokens exactly like this.
    engine, translator, sink = make_engine()
    engine.feed("hello , world ", seg="s1")
    while list(engine._seg_info["s1"][4]) != ["hello", ","]:
        assert engine.tick()
    assert engine.revise_segment("s1", "hello , world again") is True
    drain(engine)
    assert sink.frames[-1] == frame_for(translator, "hello , world again ")


def test_trailing_em_dash_token_at_the_barrier_is_not_duplicated():
    engine, translator, sink = make_engine()
    engine.feed("good — dog ", seg="s1")
    while list(engine._seg_info["s1"][4]) != ["good", "—"]:
        assert engine.tick()
    assert engine.revise_segment("s1", "good — dog again") is True
    drain(engine)
    assert sink.frames[-1] == frame_for(translator, "good — dog again ")


def test_revision_dropping_the_trailing_punctuation_keeps_alignment():
    # The final drops the standalone "," entirely. The unmatched trailing
    # credit must NOT swallow the first real revision word — the following
    # words still splice in order after the frozen "hello ,".
    engine, translator, sink = make_engine()
    engine.feed("hello , world ", seg="s1")
    while list(engine._seg_info["s1"][4]) != ["hello", ","]:
        assert engine.tick()
    assert engine.revise_segment("s1", "hello world again") is True
    drain(engine)
    assert sink.frames[-1] == frame_for(translator, "hello , world again ")


def test_retokenized_comma_onto_the_previous_word_still_aligns():
    # The final attaches the streamed standalone "," to its word
    # ("hello,"): the walk consumes the frozen text exactly; nothing
    # duplicates and the new word still splices.
    engine, translator, sink = make_engine()
    engine.feed("hello , world ", seg="s1")
    while list(engine._seg_info["s1"][4]) != ["hello", ","]:
        assert engine.tick()
    assert engine.revise_segment("s1", "hello, world again") is True
    drain(engine)
    assert sink.frames[-1] == frame_for(translator, "hello , world again ")


def test_punctuation_only_change_at_the_boundary_splices_cleanly():
    engine, translator, sink = make_engine()
    engine.feed("hello world ", seg="s1")
    engine.tick()   # freeze "hello"
    assert engine.revise_segment("s1", "Hello, world!") is True
    drain(engine)
    # The frozen "hello" cannot gain its comma; the tail splices after it.
    assert sink.frames[-1] == frame_for(translator, "hello world! ")


def test_word_grown_on_a_frozen_stem_is_a_dropped_correction():
    # "the" -> "then" is a wording change to a frozen word, not a merge:
    # nothing of it was queued, so the grown suffix must NOT splice a
    # stray "n" after the frozen word.
    engine, translator, sink = make_engine()
    engine.feed("the ", seg="s1")
    engine.tick()   # freeze "the"
    assert engine.revise_segment("s1", "then again") is True
    drain(engine)
    assert sink.frames[-1] == frame_for(translator, "the again ")


def test_retokenized_final_after_a_jump_contributes_only_new_words():
    # take_pending's captured words anchor exactly like streamed ones: the
    # split final re-covers them textually, so only the truly new word
    # streams after the jump.
    engine, translator, sink = make_engine()
    engine.feed("icecream is good ", seg="s1")
    engine.jump_to_live()   # snap captures all three words
    assert engine.revise_segment("s1", "ice cream is good yum") is True
    drain(engine)
    assert sink.frames[-1] == frame_for(translator, "icecream is good yum ")


# ---- soft segments: queue but never render until hardened -------------------

def test_soft_text_queues_but_never_renders():
    # The identity guarantee: nothing reaches the reader's fingers that the
    # finalized transcript doesn't also show — hypothesis text waits.
    engine, translator, sink = make_engine()
    engine.feed("maybe wrong ", seg="s1", final=False)
    assert engine.backlog_words() == 0      # hypothesis: not backlog yet...
    assert engine.tick() == 0               # ...and the display waits too
    assert sink.frames == []
    engine.revise_segment("s1", "definitely right", final=True)
    assert engine.backlog_words() == 2      # hardened: NOW it counts
    drain(engine)
    assert sink.frames[-1] == frame_for(translator, "definitely right ")


def test_rendering_stops_at_the_soft_boundary():
    engine, translator, sink = make_engine()
    engine.feed("done ", seg="s1", final=True)
    engine.feed("pending ", seg="s2", final=False)
    drain(engine)
    assert sink.frames[-1] == frame_for(translator, "done ")
    engine.revise_segment("s2", "pending", final=True)
    drain(engine)
    assert sink.frames[-1] == frame_for(translator, "done pending ")


def test_jump_keeps_the_soft_tail_queued():
    # The snap/summary captures only committed text; the soft tail is
    # hypothesis, so it survives the jump and streams once hardened.
    engine, translator, sink = make_engine()
    engine.feed("committed words here ", seg="s1", final=True)
    engine.feed("soft tail ", seg="s2", final=False)
    text, cells = engine.take_pending()
    assert text == "committed words here"
    assert engine.backlog_words() == 0      # tail queued but still soft
    engine.show_frame(cells)                # the snap
    engine.revise_segment("s2", "soft tail", final=True)
    assert engine.backlog_words() == 2      # hardened: counts again
    drain(engine)
    assert sink.frames[-1] == frame_for(
        translator, "committed words here soft tail ")


def test_pipeline_hardens_leftover_soft_text_at_end_of_source():
    # A source that ends mid-segment (--ws-once close, file replay) leaves
    # soft text that will never get a final: it is the best text there will
    # ever be, so it renders instead of stranding the pipeline.
    engine, translator, sink = make_engine(width=16)
    interval = {"v": 0.0}
    stop = threading.Event()
    source = _op_source([("feed", "s1", "leftover soft ", False)])
    asyncio.run(run_pipeline(source, engine, interval, stop))
    assert sink.frames[-1] == frame_for(translator, "leftover soft ",
                                        width=16)


# ---- eager soft rendering (the "fastest" latency preset) --------------------

def test_eager_streams_soft_text_up_to_the_frontier_word():
    engine, translator, sink = make_engine()
    engine.set_eager(True)
    engine.feed("hello brave world ", seg="s1", final=False)
    drain(engine)
    # The run's trailing word is the unstable frontier of a growing
    # partial: it waits for a newer partial to stand a word behind it.
    assert sink.frames[-1] == frame_for(translator, "hello brave ")
    # The held frontier word can't render yet, so it isn't backlog either
    # (the gauge counts only catch-up-able text).
    assert engine.backlog_words() == 0


def test_eager_frontier_renders_once_a_newer_partial_confirms_it():
    engine, translator, sink = make_engine()
    engine.set_eager(True)
    engine.feed("hello brave wor ", seg="s1", final=False)
    drain(engine)
    engine.revise_segment("s1", "hello brave world again")
    drain(engine)
    # "world" now has "again" behind it: confirmed, streams; "again" waits.
    assert sink.frames[-1] == frame_for(translator, "hello brave world ")


def test_eager_frontier_is_not_confirmed_by_a_later_segment():
    # Confirmation must come from the frontier word's OWN segment — a new
    # segment starting behind it says nothing about its wording, and its
    # final is imminent anyway.
    engine, translator, sink = make_engine()
    engine.set_eager(True)
    engine.feed("one two ", seg="s1", final=False)
    engine.feed("three four ", seg="s2", final=False)
    drain(engine)
    assert sink.frames[-1] == frame_for(translator, "one ")
    engine.revise_segment("s1", "one two", final=True)
    drain(engine)
    # s1 hardened and fully streams; s2's "three" is confirmed by "four",
    # which becomes the new held frontier.
    assert sink.frames[-1] == frame_for(translator, "one two three ")


def test_eager_streamed_words_are_frozen_against_the_final():
    # The append-only contract of eager mode: once a soft word has streamed,
    # even the provider's final may not take it back — the correction lands
    # only on text that has not reached the reader.
    engine, translator, sink = make_engine()
    engine.set_eager(True)
    engine.feed("the cat sad ", seg="s1", final=False)
    drain(engine)                      # "the cat" streamed; "sad" held
    engine.revise_segment("s1", "the bat sat down", final=True)
    drain(engine)
    assert sink.frames[-1] == frame_for(translator, "the cat sat down ")


def test_segment_text_is_the_engines_effective_text():
    # What the page transcript must print: the words as streamed (frozen)
    # plus the corrected tail — not the provider's last word.
    engine, _translator, _sink = make_engine()
    engine.set_eager(True)
    engine.feed("the cat sad ", seg="s1", final=False)
    drain(engine)
    engine.revise_segment("s1", "the bat sat down", final=True)
    assert engine.segment_text("s1") == "the cat sat down"
    assert engine.segment_text("never-fed") is None


def test_segment_text_matches_the_provider_when_nothing_diverged():
    engine, _translator, _sink = make_engine()
    engine.feed("plain final ", seg="s1", final=True)
    drain(engine)
    assert engine.segment_text("s1") == "plain final"


def test_eager_lone_word_waits_for_harden_all():
    engine, translator, sink = make_engine()
    engine.set_eager(True)
    engine.feed("solo ", seg="s1", final=False)
    assert engine.tick() == 0          # frontier with no confirmation yet
    engine.harden_all()                # end of source: best text there is
    drain(engine)
    assert sink.frames[-1] == frame_for(translator, "solo ")


def test_eager_off_restores_the_wait_for_harden_gate():
    engine, translator, sink = make_engine()
    engine.set_eager(True)
    engine.feed("early words here ", seg="s1", final=False)
    drain(engine)                      # "early words" streamed eagerly
    engine.set_eager(False)
    engine.revise_segment("s1", "early words here and more")
    assert engine.tick() == 0          # soft again waits, as ever
    engine.revise_segment("s1", "early words here and more", final=True)
    drain(engine)
    assert sink.frames[-1] == frame_for(translator,
                                        "early words here and more ")


def test_eager_jump_still_skips_the_soft_tail():
    # take_pending's capture stays committed-text-only: hypothesis is not
    # snapped or summarized even when eager mode would have streamed it.
    engine, _translator, _sink = make_engine()
    engine.set_eager(True)
    engine.feed("committed words ", seg="s1", final=True)
    engine.feed("maybe more ", seg="s2", final=False)
    text, _cells = engine.take_pending()
    assert text == "committed words"
    # Eager will stream "maybe" (confirmed by "more"); the frontier word
    # waits — so the honest post-jump backlog is 1, not 2.
    assert engine.backlog_words() == 1


def test_eager_stale_frontier_still_ages_out():
    engine, translator, sink = make_engine()
    engine.set_eager(True)
    engine.feed("one two ", seg="s1", final=False)
    drain(engine)                      # "one" streamed; "two" held
    assert engine.drop_stale_soft(max_age_s=0.0) == ["s1"]
    assert engine.backlog_words() == 0
    assert sink.frames[-1] == frame_for(translator, "one ")


# ---- orphaned soft segments: age-out (drop_stale_soft) ----------------------

def test_fresh_soft_segments_are_never_dropped():
    engine, _translator, sink = make_engine()
    engine.feed("still coming ", seg="s1", final=False)
    assert engine.drop_stale_soft() == []
    assert engine.backlog_words() == 0  # soft: queued but not backlog
    assert sink.frames == []


def test_stale_soft_head_is_dropped_and_later_text_flows():
    # A soft segment whose final was lost must not wedge the queue forever:
    # once its stream has been quiet past the age-out, it is withdrawn and
    # the hardened text behind it renders.
    engine, translator, sink = make_engine()
    engine.feed("orphan words ", seg="s1", final=False)
    engine.feed("later text ", seg="s2", final=True)
    assert engine.tick() == 0                      # blocked on the soft head
    assert engine.drop_stale_soft(max_age_s=0.0) == ["s1"]
    drain(engine)
    assert sink.frames[-1] == frame_for(translator, "later text ")


def test_drop_stale_soft_never_touches_a_renderable_head():
    engine, translator, sink = make_engine()
    engine.feed("ready ", seg="s1", final=True)
    assert engine.drop_stale_soft(max_age_s=0.0) == []
    drain(engine)
    assert sink.frames[-1] == frame_for(translator, "ready ")


def test_drop_forgets_the_segment_so_a_late_final_lands_at_the_tail():
    # The drop forgets the segment's bookkeeping: a final that eventually
    # arrives must NOT splice at the segment's original stream slot (that
    # would land it mid-way through later, already-rendering text). It comes
    # back as an unknown segment and the pipeline's fallback feeds it fresh
    # at the queue tail, in reader order.
    engine, translator, sink = make_engine()
    engine.feed("orphan ", seg="s1", final=False)
    assert engine.drop_stale_soft(max_age_s=0.0) == ["s1"]
    engine.feed("later text ", seg="s2", final=True)
    assert engine.knows_segment("s1") is False
    assert engine.revise_segment("s1", "orphan recovered", final=True) is False
    engine.feed("orphan recovered ", seg="s1", final=True)  # the fallback path
    drain(engine)
    assert sink.frames[-1] == frame_for(
        translator, "later text orphan recovered ")


# ---- ws_source.interpret_message -------------------------------------------

def test_final_without_id_feeds_like_the_classic_protocol():
    seen = set()
    op = interpret_message({"type": "final", "text": "hello"}, seen)
    assert op == ("feed", None, "hello ", True)
    assert seen == set()


def test_soft_then_revise_then_hardening_final():
    seen = set()
    assert interpret_message(
        {"type": "soft", "id": "x-1", "text": "hello wor"}, seen
    ) == ("feed", "x-1", "hello wor ", False)
    assert seen == {"x-1"}
    assert interpret_message(
        {"type": "revise", "revise": "x-1", "text": "hello world you"}, seen
    ) == ("revise", "x-1", "hello world you", False)
    assert interpret_message(
        {"type": "final", "id": "x-1", "text": "hello world yours"}, seen
    ) == ("revise", "x-1", "hello world yours", True)
    assert seen == set()   # hardened: the id will never be cited again


def test_revise_for_an_unseen_segment_is_dropped():
    # Connected mid-stream: the segment's final will arrive whole instead.
    seen = set()
    assert interpret_message(
        {"type": "revise", "revise": "x-9", "text": "late"}, seen) is None


def test_malformed_messages_are_ignored():
    seen = set()
    assert interpret_message("not a dict", seen) is None
    assert interpret_message({"type": "final"}, seen) is None
    assert interpret_message({"type": "soft", "text": "no id"}, seen) is None
    assert interpret_message({"type": "other", "text": "x"}, seen) is None


# ---- run_pipeline dispatch --------------------------------------------------

async def _op_source(ops):
    for op in ops:
        yield op


def test_pipeline_applies_feed_and_revise_ops():
    engine, translator, sink = make_engine(width=8)
    interval = {"v": 0.0}
    stop = threading.Event()
    source = _op_source([
        ("feed", "s1", "helo ", False),
        ("revise", "s1", "hello", True),
    ])
    asyncio.run(run_pipeline(source, engine, interval, stop))
    assert sink.frames[-1] == frame_for(translator, "hello ", width=8)


def test_pipeline_feeds_a_final_whose_soft_delivery_was_discarded():
    # HUMAN mode discards speech ops after ws_source has marked the id seen,
    # so the hardening final arrives as a revise of a segment the engine
    # never fed. It must feed fresh — never vanish.
    engine, translator, sink = make_engine(width=16)
    interval = {"v": 0.0}
    stop = threading.Event()
    source = _op_source([
        ("revise", "s1", "typed over", True),
    ])
    asyncio.run(run_pipeline(source, engine, interval, stop))
    assert sink.frames[-1] == frame_for(translator, "typed over ", width=16)


def test_type_mode_still_applies_ops_for_already_queued_segments():
    # HUMAN mode discards NEW speech, but a segment queued BEFORE typing began
    # must still receive its revisions and hardening final — dropping the
    # final would strand its soft text forever (ws_source has already retired
    # the id, so it is never re-delivered).
    engine, translator, sink = make_engine(width=16)
    interval = {"v": 0.0}
    stop = threading.Event()
    controls = PacerControls(engine, interval)
    controls.advance_mode = "ticker"   # paced: the queue drains on its own

    async def scenario():
        async def source():
            yield ("feed", "s1", "queued soft ", False)   # while listening
            controls.mode = MODE_TYPE                     # reader tabs in
            yield ("revise", "s1", "queued text", True)   # hardening final
            yield ("feed", "s2", "spoken over typing ", True)  # new speech
            controls.mode = MODE_LISTEN
        await asyncio.wait_for(asyncio.ensure_future(run_pipeline(
            source(), engine, interval, stop, controls)), 5)

    asyncio.run(scenario())
    # s1's final applied; s2 (spoken entirely during typing) stayed discarded.
    assert sink.frames[-1] == frame_for(translator, "queued text ", width=16)


def test_ws_source_latency_message_yields_the_op():
    # The server decides rendering and says so in the `render` field; the
    # source relays it verbatim.
    seen = set()
    assert interpret_message(
        {"type": "latency", "mode": "balanced", "render": "eager"},
        seen) == ("latency", "eager")
    assert interpret_message(
        {"type": "latency", "mode": "accurate", "render": "confirmed"},
        seen) == ("latency", "confirmed")
    # No usable render field: ignored.
    assert interpret_message({"type": "latency", "mode": "fastest"},
                             seen) is None
    assert interpret_message(
        {"type": "latency", "render": "warp"}, seen) is None
    assert seen == set()


def test_pipeline_latency_op_flips_eager_and_finals_ack_shown_text():
    engine, translator, sink = make_engine(width=16)
    interval = {"v": 0.0}
    stop = threading.Event()
    acks = []
    source = _op_source([
        ("latency", "eager"),
        ("feed", "s1", "hello world ", True),
    ])
    asyncio.run(run_pipeline(source, engine, interval, stop, None,
                             lambda seg, text: acks.append((seg, text))))
    assert engine.eager is True
    # The hardening feed acked the segment's effective text — what the
    # display actually got (here identical to the provider's words; the
    # divergence case is pinned by the segment_text engine tests).
    assert acks == [("s1", "hello world")]
    assert sink.frames[-1] == frame_for(translator, "hello world ", width=16)


def test_pipeline_drops_an_empty_revise_of_an_unknown_segment():
    # A withdraw for a segment nobody fed has nothing to recover.
    engine, _translator, sink = make_engine(width=8)
    interval = {"v": 0.0}
    stop = threading.Event()
    source = _op_source([
        ("revise", "s1", "", False),
    ])
    asyncio.run(run_pipeline(source, engine, interval, stop))
    assert sink.frames == []
