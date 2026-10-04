# Dotify: live speech on a braille display

Dotify is built for deafblind people who read braille. It transcribes speech
in the room and streams the words to a refreshable braille display. The
reader sets the pace and uses the display's own keys to slow down,
pause, pan back, jump to the most recent speech, or get a short summary of
what they missed.

Dotify runs on Windows 11 and Linux and works with a wide range of USB and
Bluetooth braille displays, including the HumanWare Brailliant, BrailleNote
Touch, and NLS eReader, the APH Mantis Q40 and Chameleon 20, the HIMS Braille
Edge and BrailleSense, and displays that follow the USB HID braille
standard. On Linux, Dotify supports every display BRLTTY supports.

Known issues are listed in [KNOWN-ISSUES.md](KNOWN-ISSUES.md).

## What Dotify adds to live captions

Phones offer live captions, such as Live Captions on iPhone and Live
Transcribe on Android. Dotify adds features built for reading live speech
in braille:

- **Your reading pace.** Incoming speech queues up, and the display
  advances at the speed you set. You can pause, pan back, and change the
  speed from the display's keys.
- **Steady cells.** Text stays the same once it reaches the display.
  Corrections from the speech model apply only to words you haven't read
  yet.
- **Backlog gauge.** The first cell shows how many unread words are
  waiting.
- **Catching up.** One key jumps to the most recent speech. Another
  replaces the backlog with a short summary of what was said.
- **Three reading modes.** Pages that advance on a timer, pages you advance
  with a panning key, or a ticker tape that moves one cell at a time.
- **Grade 1 or grade 2 UEB,** switchable mid-conversation.
- **Typed input and replies.** A hearing partner can type straight to the
  display, and you can type a reply on the display's keys for Dotify to
  speak aloud.
- **Personal dictionary** for names and words the speech model mishears.
- **Choice of speech models,** including NVIDIA Nemotron, which runs
  offline on your Windows computer.

## Try it on Windows

1. Download **`DotifySetup.exe`** from the
   [latest release](https://github.com/bhavyashah/dotify/releases/latest) and
   run it. The installer isn't code-signed, so Windows SmartScreen may say
   "Windows protected your PC": choose **More info**, then **Run anyway**.
2. Plug in your braille display over USB, or pair it in **Settings ›
   Bluetooth & devices** and turn on the display's Bluetooth terminal mode.
3. Launch **Start Dotify** from the Start menu. The Dotify window opens.
4. On first use, download the offline speech model, NVIDIA Nemotron: open
   **Settings › Transcription › Offline model** and choose **Download**. It
   is about 650 MB and downloads once. After that, Dotify transcribes speech
   on your computer with no API key or internet connection.

To use a cloud model, open **Settings › API keys** and paste a key for
ElevenLabs, AssemblyAI, Deepgram, or OpenAI.

To run from a source checkout, double-click
[`Windows/Launch Dotify.cmd`](Windows/Launch%20Dotify.cmd). The first run
builds a self-contained app image and needs an internet connection.
Building from source needs Node.js and Python 3.13 on PATH. More:
[Windows/docs/USER-GUIDE.md](Windows/docs/USER-GUIDE.md) (using Dotify) and
[Windows/docs/README-windows.md](Windows/docs/README-windows.md) (displays,
diagnostics, and building the `DotifySetup.exe` installer).

## Try it on Linux

See [Linux/GETTING-STARTED.md](Linux/GETTING-STARTED.md). Dotify connects
to the display through BRLTTY's BrlAPI. On Linux, transcription uses a cloud
model with your own API key.

## Try it with no hardware

The braille engine has a simulated display that prints cells in the terminal:

```bash
cd core/text-to-braille
pip install -r requirements.txt
python run.py --file sample.txt          # replay a file
python run.py --source ws                # follow a running speech server
```

## Privacy

With the offline model, audio stays on your computer. With a cloud model,
audio goes directly to that provider under your own account and its terms.
Dotify has no server of its own and collects no telemetry.
See [SECURITY.md](SECURITY.md).

## Contributing

Issues, pull requests, and forks are welcome.
[CONTRIBUTING.md](CONTRIBUTING.md) explains how to build and test, and the
rules the code follows. [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
describes how the parts fit together, and [docs/LESSONS.md](docs/LESSONS.md)
covers what we learned building Dotify.

## Acknowledgements

Thank you to Ania Filochowska, David Madey,
Haben Girma, Maurice Mines, Christopher Kchao,
Scott Davert, Mark Baxter, and Robert Stigile for their
invaluable feedback.

## License

Apache License 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE). Bundled
third-party components keep their own licenses; see
[Windows/installer/THIRD-PARTY-NOTICES.md](Windows/installer/THIRD-PARTY-NOTICES.md).
