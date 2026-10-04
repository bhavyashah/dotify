"""SRT/WebVTT caption parsing for the timed caption replay (see demo.py).

Caption-file content in (a string, never a path, so a shell can hand over a
track however it got it), a list of ``Cue(start, end, text)`` out, stdlib
only.

One block parser handles both formats, since a cue has the same shape in
each: an optional identifier line, a ``start --> end`` timing line, then
text lines until a blank line. Differences are tolerated rather than
switched on:

- timestamps accept ``HH:MM:SS,mmm`` (SRT) and ``HH:MM:SS.mmm`` (VTT),
  hours optional;
- the ``WEBVTT`` header and ``NOTE``/``STYLE``/``REGION`` blocks are
  skipped;
- cue settings after the end timestamp are ignored;
- inline markup is stripped: ``<i>``/``<b>``/``<c.class>`` tags, karaoke
  timestamps, voice spans (``<v Speaker>`` drops the speaker name too) and
  SRT ``{\\an8}``-style override braces; HTML entities are unescaped.

Multi-line cue text joins with spaces (the braille stream is linear).
Malformed cues are skipped with a status-line warning, so a damaged track
plays its readable cues; cues empty after stripping are dropped silently.
The result is sorted by start time.
"""

import html
import re
from collections import namedtuple

# One replayable cue: start/end in seconds (floats, relative to track
# start), text as a single cleaned line.
Cue = namedtuple("Cue", ("start", "end", "text"))

# HH optional (VTT short form); comma or dot before the milliseconds.
_TIMESTAMP = re.compile(
    r"^(?:(\d{1,4}):)?([0-5]?\d):([0-5]?\d)[.,](\d{1,3})$")
# The cue timing line: two timestamps around an arrow, anything after the
# end timestamp is VTT cue settings (ignored). Spaces around the arrow are
# optional — sloppy converters write "00:00:01,000-->00:00:03,500", and the
# timestamps validate afterwards anyway.
_ARROW = re.compile(r"^\s*(\S+?)\s*-{1,3}>\s*(\S+)(?:\s+.*)?$")
# Inline markup: <i>, </i>, <c.yellow>, <v Speaker Name>, <00:00:01.000>.
# Only tag-shaped runs (a letter-initiated tag or a karaoke timestamp) are
# stripped — a bare "<[^>]*>" would eat real words between an unescaped
# "less than" and the next "greater than" ("5 < 6 today > expected").
_TAG = re.compile(r"</?[A-Za-z][^<>]*>|<[0-9][0-9:.,]*>")
# SRT-style ASS/SSA override braces: {\an8} and friends.
_BRACE = re.compile(r"\{\\[^}]*\}")
# VTT block kinds that carry no cue and are skipped without a warning.
_VTT_BLOCK_KINDS = ("WEBVTT", "NOTE", "STYLE", "REGION")


def _parse_timestamp(token):
    """Seconds for one timestamp token, or None when it is not one."""
    match = _TIMESTAMP.match(token)
    if not match:
        return None
    hours, minutes, seconds, millis = match.groups()
    return (int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds)
            + int(millis.ljust(3, "0")) / 1000.0)


def _clean_text(lines):
    """Cue text lines -> one cleaned line: markup stripped, entities
    unescaped, whitespace collapsed."""
    text = " ".join(lines)
    text = _TAG.sub("", text)
    text = _BRACE.sub("", text)
    text = html.unescape(text)
    return " ".join(text.split())


def parse_captions(content, status=None):
    """Parse SRT or WebVTT caption CONTENT into a list of ``Cue``s.

    ``status`` is the [keys]-style stderr line writer (optional); each
    skipped malformed cue earns one warning there. Returns the cues
    sorted by start time — possibly empty, never raises on bad input.
    """
    status = status or (lambda msg: None)
    # Normalize: BOM off, one newline convention.
    content = content.lstrip("﻿").replace("\r\n", "\n").replace(
        "\r", "\n")
    cues = []
    for block in re.split(r"\n\s*\n", content):
        lines = [line.strip() for line in block.split("\n")]
        lines = [line for line in lines if line]
        if not lines:
            continue
        arrow_at = arrow = None
        for i, line in enumerate(lines):
            arrow = _ARROW.match(line)
            if arrow:
                arrow_at = i
                break
        if arrow_at is None:
            # No timing line: the WEBVTT header, a NOTE/STYLE/REGION
            # block, or a stray bare cue number are structure, not
            # damage. Anything else is a cue that lost its timing.
            first = lines[0]
            if first.split(None, 1)[0].rstrip(":") in _VTT_BLOCK_KINDS \
                    or (len(lines) == 1 and first.isdigit()):
                continue
            status("captions: skipped a block with no timing line "
                   f"({first[:40]!r})")
            continue
        start_token, end_token = arrow.groups()
        start = _parse_timestamp(start_token)
        end = _parse_timestamp(end_token)
        if start is None or end is None or end < start:
            status("captions: skipped a cue with a malformed timing line "
                   f"({lines[arrow_at][:60]!r})")
            continue
        text = _clean_text(lines[arrow_at + 1:])
        if text:
            cues.append(Cue(start, end, text))
    cues.sort(key=lambda cue: (cue.start, cue.end))
    return cues
