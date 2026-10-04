"""Word assembler.

Buffers incoming finalized characters and emits a completed *token* only when a
space arrives. A token may carry trailing punctuation (``end.``, ``hello,``) so
the translator gets the context UEB needs. The trailing incomplete word stays
buffered until its delimiting space, which is what guarantees a token is
translated exactly once and never revised.

Events are tuples:
    ("word", text)   a completed token, ready to translate
    ("space",)       one inter-word blank cell
"""


class WordAssembler:
    def __init__(self):
        self._buf = ""

    def feed(self, text: str) -> list:
        """Consume a chunk of text, returning the events it completes."""
        events = []
        for ch in text:
            if ch == " ":
                if self._buf:
                    events.append(("word", self._buf))
                    self._buf = ""
                events.append(("space",))
            else:
                self._buf += ch
        return events

    def flush(self) -> list:
        """Emit any buffered incomplete word (e.g. end of a file with no
        trailing space). After this the buffer is empty."""
        if self._buf:
            word = self._buf
            self._buf = ""
            return [("word", word)]
        return []
