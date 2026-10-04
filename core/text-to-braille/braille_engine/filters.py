"""Pre-translation text filters (reading density).

``digits`` rewrites spoken number words as numerals before translation:
"twenty five people" -> "25 people". On a braille display a numeral is
almost always cheaper than the words that name it ("twenty five" is ~10
grade-2 cells; "25" is the numeric indicator plus two digits). The filter
changes FORM only, never content — every quantity the speaker said is
still there, so the display stays a faithful raw transcript.

Deliberate limits:
  * Scale words from a million up stay words: "5 million" costs fewer
    cells than "5000000", so the count before the scale word converts
    and the scale word is kept.
  * A standalone "one" is left alone. It is usually a pronoun ("one of
    them", "no one"), and misreading those as "1" is worse than the one
    cell it would save. Multi-word groups ("one hundred") still convert.
  * Adjacent groups that read as a year fuse: "twenty twenty six" ->
    2026, "nineteen eighty four" -> 1984. Spoken years split into two
    groups because "eighty" cannot grammatically continue "nineteen".
  * "and" joins only when digits follow ("one hundred and five" -> 105);
    anything else unrecognized ("point", "oh", ordinals) ends the group,
    and the words around it pass through untouched.
"""

_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19,
}
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
# Scales worth converting through: the numeral is shorter than the words.
_SCALES = {"hundred": 100, "thousand": 1000}
# Scales kept as words: "5 million" beats "5000000" on the display.
_BIG = ("million", "billion", "trillion")

_NUMBER_WORDS = set(_UNITS) | set(_TENS) | set(_SCALES) | set(_BIG)

# Sentence punctuation still attached to a spoken-number token
# ("twenty five," / "(six").
_TRAIL = ".,;:!?\"')"
_LEAD = "\"'("


def _split_token(token):
    start, end = 0, len(token)
    while start < end and token[start] in _LEAD:
        start += 1
    while end > start and token[end - 1] in _TRAIL:
        end -= 1
    return token[:start], token[start:end], token[end:]


class _Group:
    """One spoken number, accumulated word by word."""

    def __init__(self, lead=""):
        self.lead = lead      # punctuation before the first word
        self.trail = ""       # punctuation after the last word
        self.total = 0        # completed thousand-chunks
        self.current = 0      # the chunk being built
        self.words = []       # source words (lowercased)
        self.raw = []         # source words as spoken (case kept)
        self.parts = []       # already-rendered output ("25 million ...")

    def value(self):
        return self.total + self.current

    def plain(self):
        """True for a bare units/tens group — the only shape years fuse."""
        return not self.parts and all(
            w in _UNITS or w in _TENS for w in self.words)

    def continues(self, word):
        """May ``word`` extend this group? Grammar, not just membership:
        "twenty five" continues, a second "five" starts a new group."""
        if not self.words:
            return word in _UNITS or word in _TENS
        last = self.words[-1]
        if word in _BIG:
            return last not in _BIG and last != "and"
        if word == "hundred":
            # "three hundred", never "thousand hundred" / "hundred hundred".
            return last in _UNITS or last in _TENS
        if word == "thousand":
            # "three hundred thousand" chains; "thousand thousand" doesn't.
            return last in _UNITS or last in _TENS or last == "hundred"
        after_scale = last in _SCALES or last in _BIG or last == "and"
        if word in _TENS:
            return after_scale
        if word in _UNITS:
            if after_scale:
                return True
            # "twenty five": a 1..9 unit may follow a bare tens word.
            return last in _TENS and 1 <= _UNITS[word] <= 9
        return False

    def take(self, word):
        self.words.append(word)
        if word in _UNITS:
            self.current += _UNITS[word]
        elif word in _TENS:
            self.current += _TENS[word]
        elif word == "hundred":
            self.current = (self.current or 1) * 100
        elif word == "thousand":
            self.total += (self.current or 1) * 1000
            self.current = 0
        elif word in _BIG:
            # Flush the count as digits, keep the scale word:
            # "twenty five million" -> "25 million".
            self.parts.append(str(self.value() or 1))
            self.parts.append(word)
            self.total = self.current = 0

    def render(self):
        if self.words == ["one"]:       # pronoun guard, case kept
            body = self.raw[0] if self.raw else "one"
        else:
            parts = list(self.parts)
            if self.value() or not parts:
                parts.append(str(self.value()))
            body = " ".join(parts)
        return self.lead + body + self.trail


def _fuse_years(items):
    """Fuse two ADJACENT plain groups spoken as a year: "nineteen" +
    "eighty four" -> 1984, "twenty twenty" -> 2020."""
    out = []
    for item in items:
        prev = out[-1] if out else None
        if (isinstance(item, _Group) and isinstance(prev, _Group)
                and prev.plain() and item.plain()
                and not prev.trail and not item.lead
                # The second half must be two digits itself: "twenty six"
                # continues "twenty" into 2026, but "ten four" is CB slang,
                # not the year 1004.
                and 10 <= prev.value() <= 99 and 10 <= item.value() <= 99
                and 1000 <= prev.value() * 100 + item.value() <= 2099):
            fused = _Group(prev.lead)
            fused.words = prev.words + item.words
            fused.current = prev.value() * 100 + item.value()
            fused.trail = item.trail
            out[-1] = fused
            continue
        out.append(item)
    return out


def digits(text: str) -> str:
    """Rewrite spoken cardinal numbers in ``text`` as numerals."""
    tokens = text.split(" ")
    items = []            # str tokens and _Group objects, in order
    group = None

    def close():
        nonlocal group
        if group is not None:
            items.append(group)
            group = None

    index = 0
    while index < len(tokens):
        token = tokens[index]
        lead, core, trail = _split_token(token)
        # Hyphenated compounds arrive as one token: "twenty-five".
        subwords = [w for w in core.lower().split("-") if w]
        is_number = bool(subwords) and all(w in _NUMBER_WORDS
                                           for w in subwords)
        if is_number:
            if group is None or lead or not group.continues(subwords[0]):
                close()
                group = _Group(lead)
                if not group.continues(subwords[0]):
                    # A group can't OPEN with a scale word ("hundred
                    # people" alone stays words).
                    group = None
                    items.append(token)
                    index += 1
                    continue
            for w in subwords:
                if group.continues(w):
                    group.take(w)
                else:                    # "twenty-twenty" style compound
                    close()
                    group = _Group()
                    group.take(w)
            group.raw.append(core)
            if trail:
                group.trail = trail
                close()
            index += 1
            continue
        if (group is not None and core.lower() == "and" and not lead
                and not trail and index + 1 < len(tokens)
                and (group.words[-1] in _SCALES or group.words[-1] in _BIG)):
            # "and" glue: only between a scale word and a following
            # units/tens word, so "one hundred and five" joins to 105 but
            # "one and two" stays two numbers (it is a list, not a sum).
            # A following word with leading punctuation opens its own group,
            # so the "and" must stay a word or it would vanish.
            nxt_lead, nxt, _ = _split_token(tokens[index + 1])
            nxt_first = nxt.lower().split("-")[0]
            if not nxt_lead and (nxt_first in _UNITS or nxt_first in _TENS):
                group.words.append("and")
                index += 1
                continue
        close()
        items.append(token)
        index += 1
    close()

    return " ".join(
        item.render() if isinstance(item, _Group) else item
        for item in _fuse_years(items))
