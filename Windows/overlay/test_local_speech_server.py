"""local_speech_server: the model-ready contract and the decode protocol.

The recognizer is faked — these tests pin the PROTOCOL (partial dedupe,
endpoint finals, the empty-final withdrawal, finish flush), not sherpa-onnx.
The real model decode is exercised by the staged verify smoke.
"""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from local_speech_server import (
    AudioBacklog,
    DecodeSession,
    SAMPLE_RATE,
    files_from_manifest,
    model_ready,
    pcm16_to_floats,
)


class FakeStream:
    def __init__(self, fail_set_option=False):
        self.fed = []
        self.fail_set_option = fail_set_option
        self.options = {}

    def set_option(self, key, value):
        if self.fail_set_option:
            raise AttributeError("no options in this binding")
        self.options[key] = value

    def accept_waveform(self, sample_rate, samples):
        assert sample_rate == SAMPLE_RATE
        self.fed.append(list(samples))


class FakeRecognizer:
    """Result/endpoint state is set by each test between feeds."""

    def __init__(self, plain_str_results=False, fail_set_option=False):
        self.result_text = ""
        self.endpoint = False
        self.resets = 0
        self.plain_str_results = plain_str_results
        self.fail_set_option = fail_set_option

    def create_stream(self):
        return FakeStream(self.fail_set_option)

    def is_ready(self, stream):
        return False

    def decode_stream(self, stream):
        raise AssertionError("is_ready is always False here")

    def get_result(self, stream):
        if self.plain_str_results:
            return self.result_text
        return type("R", (), {"text": self.result_text})()

    def is_endpoint(self, stream):
        return self.endpoint

    def reset(self, stream):
        self.resets += 1
        self.result_text = ""
        self.endpoint = False


CHUNK = b"\x00\x00" * 4


class DecodeProtocolTest(unittest.TestCase):
    def setUp(self):
        self.rec = FakeRecognizer()
        self.session = DecodeSession(self.rec)

    def test_partials_stream_and_dedupe(self):
        self.rec.result_text = "hello"
        self.assertEqual([{"type": "partial", "text": "hello"}],
                         self.session.feed(CHUNK))
        # Same hypothesis again: silence on the wire.
        self.assertEqual([], self.session.feed(CHUNK))
        self.rec.result_text = "hello there"
        self.assertEqual([{"type": "partial", "text": "hello there"}],
                         self.session.feed(CHUNK))

    def test_endpoint_with_text_emits_final_and_resets(self):
        self.rec.result_text = "hello there"
        self.session.feed(CHUNK)
        self.rec.endpoint = True
        self.assertEqual([{"type": "final", "text": "hello there"}],
                         self.session.feed(CHUNK))
        self.assertEqual(1, self.rec.resets)
        # The dedupe state died with the utterance: the same words spoken
        # again must stream again.
        self.rec.result_text = "hello there"
        self.assertEqual([{"type": "partial", "text": "hello there"}],
                         self.session.feed(CHUNK))

    def test_endpoint_settling_to_nothing_withdraws_streamed_partials(self):
        self.rec.result_text = "hel"
        self.session.feed(CHUNK)
        self.rec.result_text = ""
        self.rec.endpoint = True
        # The overlay treats an empty final as "withdraw the soft text".
        self.assertEqual([{"type": "final", "text": ""}],
                         self.session.feed(CHUNK))

    def test_silence_endpoint_with_no_partials_is_quiet(self):
        self.rec.endpoint = True
        self.assertEqual([], self.session.feed(CHUNK))
        self.assertEqual(1, self.rec.resets)  # the stream still resets

    def test_finish_flushes_a_final_and_resets(self):
        self.rec.result_text = "closing words"
        self.assertEqual({"type": "final", "text": "closing words"},
                         self.session.finish())
        self.assertEqual(1, self.rec.resets)
        self.assertEqual("", self.session.last_partial)

    def test_plain_string_results_are_accepted(self):
        rec = FakeRecognizer(plain_str_results=True)
        session = DecodeSession(rec)
        rec.result_text = "  spaced  "
        self.assertEqual([{"type": "partial", "text": "spaced"}],
                         session.feed(CHUNK))

    def test_language_pin_is_best_effort(self):
        rec = FakeRecognizer(fail_set_option=True)
        DecodeSession(rec)  # must not raise
        pinned = DecodeSession(FakeRecognizer())
        self.assertEqual({"language": "en"}, pinned.stream.options)


class ModelContractTest(unittest.TestCase):
    FILES = [("encoder.int8.onnx", 100), ("tokens.txt", 10)]

    def test_ready_needs_every_file_at_its_exact_size(self):
        with TemporaryDirectory() as tmp:
            d = Path(tmp)
            self.assertFalse(model_ready(d, self.FILES))
            (d / "encoder.int8.onnx").write_bytes(b"x" * 100)
            self.assertFalse(model_ready(d, self.FILES))
            (d / "tokens.txt").write_bytes(b"y" * 10)
            self.assertTrue(model_ready(d, self.FILES))
            # A torn (or foreign-export) file un-readies the model.
            (d / "tokens.txt").write_bytes(b"y" * 11)
            self.assertFalse(model_ready(d, self.FILES))

    def test_missing_dir_is_simply_not_ready(self):
        self.assertFalse(model_ready(Path("does/not/exist"), self.FILES))

    def test_manifest_parse_matches_the_node_manifest_shape(self):
        with TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "offline-model.json"
            manifest.write_text(json.dumps({
                "dirName": "m",
                "baseUrl": "http://example.invalid/",
                "files": [{"name": "a.onnx", "bytes": 7}],
            }), encoding="utf-8")
            self.assertEqual([("a.onnx", 7)], files_from_manifest(manifest))

    def test_repo_manifest_lists_the_four_nemotron_files(self):
        manifest = (Path(__file__).resolve().parents[2]
                    / "core" / "speech-to-text" / "offline-model.json")
        files = dict(files_from_manifest(manifest))
        self.assertEqual(
            {"encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx",
             "tokens.txt"},
            set(files),
        )
        self.assertEqual(657_601_403, files["encoder.int8.onnx"])


class AudioBacklogTest(unittest.TestCase):
    """The bounded reader-to-decoder buffer: overload sheds OLDEST audio."""

    SECOND = SAMPLE_RATE * 2  # bytes of one second of PCM16 mono

    def test_fifo_below_the_cap(self):
        backlog = AudioBacklog(max_seconds=2.0)
        self.assertEqual(0.0, backlog.push(b"a" * 100))
        self.assertEqual(0.0, backlog.push(b"b" * 100))
        self.assertEqual(b"a" * 100, backlog.pop())
        self.assertEqual(b"b" * 100, backlog.pop())
        self.assertIsNone(backlog.pop())
        self.assertEqual(0.0, backlog.dropped_seconds)

    def test_overflow_drops_oldest_first_and_reports_seconds(self):
        backlog = AudioBacklog(max_seconds=1.0)
        backlog.push(b"a" * self.SECOND)
        shed = backlog.push(b"b" * (self.SECOND // 2))
        # The OLDEST chunk went, newest survived.
        self.assertEqual(1.0, shed)
        self.assertEqual(1.0, backlog.dropped_seconds)
        self.assertEqual(b"b" * (self.SECOND // 2), backlog.pop())
        self.assertIsNone(backlog.pop())

    def test_one_oversized_chunk_is_kept_not_looped_away(self):
        # The shed loop must leave at least the newest chunk even when that
        # single chunk exceeds the cap on its own.
        backlog = AudioBacklog(max_seconds=0.001)
        self.assertEqual(0.0, backlog.push(b"a" * self.SECOND))
        self.assertEqual(b"a" * self.SECOND, backlog.pop())

    def test_pop_keeps_byte_accounting_in_step(self):
        backlog = AudioBacklog(max_seconds=1.0)
        backlog.push(b"a" * self.SECOND)
        backlog.pop()
        # An emptied backlog has room again: nothing sheds.
        self.assertEqual(0.0, backlog.push(b"b" * self.SECOND))
        self.assertEqual(0.0, backlog.dropped_seconds)


class Pcm16Test(unittest.TestCase):
    def test_conversion_and_odd_trailing_byte(self):
        chunk = (16384).to_bytes(2, "little", signed=True) \
            + (-32768).to_bytes(2, "little", signed=True) + b"\x7f"
        self.assertEqual([0.5, -1.0], pcm16_to_floats(chunk))


if __name__ == "__main__":
    unittest.main()
