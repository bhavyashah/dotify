"""Braille cell primitives.

A cell is an int bitmask over the eight braille dots:

    dot1=1  dot2=2  dot3=4  dot4=8  dot5=16  dot6=32  dot7=64  dot8=128

This is exactly the low byte of the Unicode braille block, so a cell's glyph
is ``chr(0x2800 + pattern)``. A blank cell is 0.
"""

BLANK = 0

BRAILLE_BASE = 0x2800


def dots_to_pattern(dots: str) -> int:
    """Convert a dot string like ``"145"`` into a cell bitmask."""
    pattern = 0
    for d in dots:
        pattern |= 1 << (int(d) - 1)
    return pattern


def to_unicode(pattern: int) -> str:
    """Return the Unicode braille glyph for a cell pattern."""
    return chr(BRAILLE_BASE + pattern)


def frame_to_str(cells) -> str:
    """Render a list of cell patterns as a Unicode braille string."""
    return "".join(to_unicode(c) for c in cells)
