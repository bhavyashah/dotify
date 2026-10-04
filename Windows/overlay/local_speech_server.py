"""Loopback-only offline speech service for the Windows browser overlay.

Decodes with the downloaded Nemotron 3.5 streaming model through sherpa-onnx.
The model is an optional ~650 MB download managed by the speech server
(core/speech-to-text/offline-model.js) into the user's data folder; this
process only reads it.

Startup contract with the launcher: the port binds IMMEDIATELY, model or no
model — readiness is re-checked per connection, so a model downloaded
mid-session starts serving on the next Start without a relaunch. The model
loads lazily on the first ready connection (it is large; a reader who never
goes offline never pays the RAM) and stays loaded after that.

Wire protocol (the speech overlay is the client):
  in:  binary frames of PCM16 LE mono @ 16 kHz; {"command": "finish"} text
  out: {"type": "partial"|"final", "text": ...}   an empty final withdraws
       the current utterance's soft text (silence settled to nothing)
       {"type": "status", "state": "loading"|"ready"}   load progress
       {"type": "status", "state": "behind"}   decode fell behind realtime and
       is shedding oldest audio; a later "ready" means it caught back up
       {"type": "error", "message": ...}   model missing / load failure
"""

from __future__ import annotations

import argparse
import array
import asyncio
import collections
import contextlib
import json
import logging
import sys
import time
import traceback
from pathlib import Path


SAMPLE_RATE = 16_000
ALLOWED_ORIGINS = ["http://localhost:8788", "http://127.0.0.1:8788"]

# Endpointing: rule2 is the caption cadence (0.8 s silence after speech
# finalizes the segment), rule1 clears long non-speech, rule3 caps an
# unbroken monologue at 20 s.
ENDPOINT_RULE1_TRAILING_S = 2.0
ENDPOINT_RULE2_TRAILING_S = 0.8
ENDPOINT_RULE3_UTTERANCE_S = 20.0

# Decode threads. Keep this well below the core count: sustained all-core
# load has hard-reset the laptops this was measured on.
NUM_THREADS = 4


def files_from_manifest(manifest_path: Path) -> list[tuple[str, int]]:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    return [(f["name"], int(f["bytes"])) for f in payload["files"]]


def model_ready(model_dir: Path, files: list[tuple[str, int]]) -> bool:
    """All model files present at their exact byte sizes (the same torn-
    download guard as the Node downloader)."""
    for name, size in files:
        target = model_dir / name
        try:
            if target.stat().st_size != size:
                return False
        except OSError:
            return False
    return True


def pcm16_to_floats(chunk: bytes) -> list[float]:
    samples = array.array("h")
    samples.frombytes(chunk[: len(chunk) - (len(chunk) % 2)])
    return [s / 32768.0 for s in samples]


# Decode that falls behind the microphone sheds OLDEST audio beyond this cap.
# Native decode never fills it in practice, but a machine whose decode hovers
# near realtime (the x64-emulated fallback, a loaded CPU) would turn a long
# queue into a permanent caption lag. Words garble at a drop seam; captions
# staying near-live is the better trade.
MAX_BACKLOG_SECONDS = 10.0


class AudioBacklog:
    """Bounded audio queue between the socket reader and the decoder.

    The reader drains the connection into this buffer without ever
    stalling: a stalled reader keeps the websockets library from processing
    frames, which has killed sessions with 1011 "keepalive ping timeout".
    The decoder consumes at its own pace; push() drops the oldest audio past
    the cap and reports what it shed.
    """

    def __init__(self, max_seconds: float = MAX_BACKLOG_SECONDS):
        self.chunks: collections.deque[bytes] = collections.deque()
        self.max_bytes = int(max_seconds * SAMPLE_RATE) * 2
        self.buffered_bytes = 0
        self.dropped_bytes = 0

    @property
    def dropped_seconds(self) -> float:
        return self.dropped_bytes / 2 / SAMPLE_RATE

    def push(self, chunk: bytes) -> float:
        """Queue a chunk; returns the seconds of audio shed to stay bounded."""
        self.chunks.append(chunk)
        self.buffered_bytes += len(chunk)
        shed = 0
        while self.buffered_bytes > self.max_bytes and len(self.chunks) > 1:
            oldest = self.chunks.popleft()
            self.buffered_bytes -= len(oldest)
            shed += len(oldest)
        self.dropped_bytes += shed
        return shed / 2 / SAMPLE_RATE

    def pop(self) -> bytes | None:
        if not self.chunks:
            return None
        chunk = self.chunks.popleft()
        self.buffered_bytes -= len(chunk)
        return chunk


def load_recognizer(model_dir: Path):
    """Build the sherpa-onnx streaming recognizer. Deferred import: the
    module stays importable (and testable) without the wheel."""
    import sherpa_onnx

    return sherpa_onnx.OnlineRecognizer.from_transducer(
        tokens=str(model_dir / "tokens.txt"),
        encoder=str(model_dir / "encoder.int8.onnx"),
        decoder=str(model_dir / "decoder.int8.onnx"),
        joiner=str(model_dir / "joiner.int8.onnx"),
        num_threads=NUM_THREADS,
        sample_rate=SAMPLE_RATE,
        feature_dim=80,
        enable_endpoint_detection=True,
        rule1_min_trailing_silence=ENDPOINT_RULE1_TRAILING_S,
        rule2_min_trailing_silence=ENDPOINT_RULE2_TRAILING_S,
        rule3_min_utterance_length=ENDPOINT_RULE3_UTTERANCE_S,
        decoding_method="greedy_search",
    )


class DecodeSession:
    """One connection's decode state over a shared recognizer.

    Pure protocol logic (no sockets), so tests drive it with a fake
    recognizer. Mirrors NemotronSession's emission rules: partials dedupe,
    an endpoint with text emits a final, an endpoint that settled to nothing
    AFTER partials streamed withdraws them with an empty final.
    """

    def __init__(self, recognizer):
        self.recognizer = recognizer
        self.stream = recognizer.create_stream()
        # Nemotron 3.5 is prompt-conditioned multilingual; pin English like
        # every other engine. Best-effort — older bindings lack the option.
        try:
            self.stream.set_option("language", "en")
        except Exception:
            pass
        self.last_partial = ""

    def _text(self) -> str:
        result = self.recognizer.get_result(self.stream)
        text = result.text if hasattr(result, "text") else result
        return str(text).strip()

    def feed(self, chunk: bytes) -> list[dict[str, str]]:
        self.stream.accept_waveform(SAMPLE_RATE, pcm16_to_floats(chunk))
        while self.recognizer.is_ready(self.stream):
            self.recognizer.decode_stream(self.stream)
        text = self._text()
        out: list[dict[str, str]] = []
        if self.recognizer.is_endpoint(self.stream):
            if text or self.last_partial:
                out.append({"type": "final", "text": text})
            self.last_partial = ""
            self.recognizer.reset(self.stream)
        elif text and text != self.last_partial:
            self.last_partial = text
            out.append({"type": "partial", "text": text})
        return out

    def finish(self) -> dict[str, str]:
        while self.recognizer.is_ready(self.stream):
            self.recognizer.decode_stream(self.stream)
        text = self._text()
        self.last_partial = ""
        self.recognizer.reset(self.stream)
        return {"type": "final", "text": text}


async def serve(model_dir: Path, files: list[tuple[str, int]], port: int) -> None:
    import websockets

    # The PowerShell launcher uses a short TCP readiness probe before the
    # browser connects. websockets otherwise logs that expected non-HTTP probe
    # as a full traceback even though startup succeeded.
    logging.getLogger("websockets.server").setLevel(logging.CRITICAL)

    recognizer = None
    load_lock = asyncio.Lock()
    # sherpa's recognizer/stream objects are not safe to drive concurrently;
    # decode runs off-loop below, so concurrent connections must take turns.
    decode_lock = asyncio.Lock()

    async def ensure_recognizer():
        nonlocal recognizer
        async with load_lock:
            if recognizer is None:
                # Off-thread: a ~650 MB load must not freeze the accept loop.
                recognizer = await asyncio.to_thread(load_recognizer, model_dir)
        return recognizer

    def log(message: str) -> None:
        # stderr reaches the launcher's speech-error.log; the timestamp is
        # what makes a died-mid-session report reconstructable afterwards.
        print(f"[{time.strftime('%H:%M:%S')}] {message}", file=sys.stderr, flush=True)

    async def transcribe(websocket):
        if not model_ready(model_dir, files):
            await websocket.send(json.dumps({
                "type": "error",
                "message": "The offline model is not downloaded.",
            }))
            await websocket.close()
            return
        try:
            if recognizer is None:
                await websocket.send(json.dumps({"type": "status", "state": "loading"}))
            shared = await ensure_recognizer()
            await websocket.send(json.dumps({"type": "status", "state": "ready"}))
            session = DecodeSession(shared)

            # Reader and decoder are separate tasks around a bounded backlog
            # (see AudioBacklog), so slow decode never stalls the socket.
            backlog = AudioBacklog()
            arrived = asyncio.Event()
            finish_requested = False   # the client's flush command
            closed = False             # the socket ended, orderly or not

            def log_close(exc):
                close = exc.rcvd or exc.sent
                if close is None:
                    log("offline session ended without a close frame")
                elif close.code not in (1000, 1001, 1005):
                    log(f"offline session closed abnormally: "
                        f"code={close.code} reason={close.reason!r}")

            async def reader():
                nonlocal finish_requested, closed
                try:
                    async for message in websocket:
                        if finish_requested:
                            continue  # drain-only: the close is moments away
                        if isinstance(message, bytes):
                            backlog.push(message)
                        else:
                            try:
                                command = json.loads(message).get("command")
                            except (json.JSONDecodeError, AttributeError):
                                command = None
                            if command == "finish":
                                finish_requested = True
                        arrived.set()
                except websockets.ConnectionClosed as exc:
                    log_close(exc)
                finally:
                    closed = True
                    arrived.set()

            reader_task = asyncio.create_task(reader())
            behind = False
            episode_start_dropped = 0.0
            try:
                while True:
                    arrived.clear()
                    chunk = backlog.pop()
                    if chunk is None:
                        if behind:
                            behind = False
                            log("offline decode caught back up "
                                f"(dropped {backlog.dropped_seconds - episode_start_dropped:.1f} s "
                                f"this episode, {backlog.dropped_seconds:.1f} s this session)")
                            episode_start_dropped = backlog.dropped_seconds
                            if not closed:
                                await websocket.send(json.dumps(
                                    {"type": "status", "state": "ready"}))
                        if finish_requested or closed:
                            break
                        await arrived.wait()
                        continue
                    async with decode_lock:
                        results = await asyncio.to_thread(session.feed, chunk)
                    if backlog.dropped_seconds > episode_start_dropped and not behind:
                        behind = True
                        episode_start_dropped = backlog.dropped_seconds
                        log("offline decode fell behind realtime; "
                            "dropping oldest audio to stay live")
                        await websocket.send(json.dumps(
                            {"type": "status", "state": "behind"}))
                    for result in results:
                        await websocket.send(json.dumps(result))
                if finish_requested and not closed:
                    # The flush final hardens the last utterance; the client
                    # closes 150 ms after asking, so losing this race is
                    # routine (the reader already logged anything abnormal).
                    async with decode_lock:
                        flushed = await asyncio.to_thread(session.finish)
                    with contextlib.suppress(websockets.ConnectionClosed):
                        await websocket.send(json.dumps(flushed))
            finally:
                reader_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await reader_task
        except websockets.ConnectionClosed:
            raise  # routine here; the reader logged any abnormal close code
        except Exception:
            # The websockets.server logger is silenced above, which also
            # hides its "connection handler failed" traceback — log real
            # handler failures ourselves, or offline speech dies mid-session
            # with an empty speech-error.log.
            traceback.print_exc()
            raise

    async with websockets.serve(
        transcribe,
        "127.0.0.1",
        port,
        origins=ALLOWED_ORIGINS,
        max_size=256 * 1024,
        compression=None,
        # No keepalive on loopback: a dead peer shows up as a TCP close, and
        # a missed pong deadline under decode load only kills good sessions.
        ping_interval=None,
    ):
        print(f"offline speech ready on 127.0.0.1:{port}", flush=True)
        await asyncio.Future()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True,
                        help="the downloaded model directory (may not exist yet)")
    parser.add_argument("--manifest", type=Path, required=True,
                        help="offline-model.json listing file names and exact sizes")
    parser.add_argument("--port", type=int, default=8791)
    args = parser.parse_args()
    if not args.manifest.is_file():
        parser.error(f"offline model manifest not found: {args.manifest}")
    files = files_from_manifest(args.manifest)
    asyncio.run(serve(args.model.resolve(), files, args.port))


if __name__ == "__main__":
    main()
