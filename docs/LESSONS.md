# What we learned

Notes from building Dotify, for anyone putting live text on a braille
display.

## Braille hardware on Windows

**You probably don't need a driver.** Most braille displays ship bound to
Windows' in-box `HidUsb` driver. BRLTTY on Windows can only reach them
through libusb, which needs WinUSB bound to the device, and every way of
getting there failed:

- BRLTTY's libusb0 filter driver is unsigned, and it's x64-only. Windows on
  ARM doesn't emulate kernel drivers.
- usbipd's force-bind stub isn't usable by libusb-1.0.
- Zadig can't be scripted. Selecting a device with synthetic window
  messages doesn't fill its internal device record, so it builds a generic
  package that binds nothing. It needs a real mouse click, which leaves
  out a blind user installing on their own.
- A hand-written WinUSB INF with a self-signed catalog is rejected by
  Windows 11, even with the certificate in `LocalMachine\Root`. It wants an
  attestation-signed catalog.

Talking to the device over plain HID worked straight away, using the same
report sequence NVDA's `brailliantB` driver uses. Dotify's Windows display
sinks are all user-mode HID or serial. The full log, with exact errors, is
in [Windows/docs/HARDWARE-FINDINGS.md](../Windows/docs/HARDWARE-FINDINGS.md).

Gotchas that cost us days:

- **usbipd's `Shared` state breaks native HID silently.** Descriptors and
  capabilities still read fine, but every live read and write fails with
  error 1167. `usbipd unbind` fixes it. The launcher checks for this.
- **The first open after a USB mode switch can be descriptor-only.** Two
  short shared open passes before the exclusive session wake it reliably.
- **HID instance paths change** after a replug or driver change. Enumerate
  fresh every time.
- **The display must be in its terminal/USB-terminal mode**, or I/O fails
  even though the handle opens.
- **The Brailliant BI 40X suspends itself off the USB bus when idle.** A
  reader who is caught up produces no writes, so the display falls asleep
  mid-conversation. Dotify doesn't send a keep-alive write
  ([KNOWN-ISSUES.md](../KNOWN-ISSUES.md)).

## How text should move under the fingers

**Append-only is non-negotiable.** A sighted reader can glance back at a
corrected word. A braille reader whose fingers have already passed it never
will, and a cell changing under a resting finger is disorienting. Dotify
never rewrites a cell that has been shown. Corrections only happen while
text is still in the queue, which is most of the time, because reading
braille is slower than speech.

**Use the queue as a correction window.** Speech engines revise their
hypotheses for a second or two. Instead of waiting for finals, Dotify sends
the stable part of each hypothesis early and revises it until the reader
reaches it (see [ARCHITECTURE.md](ARCHITECTURE.md)). Text arrives sooner and
the reader never feels a correction.

**"Stable" has to be measured per engine.** We replayed a 17.5-minute
recorded meeting through each engine and counted how often a word, once
frozen, differed from the engine's own final:

| Engine | Filter (depth, persist) | Regret rate |
|---|---|---|
| ElevenLabs Scribe v2 | 5, 2 | ~0.5% (no measurable WER cost) |
| AssemblyAI | 5, 2 | 0 of 90 |
| Deepgram Nova-3 | 5, 3 | 0 of 60 (persist 2 regrets 7–8% at any depth) |
| OpenAI realtime | 2, 1 | 0 (its deltas are append-only) |
| Nemotron (sherpa-onnx, offline) | 2, 1 | 0 (greedy transducer never takes a word back) |

A vendor's advertised stability bound isn't enough. ElevenLabs reports
"recompute at most 5 tokens", but in practice revisions reached 18 words
deep.

**Ticker tape or pages is a real trade-off, so offer both.** The original
design was a one-cell-at-a-time marquee. It has a real advantage: pages
force a right-to-left hand return at every flip, and a marquee never does.
The current default is word-wrapped pages that flip on a clock (5 s per
page, matching JAWS' auto-advance, so the timing is what screen-reader
users already know). The marquee is still there (`--window 1`), and manual
flipping too.

**The "choppy" feel wasn't the firmware.** The NLS eReader's pins seemed to
drop and re-rise on every scroll step. We tested it. The eReader moves
only the pins that differ, at reading speed, over our path. The roughness
came from the display's physical feel and from large catch-up jumps, not
from anything software could change.

**Tell the reader how far behind they are, without words.** Cell 1 is a
thermometer: blank when caught up, filling upward through four steps to a
solid block when more than ~100 words behind. It costs one cell and
replaces a lot of guessing.

**Every command must answer on the display.** We audited every chord for
"silent" outcomes: a command that did nothing, or failed, without telling
the reader. A screen reader announcing on the page doesn't help if nobody
is reading the page. See
[silent-chord-audit.md](../core/text-to-braille/docs/silent-chord-audit.md).

**Grade 2 matters.** Contracted UEB takes 2–3× fewer cells for the same
text, so the same pace reads proportionally faster. Translating word by
word means the reader can switch grades mid-stream without disturbing
cells already queued.

## Choosing a streaming speech engine

**Measure on far-field conversation, not on a headset.** The person who
matters is the one across the room, not the one wearing the mic. Engines
that are excellent on the wearer's own voice can drop a fainter
conversation partner almost entirely. We used the AMI meeting corpus
(ES2004a, CC-BY 4.0) with `tools/wer-replay.js`, replayed at 1×, because
vendor endpointing reacts to arrival timing.

**Our own latency trick was hiding the best engine.** A 2.5-second forced
commit kept latency predictable, but it cut phrases mid-word. It cost
AssemblyAI 1.5 WER points and ElevenLabs 6.4. With the backstop on, the two
engines looked tied. Without it, ElevenLabs was clearly better (15.9% vs
19.2%). Engines built around voice-activity segments should keep their own
turn boundaries.

**Offline is good enough to be the default.** NVIDIA's Nemotron 3.5
streaming model (0.6B, int8, through sherpa-onnx) scored 27.4% on our
five-meeting AMI set, against 38.1% for Vosk-small (ElevenLabs, online,
scored 19.2% on the same set). It ran at a real-time factor of 0.33 even on
a phone-class ARM processor. The Parakeet family was effectively deaf to
far-field speech, and Kaldi/Vosk topped out around 31%.

**Withhold silence from paid engines, but buffer the onset.** Most of a
conversation is silence. Gating it saves money, but re-opening a socket
loses the first syllable unless you keep a short pre-roll buffer and replay
it. Some vendors close idle streams on their own (ElevenLabs after about
15 s, regardless of pings), so close first, on your own terms.

**The last words before Stop were silently dropped.** At least one
realtime API transcribes a committed segment only once more audio follows
it. Always send a short tail of silence after the final commit.
