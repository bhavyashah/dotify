"""File / stdin source: replays text at typing speed.

Yields one character at a time with a small delay so the whole pipeline can be
exercised without speech or hardware. ``path="-"`` reads stdin.
"""

import asyncio
import sys


async def file_source(path: str, char_delay: float = 0.05):
    if path == "-":
        text = sys.stdin.read()
    else:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    for ch in text:
        yield ch
        if char_delay:
            await asyncio.sleep(char_delay)
