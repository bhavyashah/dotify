# Dotify for Windows: user guide

**Live speech on a refreshable braille display.**

Dotify turns spoken words into braille pages you read at your own pace. In
**auto mode** (the default) Dotify shows a new page every 5 seconds until you
change the timing. In **manual mode** you press a key for each next page. A
smaller cell window shows a few new cells per refresh instead of a whole
page, down to one cell at a time. Each of these lets you follow a
conversation, a lecture, or a meeting in real time. Speech is transcribed to
text, the text is translated to contracted (grade 2) UEB braille, and the
braille is sent to your display, all on your own computer. The offline
speech model needs **no account, no API key, and no internet** after a
one-time download.

The person speaking doesn't need to know braille. They can even glance at the
screen and see exactly where your fingers are, so they can pace themselves — or
type their words directly when the room is too loud to transcribe.

## Contents

1. [What you need](#1-what-you-need)
2. [Install and first launch](#2-install-and-first-launch)
3. [Prepare your braille display](#3-prepare-your-braille-display)
4. [The one-window interface](#4-the-one-window-interface)
5. [Your first session](#5-your-first-session)
6. [Three ways to control Dotify](#6-three-ways-to-control-dotify)
7. [Reading modes: manual and auto](#7-reading-modes-manual-and-auto)
8. [Speed and grade](#8-speed-and-grade)
9. [Catching up: "what did I miss?"](#9-catching-up-what-did-i-miss)
10. [The cell window](#10-the-cell-window)
11. [Pausing: braille output vs. transcription](#11-pausing-braille-output-vs-transcription)
12. [Human mode: typing instead of speaking](#12-human-mode-typing-instead-of-speaking)
13. [Speech models and optional API keys](#13-speech-models-and-optional-api-keys)
14. [Transcription responsiveness](#14-transcription-responsiveness)
15. [The personal dictionary](#15-the-personal-dictionary)
16. [Conversation mode and named speakers](#16-conversation-mode-and-named-speakers)
17. [The three-band transcript: where the reader is](#17-the-three-band-transcript-where-the-reader-is)
18. [Quitting Dotify](#18-quitting-dotify)
19. [Troubleshooting](#19-troubleshooting)
20. [More](#more)

---

## 1. What you need

- **Windows 11** (x64 or ARM64 — one installer covers both; on ARM64 the offline
  Nemotron model decodes natively and the rest of the app runs under Windows'
  transparent x64 emulation).
- **A supported refreshable braille display**, connected by **USB** or
  paired over **Bluetooth**. The HumanWare **NLS eReader** and
  **Brailliant BI 40X** have been used end to end over USB (the BI 40X can
  drop off the USB bus when idle; see
  [§19](#19-troubleshooting)). Other displays are listed in
  [README-windows.md](README-windows.md#display-support). Bluetooth support
  is implemented for the HumanWare/APH and HIMS families but has **not been
  tested with a physical display**; see
  [§3](#3-prepare-your-braille-display).
- **A microphone** (built-in or external — see
  [§1a](#1a-microphone-placement-small-moves-big-accuracy-gains) for where
  to put it).
- **Internet:** for the cloud speech models, and once for the optional
  offline model download (about 650 MB — see
  [§13a](#13a-the-offline-model)). After that download, offline
  transcription needs no internet at all.

You do **not** need WSL, BRLTTY, Zadig, a custom USB driver, a terminal, or an
API key.

## 1a. Microphone placement: small moves, big accuracy gains

Where the microphone sits matters more than which speech model you pick.
Three rules cover most situations:

- **Get the microphone close to whoever is speaking.** Accuracy falls off
  quickly with distance: within arm's reach of the speaker is good, the
  same table is workable, across the room is where every model starts
  guessing. If one person does most of the talking, a clip-on (lavalier)
  microphone on that person beats a better microphone placed farther away.
- **Closer to the voice than to the noise.** A fan, air conditioner, open
  window, or speaker playing music should always be farther from the
  microphone than the talking person's mouth is. If you can't move the
  noise, move the microphone.
- **Soft rooms beat echoey rooms.** Bare walls, glass, and empty rooms
  smear speech with echo; carpet, curtains, cushions, and people absorb
  it. In an echoey room, halving the distance to the speaker helps more
  than any setting in Dotify.

Practical notes:

- **Wired or USB beats Bluetooth for capture.** Many Bluetooth headsets
  drop their microphone to telephone quality while capturing, which
  measurably hurts transcription. When a USB microphone is attached,
  Dotify prefers it automatically and the status line says which
  microphone is live.
- **The laptop's built-in microphones are tuned for the person at the
  screen.** They are fine for one speaker sitting at the laptop; for a
  meeting, an external microphone near the middle of the table — or on
  the main speaker — does noticeably better.
- **Check the microphone level meter** on the main screen: if it barely
  moves while someone speaks, the microphone is too far away or Windows
  is capturing the wrong device (see
  [§19 Troubleshooting](#19-troubleshooting)).

## 2. Install and first launch

1. Download **`DotifySetup.exe`** from the
   [latest release](https://github.com/bhavyashah/dotify/releases/latest) and
   run it. The installer isn't code-signed, so Windows SmartScreen may show
   "Windows protected your PC" with only a **Don't run** button. Choose the
   **More info** link, then the **Run anyway** button that appears. Then
   approve the installation prompt.
2. Finish the wizard. You'll get a **Start Dotify** entry in the Start menu and
   a **Start Dotify** desktop shortcut.
3. Connect your braille display by USB and put it in its terminal /
   screen-reader mode (see [§3](#3-prepare-your-braille-display)).
4. Launch **Start Dotify**. One browser window opens containing everything —
   the live transcription and the braille controls. No terminal appears, and no
   key is asked for.

That's it. Dotify starts listening on launch; you don't press a "start" button
in the normal case.

Your personal data (API keys, dictionary, voiceprints, transcripts, logs) lives
in **`%LOCALAPPDATA%\Dotify`**, outside the installed program.

## 3. Prepare your braille display

Connect by USB and switch the display into the mode that lets an external
program drive its cells:

| Display | How to enter terminal mode |
|---|---|
| **NLS eReader** | **Braille Display › Connected devices › USB connection** |
| **BrailleNote Touch / Touch Plus** | Open **Braille Terminal**, select USB |
| **Brailliant BI 20X / 40X** | Select its USB terminal connection |
| **BrailleSense** | **Terminal for Screen Reader**, select USB *(most models are not supported yet; see [README-windows.md](README-windows.md#display-support))* |

**Bluetooth (implemented, not tested on hardware).** Pair the display in
**Windows Settings › Bluetooth & devices**, then on the display choose its
Bluetooth terminal connection (on HumanWare devices: **Braille Display ›
Connected devices › Bluetooth connection**). Dotify finds paired
HumanWare/APH and HIMS displays by their paired name. Caveats:

- **USB always wins.** Bluetooth is only consulted when no USB display is
  connected, and a session that started over USB never falls back to Bluetooth
  mid-run — restart Dotify to switch transports.
- The **display's own keys work over Bluetooth on HumanWare/APH** displays;
  HIMS Bluetooth is **output-only**.
- This code has not been tested with a physical display. It uses the same
  protocol as the USB serial path, but treat the first pairing as a test.

**Close any other program that owns the display** — NVDA, JAWS, BRLTTY, or
another braille app. Dotify speaks to the display directly, so it needs
exclusive use of it.

If Dotify starts while a screen reader still holds the display, it does **not**
crash. It waits and tells you how to free the display (spoken through that very
screen reader), then connects the moment the display is released — no relaunch.
In NVDA, set the braille display to **No braille**; you keep NVDA speech and
lose nothing.

## 4. The one-window interface

The launcher opens **one** browser window (Edge that ships with Windows 11;
Chrome is a fallback) in a dedicated app-mode window. Top to bottom:

- **Braille** (a bordered panel): the every-session buttons in two labeled
  rows — **Speed** (Slower, Faster, Catch up, Summarize) and **Reading** (Switch grade,
  Change reading mode, Pause braille) — plus a live readout of grade / mode /
  reading mode / reading speed / model (the cell window's value lives with
  its control under Settings › **Braille settings**). While no display is
  connected the
  panel's status states the fix in one sentence; the full technical reason
  (which transports were checked) waits under the collapsed **Connection
  details** disclosure.
- **Live transcription**: the Pause microphone button, a status line, a
  microphone level meter, and the **Finalized transcript** box.
- **The typing box** — "Type instead of speaking" — sits directly **under the
  transcript**, where a sighted partner's eyes naturally land. See
  [§12](#12-human-mode-typing-instead-of-speaking).
- **Settings**, one collapsed disclosure at the bottom, opens to four
  category buttons. Choose a category to open its settings; **Back to settings
  categories** returns to that short list. **Transcription** contains the model
  picker, offline-model download, personal dictionary, conversation mode, and
  named speakers. **API keys** holds the optional cloud provider keys (see
  [§13](#13-speech-models-and-optional-api-keys)). **Braille** contains the
  **Cells per refresh** box, the **Space time** and **Punctuation time** dials,
  and the **Lowercase braille** switch — see
  [§10a](#10a-reading-density-keeping-up-with-fast-speech). **Help and tools**
  contains the keyboard-shortcut reference and the play-text and caption-file
  controls. The main Settings page therefore stays categories-only.
- **Quit Dotify**, the very last control on the page (`Alt+Shift+Q`). Closing
  the window quits too: a few seconds after the window is gone, the whole
  appliance shuts itself down. A refresh of the page does **not** quit.

Everything is reachable by mouse, by keyboard shortcut, and — for the reading
controls — from the display's own keys. Every control has a tooltip and an
`aria-keyshortcuts` hint.

## 5. Your first session

1. Make sure your display is connected and in terminal mode
   ([§3](#3-prepare-your-braille-display)).
2. Launch Dotify. The braille controls panel says *Connecting to braille
   display…* then *Connected to …* once your display is found.
3. With no API keys saved, transcription starts keyless on **Nemotron
   (offline)** once you've downloaded the offline model
   ([§13a](#13a-the-offline-model)); until then the page tells you to
   download it. (If you've saved keys, Dotify starts on the
   best configured model instead — ElevenLabs first; see
   [§13](#13-speech-models-and-optional-api-keys).)
4. Speak. When the browser asks, **allow microphone access** for `localhost`.
5. Watch the meter move and finalized text appear a couple of seconds after
   you speak.
6. Feel contracted grade-2 braille arrive on your display as word-wrapped
   pages that flip every 5 seconds.

If the words come faster than you read, they queue — you're never skipped past.
[Catch up](#9-catching-up-what-did-i-miss) whenever you want.

## 6. Three ways to control Dotify

Every reading control is available three ways. Use whichever is in reach — you
don't need a laptop keyboard near the display.

### a) On-screen buttons

Slower · Faster · Switch grade · Change reading mode · Catch up · Summarize · Pause braille ·
Quit Dotify — plus, under Settings: the **Play text on braille display**
button with its **Text to play** picker, the **Play caption file** button
with its caption-file picker, and the **Braille settings** section
(Cells per refresh, Space time, Punctuation time, Lowercase braille) — and
the **typing box** under the transcript.

### b) Keyboard shortcuts (work from anywhere on the page)

| Shortcut | Action |
|---|---|
| `Alt+Shift+S` / `Alt+Shift+F` | Slower / Faster braille |
| `Alt+Shift+G` | Switch braille grade (2 → 3 → 1) |
| `Alt+Shift+R` | Change reading mode (manual / auto) |
| `Alt+Shift+L` | Catch up: snap straight to live (no summary) |
| `Alt+Shift+U` | Summarize what you missed; press again to cancel the fetch or skip the recap |
| `Alt+Shift+P` | Pause or resume **braille** output |
| `Alt+Shift+D` | Start or stop the demo stream (preset text, no microphone) |
| `Alt+Shift+V` | Play or stop a caption file (SRT or WebVTT) on the braille display |
| `Alt+Shift+T` | Focus the typing box (enters Human mode) |
| `Alt+Shift+Q` | Quit Dotify |
| `Alt+Shift+M` | Pause or resume **transcription** (mic off, nothing billed) |
| `Alt+Shift+X` | Focus the finalized transcript |
| `Escape` *(while in the typing box)* | Back to AI mode |

There is deliberately **no start/stop-transcription shortcut**: Dotify starts
listening on launch, a model change restarts it seamlessly, and **pause**
(`Alt+Shift+M`) covers "stop billing." A visible **Start transcription** button
appears on its own only when transcription is genuinely stopped — a failed
start or a denied microphone — and that button is your recovery path.

### c) The braille display's own keys

The display mirrors the panel:

| Display key / chord | Action |
|---|---|
| Thumb **Left** / **Right** | Pan back / forward through what was already shown — by the cell window when auto mode streams cells, by a full page in manual and at the full-display window. While you're back in history the display freezes (speech keeps queueing) and the gauge in cell 1 shows how far back you are; Right at the live edge fast-forwards: in auto each press pulls one extra window of backlog onto the display ahead of the pace (in manual it flips the next page), and Thumb Next always jumps you home |
| Thumb **Previous** (outer left) | Cycle reading mode |
| Thumb **Next** (outer right) | Catch up: snap straight to live (mid-recap it skips the rest of the summary) |
| **Space + dot 1** / **dot 4** | Slower / Faster |
| **Space + dot 2** / **dot 5** | Unassigned |
| **Space + dot 3** | Pause / resume braille |
| **Space + dot 6** | Pause / resume transcription (the microphone) |
| **Space + G** (dots 1-2-4-5) | Cycle the grade: 1 (uncontracted) → 2 (contracted) → 3 (experimental) |
| **Space + W** (dots 2-4-5-6) | Cycle cell window *(auto mode only)* |
| **Space + S** (dots 2-3-4) | Summarize what you missed (press again to cancel / skip) |
| **Space + I** (dots 2-4) | Status flash (mode, window, speed, input source, grade) |
| **Space + T** (dots 2-3-4-5) | Start / stop the demo stream (preset text) |
| **Space + R** (dots 1-2-3-5) | Enter or leave Reply mode ([§12a](#12a-reply-mode)) |

Chords accumulate while held and fire once when you release all keys. The
eReader's **Home** button is reserved by the device itself (your
always-available escape back to its menu), so there is **no quit on the
display** — quitting Dotify is a panel/keyboard action. The
transcription-pause chord (Space+dot-6) acts in the browser page and
confirms back on the display
with a short flash ("mic off", "mic on"), because that's where the
microphone lives. Choosing a speech model is done in Settings.

**Play text on display** (Space+T, Settings › **Play text on braille
display**, or `Alt+Shift+D`): streams text to the display exactly the way
live speech streams — words in small groups, sentence by sentence — with
no microphone, key, or internet needed. The **Text
to play** picker next to the button chooses what plays: the built-in **Short
sample**, a **News article** (The New York Times on Mark Twain, 1910), a
**Novel** (Alice's Adventures in Wonderland), or a **Text file from this
computer** (a plain `.txt` file up to 1 MB — picking that source reveals a
file chooser). Use it to show Dotify to someone, check a display, practice
the reading controls, or just read something. It answers with a "demo on"
flash; the same button stops it ("demo off") and withdraws whatever hasn't
reached your fingers yet. Space+T always plays the short sample; the
picker is a panel feature.

**Play caption file** (Settings › **Play caption file**, or `Alt+Shift+V`):
the same idea, but for a real captioned event. Choose a caption file in SRT
or WebVTT format (up to 1 MB) with the picker next to the button, and the
track replays on the display with the timing the captions actually had:
bursts when the speaker was quick, silence where the room paused. That makes
it a realistic way to feel what a captioned talk is like in braille — the
backlog gauge fills and the catch-up controls matter, exactly as in a live
session. While anything is playing, this button and Play text both read
**Stop playing text**, and either one stops the stream and withdraws
whatever hasn't reached your fingers yet. A file with no readable captions
in it is refused with a message; nothing plays.

## 7. Reading modes: manual and auto

Toggle with the **Change reading mode** button, `Alt+Shift+R`, or **Thumb Previous**.
Each switch briefly flashes the new mode on the display so you can confirm it
by touch.

- **Auto (default).** Paced reading: Dotify flips to the next page on a
  steady clock, so live captions continue without a press. By default every
  refresh is a whole word-wrapped **page**: read the line, wait for the
  next page, the way a notetaker user pans a document.
  Faster/Slower changes the pace; the panel shows it as an estimated
  **words per minute**. The [cell window](#10-the-cell-window) can shrink a
  refresh from the full-display page down to a stream of a few cells —
  or one, the classic ticker-tape crawl.

Missed a line? **Thumb Left** pans back through what was already shown —
by the cell window when auto mode streams cells, by a whole page in manual
and at auto's full-display window (pages replay word-aligned, exactly as you
read them). While you are back in history the stream freezes and the
gauge in cell 1 rises to show how far back you are; speech keeps queueing,
so nothing is lost. **Thumb Right** walks you forward again, and **Thumb Next** snaps
straight home to live from any depth.

In **manual** — and in auto at the full-display window — every flip repaints
the *whole* display as a clean, left-aligned page. Words never split across a
page unless a single word is wider than the entire display. The cell window
and per-cell dwell do not apply in manual mode — their rows under **Braille
settings** hide (the Lowercase switch stays: it applies in every mode).

## 8. Speed and grade

- **Speed** — **Slower** / **Faster** (`Alt+Shift+S` / `Alt+Shift+F`, or
  Space+dot-1 / Space+dot-4). What it changes depends on the reading mode. In auto at the
  full-display window (the default), the pace *is* how long each page is
  shown: 5 seconds on a fresh install (the same auto-advance default as
  JAWS), and each press changes it by **exactly half a second per page**,
  from 1 second up to 30, with the new duration flashed on the display
  ("4.5s per page") so every press is felt. At the smaller cell windows the keys step the streaming
  pace proportionally instead (a step feels the same at any speed), and the
  press flashes the resulting rate as "about N wpm". In manual mode the display
  changes only when you ask: Faster flips the next page, and Slower answers
  "manual, no pace". The panel's **Reading speed** readout always shows the
  current meaning; in auto it reads "about N words per minute". That number
  is the **reader's** display pace, not how fast anyone is talking — a
  speaker who wants the reader to keep up live can aim near it. It is an
  estimate, measured from the words that actually streamed recently — real
  contractions, capital indicators, punctuation, and the space/punctuation
  time discounts all priced exactly as the pacer times them (before enough
  text has streamed it falls back to a grade-aware average).
- **Grade** — **Switch grade** (`Alt+Shift+G`, or Space+G).
  Dotify starts in **grade 2 (contracted UEB)** — the presentation fluent
  readers actually read. The switch cycles 2 → 3 → 1: grade 3 is liblouis'
  experimental English grade 3, and grade 1 is uncontracted. The panel's
  Grade readout shows which is active. Switching is safe mid-stream:
  cells already shown or queued never change; only words translated *after* the
  switch change form. Grade 2 packs 2–3× more text into the same cells, so at
  the same pace it reads proportionally faster.

## 9. Catching up: "what did I miss?"

Two commands, split so the common case costs one press:

- **Catch up** (`Alt+Shift+L`, or **Thumb Next**) **snaps** you instantly to
  the newest cells — always, however far behind you are. No summary, no
  waiting. Nothing is lost by the snap itself: committed text is captured
  and in-flight speech survives it.
- **Summarize** (`Alt+Shift+U`, or **Space+S**, dots 2-3-4) asks for a
  **summary of what you missed**, sized to the backlog — roughly one summary
  character per five characters of missed speech, up to a cap of 700 — so a
  long absence gets a real recap, not a one-line squeeze. (A backlog that
  already fits on the display just snaps: there is nothing worth
  summarizing.) The display holds still for a second or two while the
  summary is fetched, then the recap **streams like ordinary text**, at your
  own reading settings (auto pace, or full-window/manual page flips, in your
  current grade). While any recap text is under your fingers, **cell 2 shows an `s`**
  so you always know you're reading a summary, not live speech. New speech
  queues behind the recap and streams the moment it drains. **Press
  Summarize again** to cancel the fetch or skip the rest of the recap —
  the panel button reads **Cancel summary** while fetching and **Skip
  recap** while it streams — and **Catch up mid-recap also snaps you
  straight to live** (the panel button reads **Go live now** then), so
  each press's label matches what that press does.

The summary uses your OpenAI key if you've configured one; **without a key it
falls back to a built-in local summary** — the most important words from what
you missed, in spoken order, computed on your own machine with no network and
no cost. Any failure falls back to the plain snap automatically. Summaries
are written in lowercase except names and acronyms, where a capital is
worth its indicator cell.

**The two status cells.** Cell 1 of the display is a backlog "thermometer" that
fills upward as your backlog grows; cell 2 names the **source** of the text
under your fingers; the rest is your text.

Cell 1 — how far behind:

| How far behind | Cell-1 pattern | Feels like |
|---|---|---|
| Caught up (≤ ~5 words) | blank | nothing |
| ~a sentence (6–15) | dots 7-8 | low ridge |
| ~a paragraph (16–40) | dots 3-6-7-8 | half full |
| Far behind (41–100) | dots 2-3-5-6-7-8 | nearly full |
| Very far (100+) | all 8 dots | solid block |

Cell 2 — what you're reading:

| Marker | Pattern | Meaning |
|---|---|---|
| `s` | dots 2-3-4 | An on-demand **summary** is streaming (also shown while the summary is being fetched) |
| `h` | dots 1-2-5 | **Human-typed** text ([§12](#12-human-mode-typing-instead-of-speaking)) |
| `r` | dots 1-2-3-5 | Your own typing in Reply mode ([§12a](#12a-reply-mode)) |
| blank | — | Live speech or captions, or an empty display |

When a wide display briefly holds a mix (one source draining into another),
the marker reports the higher-priority source: `s` over `h`.

One brush of a finger tells you where you stand — no counting. Jumping to live
drains the gauge to blank in the same instant, so you can *feel* that the
command worked; summary words don't count as backlog, so the gauge keeps
measuring your real distance behind live even mid-recap.

## 10. The cell window

By default auto mode refreshes the **full display** — every refresh is a
whole word-wrapped page: the display fills, holds while you read it, and
flips. How long a page is shown follows from the pace setting (a page's
worth of cells at your per-cell pace), so Faster/Slower keeps meaning what
it always means, and a page with less on it is replaced sooner.

If you prefer motion under your fingers, shrink the **window** — how many
new cells arrive per refresh. Set any whole number in **Cells per refresh**
(under Settings › **Braille settings**), up to the display's content width
(its cell count minus the two status cells), or cycle 1 / 2 / 4 / 6 / 8 /
**full display** with **Space + W** on the display. At **1** the text
crawls one cell at a time, like a ticker tape.

Bigger window = chunkier refreshes with a proportionally longer pause between
them; your reading speed is unchanged. A refresh never ends
in the middle of a braille sign — a two-cell contraction that wouldn't fit
whole waits and leads the next refresh.

The window applies to **auto mode only**. Manual always flips the full
display, so the window control hides there and Space+W answers "window:
auto only."

## 10a. Reading density: keeping up with fast speech

People speak faster than most fingers read. Four settings
buy back time **without dropping a single word** — the transcript stays raw;
only how long each cell dwells, or how it is written, changes:

- **Space time** (Settings › Braille settings): the percent of the normal
  pace a **space cell** dwells. At 100% a space costs a full refresh like any
  letter; at 25% the word gap flashes past in a quarter of the time. On real
  meeting transcripts about one cell in five is a space, so 25% here alone
  reads about 18% faster at the same finger pace.
- **Punctuation time**: the same dial for punctuation cells (commas, periods,
  quotes — question and exclamation marks too). Nothing is stripped; the
  marks just linger less. Worth about another 5%.
- **Lowercase braille, no capital signs**: translates everything lowercased
  so no cell is spent on capital indicators. Off by default; the on-screen
  transcript keeps its capitals either way. About 4%.
- **Numbers as digits** (always on): spoken numbers arrive as numerals —
  "twenty five" reads as `25`, "nineteen eighty four" as `1984`. "One" alone
  stays a word (it is usually a pronoun), and "million" and up stay words
  because `5 million` is shorter than `5000000` in braille. Turn it off with
  the `--no-digits` engine flag if you ever want number words verbatim.

All four together at the settings above read roughly **27% faster at the same
finger pace** (measured on four hours of AMI meeting recordings) — a 22 words
per minute pace reads like 30. The space and punctuation dials apply in
auto mode, full-display pages included (manual has no pace at all);
lowercase applies everywhere.

## 11. Pausing: braille output vs. transcription

Two different pauses, for two different reasons:

- **Pause braille** (`Alt+Shift+P`, Space+dot-3, or the **Pause braille** button)
  freezes the *display*. Speech keeps being transcribed and **keeps queueing** —
  nothing is lost. Resume and it picks up where it stopped. Use this to stop
  reading for a moment without missing anything.
- **Pause transcription** (`Alt+Shift+M`, or Space+dot-6) stops the text
  coming *in*: the pause turns the **microphone off** and closes the speech
  session — **guaranteed zero usage/billing**. Speech during the pause is
  simply not transcribed. Resuming costs about a second to reconnect. Use
  this to protect a paid model's quota when you don't need transcription.

Transcription also **auto-pauses** while you're typing in
[Human mode](#12-human-mode-typing-instead-of-speaking) (speech is discarded
there anyway, so there's no reason to burn quota), and auto-resumes when you go
back to AI mode. A pause **you** set by hand is sticky — Dotify won't silently
resume it.

## 12. Human mode: typing instead of speaking

Speech models get names, addresses, and spellings wrong — and some rooms are
too loud to transcribe at all. **Human mode** is a manual channel straight to
the braille display: a hearing partner types, the braille reader reads.

Dotify has two input modes, and the display itself tells you which is active:
**AI mode** (speech is transcribed; cell 2 is blank) and **Human mode**
(a person types; `h` in cell 2).

- **Enter it** by focusing the **Type instead of speaking** box directly under
  the transcript (`Alt+Shift+T`, or click/tab into it). While you type there,
  Dotify is in Human mode: the **microphone auto-pauses** (nothing is captured
  or billed) and live speech is discarded. A status line right beside the box
  says so plainly.
- **Type or paste.** Each word streams to the display **the moment you finish
  it with a space**; if you pause a few seconds mid-word, that last word
  streams too. There's **no Send button**. Typed words also **echo into the
  transcript above** as they're delivered, so the typist sees exactly what the
  reader got — and the session transcript records them.
- **No editing by design.** Backspace changes the input box but **cannot recall
  braille that was already shown** — a typo just scrolls out, exactly as spoken
  text would.
- **Leave it** with the **Back to AI mode** button (it appears beside the box
  only while you are in Human mode), `Escape`, or by
  focusing anything else. A half-typed word is committed first, and the
  microphone resumes listening, unless you had paused transcription manually,
  in which case it stays paused until you press Resume.

Briefly tabbing through the box (a screen reader walking the page) does *not*
flip modes — only settling in it, or typing, does.

## 12a. Reply mode

To answer aloud from the braille display, press **Space+R** (space with dots
1, 2, 3, and 5). Reply mode freezes caption movement while you type on the
display's Perkins keys and marks the typing echo with `r` in cell 2. Space
ends a word, dot 7 erases a cell, and dot 8 speaks the sentence immediately.
Press **Space+R again** to leave Reply mode and resume captions where you
stopped. Reply mode is built in; there is no setting or command-line switch
to enable it.

## 13. Speech models and optional API keys

Choose a model in the **Transcription model** picker (inside **Settings** ›
**Transcription**, reachable by normal tabbing). On the appliance, switching
models restarts transcription seamlessly — no stop needed.

| Model | Needs a key? | Notes |
|---|---|---|
| **Nemotron (offline)** | No | Runs entirely on your computer with the downloaded Nemotron model ([§13a](#13a-the-offline-model)) — no account, no internet, no cost, and your audio never leaves the machine. Pinnable online or not; before the download it answers with the download instruction. |
| **ElevenLabs Scribe v2 Realtime** (`scribe_v2_realtime`) | `ELEVENLABS_API_KEY` | **Default keyed model** — won the measured accuracy comparison (15.9% word-error rate vs AssemblyAI's 19.2% on recorded meeting audio). Streams eagerly: words reach the display as they're heard. |
| **AssemblyAI** (`universal-streaming-english`) | `ASSEMBLYAI_API_KEY` | Cloud; the only model with **speaker diarization** (see [§16](#16-conversation-mode-and-named-speakers)). |
| **Deepgram** (`nova-3`) | `DEEPGRAM_API_KEY` | Cloud. |
| **OpenAI** (`gpt-live-transcribe`) | `OPENAI_API_KEY` | Cloud. The same key also powers the better jump-to-live summaries ([§9](#9-catching-up-what-did-i-miss)). |

**Which model starts?** Dotify starts listening on launch. With keys saved,
it starts on ElevenLabs, then AssemblyAI, then OpenAI — the first one
configured. With no keys, Nemotron starts if its model is downloaded;
otherwise the page says to download it. Remember that a keyed model bills
from the moment it starts; `Alt+Shift+M` pauses it.

**If the internet drops mid-session**, captions do not silently stop: Dotify
retries your model briefly, then switches itself to Nemotron offline. With
the model downloaded you get offline captions (an "offline captions" flash
on the braille display); without it, captions stop and Dotify says so
(so download the model if you want this fallback). Either way it returns to
your chosen model automatically — with a "back online" flash — once the
connection is back.
Your model choice is never forgotten; picking a model yourself during the
outage turns the automatic switching off.

## 13a. The offline model

Nemotron (offline) is powered by NVIDIA's **Nemotron 3.5 streaming model**,
the most accurate offline model tested for Dotify (27.4% word-error rate on
recorded meeting audio, against 38.1% for a much smaller offline model).
It is about **650 MB**, so it is not part of the installer: it's a one-time
download you choose to make.

- **Download it** in **Settings › Transcription › Offline model ›
  Download**. Progress is
  announced in whole steps, and the braille display flashes *offline model
  ready* when it completes. You can keep transcribing on any model while it
  downloads.
- **Interrupted?** The download **resumes where it stopped** — press
  Download again. Cancel any time.
- **Where it lives:** `%LOCALAPPDATA%\Dotify\models` — with your data, not
  the program. Upgrading or reinstalling Dotify never re-downloads it.
- **Deleting:** Settings › Transcription › Offline model › Delete frees the disk space
  (Nemotron and the internet-loss rescue then stop working until it's
  downloaded again).
- The first offline start after a launch loads the model for a few seconds —
  the status line says so. Nothing you say in that gap is lost; it's
  transcribed as soon as the model is up.

**Adding keys.** Open **Settings › API keys**, paste a key, and save — no
restart. A keyed model stays disabled
in the picker until its key is set. The page only ever tells you *whether* a
provider is configured; it never shows a saved key back. Keys are stored in
`%LOCALAPPDATA%\Dotify`, outside the install folder, and are never packaged or
transmitted anywhere except to that provider. The Start-menu **Configure
Optional API Keys** shortcut is a console alternative (it covers the AssemblyAI
and OpenAI keys; the page covers all four).

English only, for now: the app pins recognition to English on every model
(auto-detect occasionally mistranscribed English into other scripts).

## 14. Transcription responsiveness

Dotify uses one measured, recommended responsiveness policy automatically; it
does not ask you to tune a speed-versus-certainty setting. For engines whose
partial words were verified stable, words can enter the braille queue once
they hold steady across the model's latest guesses—usually within about a
second. An engine without a verified stable stream waits for confirmed text.

ElevenLabs uses its safest measured variation of this policy: its filtered
stable prefix arrives eagerly while volatile tail words stay hidden, and the
provider decides natural phrase endings. OpenAI uses its medium context and
latency setting. The thresholds are per model (`SOFT_STABILITY` in
`core/speech-to-text/server.js`); there is no user-facing control for them.

## 15. The personal dictionary

Everyone has words that are common *for them* but rare in general English —
friends' names, organizations, jargon. Models mis-hear exactly these.
**Settings › Transcription › Personal dictionary** lets you teach Dotify yours (up to 100
entries):

- **Add a word or name.** From the next time transcription starts, the keyed
  models are told to expect it (each model's native vocabulary boosting).
  Keep the list lean — boosting has a measured cost on everything else, so
  twenty well-chosen names beat a hundred maybes.
- **"Always replace" (optional).** If a model consistently prints something
  wrong for your word ("dot if I" for "Dotify"), list the mis-hearing here.
  This is an **unconditional find-and-replace**: every
  exact occurrence is rewritten to your word, everywhere in the transcript,
  starting immediately. If the mis-hearing you list is plausible everyday
  English, Dotify warns you when you add it, because it *will* rewrite correct
  speech too.
- Corrections apply before any text reaches the display, the transcript box, or
  the transcript file — all surfaces always agree.

The dictionary is stored beside your API keys in `%LOCALAPPDATA%\Dotify`,
survives reinstalls, and never leaves your computer.

## 16. Conversation mode and named speakers

*(AssemblyAI only, experimental — in **Settings › Transcription › Conversation mode**.)* In a
multi-person conversation, turn on **conversation mode** to prefix each
speaker's turns. By default you get anonymous labels — `A:`, `B:` — printed
only when the speaker *changes* (to save braille cells). Text reaches the
display in larger, less frequent chunks while this is on — the trade for
telling speakers apart accurately.

**Named speakers.** Enroll a person's voice once (~20 seconds of them talking)
in the **Named speakers** panel, and their turns print their **real name**
(`Alice:`) instead of `A:`. This runs entirely locally — a small on-device
voice-print service, no cloud speaker ID. It names a speaker only once it's
confident (at least ~3 seconds of clear speech and a clear best match).
Without it, sessions simply keep the anonymous labels.

Because braille is append-only, Dotify decides a speaker label *before* it
prints the text and holds a suspected speaker change until the next turn
confirms it, so a one-turn flicker never reaches your fingers.

## 17. The three-band transcript: where the reader is

The transcript is three bands that read as one flowing text, in reading
order:

- **History** (the box): everything the reader has already read.
- **Current** (the yellow band right below it): the exact words whose braille
  cells are **under the reader's fingers right now**, tracked frame by frame
  (a quarter-second poll, so it moves with the display, not with the page's
  slower status refresh). When the reader requests a summary the
  band shows **"Recap: …"**; a transient display announcement shows as
  **"Announcement: …"**; while the reader types on the display's own keys it
  says so.
- **Pending** (the subdued gray band below that): the head of the text the
  display **still owes the reader** — corrections applied, not-yet-final
  words included. Empty means the reader is at the live edge; when it grows,
  slow down.

A sighted speaker glances at the bands, sees where the reader actually is
and what's still coming, and paces themselves — no need to know braille. A
sighted tester watching them sees the same reading experience the deafblind
reader gets — same words, same timing — with one deliberate difference:
print reads faster than braille, so judge speed by the moving bands, not by
your own reading pace.

**Text sources are labeled and colored.** Ordinary transcribed speech is
plain text. The exceptions carry a prefix and a color, the same attribution
the braille reader feels in the display's marker cell: **Recap:** (purple) —
an on-demand summary, moved into history once the reader is back live;
**Human:** (green) — text a partner typed instead of
speaking. The prefix tells a screen reader what the color tells a sighted
user, so nothing relies on color alone.

**Screen readers:** none of the bands is a live region, so their updates
never interrupt anyone; each band reads normally when navigated to, with
its name announced ("On the braille display", "Pending for the braille
display").

## 18. Quitting Dotify

**Quit Dotify** (`Alt+Shift+Q`, or the **Quit Dotify** button) shuts down the
background speech server and braille ticker and **closes Dotify's own window**.
The launcher opens Dotify in a dedicated app-mode window with no other tabs, so
nothing else you had open is at risk. If a browser refuses to close the window
(for example a default-browser fallback), you'll see a clear notice telling you
to close the window and re-launch.

To start again, launch **Start Dotify** from the Start menu or desktop
shortcut.

## 19. Troubleshooting

| Symptom | Fix |
|---|---|
| **No supported display found** | Put the display in USB terminal mode, reconnect, relaunch. |
| **"Waiting for the braille display…"** on the panel | Another app (NVDA/JAWS/BRLTTY) holds the display. Free it (in NVDA, braille display → **No braille**); Dotify connects automatically — no relaunch. |
| **Access denied / ownership error** | Close NVDA, JAWS, BRLTTY, or any braille app using the display. |
| **More than one display found** | Launch with a chosen profile, e.g. `launcher.ps1 -Display brailliant-bi-20x`. |
| **Display goes silent mid-session (Brailliant BI 40X)** | The BI 40X can suspend itself off the USB bus when idle; press a key on the display to wake it. If braille doesn't resume, relaunch: Dotify does not yet reliably reconnect after this. |
| **Braille falls back to grade 1** | Reinstall — the bundled `vendor\liblouis` is missing or damaged. |
| **Microphone meter stays at zero** | Allow microphone access for `localhost` and confirm the right Windows input device is selected. Capture errors show in the status text. |
| **Nemotron says "not downloaded"** | **Settings › Transcription › Offline model › Download** — an interrupted download resumes where it stopped. |
| **Offline speech didn't start** | Check `%LOCALAPPDATA%\Dotify\logs\speech-error.log`; keyed models remain available. If the model download was interrupted, finish it in Settings › Transcription › Offline model. |
| **"A leftover Dotify session probably still holds port 8788/8790"** | An earlier session didn't fully exit. Close any Dotify windows, wait a few seconds, and relaunch. |
| **Braille controls "not responding"** | The display may be reconnecting. If it persists, close the window and relaunch. |

Startup failures also appear in a normal Windows message box, and diagnostic
logs are kept in `%LOCALAPPDATA%\Dotify\logs`.

## More

- [README-windows.md](README-windows.md): installing, the full display
  support table, diagnostics, and building the installer.
- [SECURITY.md](../../SECURITY.md): what leaves your computer and what is
  stored on it.
- [docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md): how the pieces fit
  together.
