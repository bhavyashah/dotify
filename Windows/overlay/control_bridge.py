"""Loopback HTTP bridge between the Windows browser panel and the ticker.

Serves the panel script (appliance_controls.js) and exposes the braille
engine's ``PacerControls`` to it: GET /api/state and /api/frame for polling,
POST /api/command for actions, and POST /api/page-hidden for the window-close
beacon. Binds to loopback only; every /api/ request needs the per-process
token, which is baked into the served script, and an allowed Origin.
"""

from __future__ import annotations

import json
import secrets
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# braille_engine sits beside this file in the installed image.
from braille_engine.controls import (
    DEFAULT_ADVANCE_MODE,
    advance_label,
    reading_wpm,
)


CONTROL_HOST = "127.0.0.1"
CONTROL_PORT = 8790
# The demo command carries whole documents (the bundled novel is ~150k
# characters); the panel caps uploads at 1 MB, and this is the same ceiling
# in characters.
MAX_DEMO_TEXT_CHARS = 1_000_000
# Headroom over the demo cap: JSON escaping can spend six bytes a character.
# A sanity bound, not the security boundary (that is loopback + token).
MAX_COMMAND_BODY_BYTES = 8 * 1024 * 1024
ALLOWED_ORIGINS = {
    "http://127.0.0.1:8788",
    "http://localhost:8788",
}


def claim_port_exclusively(sock):
    """Make a second bind of this socket's port fail on Windows.

    Windows lets two SO_REUSEADDR sockets bind the same port and splits
    connections between them, so a leftover ticker and a new one would
    answer the panel by turns. SO_EXCLUSIVEADDRUSE refuses the second bind
    instead, and still allows an immediate rebind while the previous
    session's connections sit in TIME_WAIT. Elsewhere SO_REUSEADDR has no
    such hazard and is kept.
    """
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    else:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)


class _ExclusiveHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = False  # claim_port_exclusively decides

    def server_bind(self):
        claim_port_exclusively(self.socket)
        super().server_bind()


class ControlBridge:
    # How long after a pagehide beacon a fresh /api/state poll must arrive
    # before the window is presumed CLOSED rather than reloading. A reloaded
    # panel polls within a second or two of loading; generous headroom on
    # top of that, because concluding wrongly stops the whole appliance.
    CLOSE_QUIT_GRACE_SECONDS = 10.0

    def __init__(self, controls, stop_event, host=CONTROL_HOST, port=CONTROL_PORT):
        self.controls = controls
        self.stop_event = stop_event
        self.host = host
        self.port = port
        self.token = secrets.token_urlsafe(32)
        self._lock = threading.Lock()
        self._server = None
        self._thread = None
        self._state_requests = 0

    def _arm_close_quit(self):
        """Closing the panel window quits Dotify.

        pagehide also fires on a reload, so a beacon only starts a grace
        timer against the poll count it saw. A live panel (a reload, or a
        second window) polls within the grace period and nothing happens;
        silence means the window is gone, and stopping the ticker lets the
        launcher tear down the other services.
        """
        polls_at_beacon = self._state_requests

        def conclude():
            if self._state_requests != polls_at_beacon:
                return
            sys.stderr.write("[bridge] panel window closed; stopping Dotify\n")
            sys.stderr.flush()
            self.controls.stop = True
            self.stop_event.set()

        timer = threading.Timer(self.CLOSE_QUIT_GRACE_SECONDS, conclude)
        timer.daemon = True
        timer.start()

    def state(self):
        engine = self.controls.engine
        backlog_words = getattr(engine, "backlog_words", lambda: 0)()
        gauge = getattr(engine, "gauge", None)
        backlog_level = None
        if gauge is not None:
            # peek, never update: the engine thread is the gauge's only
            # stepper, and a poll-time update() would absorb dips it never
            # rendered.
            backlog_level = gauge.peek(backlog_words)
        sink = getattr(engine, "sink", None)
        display = getattr(sink, "display_name", None)
        display_wait = getattr(engine, "display_wait_reason", None)
        if display is None and display_wait is None and hasattr(sink, "display_name"):
            # Nothing connected yet and no reason published (startup, or the
            # moment between a write failure and recover_display). Without
            # this the panel would announce "Connected to AutoDisplaySink."
            display_wait = "still connecting (normal for a few seconds at startup)"
        # The panel's cell-window maximum: the content width, 40 until a
        # display has connected.
        max_window = int(getattr(engine, "content_width", None) or 40)
        advance_mode = getattr(self.controls, "advance_mode",
                               DEFAULT_ADVANCE_MODE)
        return {
            "connected": True,
            "display": display or sink.__class__.__name__ if sink else "unknown",
            "grade": int(getattr(engine.translator, "grade", 1)),
            "mode": self.controls.mode,
            "paused": bool(getattr(engine, "paused", False)),
            # Before a display connects, set_window_full() leaves an oversize
            # sentinel (10**6) in engine.window that start() clamps later;
            # unclamped, the panel's input would read "one million". After
            # connect the min() changes nothing. Display only: the panel
            # writes back only on a human edit.
            "window": min(int(getattr(engine, "window", 1)), max_window),
            "max_window": max_window,
            # The engine's own paging predicate, so the panel never derives it.
            "window_is_full": bool(getattr(engine, "window_is_full", False)),
            "pace_ms": round(float(self.controls.interval["v"]) * 1000),
            # The engine's reading-speed estimate, so no JS copy can drift.
            "wpm": reading_wpm(float(self.controls.interval["v"]),
                               int(getattr(engine.translator, "grade", 1)),
                               engine=engine),
            # Wire value 'ticker' (shown as "auto") or 'manual'; the panel
            # shows advance_label, the engine's own word for the mode.
            "advance": advance_mode,
            "advance_label": advance_label(advance_mode),
            # Summarize-on-demand: 'fetching' while the summary request is
            # out, 'streaming' while it plays on the display, else None.
            "catchup": getattr(self.controls, "catchup", None),
            "summary": getattr(self.controls, "last_summary", None),
            # Cells panned back from the live edge (0 = live). While panned,
            # streaming holds and speech queues.
            "pan_offset": int(getattr(engine, "pan_offset", 0)),
            # The print words whose cells are on the display ("" when
            # unknown), which the page marks in the transcript so a sighted
            # speaker can see where the reader is.
            "shown": getattr(engine, "shown_source", lambda: "")(),
            # The literal display line, recap included. /api/frame is the
            # fast path; this copy keeps the 1 s poll self-sufficient.
            "shown_display": getattr(engine, "shown_display", lambda: "")(),
            # The queue head. Also on this poll because it grows while no
            # frames are written, and /api/frame only reports on writes.
            "pending_text": getattr(engine, "pending_text", lambda: "")(),
            "backlog_words": backlog_words,
            "backlog_level": backlog_level,
            # Why the display is unavailable, or None. The panel announces
            # it: the braille display is dead, and the screen reader that
            # usually caused the collision is what speaks the fix.
            "display_wait": display_wait,
            # Relay counters for actions the page must perform (the
            # microphone lives in the page): the Space+dot-6 mic toggle, and
            # the idle watchdog's mic pause and resume. The panel acts on
            # each increase, never on the value.
            "mic_toggle": int(
                getattr(self.controls, "mic_toggle_requests", 0)),
            "idle_pause": int(
                getattr(self.controls, "idle_pause_requests", 0)),
            "idle_resume": int(
                getattr(self.controls, "idle_resume_requests", 0)),
            # The microphone label the page last reported ("" when not
            # capturing); shown in the Space+I status flash.
            "mic": str(getattr(self.controls, "mic_label", "")),
            # True while sample, file or caption text is playing.
            "demo": bool(getattr(self.controls, "demo_active", False)),
            # Percent of the pace a space or punctuation cell dwells.
            "space_time": round(getattr(engine, "space_dwell", 1.0) * 100),
            "punct_time": round(getattr(engine, "punct_dwell", 1.0) * 100),
            "lowercase": bool(getattr(engine, "lowercase", False)),
            # The reader typing a reply on the display's keys: {active,
            # session, seq, text, sentences}. The page pauses the mic,
            # prints the text and speaks each finished sentence.
            "reply": self.controls.reply.state()
            if getattr(self.controls, "reply", None) else None,
        }

    def command(self, name, value=None):
        with self._lock:
            # Panel commands count as human activity for the idle watchdog,
            # except the page's own relays: counting those would let Dotify
            # keep itself awake (the watchdog's own mic-off alert would wake
            # it and resume the mic within one poll).
            if name not in ("announce", "alert", "mic"):
                touch = getattr(self.controls, "touch_activity", None)
                if touch:
                    touch()
            if name == "faster":
                self.controls.handle("\x06")
            elif name == "slower":
                self.controls.handle("\x13")
            elif name == "grade":
                self.controls.handle("\x07")
            elif name == "live":
                # Through PacerControls, which also cancels a recap.
                self.controls.jump_to_live()
            elif name == "summarize":
                # A repeat press cancels the fetch or skips the recap.
                self.controls.summarize_now()
            elif name == "mode":
                # No value toggles. A value switches only if needed, so the
                # page's focus-driven mode changes can't race a toggle.
                if value is not None and value not in ("listen", "type"):
                    raise ValueError("mode value must be 'listen' or 'type'")
                if value is None or self.controls.mode != value:
                    self.controls.handle("\t")
            elif name == "advance":
                # Same toggle/target contract as "mode". 'auto' is accepted
                # for the paced mode, whose wire value is 'ticker'.
                if value is not None and value not in (
                        "ticker", "auto", "manual"):
                    raise ValueError(
                        "advance value must be 'auto', 'ticker' or 'manual'")
                if value == "auto":
                    value = "ticker"
                if value is None:
                    self.controls._cycle_advance_mode()
                elif self.controls.advance_mode != value:
                    self.controls.set_advance_mode(value)
            elif name == "type_text":
                text = str(value or "")
                if len(text) > 4096:
                    raise ValueError("typed text is limited to 4096 characters")
                # Typed text is content: drop the bytes handle() would read
                # as commands (Tab switches mode, \x03/\x04 quit, other
                # control bytes are shortcuts).
                text = "".join(
                    " " if ch in ("\t", "\r", "\n")
                    else ch if ch == " " or ch.isprintable() else ""
                    for ch in text)
                if self.controls.mode != "type":
                    self.controls.handle("\t")
                for character in text:
                    self.controls.handle(character)
            elif name == "window":
                # Any whole number of cells up to the content width (the full
                # width flips pages). Auto mode only: manual always shows the
                # full display, so a window set there would change nothing.
                if getattr(self.controls, "advance_mode", "ticker") \
                        != "ticker":
                    raise ValueError(
                        "the cell window applies to auto mode only; "
                        "manual always flips the full display")
                engine = self.controls.engine
                # Before connect, engine.window holds the full-display
                # sentinel that start() clamps; a literal set now would
                # survive the clamp and full-page flipping would never arm.
                if getattr(engine, "content_width", None) is None:
                    raise ValueError(
                        "wait for the braille display to connect "
                        "before changing the cell window")
                limit = int(engine.content_width)
                if isinstance(value, bool) or not isinstance(value, int) \
                        or not 1 <= value <= limit:
                    raise ValueError(
                        f"window must be a whole number from 1 to {limit}")
                engine.set_window(value)
            elif name == "pause":
                # Toggle by default; true/false is idempotent. Goes through
                # PacerControls so the display answers like the Space+dot-3
                # chord (a resume flashes "resumed").
                engine = self.controls.engine
                if value is None:
                    target = not engine.paused
                elif isinstance(value, bool):
                    target = value
                else:
                    raise ValueError("pause value must be true or false")
                self.controls.set_paused(target)
                # Logged like the keyboard path, so the log explains a
                # display that stopped moving.
                sys.stderr.write("[bridge] braille "
                                 + ("paused\n" if engine.paused else "resumed\n"))
                sys.stderr.flush()
            elif name == "flush":
                # Stream a typed word that has no trailing space yet. Typing
                # mode only; in listen mode partial words are the speech
                # source's.
                if self.controls.mode == "type":
                    self.controls.engine.flush_input()
            elif name == "demo":
                # Toggles the text stream. No value plays the built-in sample
                # (or stops whatever plays); a string plays that text; an
                # object {"captions": ...} replays SRT/WebVTT file content
                # at its own timing.
                timed = isinstance(value, dict)
                text = value.get("captions") if timed else value
                noun = ("demo captions (SRT or WebVTT file content)"
                        if timed else "demo text")
                if timed or text is not None:
                    if not isinstance(text, str) or not text.strip():
                        raise ValueError(
                            f"{noun} must be a non-empty string")
                    if len(text) > MAX_DEMO_TEXT_CHARS:
                        raise ValueError(f"{noun} is limited to 1 MB")
                # Unusable caption content raises ValueError -> 400.
                self.controls.toggle_demo(text, timed=timed)
            elif name == "announce":
                # The page confirming an outcome on the display (e.g. "mic
                # off" after the mic chord). Capped to what a frame can show.
                if not isinstance(value, str) or not value.strip():
                    raise ValueError("announce needs a non-empty string")
                if len(value) > 80:
                    raise ValueError(
                        "announce text is limited to 80 characters")
                self.controls.announce(value.strip())
            elif name == "alert":
                # Like announce, for notices nobody asked for (the idle
                # watchdog's mic-off): they get the longer dwell.
                if not isinstance(value, str) or not value.strip():
                    raise ValueError("alert needs a non-empty string")
                if len(value) > 80:
                    raise ValueError(
                        "alert text is limited to 80 characters")
                self.controls.announce(value.strip(), important=True)
            elif name == "mic":
                # Which microphone the page captures from ("usb mic", "" when
                # none), for the Space+I status flash.
                if not isinstance(value, str):
                    raise ValueError("mic needs a string label")
                if len(value) > 40:
                    raise ValueError(
                        "mic label is limited to 40 characters")
                self.controls.set_input_source(value.strip())
            elif name in ("space_time", "punct_time"):
                # Percent of the per-cell pace a space or punctuation cell
                # dwells (timing only; no cell is dropped).
                if isinstance(value, bool) or not isinstance(value, int) \
                        or not 1 <= value <= 100:
                    raise ValueError(
                        f"{name} must be a whole percent from 1 to 100")
                if name == "space_time":
                    self.controls.set_space_time(value)
                else:
                    self.controls.set_punct_time(value)
            elif name == "lowercase":
                # Same toggle/idempotent contract as pause.
                engine = self.controls.engine
                if value is None:
                    self.controls.set_lowercase(not engine.lowercase)
                elif isinstance(value, bool):
                    self.controls.set_lowercase(value)
                else:
                    raise ValueError("lowercase value must be true or false")
            elif name == "quit":
                self.controls.stop = True
                self.stop_event.set()
            else:
                raise ValueError(f"unknown command: {name}")
        return self.state()

    def _javascript(self):
        template = Path(__file__).with_name("appliance_controls.js").read_text(
            encoding="utf-8"
        )
        base = f"http://{self.host}:{self.port}"
        return (
            template.replace("__DOTIFY_TOKEN_JSON__", json.dumps(self.token))
            .replace("__DOTIFY_BASE_JSON__", json.dumps(base))
            .encode("utf-8")
        )

    def start(self):
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            # Keep-alive for the 250 ms frame poll. With HTTP/1.1 every reply
            # must consume the request body or close the connection (see
            # _refuse), or the next request on it desyncs.
            protocol_version = "HTTP/1.1"

            def log_message(self, _format, *_args):
                return

            def _refuse(self, status, body):
                """Error reply on a request whose body was not consumed:
                drop the connection instead of desyncing keep-alive."""
                self.close_connection = True
                self._send(status, body)

            def _origin_allowed(self):
                return self.headers.get("Origin") in ALLOWED_ORIGINS

            def _cors(self):
                origin = self.headers.get("Origin")
                if origin in ALLOWED_ORIGINS:
                    self.send_header("Access-Control-Allow-Origin", origin)
                    self.send_header("Vary", "Origin")
                    self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Dotify-Token")
                    self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                    # The token header forces preflights; cache them so the
                    # 1 s poll isn't re-preflighted every few seconds.
                    self.send_header("Access-Control-Max-Age", "86400")

            def _send(self, status, body, content_type="application/json; charset=utf-8"):
                if isinstance(body, (dict, list)):
                    body = json.dumps(body).encode("utf-8")
                elif isinstance(body, str):
                    body = body.encode("utf-8")
                self.send_response(status)
                self._cors()
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _authorized(self):
                return (
                    self._origin_allowed()
                    and self.headers.get("X-Dotify-Token") == bridge.token
                )

            def do_OPTIONS(self):
                if not self._origin_allowed():
                    self._send(403, {"error": "origin not allowed"})
                    return
                self.send_response(204)
                self._cors()
                self.end_headers()

            def do_GET(self):
                if self.path == "/health":
                    self._send(200, {"ok": True})
                elif self.path == "/dotify-controls.js":
                    self._send(
                        200,
                        bridge._javascript(),
                        "application/javascript; charset=utf-8",
                    )
                elif self.path == "/api/state" and self._authorized():
                    # Under the command lock: a mid-command snapshot could
                    # outrace the command's own response and repaint stale
                    # state in the panel.
                    with bridge._lock:
                        bridge._state_requests += 1
                        state = bridge.state()
                    self._send(200, state)
                elif self.path == "/api/frame" and self._authorized():
                    # The 250 ms mirror poll: the engine's latest frame event
                    # ({seq, kind, text, source}, {} before the first). The
                    # event is an atomic engine snapshot, so the command lock
                    # is held only for the liveness count.
                    with bridge._lock:
                        bridge._state_requests += 1
                    engine = bridge.controls.engine
                    event = getattr(engine, "frame_event", lambda: None)()
                    self._send(200, event or {})
                else:
                    self._send(403 if self.path.startswith("/api/") else 404, {"error": "not found"})

            def do_POST(self):
                if self.path == "/api/page-hidden":
                    # sendBeacon cannot set headers, so the token is the body.
                    try:
                        length = int(self.headers.get("Content-Length", "0"))
                    except ValueError:
                        length = 0
                    body = ""
                    if 0 < length <= 4096:
                        body = self.rfile.read(length).decode("utf-8", "replace")
                    if not self._origin_allowed() or body != bridge.token:
                        self._refuse(403, {"error": "not authorized"})
                        return
                    bridge._arm_close_quit()
                    self._send(200, {"ok": True})
                    return
                if self.path != "/api/command" or not self._authorized():
                    self._refuse(403, {"error": "not authorized"})
                    return
                payload = {}
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if length < 1 or length > MAX_COMMAND_BODY_BYTES:
                        raise ValueError("invalid request length")
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                    if not isinstance(payload, dict):
                        raise ValueError("request body must be a JSON object")
                    state = bridge.command(payload.get("command"), payload.get("value"))
                    self._send(200, state)
                except (ValueError, UnicodeError) as exc:
                    # The length check raises before the body is read.
                    self._refuse(400, {"error": str(exc)})
                except (OSError, RuntimeError) as exc:
                    # A display outage mid-command (say, a jump-to-live
                    # repaint). As on the terminal key path, answer and let
                    # the pacer own the reconnect.
                    sys.stderr.write(
                        f"[bridge] display write failed during "
                        f"'{payload.get('command')}' ({exc}); "
                        "the ticker is reconnecting\n")
                    self._send(503, {"error": "display disconnected; "
                                              "the ticker is reconnecting"})

        self._server = _ExclusiveHTTPServer((self.host, self.port), Handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="dotify-control-bridge",
            daemon=True,
        )
        self._thread.start()
        return self

    def close(self):
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
