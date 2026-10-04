# Dotify for Windows: user guide

Dotify is a communication aid for deafblind people who read braille. It
transcribes speech in the room and streams the words to a refreshable braille
display. You set the pace and use the display's own keys to slow down, pause,
pan back, jump to the most recent speech, or get a short summary of what you
missed.

The person speaking doesn't need to know braille. The Dotify window shows them
which words are under your fingers, so they can pace themselves. When the room
is too loud, they can type instead.

> **Dotify is not a replacement for professional human captioning (CART) or
> interpreting.** Speech recognition makes mistakes, especially with names,
> accents, crosstalk, and background noise. For medical, legal, educational,
> and other important conversations, use a professional captioner or
> interpreter.

## Contents

1. [What you need](#1-what-you-need)
2. [Install and first launch](#2-install-and-first-launch)
3. [Prepare your braille display](#3-prepare-your-braille-display)
4. [Place the microphone](#4-place-the-microphone)
5. [The Dotify window](#5-the-dotify-window)
6. [Your first session](#6-your-first-session)
7. [Controls: buttons, keyboard, and display keys](#7-controls-buttons-keyboard-and-display-keys)
8. [Reading modes](#8-reading-modes)
9. [Speed and grade](#9-speed-and-grade)
10. [Catching up and summaries](#10-catching-up-and-summaries)
11. [The two status cells](#11-the-two-status-cells)
12. [Cells per refresh](#12-cells-per-refresh)
13. [Reading faster](#13-reading-faster)
14. [Pausing](#14-pausing)
15. [Typing instead of speaking](#15-typing-instead-of-speaking)
16. [Replying from the braille display](#16-replying-from-the-braille-display)
17. [Playing text and caption files](#17-playing-text-and-caption-files)
18. [Speech models and API keys](#18-speech-models-and-api-keys)
19. [The offline model](#19-the-offline-model)
20. [Personal dictionary](#20-personal-dictionary)
21. [Conversation mode and named speakers](#21-conversation-mode-and-named-speakers)
22. [Following along on screen](#22-following-along-on-screen)
23. [Quitting Dotify](#23-quitting-dotify)
24. [Troubleshooting](#24-troubleshooting)

---

## 1. What you need

- **Windows 11.** The same installer works on x64 and ARM64 computers.
- **A refreshable braille display**, connected by USB or paired over
  Bluetooth. Dotify works with HumanWare displays (NLS eReader, Brailliant,
  BrailleNote Touch), APH Mantis Q40 and Chameleon 20, HIMS Braille Edge, and
  displays that follow the USB HID braille standard. The NLS eReader and
  Brailliant BI 40X have been tested the most. See the
  [display list](README-windows.md#display-support) for details.
- **A microphone.** The computer's built-in microphone works. An external
  microphone near the speaker works better (see
  [section 4](#4-place-the-microphone)).
- **Internet**, for the cloud speech models and for a one-time download of
  the offline model (about 650 MB). After that download, Dotify can
  transcribe with no internet at all.

You don't need a screen reader, a special driver, or an API key.

## 2. Install and first launch

1. Download **DotifySetup.exe** from the
   [latest release](https://github.com/bhavyashah/dotify/releases/latest) and
   run it. The installer isn't code-signed, so Windows SmartScreen may say
   "Windows protected your PC" and show only a **Don't run** button. Choose
   the **More info** link, then the **Run anyway** button that appears.
   Windows then asks for permission to install. Dotify installs for everyone
   who uses the computer.
2. Finish the setup wizard. It adds **Start Dotify** to the Start menu and
   the desktop.
3. Connect your braille display and put it in terminal mode (see
   [section 3](#3-prepare-your-braille-display)).
4. Open **Start Dotify**. The Dotify window opens and starts listening.

Your settings, API keys, personal dictionary, voice samples, transcript,
offline model, and logs are kept in **%LOCALAPPDATA%\Dotify**. Reinstalling or
upgrading Dotify keeps them.

## 3. Prepare your braille display

Connect the display by USB and switch it to the mode that lets a computer
program write to its cells:

| Display | How to enter terminal mode |
|---|---|
| **NLS eReader** | **Braille Display › Connected devices › USB connection** |
| **BrailleNote Touch / Touch Plus** | Open **Braille Terminal** and choose USB |
| **Brailliant BI 20X / 40X** | Choose the USB terminal connection |

**Turn off your screen reader's braille.** Dotify needs the display to
itself. Your screen reader keeps speaking.

| Screen reader | How to turn off its braille |
|---|---|
| **NVDA** | NVDA menu › Preferences › Settings › Braille: set the braille display to **No braille** |
| **JAWS** | Settings Center: set the braille display to **No Display** |
| **Narrator** | Turn off braille in Narrator settings |

Close any other braille app as well. If a screen reader is still using the
display when Dotify starts, Dotify tells you how to free it and connects as
soon as the display is free. When you finish with Dotify, turn your screen
reader's braille back on.

**Bluetooth.** Pair the display in **Windows Settings › Bluetooth & devices**,
then choose its Bluetooth terminal connection on the display (on HumanWare
displays: **Braille Display › Connected devices › Bluetooth connection**).
Dotify finds paired HumanWare, APH, and HIMS displays by name. Some things to
know:

- Bluetooth support hasn't yet been tested with a physical
  display, so treat your first Bluetooth session as a test.
- When a display is connected by USB, Dotify uses USB. To switch from USB to
  Bluetooth, unplug the cable and restart Dotify.
- The display's own keys work over Bluetooth on HumanWare and APH displays.
  On HIMS displays, Bluetooth shows braille only.

## 4. Place the microphone

Where the microphone sits matters more than which speech model you choose.

- **Put the microphone close to the person speaking.** Within arm's reach is
  best. If one person does most of the talking, a clip-on microphone on that
  person works very well.
- **Keep it closer to the voice than to the noise.** Fans, air conditioners,
  open windows, and music should be farther from the microphone than the
  speaker is.
- **Soft rooms help.** Carpet, curtains, and furniture absorb echo. In an
  echoey room, moving the microphone closer to the speaker helps most.
- **Use a wired or USB microphone when you can.** Many Bluetooth headsets
  lower their microphone quality while recording.
- **Laptop microphones** work well for one person sitting at the laptop. For
  a meeting, place an external microphone in the middle of the table or on
  the main speaker.

With a cloud speech model, Dotify uses a USB microphone automatically when one
is plugged in. The offline model uses the Windows default microphone, so set
your preferred microphone as the default in **Windows Settings › System ›
Sound**. Watch the **Microphone level** meter: it moves when the microphone
hears speech.

## 5. The Dotify window

Dotify opens in its own window in Microsoft Edge (or Chrome if Edge isn't
available). From top to bottom:

- **Pause microphone** button and a status line.
- **Braille** panel: the display's connection status and the **Catch up**
  and **Summarize** buttons. If no display is connected, the panel says what
  to do.
- **Microphone level** meter.
- **Transcript**: everything you have already read, with a **Clear history**
  button.
- **On the braille display**: the words under your fingers right now.
- **Pending for the braille display**: the words waiting for you.
- **Type instead of speaking**: a box where a partner can type to you.
- **Settings**, which opens to four categories:
  - **Transcription**: the speech model, the offline model download, the
    personal dictionary, and conversation mode.
  - **API keys**: optional keys for the cloud speech models.
  - **Braille**: the **Slower**, **Faster**, **Switch grade**, **Change
    reading mode**, and **Pause braille** buttons; a readout of the current
    grade, mode, reading mode, reading speed, and speech model; and the
    reading settings in [section 13](#13-reading-faster).
  - **Help and tools**: the list of keyboard shortcuts, and the players for
    text and caption files.

  Each category has a **Back to settings categories** button. **Quit Dotify**
  is at the end of the category list.

## 6. Your first session

1. Connect your display and put it in terminal mode.
2. Open Dotify. The Braille panel says *Connecting to braille display*, then
   *Connected* once it finds your display.
3. If you haven't added any API keys, Dotify uses the offline model. The first
   time, open **Settings › Transcription › Offline model** and choose
   **Download** (see [section 19](#19-the-offline-model)).
4. When the browser asks, allow microphone access for **127.0.0.1** (your own
   computer).
5. Speak. The microphone meter moves and text appears in the transcript.
6. Braille arrives on your display one page at a time, with a new page every
   5 seconds.

If speech comes faster than you read, it waits in a queue for you. You can
[catch up](#10-catching-up-and-summaries) at any time.

## 7. Controls: buttons, keyboard, and display keys

Every reading control works from the Dotify window, from the computer
keyboard, and from the braille display's own keys.

### Keyboard shortcuts

These work from anywhere in the Dotify window.

| Shortcut | Action |
|---|---|
| `Alt+Shift+S` / `Alt+Shift+F` | Slower / Faster |
| `Alt+Shift+G` | Switch braille grade |
| `Alt+Shift+R` | Change reading mode (auto or manual) |
| `Alt+Shift+L` | Catch up: jump to the most recent speech |
| `Alt+Shift+U` | Summarize what you missed (press again to cancel or skip) |
| `Alt+Shift+P` | Pause or resume braille |
| `Alt+Shift+M` | Pause or resume the microphone |
| `Alt+Shift+T` | Go to the typing box |
| `Alt+Shift+X` | Go to the transcript |
| `Alt+Shift+D` | Play or stop text on the braille display |
| `Alt+Shift+V` | Play or stop a caption file |
| `Alt+Shift+Q` | Quit Dotify |
| `Escape` in the typing box | Return to speech |

Dotify starts listening on launch, so there is no start shortcut. If
transcription stops because of a problem, a **Start transcription** button
appears.

### Braille display keys

| Display key | Action |
|---|---|
| Thumb **Left** / **Right** | Pan back / forward through what you've read |
| Thumb **Previous** (outer left) | Change reading mode |
| Thumb **Next** (outer right) | Catch up: jump to the most recent speech |
| **Space + dot 1** / **Space + dot 4** | Slower / Faster |
| **Space + dot 3** | Pause or resume braille |
| **Space + dot 6** | Pause or resume the microphone |
| **Space + G** (dots 1-2-4-5) | Switch grade |
| **Space + W** (dots 2-4-5-6) | Change cells per refresh (auto mode) |
| **Space + S** (dots 2-3-4) | Summarize what you missed (press again to cancel or skip) |
| **Space + I** (dots 2-4) | Show status: mode, cells per refresh, speed, input, and grade |
| **Space + T** (dots 2-3-4-5) | Play or stop the short sample text |
| **Space + R** (dots 1-2-3-5) | Start or end a reply ([section 16](#16-replying-from-the-braille-display)) |

A chord takes effect when you release all the keys. Each command confirms
with a short message on the display. To quit Dotify, use the Dotify window
or `Alt+Shift+Q`.

**Panning back.** Thumb Left moves back through text you have already read.
While you are back in history, the display holds still, new speech keeps
waiting in the queue, and cell 1 shows how far back you are. Thumb Right
moves forward again. At the newest text, Thumb Right brings the next part of
the queue onto the display right away. Thumb Next takes you straight back to
the most recent speech.

## 8. Reading modes

Dotify has two reading modes. Switch between them with **Change reading
mode**, `Alt+Shift+R`, or Thumb Previous. The display shows the new mode
briefly.

- **Auto** (the default): Dotify shows the next page on a steady timer, so
  you can keep your hands on the display. By default each refresh is a full,
  word-wrapped page. You can also have text arrive a few cells at a time, or
  one cell at a time like a ticker tape (see
  [section 12](#12-cells-per-refresh)).
- **Manual**: the display changes only when you press Thumb Right or Faster.
  Each press shows the next full page.

A full page is always left-aligned and never splits a word, unless the word
is wider than the whole display.

## 9. Speed and grade

**Speed.** Use **Slower** and **Faster** (`Alt+Shift+S` / `Alt+Shift+F`, or
Space + dot 1 / Space + dot 4).

- With full pages in auto mode, each page shows for **5 seconds** to start.
  Each press changes this by half a second, from 1 to 30 seconds. The display
  confirms the new time, such as "4.5s per page".
- With fewer cells per refresh, each press changes the pace by a steady step,
  and the display confirms the new pace in words per minute.
- In manual mode, Faster shows the next page.

The **Reading speed** readout under **Settings › Braille** shows your current
pace, in words per minute in auto mode. It measures your reading pace on the
display, so a speaker can use it as a guide for how fast to talk.

**Grade.** Use **Switch grade** (`Alt+Shift+G`, or Space + G). Dotify starts
in **grade 2 (contracted UEB)**. Each press moves to the next grade: from 2
to 3, then 1, then back to 2. Grade 1 is uncontracted. Grade 3 is an
experimental English grade 3. A new grade applies to new words, and words
already on the display or in the queue stay as they are.

## 10. Catching up and summaries

- **Catch up** (`Alt+Shift+L`, Thumb Next, or the **Catch up** button) jumps
  straight to the most recent speech, however far behind you are. The text
  you skipped stays in the transcript.
- **Summarize** (`Alt+Shift+U`, Space + S, or the **Summarize** button) gives
  you a short summary of what you missed, then continues with live speech.
  The longer you were away, the longer the summary, up to 700 characters. If
  everything you missed already fits on the display, Summarize simply catches
  you up.

The summary takes a moment to prepare, then reads like ordinary text at your
usual pace and grade. While you read it, cell 2 shows an `s`. New speech
waits behind the summary. To cancel the summary or skip the rest of it, press
Summarize again (the button reads **Cancel summary** or **Skip recap**). Catch
up also skips the rest (the button reads **Go live now**).

With an OpenAI key, OpenAI writes the summary. Without one, Dotify makes a
simpler summary on your own computer from the most important words you
missed, in the order they were spoken.

## 11. The two status cells

The first two cells of the display tell you where you are. The rest of the
display is your text.

**Cell 1 shows how far behind you are.** It fills as more text waits for you,
so one touch tells you how much is in the queue.

| Words waiting | Cell 1 |
|---|---|
| Up to 5 | blank |
| 6 to 15 | dots 7-8 |
| 16 to 40 | dots 3-6-7-8 |
| 41 to 100 | dots 2-3-5-6-7-8 |
| More than 100 | all 8 dots |

When you pan back, cell 1 shows how far back you are. When you catch up, it
empties at once, so you can feel that the command worked.

**Cell 2 shows where the text comes from.**

| Cell 2 | Meaning |
|---|---|
| blank | Speech, or a caption file |
| `s` (dots 2-3-4) | A summary |
| `h` (dots 1-2-5) | Text a partner typed ([section 15](#15-typing-instead-of-speaking)) |
| `r` (dots 1-2-3-5) | Your own reply ([section 16](#16-replying-from-the-braille-display)) |

## 12. Cells per refresh

In auto mode, each refresh shows a full page by default. The page stays while
you read it, then the next page appears. Pages with less text move on sooner.

To have text arrive in smaller pieces, set **Cells per refresh** under
**Settings › Braille** to any number up to the width of your display (minus
the two status cells). On the display, Space + W cycles through 1, 2, 4, 6,
8, and full page. At 1, text moves across the display one cell at a time.

Your reading pace stays the same at any setting. Larger pieces arrive less
often. A braille contraction always arrives whole.

Cells per refresh applies to auto mode. Manual mode always shows full pages.

## 13. Reading faster

People often speak faster than braille can be read. These settings under
**Settings › Braille** help you keep up while keeping every word:

- **Space time, percent of pace**: how long a space between words stays on
  the display, compared with a letter. 100% (the default) gives a space the
  same time as a letter. Lower settings let spaces pass more quickly.
- **Punctuation time, percent of pace**: the same setting for punctuation.
  The punctuation stays in the text and passes more quickly.
- **Lowercase braille, no capital signs**: leaves out capital signs to save
  cells. This is off by default. The on-screen transcript keeps its capitals.

Spoken numbers always appear as digits: "twenty five" reads as 25. The word
"one" stays a word, since it often isn't a number. Words like "million" stay
as words, since "5 million" is shorter than "5000000" in braille.

Space time and punctuation time apply in auto mode. Lowercase braille applies
in both modes.

## 14. Pausing

Dotify has two pauses:

- **Pause braille** (`Alt+Shift+P`, Space + dot 3, or the **Pause braille**
  button) holds the display still. Speech keeps arriving in the queue, so you
  miss nothing. Resume to continue where you stopped.
- **Pause microphone** (`Alt+Shift+M`, Space + dot 6, or the **Pause
  microphone** button) turns the microphone off and ends the connection to the
  speech service, so a paid model stops billing. Speech during the pause isn't
  transcribed. Resuming takes about a second. The display confirms with "mic
  off" or "mic on".

The microphone also pauses on its own while a partner types or you reply,
and resumes afterward. If you paused it yourself, it stays paused until you
resume it.

## 15. Typing instead of speaking

Speech recognition can get names and spellings wrong, and some rooms are too
loud to transcribe. A partner can type to you instead.

- **Start typing** in the **Type instead of speaking** box under the
  transcript (`Alt+Shift+T`). While someone types there, the microphone
  pauses and cell 2 shows `h`.
- **Each word goes to the display when it is followed by a space.** If the
  typist stops for 3 seconds in the middle of a word, that word goes too.
  There is no Send button. Typed words also appear in the transcript, so the
  typist sees what you received.
- **Words already sent stay sent.** Backspace changes only the typing box.
- **Return to speech** with the **Back to AI mode** button, `Escape`, or by
  moving to another part of the window. The microphone turns back on, unless
  you had paused it yourself.

A screen reader user can tab through the typing box without starting typing
mode.

## 16. Replying from the braille display

You can answer out loud by typing on the braille display's keys.

1. Press **Space + R** (dots 1-2-3-5). Captions hold still, the microphone
   pauses, and cell 2 shows `r`.
2. Type your reply in braille. Space ends a word, and dot 7 erases the last
   cell. Your words appear in the transcript.
3. Dotify speaks each sentence aloud with the computer's voice when it ends
   with a period, question mark, or exclamation mark. Press dot 8 to speak
   right away.
4. Press **Space + R** again to finish. Dotify speaks anything left, and
   captions continue where you stopped.

## 17. Playing text and caption files

These are under **Settings › Help and tools**. They need no microphone,
key, or internet.

**Play text on braille display** (`Alt+Shift+D`) sends text to your display
the same way live speech arrives. Choose what to play in the **Text to play**
list:

- **Short sample**
- **News article**: The New York Times on Mark Twain, 1910
- **Novel**: *Alice's Adventures in Wonderland*
- **Text file from this computer**: any plain text (.txt) file up to 1 MB

Use it to show Dotify to someone, check a display, practice the controls, or
read something. Space + T on the display plays the short sample.

**Play caption file** (`Alt+Shift+V`) plays a caption file (SRT or WebVTT, up
to 1 MB) with the timing of the original event: bursts when the speaker was
quick and quiet stretches when the room paused. It is a good way to try
reading a captioned talk in braille.

While anything is playing, both buttons read **Stop playing text**. Stopping
also removes the rest of the text from the queue.

## 18. Speech models and API keys

Choose a speech model in **Settings › Transcription › Transcription model**.
A new choice takes effect right away.

| Model | Key needed | About |
|---|---|---|
| **Nemotron (offline)** | None | NVIDIA Nemotron runs on your computer after a one-time download ([section 19](#19-the-offline-model)). It is free, works with no internet, and your audio stays on your computer. |
| **ElevenLabs Scribe v2 Realtime** | ElevenLabs | The most accurate model in Dotify's tests. Words arrive quickly. |
| **AssemblyAI Universal-Streaming English** | AssemblyAI | Can tell speakers apart ([section 21](#21-conversation-mode-and-named-speakers)). |
| **Deepgram Nova-3** | Deepgram | |
| **OpenAI gpt-live-transcribe** | OpenAI | An OpenAI key also gives you better summaries ([section 10](#10-catching-up-and-summaries)). |

The cloud models send your audio to that company and bill your account with
them. Dotify transcribes English.

**Adding keys.** Open **Settings › API keys**, paste a key, and choose
**Save**. The key works right away. A cloud model becomes available in the
list once its key is saved. Dotify never shows a saved key on screen, keeps
keys in %LOCALAPPDATA%\Dotify, and sends each key only to its own company.
The Start menu also has **Configure Optional API Keys**, which sets the
AssemblyAI and OpenAI keys.

**Which model starts.** Dotify starts with the first of these that has a key:
ElevenLabs, AssemblyAI, then OpenAI. Otherwise it starts with Nemotron, or
asks you to download it. You can choose any model in Settings, including
Deepgram. Pause the microphone (`Alt+Shift+M`) whenever you want a paid model
to stop billing.

**If the internet drops,** Dotify tries your model again, then switches to
Nemotron and shows "offline captions" on the display. When the connection
returns, Dotify goes back to your model and shows "back online". If the
offline model isn't downloaded, the display shows "connection lost" until the
internet returns. If you choose a model yourself during the outage, Dotify
keeps your choice.

Dotify sends words to the display as soon as the speech model is sure of
them, so there is nothing to adjust.

## 19. The offline model

Nemotron (offline) uses NVIDIA's Nemotron streaming speech model, the most
accurate offline model in Dotify's tests. It is about 650 MB, so you download
it once after installing.

- **Download:** open **Settings › Transcription › Offline model** and choose
  **Download (about 650 MB)**. The status line shows progress, and the display
  shows "offline model ready" when it finishes. You can keep using another
  model while it downloads.
- **Interrupted:** choose **Download** again and it continues where it
  stopped. You can cancel at any time.
- **Where it's stored:** %LOCALAPPDATA%\Dotify\models. Reinstalling or
  upgrading Dotify keeps it.
- **Removing it:** choose **Delete the downloaded model** to free the space.
  Download it again to use Nemotron or the internet backup.

The first time Nemotron starts after you open Dotify, it takes a moment to
load. Speech during that moment is transcribed once it's ready.

## 20. Personal dictionary

Speech models often miss names, places, and specialized words. Add yours in
**Settings › Transcription › Personal dictionary** (up to 100 entries).

- **Add a word or name.** The cloud models listen for it from the next time
  transcription starts. A short list of important words works best.
- **Always replace (optional).** If a model keeps writing your word wrong,
  such as "dot if I" for "Dotify", add that mistake here. Dotify replaces it
  everywhere, starting right away. If the mistake is a common English phrase,
  Dotify warns you, since it would replace that phrase whenever anyone says
  it.

Corrections apply before text reaches the display, the transcript, or the
transcript file. The dictionary stays on your computer.

## 21. Conversation mode and named speakers

*Conversation mode is experimental and works with AssemblyAI. Turn it on in
**Settings › Transcription › Conversation mode (experimental)**.*

Conversation mode labels who is speaking, such as `A:` and `B:`. A label
appears only when the speaker changes, to save cells. Text arrives in larger
pieces while conversation mode is on.

**Named speakers.** Record about 20 seconds of a person talking with **Enroll
voice**, and their turns show their name, such as `Alice:`, instead of a
letter. Voice samples stay on your computer. Dotify uses a name once it has
heard at least 3 seconds of clear speech and is confident of the match.

Dotify waits for a speaker change to hold for a moment before it shows a new
label, so a brief mix-up doesn't reach the display.

## 22. Following along on screen

The Dotify window shows a sighted partner where you are in the text:

- **Transcript:** what you have already read.
- **On the braille display** (highlighted): the words under your fingers right
  now. It follows the display as it changes. It also shows a summary, an
  announcement, or your reply while you read or type it.
- **Pending for the braille display** (gray): the words still waiting for you.
  When it's empty, you are caught up. When it grows, the speaker can slow
  down.

A sighted person reading along sees the same words at the same time you
receive them. Print is quicker to read than braille, so the moving bands are
the best guide to your pace.

Summaries, typed text, and replies are labeled **Recap:**, **Human:**, and
**Reply:**, each in its own color. Screen readers read the labels, so the
meaning doesn't depend on color. The bands update quietly, and a screen reader
reads them when you move to them.

## 23. Quitting Dotify

Choose **Quit Dotify** at the end of the Settings categories, or press
`Alt+Shift+Q`. Dotify stops listening, releases the braille display, and
closes its window. Closing the window also quits Dotify, about 10 seconds
later. Reloading the page keeps Dotify running.

To start again, open **Start Dotify** from the Start menu or the desktop.

## 24. Troubleshooting

| Problem | What to do |
|---|---|
| **No compatible display was found** | Put the display in USB terminal mode, reconnect it, and restart Dotify. |
| **"Waiting for the braille display"** | Another program, such as NVDA or JAWS, is using the display. Free it (in NVDA, set the braille display to **No braille**). Dotify connects on its own. |
| **More than one display is connected** | Disconnect the displays you aren't using and restart Dotify. |
| **The Brailliant BI 40X stops showing braille** | The BI 40X can disconnect from USB when idle. Press a key on the display to wake it. If braille doesn't return, restart Dotify. |
| **Braille appears only in grade 1** | Reinstall Dotify. |
| **The microphone meter doesn't move** | Allow microphone access for 127.0.0.1, and check that Windows is using the right microphone. |
| **Nemotron says "not downloaded"** | Open **Settings › Transcription › Offline model** and choose **Download**. |
| **Offline speech didn't start** | Check %LOCALAPPDATA%\Dotify\logs\speech-error.log. The cloud models are still available. |
| **"A leftover Dotify session probably still holds port 8788"** (or 8790) | An earlier session is still closing. Close any Dotify windows, wait a few seconds, and open Dotify again. |
| **"Braille controls are not responding"** | The display may be reconnecting. If the message stays, close the window and open Dotify again. |

If Dotify can't start, it says why in a Windows message box. Logs are kept in
%LOCALAPPDATA%\Dotify\logs.

## More

- [README-windows.md](README-windows.md): the full display list, diagnostics,
  and building the installer.
- [SECURITY.md](../../SECURITY.md): what leaves your computer and what stays
  on it.
