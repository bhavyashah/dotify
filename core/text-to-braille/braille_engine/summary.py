"""Summarize-on-demand client ("what did I miss").

Sends the reader's backlog to the speech server's ``/summarize`` endpoint
(which holds the LLM key; this is a stdlib-only HTTP client so ``core/``
stays dependency-free) and returns a summary sized to the backlog, which the
engine then streams like ordinary text.

Any failure returns None and the caller falls back to the plain snap, so a
dead server or missing key never breaks catching up.
"""

import http.client
import json
import urllib.error
import urllib.parse
import urllib.request

# Seconds per request. Must stay above the server's own LLM deadline (10 s in
# summarize.js), so a hung model still gets the server's extractive fallback
# back to us before we give up and snap.
DEFAULT_TIMEOUT = 12.0


def summary_url_from_ws(ws_url: str):
    """Derive the summarize endpoint from the finalized-text WebSocket URL,
    e.g. ws://localhost:8788/finalized -> http://localhost:8788/summarize.
    Returns None when the URL isn't a ws/wss URL (file replay etc.)."""
    parsed = urllib.parse.urlparse(ws_url)
    if parsed.scheme not in ("ws", "wss"):
        return None
    scheme = "https" if parsed.scheme == "wss" else "http"
    return f"{scheme}://{parsed.netloc}/summarize"


class Summarizer:
    """POSTs pending text to the speech server and returns the summary."""

    def __init__(self, url: str, timeout: float = DEFAULT_TIMEOUT):
        self.url = url
        self.timeout = timeout

    def summarize(self, text: str, chars: int, grade: int):
        """One summary attempt: ``chars`` is the hard character budget the
        model is asked to stay under; ``grade`` tells it whether contracted
        braille will shrink the rendering. Returns the summary string, or
        None on any error/timeout (callers treat that as "no summary")."""
        payload = json.dumps({
            "text": text,
            "chars": int(chars),
            "grade": int(grade),
        }).encode("utf-8")
        request = urllib.request.Request(
            self.url, data=payload,
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request,
                                        timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError,
                http.client.HTTPException):
            # HTTPException covers what urllib re-raises RAW from the
            # response stage (BadStatusLine, IncompleteRead...) — only the
            # request stage gets wrapped in URLError. Without it a garbled
            # response breaks the documented "None on any failure" contract.
            return None
        summary = body.get("summary") if isinstance(body, dict) else None
        if isinstance(summary, str) and summary.strip():
            return " ".join(summary.split())   # normalize whitespace/newlines
        return None
