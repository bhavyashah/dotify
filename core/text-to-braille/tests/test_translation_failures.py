"""A word the translator cannot handle is content, not a display outage:
it is skipped, and nothing escapes into the pacer's reconnect path."""

import asyncio
import threading
from pathlib import Path

import pytest

from braille_engine.engine import BrailleEngine
from braille_engine.sinks.simulated import SimulatedSink
from braille_engine.translator import (
    DevUebTranslator,
    LiblouisDllTranslator,
    TranslationError,
)
from run import run_pipeline


class StrictEncodingTranslator(DevUebTranslator):
    """Encodes like the liblouis DLL does, so a lone surrogate raises
    UnicodeEncodeError exactly where the real translator would."""

    def translate_units_tagged(self, token):
        token.encode("utf-16-le")
        return super().translate_units_tagged(token)


class FailingTranslator(DevUebTranslator):
    """liblouis refusing one particular word (a damaged table entry)."""

    def translate_units_tagged(self, token):
        if token == "bad":
            raise TranslationError("table failure")
        return super().translate_units_tagged(token)


def _engine(translator):
    sink = SimulatedSink(width=12, echo=False)
    engine = BrailleEngine(translator, sink, window=1)
    engine.start()
    return engine, sink


def _drain(engine):
    while engine.tick():
        pass


def test_translation_error_is_not_a_display_failure_type():
    assert not issubclass(TranslationError, (OSError, RuntimeError))


def test_lone_surrogate_does_not_raise_and_later_words_still_show():
    engine, sink = _engine(StrictEncodingTranslator())
    engine.feed("\ud83d ab ")
    _drain(engine)
    assert engine.shown_source().split()[-1] == "ab"
    assert not engine.has_pending()


def test_surrogate_pair_split_across_keys_is_rejoined():
    engine, _ = _engine(StrictEncodingTranslator())
    # msvcrt delivers an astral character as two keys; the assembler joins
    # them into one word, which must translate as the real character.
    assert engine._display_form("😀") == "\U0001f600"


def test_untranslatable_word_is_skipped_not_raised():
    engine, sink = _engine(FailingTranslator())
    engine.feed("bad ok ")
    _drain(engine)
    assert "ok" in engine.shown_source()
    assert not engine.has_pending()


def test_take_pending_and_flash_survive_untranslatable_words():
    engine, _ = _engine(FailingTranslator())
    engine.feed("bad ok ")
    text, cells = engine.take_pending()
    assert text == "bad ok"
    assert cells          # "ok" and its spaces still translate
    engine.flash("bad")   # fit_cells goes through the same guard


def test_pipeline_does_not_reconnect_over_a_translation_failure():
    engine, sink = _engine(FailingTranslator())
    connects = []
    original = sink.connect
    sink.connect = lambda: connects.append(1) or original()

    async def source():
        yield "bad ok "

    asyncio.run(run_pipeline(source(), engine, {"v": 0.0},
                             threading.Event()))
    assert connects == []
    assert "ok" in engine.shown_source()


def test_dll_failure_raises_translation_error():
    class RefusingLib:
        def lou_charSize(self):
            return 2

        def lou_translateString(self, *_args):
            return 0

        def lou_translate(self, *_args):
            return 0

    translator = LiblouisDllTranslator(lib=RefusingLib(),
                                       tables_dir=Path("T"))
    with pytest.raises(TranslationError):
        translator.translate("word")
    with pytest.raises(TranslationError):
        translator.translate_units_tagged("word")
