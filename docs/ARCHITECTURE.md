# Architecture

Dotify is two engines joined by a WebSocket, wrapped in a thin platform
shell. Every process binds to `127.0.0.1`.

```
browser page  (http://127.0.0.1:8788: mic, engine picker, settings, transcript)
    │
    │  /audio WebSocket (PCM16, 24 kHz)      POST /ingest (offline engine,
    ▼                                        demo feeder, typed text)
speech server  (core/speech-to-text, Node)  ◄──────────┘
    cost gate → provider session → soft/final segments → personal dictionary
    │
    │  /finalized WebSocket  {final | soft | revise | latency}
    ▼
braille engine  (core/text-to-braille, Python)
    word assembler → liblouis → queue + pacer → display sink
    │
    ▼
display  (USB HID · USB serial · Bluetooth serial · BrlAPI on Linux)
```

On Windows the shell adds three more local services: the offline recognizer
(sherpa-onnx, `:8791`), the control bridge that carries settings and
display-key commands between the page and the braille engine (`:8790`), and
the optional speaker-identification service (`:8792`).

## Speech server — `core/speech-to-text/`

`server.js` serves the page and relays microphone audio from the `/audio`
WebSocket to one provider module in `providers/`. Every provider exposes
the same `createSession()` interface and reports interim hypotheses
(`onPartial`) and finished segments (`onFinal`).

Before audio reaches a paid provider it passes the **cost gate**
(`providers/gated-session.js`, `speech-gate.js`): silence is withheld, a
long-idle provider socket is closed, and a short **pre-open buffer** replays
the first syllables while the socket re-dials, so the reader doesn't lose
the start of the next sentence. A **socket watchdog** notices a provider
that has gone quiet and lets the page fall back to the offline engine,
probing (`/api/net-probe`) before switching back.

Text that doesn't come from a server-side provider (the offline
recognizer, the demo caption feeder, typed text) is posted to
`POST /ingest` and joins the same pipeline.

### The revisable-buffer protocol (`/finalized`)

Reading braille is slower than speech, so most text waits in the braille
engine's queue. The server uses that time: it sends text early and corrects
it until it hardens.

| message | meaning |
|---|---|
| `{type:"soft", id, text}` | the stable prefix of a hypothesis; may still change |
| `{type:"revise", revise:id, text}` | replace a soft segment's text (`""` withdraws it) |
| `{type:"final", id, text}` | the segment's last word on the matter |
| `{type:"latency", mode, render}` | whether consumers may render soft text eagerly |

The braille engine may revise a soft segment only while none of its cells
have reached the display. Once a cell is under the reader's fingers it never
changes; that is the system's central invariant.

How much of a hypothesis counts as "stable" is measured per engine, not
guessed: a word becomes soft only after it has sat `depth` words back from
the hypothesis tail, unchanged, for `persist` consecutive updates
(`SOFT_STABILITY` in `server.js`). The numbers came from replaying a recorded
meeting through each engine; see [LESSONS.md](LESSONS.md).

## Braille engine — `core/text-to-braille/`

```
source → word assembler → translator → engine (queue + pacer) → sink
```

- **Sources** (`sources/`): the `/finalized` WebSocket, a file, or stdin.
- **Translator** (`translator.py`): liblouis with the UEB tables, one word
  at a time, so switching grade 1 ↔ 2 mid-stream never alters cells already
  queued.
- **Engine** (`engine.py`): the queue, the pacer, and the rolling display
  buffer. *Auto* mode advances on a clock (a page every ~5 s by default, or
  a one-cell ticker tape with `--window 1`); *manual* mode waits for the
  reader. The reader can pan back, jump to the live edge, or request a
  summary of the backlog.
- **Gauge** (`gauge.py`): cell 1 shows how far behind live the reader is;
  cell 2 marks exceptional content (`s` summary, `h` typed text).
- **Controls** (`controls.py`, `display_keys.py`): terminal keys and braille
  display chords map to the same commands. Every command acknowledges on the
  display.
- **Sinks** (`sinks/`): simulated (terminal) and BrlAPI. Windows adds native
  sinks in `Windows/overlay/`.

## Windows shell — `Windows/`

`launcher.ps1` builds or reuses a self-contained app image (embedded Python
and Node, liblouis, sherpa-onnx), starts the services, and opens the page in
an app-mode browser window.

Displays are driven without BRLTTY and without installing a driver:

- `native_hid_sink.py` — HumanWare's vendor HID protocol (NLS eReader,
  Brailliant, APH devices built on it).
- `standard_hid_sink.py` — the USB HID Braille standard (usage page 0x41).
- `hims_hid_sink.py`, `serial_sinks.py` — HIMS devices and USB/Bluetooth
  serial links.
- `auto_display_sink.py` — picks a sink from
  `Windows/hardware/display_profiles.json`, preferring USB over Bluetooth.

The installer (`Windows/installer/`) packages the same image with Inno Setup.
Its build refuses to stage `.env`, `*.key`, or `speakers/`.

## Linux shell — `Linux/`

`launch-dotify.sh` starts BRLTTY, the speech server, and the braille engine
with the BrlAPI sink. Anything BRLTTY supports should work.
