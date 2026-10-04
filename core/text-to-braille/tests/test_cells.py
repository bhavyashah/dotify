from braille_engine.cells import BLANK, dots_to_pattern, to_unicode, frame_to_str


def test_dots_to_pattern():
    assert dots_to_pattern("145") == 1 + 8 + 16 == 25
    assert dots_to_pattern("1") == 1
    assert dots_to_pattern("") == 0


def test_to_unicode():
    assert to_unicode(0) == chr(0x2800)
    assert to_unicode(1) == "⠁"  # dot 1


def test_frame_to_str():
    assert frame_to_str([0, 1]) == chr(0x2800) + chr(0x2801)


def test_blank():
    assert BLANK == 0
