"""Backlog gauge: one cell that fills like a thermometer.

Shows the distance between the text under the reader's fingers and the live
edge, in words, as an 8-dot pattern that fills upward, readable with one
brush of a finger. The distance is whatever the engine feeds it: the queued
backlog, plus the pan depth while the view sits back in history.

The level changes only at bucket boundaries. Rising edges are exact; falling
edges have a small hysteresis band so the cell doesn't flutter when the
backlog hovers at a boundary. A deliberate catch-up calls :meth:`reset`
instead of riding the hysteresis down: the drained cell is the reader's
confirmation that the command worked.
"""

from .cells import BLANK, dots_to_pattern

# (words at which the level starts, cell pattern), lowest level first.
LEVELS = [
    (6, dots_to_pattern("78")),        # ~a sentence behind
    (16, dots_to_pattern("3678")),     # ~a paragraph
    (41, dots_to_pattern("235678")),   # far behind
    (101, dots_to_pattern("12345678")),  # very far
]

# A level is only left downward once the count falls this far below its
# rising boundary (boundary 6 -> holds until the count drops under 4).
HYSTERESIS = 2


class BacklogGauge:
    def __init__(self):
        self._level = 0  # 0 = caught up (blank), 1..len(LEVELS) per LEVELS

    def update(self, words_behind: int, floor: int = 0) -> int:
        """Absorb the current distance and return the cell pattern to show.

        ``floor`` is the minimum level the caller's view demands: the engine
        passes 1 while the view is off the live edge, so a blank gauge
        always means live and caught up."""
        self._level = self.peek(words_behind, floor)
        return self.pattern

    @property
    def pattern(self) -> int:
        return BLANK if self._level == 0 else LEVELS[self._level - 1][1]

    @property
    def level(self) -> int:
        """The fill level (0 = caught up, 1..4), for surfaces that cannot
        render a braille cell."""
        return self._level

    def reset(self) -> None:
        """Snap to caught-up (level 0) now, skipping the hysteresis, after
        a deliberate catch-up. The next update refills from the real
        count."""
        self._level = 0

    def peek(self, words_behind: int, floor: int = 0) -> int:
        """The level the gauge would hold at this count, without absorbing
        it, for pollers that must not step the hysteresis the engine renders
        from. ``floor`` as in :meth:`update` (never absorbed)."""
        level = self._level
        while level < len(LEVELS) and words_behind >= LEVELS[level][0]:
            level += 1
        while level > 0 and words_behind < LEVELS[level - 1][0] - HYSTERESIS:
            level -= 1
        return max(level, min(int(floor), len(LEVELS)))
