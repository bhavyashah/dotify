"""Behavior checks for functions inside the browser overlay scripts.

The panel (appliance_controls.js) and the speech overlay are each one
browser IIFE, so these tests lift a named function out of the source and run
it under Node against stubs. Skipped when Node is not on PATH.
"""

import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest


PANEL = Path(__file__).with_name("appliance_controls.js")
SPEECH_OVERLAY = Path(__file__).parent / "speech_ui" / "windows-speech-overlay.js"

node = shutil.which("node")
pytestmark = pytest.mark.skipif(node is None, reason="node is not on PATH")


def lift(source, start_pattern):
    """Return the top-level IIFE statement that starts at start_pattern
    (two-space indent) through its closing line."""
    match = re.search(rf"^  {start_pattern}.*?^  }};?$", source,
                      re.MULTILINE | re.DOTALL)
    assert match, f"{start_pattern!r} not found"
    return match.group(0)


def run_node(script):
    result = subprocess.run([node, "-e", script], capture_output=True,
                            text=True, timeout=30, check=True)
    return json.loads(result.stdout)


def test_grade_readout_names_every_grade_the_engine_cycles():
    source = PANEL.read_text(encoding="utf-8")
    script = "\n".join((
        lift(source, r"const GRADE_LABELS = \{"),
        lift(source, r"function gradeLabel\("),
        "console.log(JSON.stringify("
        "[1, 2, 3, undefined, 7].map(gradeLabel)));",
    ))
    assert run_node(script) == [
        "1 (uncontracted)",
        "2 (contracted)",
        "3 (experimental)",
        "--",
        "7",
    ]


def command_harness(status, body):
    """command() with a stubbed fetch answering (status, body); returns the
    command's result, the shown errors, and whether anything rendered."""
    source = PANEL.read_text(encoding="utf-8")
    return "\n".join((
        "const base = ''; const headers = {}; let requestSeq = 0;",
        "const errors = []; let rendered = 0;",
        "function showError(message) { errors.push(message); }",
        "function renderIfFresh() { rendered += 1; return true; }",
        "function fetchSignal() { return undefined; }",
        "async function state() {}",
        f"globalThis.fetch = async () => ({{ status: {status}, "
        f"ok: {str(200 <= status < 300).lower()}, "
        f"json: async () => ({json.dumps(body)}) }});",
        lift(source, r"async function command\("),
        "command('window', 30).then((ok) => console.log(JSON.stringify("
        "{ ok, errors, rendered })));",
    ))


def test_a_refused_command_is_not_reported_as_an_outage():
    result = run_node(command_harness(
        400, {"error": "the cell window applies to auto mode only"}))
    assert result["ok"] is False
    assert result["rendered"] == 0
    assert result["errors"] == [
        "Not applied: the cell window applies to auto mode only."]


def test_a_failing_service_is_still_reported_as_unavailable():
    result = run_node(command_harness(
        503, {"error": "display disconnected; the ticker is reconnecting"}))
    assert result["ok"] is False
    assert result["errors"] == [
        "Braille control unavailable: display disconnected; "
        "the ticker is reconnecting"]


def test_quit_stops_the_frame_poll_too():
    # markStopped() promises nothing repaints after quit; the 250 ms frame
    # poll paints the current band, so it must stop with the state poll and
    # drop a response that was already in flight.
    source = PANEL.read_text(encoding="utf-8")
    stop = lift(source, r"function markStopped\(")
    assert "clearInterval(pollTimer);" in stop
    assert "clearInterval(framePollTimer);" in stop
    frame_poll = source[source.index("const framePollTimer = setInterval("):]
    assert "dataset.dotifyStopped) return;" in frame_poll.split(".catch(")[0]


def test_a_stop_during_the_microphone_prompt_releases_the_microphone():
    # stop() can run while getUserMedia is still pending (an engine switch
    # during the permission prompt). The stream that resolves afterwards has
    # to be released, or the microphone stays open with nothing using it.
    source = SPEECH_OVERLAY.read_text(encoding="utf-8")
    script = "\n".join((
        "let active = true; let meterStream = null; let meterContext = null;",
        "let stopped = 0; let resolveStream;",
        "const stream = { getTracks: () => [{ stop: () => { stopped += 1; } }],",
        "  getAudioTracks: () => [{}] };",
        "Object.defineProperty(globalThis, 'navigator', { value: {",
        "  mediaDevices: { getUserMedia: () =>",
        "    new Promise((resolve) => { resolveStream = resolve; }) } } });",
        lift(source, r"async function startMeter\("),
        "const started = startMeter();",
        "active = false;",
        "resolveStream(stream);",
        "started.then(() => console.log(JSON.stringify(",
        "  { stopped, held: meterStream !== null })));",
    ))
    assert run_node(script) == {"stopped": 1, "held": False}
