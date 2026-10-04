import asyncio

from braille_engine.sources.file_source import file_source


async def _collect(path):
    return [c async for c in file_source(path, char_delay=0)]


def test_file_source_yields_chars(tmp_path):
    p = tmp_path / "sample.txt"
    p.write_text("hi", encoding="utf-8")
    assert asyncio.run(_collect(str(p))) == ["h", "i"]
