"""WebSocket source: the live finalized-text stream from the speech server.

Connects to the speech server's ``ws://localhost:8788/finalized`` and yields
one operation tuple per message:

    ("feed",   seg_id_or_None, text, final)   queue new text under a segment
    ("revise", seg_id,         text, final)   replace that segment's queued
                                              words
    ("latency", render)                       the render gate, "eager" or
                                              "confirmed", decided by the
                                              server

``final`` marks the segment hardened after the op. Soft text queues in the
engine but does not render until its final arrives, so the braille output
and the page's finalized transcript are the same text.

Wire messages:
  {"type":"final","id":...,"text":...}   a hardened segment. An unseen id
      (or no id) is fed whole; an id already fed soft becomes its last
      revision.
  {"type":"soft","id":...,"text":...}    first emission of a revisable
      segment (or, for an id already fed, a revision of it).
  {"type":"revise","revise":...,"text":...}  corrected text for a soft
      segment. Dropped for an id never fed (connected mid-stream); its final
      will arrive whole.
  {"type":"latency","render":...}        the render gate.

Upstream, the source forwards ``{"type":"shown","id","text"}`` acks from
``ack_queue``: the effective text a final put on the display.

Reconnects: by default a lost (or never established) connection is retried
forever with exponential backoff, so the reader survives speech-server
restarts. The stream has no replay, so text finalized while disconnected is
lost, and the log says so. A segment fed soft before an outage whose final
arrives after it is recognized by the engine and applied as a revision.
``reconnect=False`` makes one connection and ends when it closes (raising
if it closes abnormally).

WSL: under WSL2 "localhost" is the Linux VM, not the Windows host where the
speech server runs. If a localhost URL fails under WSL, the default-route
gateway (the Windows host) is tried.
"""

import asyncio
import json
import sys


def _wsl_host_ip():
    """The Windows host's IP as seen from WSL2 (default-route gateway)."""
    try:
        with open("/proc/net/route") as f:
            for line in f.readlines()[1:]:
                parts = line.split()
                if parts[1] == "00000000":  # default route
                    gw = int(parts[2], 16)
                    return ".".join(str((gw >> (8 * i)) & 0xFF) for i in range(4))
    except OSError:
        pass
    return None


def _is_wsl():
    try:
        with open("/proc/version") as f:
            return "microsoft" in f.read().lower()
    except OSError:
        return False


def interpret_message(msg, seen):
    """One wire message -> one op tuple (or None to ignore).

    ``seen`` is the set of segment ids this stream has fed as SOFT and not
    yet hardened; the caller owns it across messages (and, deliberately,
    across reconnects of the same server — ids carry a per-server-boot
    nonce, so a restarted server can never collide with them)."""
    if not isinstance(msg, dict):
        return None
    if msg.get("type") == "latency":
        # Sent on connect and on change.
        render = msg.get("render")
        if render in ("eager", "confirmed"):
            return ("latency", render)
        return None
    if not isinstance(msg.get("text"), str):
        return None
    mtype = msg.get("type")
    text = msg["text"]
    if mtype == "final":
        seg = msg.get("id")
        if seg is not None and seg in seen:
            seen.discard(seg)      # hardened: nothing more will cite this id
            return ("revise", seg, text, True)
        return ("feed", seg, text + " ", True)
    if mtype == "soft":
        seg = msg.get("id")
        if seg is None:
            return None
        if seg in seen:
            return ("revise", seg, text, False)
        seen.add(seg)
        return ("feed", seg, text + " ", False)
    if mtype == "revise":
        seg = msg.get("revise")
        if seg is not None and seg in seen:
            return ("revise", seg, text, False)
        return None
    return None


async def _connect(url):
    import websockets

    try:
        return await websockets.connect(url)
    except OSError:
        host_ip = _wsl_host_ip() if _is_wsl() and "localhost" in url else None
        if not host_ip:
            raise
        fallback = url.replace("localhost", host_ip)
        sys.stderr.write(f"[info] localhost unreachable under WSL; "
                         f"using Windows host {fallback}\n")
        return await websockets.connect(fallback)


async def ws_source(url: str = "ws://localhost:8788/finalized", *,
                    reconnect: bool = True,
                    initial_delay: float = 0.5,
                    max_delay: float = 15.0,
                    ack_queue=None):
    # Imported lazily so the package loads without it. The exceptions
    # submodule must be imported explicitly: websockets lazy-loads its
    # submodules, so plain `import websockets` doesn't guarantee the
    # `websockets.exceptions` attribute exists yet.
    import websockets.exceptions

    recoverable = (OSError, asyncio.TimeoutError,
                   websockets.exceptions.WebSocketException)

    # ``ack_queue`` (asyncio.Queue, optional) carries upstream messages —
    # the display's {"type":"shown","id","text"} acks, the effective text a
    # final actually put under the reader's fingers (the fastest preset's
    # screen contract). Forwarded on whatever connection is live; a send
    # failure just ends the forwarder, because the reader loop is about to
    # notice the same drop and reconnect. Acks queued across an outage go
    # to the new server harmlessly: segment ids carry a per-boot nonce, so
    # a restarted server ignores them, and the server's ack timeout has
    # long since fallen back to the provider text anyway.
    async def _forward_acks(ws):
        while True:
            item = await ack_queue.get()
            try:
                await ws.send(json.dumps(item))
            except recoverable:
                return
    delay = initial_delay
    had_stream = False       # some earlier connection delivered a message
    warned_waiting = False
    seen = set()             # soft segment ids fed and not yet hardened
    while True:
        try:
            ws = await _connect(url)
        except recoverable as error:
            if not reconnect:
                raise
            if not warned_waiting:
                # One line per outage, not one per attempt.
                warned_waiting = True
                sys.stderr.write(f"[warn] finalized stream unavailable "
                                 f"({error}); retrying until it comes back\n")
            await asyncio.sleep(delay)
            delay = min(delay * 2, max_delay)
            continue

        fresh = True         # nothing received on THIS connection yet
        sender = (asyncio.ensure_future(_forward_acks(ws))
                  if ack_queue is not None else None)
        try:
            async for raw in ws:
                if fresh:
                    # The stream is proven healthy only once a message
                    # arrives — a server that accepts and instantly closes
                    # must keep the backoff growing, not reset it.
                    fresh = False
                    if had_stream:
                        sys.stderr.write(
                            "[info] finalized stream reconnected; anything "
                            "spoken while disconnected was not delivered\n")
                    had_stream = True
                    warned_waiting = False
                    delay = initial_delay
                try:
                    msg = json.loads(raw)
                except (ValueError, TypeError):
                    continue
                # The server emits segments without a trailing space; feed
                # ops add one so the assembler completes each segment's final
                # word. (A non-object frame must not kill the stream —
                # interpret_message ignores it.)
                op = interpret_message(msg, seen)
                if op is not None:
                    yield op
        except recoverable:
            # Dropped mid-stream. With reconnect this is the same path as a
            # clean close; with --ws-once it surfaces, so a crashed server
            # doesn't look like a finished session.
            if not reconnect:
                raise
        finally:
            if sender is not None:
                sender.cancel()
            try:
                await ws.close()
            except recoverable:
                pass

        if not reconnect:
            return
        if fresh:
            if not warned_waiting:
                warned_waiting = True
                sys.stderr.write("[warn] finalized stream keeps closing "
                                 "before delivering anything; retrying with "
                                 "backoff\n")
        else:
            sys.stderr.write("[warn] finalized stream closed; reconnecting\n")
        await asyncio.sleep(delay)
        delay = min(delay * 2, max_delay)
