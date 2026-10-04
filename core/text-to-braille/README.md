# Braille reading engine

Streams text to a refreshable braille display as **word-wrapped pages** that
flip themselves on a steady clock (auto mode, the default) or that the
reader flips on request (manual mode). With a small `--window` it streams a
few cells per refresh instead, down to a one-cell-at-a-time, right-aligned
ticker-tape scroll (`--window 1`).

It consumes the finalized-text stream from the speech server
(`core/speech-to-text`) and drives the display directly rather than through
a screen reader: via BRLTTY's BrlAPI on Linux, or the Windows shell's native
display sinks (`Windows/overlay`). The core runs on the Python standard
library alone; `websockets` (for live speech), liblouis and BrlAPI are
optional extras (see `requirements.txt`).

## Design

1. **The reader controls the pace.** Manual mode moves only when the reader
   flips; auto mode advances on a per-cell clock whose speed is adjustable
   live.
2. **Shown text never changes.** Cells are append-only. Words are translated
   when they reach the display, so a grade switch applies to everything not
   yet shown, and nothing under the reader's fingers is ever rewritten.
3. **Catching up is one command.** When speech outruns the reader it queues;
   jump-to-live discards the queue and snaps to the newest text, and
   summarize-on-demand streams a short recap of what was skipped.

## Pipeline

```
Input source -> Word assembler -> Translator -> Engine         -> Display sink
 file/stdin/ws   word at a time    liblouis      queue, pacer,     simulated /
                                   (UEB)         rolling buffer    BrlAPI / Windows
```

Source, translator and sink are small interfaces (`sinks/base.py` documents
the sink contract, including which exceptions mean "display lost"), so the
display library, braille table or input can change without touching the
engine.

## Quick start (no hardware, no speech)

```bash
# Replay a file on the simulated display (it renders in the terminal):
python run.py --file sample.txt

# Or pipe text in:
echo "hello world" | python run.py --source stdin
```

Reading starts in auto mode: word-wrapped pages flipping on a steady clock.
Press `r` (or pass `--advance-mode manual`) for manual reading, where
nothing moves until `f` flips the next page.

### Terminal keys

Two input modes, toggled with **Tab**. In **AI mode** (the default; internal
value `listen`) speech streams and keys are commands. In **Human mode**
(internal value `type`) speech is discarded and every printable key is
content sent to the display, so a person can type names, addresses or
anything speech gets wrong. Enter is a space; there are no editing keys.

AI-mode commands:

| Key            | Action                                                   |
| -------------- | -------------------------------------------------------- |
| `f` / `s`      | read faster / slower (in manual mode `f` flips the page) |
| `g`            | cycle grade 1 → 2 → experimental English 3               |
| `w`            | cycle the cell window (auto mode only)                   |
| `r`            | toggle the advance mode (auto / manual)                  |
| `p`            | pause / resume the display (input keeps queueing)        |
| `space` or `l` | jump to the live edge                                    |
| `S` (shift+s)  | summarize the backlog and read the recap (`ws` runs)     |
| `q`            | quit                                                     |
| `Tab`          | switch to Human mode                                     |

In both modes `Ctrl+G` / `Ctrl+F` / `Ctrl+S` / `Ctrl+W` / `Ctrl+R` /
`Ctrl+P` do grade / faster / slower / window / advance mode / pause, and
`Ctrl-C` quits.

### Display keys

When the sink can read the display's own keys (BrlAPI, and the Windows
sinks), the same commands are available as chords: Space+dot-1/dot-4 for
slower/faster, Space+dot-3 to pause, the thumb keys to pan through
history, jump to live and switch modes, and mnemonic letter chords
(Space+G grade, Space+W window, Space+S summarize, Space+I status,
Space+T demo text, Space+R reply). The full map is in
`braille_engine/display_keys.py`. Space+dot-6 asks the platform shell to
pause or resume the input source (the microphone); the engine only relays
the request through the `mic_toggle_requests` counter, and the shell
confirms on the display.

**Reply mode** (Space+R) lets a reader who types on the display's Perkins
keys answer the conversation: streaming freezes, the typed cells echo on
the line, and the platform shells print each word and speak each sentence
(`braille_engine/reply.py`).

### Cells 1 and 2

With the gauge on (the default), cell 1 is a **backlog gauge** that fills
like a thermometer the further the reader is from the live edge (including
while panned back through history), and is blank only when the reader is
live and caught up. Cell 2 marks exceptional content: `s` (dots 2-3-4)
while a summary is being fetched or read, `h` (dots 1-2-5) while
human-typed text is shown, `r` during a reply, and blank over the ordinary
live stream. `--no-gauge` frees both cells for content, at the cost of
those cues.

### Summarize-on-demand

In live (`ws`) runs, `S` (Space+S on the display) is the "what did I miss?"
command. A backlog that fits on the display just snaps. A bigger one is
sent to the speech server (`POST /summarize`) with a character budget that
scales with how much was missed; the display holds with `s` in cell 2 while
the summary is fetched, then the recap streams like ordinary text at the
reader's own settings, and live speech queues behind it. Pressing summarize
or jump-to-live again skips the rest of the recap, and any failure falls
back to the plain snap. `--no-summary` turns it off.

### Useful flags

| Flag             | Default | Meaning |
| ---------------- | ------- | ------- |
| `--source`       | `file`  | `file`, `stdin`, or `ws` |
| `--file`         | –       | path to replay (`-` = stdin) |
| `--url`          | `ws://localhost:8788/finalized` | WebSocket URL for `--source ws` |
| `--ws-once`      | off     | exit when the stream closes instead of reconnecting |
| `--translator`   | `auto`  | `auto`, `liblouis`, or `dev` |
| `--grade`        | `2`     | 2 contracted (liblouis only; falls back to 1), 1 uncontracted, or experimental English 3 |
| `--sink`         | `sim`   | `sim` (terminal) or `brlapi` (hardware) |
| `--width`        | `40`    | simulated display width in cells |
| `--pace`         | 5 s/page | ms per cell; by default sized to the display so a full page lasts 5 s (the JAWS auto-advance default), about 278 ms/cell on a 20-cell display |
| `--advance-mode` | `auto`  | `auto` (`ticker` is an alias) or `manual` |
| `--window`       | full display | cells per auto-mode refresh; the default flips whole pages, `1` is ticker-tape scrolling |
| `--char-delay`   | `0.05`  | seconds between replayed characters |
| `--no-gauge`     | off     | no backlog gauge or cell-2 marker |
| `--no-display-keys` | off  | ignore the display's own keys |
| `--no-summary`   | off     | disable summarize-on-demand (always snap) |
| `--space-time`   | `100`   | percent of the pace a space cell dwells (timing only; no cell is dropped) |
| `--punct-time`   | `100`   | percent of the pace a punctuation cell dwells |
| `--lowercase`    | off     | translate lowercased, so no cells go to capital signs (display only) |
| `--no-digits`    | off     | keep spoken numbers as words ("twenty five" instead of "25") |

Environment: `DOTIFY_LIBLOUIS_DIR` (location of a bundled liblouis on
Windows), `DOTIFY_SUMMARY_URL` (override the summarize endpoint),
`DOTIFY_BRLAPI_DLL` (location of BRLTTY's `brlapi.dll` on Windows).

## Connecting to the speech server

Start the speech server (`core/speech-to-text`, `node server.js`), then:

```bash
python run.py --source ws --url ws://localhost:8788/finalized
```

The engine consumes the server's segment stream: `soft` segments queue but
don't render, `revise` corrects a still-queued segment in place, and `final`
hardens it, so only text the server has committed reaches the reader. A
`latency` message sets the render gate (in eager mode soft text may stream
before its final). The engine answers each hardened segment with a `shown`
message naming the text it actually displayed, which the server uses for
the transcript. If the server restarts, the engine reconnects on its own.

With no speech at all, the demo feeder replays a real caption track through
the same path (see [demo_tracks/README.md](demo_tracks/README.md)), and
Space+T streams built-in demo text.

## Translation

The translator is **liblouis** with the UEB tables. Contracted UEB (grade 2)
is the default; `--grade 1` starts uncontracted, and `g` (Ctrl+G in Human
mode) cycles grades live. Grade 2 typically needs two to three times fewer
cells for the same text ("knowledge and the" is 18 cells in grade 1 and 6 in
grade 2), so the same pace reads proportionally faster. Grade 3 uses
liblouis's experimental English table.

liblouis is loaded from the `louis` Python module when available (Linux),
otherwise from a bundled `liblouis.dll` via ctypes (Windows). Without
either, the engine falls back to a small pure-Python grade-1 stand-in
(letters, capital and numeric indicators, common punctuation) and prints a
warning; it keeps the engine runnable and testable anywhere but is not a
substitute for liblouis.

### Real UEB on Linux / WSL

```bash
sudo apt update
sudo apt install python3-louis liblouis-bin
python3 run.py --file sample.txt          # auto-selects liblouis
```

## Real hardware on Linux / WSL

Verified on the NLS eReader (HumanWare, 20 cells) from Windows 11 with the
USB device passed to WSL2 by usbipd-win, a single BRLTTY running the
NoScreen driver, and the engine claiming the display globally via BrlAPI.

```bash
# Windows: give the USB device to WSL (after each plug-in or reboot):
usbipd attach --busid 1-1 --wsl
# WSL: start the display service (stops competing BRLTTYs, NoScreen driver):
sudo bash ../../Linux/hardware/start_display.sh
# WSL: run the engine (file replay, or live from the speech server):
sudo python3 run.py --file sample.txt --sink brlapi
sudo python3 run.py --source ws --sink brlapi
```

Setup for Linux and WSL is in
[Linux/GETTING-STARTED.md](../../Linux/GETTING-STARTED.md); the BRLTTY
pitfalls the start script guards against are described at the top of
`Linux/hardware/start_display.sh`. The cell width is read from the display
at runtime. On Windows the packaged app uses its own USB, serial and
Bluetooth display sinks instead
([Windows/docs/README-windows.md](../../Windows/docs/README-windows.md)).

## Tests

```bash
python -m pytest -q
```

The live liblouis tests run only where liblouis is available: the `louis`
module, or on Windows the DLL under `Windows/installer/vendor/liblouis`.

## Layout

```
braille_engine/
  cells.py          cell bitmask <-> Unicode braille
  assembler.py      buffers characters into word tokens
  translator.py     liblouis (module and Windows DLL) + the dev fallback
  filters.py        spoken numbers -> numerals
  engine.py         queue, revisable segments, pacer tick, page flips,
                    rolling buffer, panning, catch-up
  gauge.py          the backlog gauge (cell 1)
  controls.py       commands, terminal keys, AI/Human modes, catch-up
  display_keys.py   commands from the display's own keys
  reply.py          reply mode: typing braille on the display
  demo.py           demo text and timed caption replay
  captions.py       SRT/WebVTT parsing for caption replay
  summary.py        client for the speech server's /summarize
  idle_watchdog.py  asks the shell to stop transcription nobody is reading
  sources/          file/stdin source + the speech server's WebSocket
  sinks/            sink interface, simulated display, BrlAPI (+ Windows DLL)
run.py              CLI that wires it all together
demo_tracks/        public-domain timed caption tracks
docs/               the chord acknowledgement audit
```
