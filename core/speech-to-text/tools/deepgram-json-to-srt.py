#!/usr/bin/env python3
"""Convert a Deepgram prerecorded JSON response into short SRT cues.

The demo caption feeder works best with the same small bursts live captions
arrive in. Deepgram's word timings are therefore grouped at sentence
punctuation, noticeable speech gaps, or a conservative word limit instead of
turning an entire utterance into one long cue.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def srt_time(seconds: float) -> str:
    millis = max(0, round(seconds * 1000))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1_000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def cues(words: list[dict], max_words: int, gap_seconds: float):
    group: list[dict] = []
    for word in words:
        if group and float(word["start"]) - float(group[-1]["end"]) >= gap_seconds:
            yield group
            group = []
        group.append(word)
        token = str(word.get("punctuated_word") or word["word"])
        if len(group) >= max_words or token.endswith((".", "?", "!")):
            yield group
            group = []
    if group:
        yield group


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--max-words", type=int, default=10)
    parser.add_argument("--gap-seconds", type=float, default=0.55)
    args = parser.parse_args()

    payload = json.loads(args.input.read_text(encoding="utf-8"))
    words = payload["results"]["channels"][0]["alternatives"][0]["words"]
    blocks = []
    for index, group in enumerate(cues(words, args.max_words, args.gap_seconds), 1):
        start = float(group[0]["start"])
        end = max(start + 0.25, float(group[-1]["end"]))
        text = " ".join(str(word.get("punctuated_word") or word["word"])
                        for word in group)
        blocks.append(
            f"{index}\n{srt_time(start)} --> {srt_time(end)}\n{text}\n"
        )
    args.output.write_text("\n".join(blocks), encoding="utf-8")


if __name__ == "__main__":
    main()
