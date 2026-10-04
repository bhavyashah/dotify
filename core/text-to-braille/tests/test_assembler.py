from braille_engine.assembler import WordAssembler


def test_word_then_space():
    a = WordAssembler()
    assert a.feed("hi ") == [("word", "hi"), ("space",)]


def test_incomplete_word_buffered_across_chunks():
    a = WordAssembler()
    assert a.feed("wo") == []
    assert a.feed("rld ") == [("word", "world"), ("space",)]


def test_multiple_spaces_emit_blanks():
    a = WordAssembler()
    assert a.feed("a  b") == [("word", "a"), ("space",), ("space",)]
    assert a.flush() == [("word", "b")]


def test_trailing_punctuation_kept_with_word():
    a = WordAssembler()
    assert a.feed("end. ") == [("word", "end."), ("space",)]


def test_flush_empty():
    a = WordAssembler()
    assert a.flush() == []
