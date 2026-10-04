# Dotify: live speech on a braille display

Dotify is built for deafblind people who read braille. It transcribes speech
in the room and shows the words on a refreshable braille display a second
or two later, in contracted (grade 2) or uncontracted (grade 1) UEB braille.
The reader sets the pace and uses the display's own keys to slow down,
pause, pan back, jump to the most recent speech, or get a short summary of
what they missed.

Dotify runs on Windows 11 and Linux and works with a wide range of USB and
Bluetooth braille displays, including the HumanWare Brailliant, BrailleNote
Touch, and NLS eReader, the APH Mantis Q40 and Chameleon 20, the HIMS Braille
Edge and BrailleSense, and displays that follow the USB HID braille
standard. On Linux, Dotify supports every display BRLTTY supports.

Known issues are listed in [KNOWN-ISSUES.md](KNOWN-ISSUES.md).

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

## How it fits together

```
microphone ─► speech server ─► /finalized WebSocket ─► braille engine ─► display
 (browser)    core/speech-to-text   (text segments)     core/text-to-braille   (HID / serial /
                                                                              Bluetooth / BrlAPI)
```

- [`core/speech-to-text/`](core/speech-to-text/): a Node.js server that
  sends microphone audio to a speech recognition model and passes on the
  finished text.
- [`core/text-to-braille/`](core/text-to-braille/): a Python program that
  translates the text with liblouis and paces the cells across the display.
- [`Windows/`](Windows/) and [`Linux/`](Linux/): platform launchers, display
  drivers, and the Windows installer.

[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) describes each part in more
detail. [docs/LESSONS.md](docs/LESSONS.md) covers what we learned building
Dotify: braille hardware on Windows, pacing text for touch reading, and
choosing a streaming speech model.

## Privacy

With the offline model, audio stays on your computer. With a cloud model,
audio goes directly to that provider under your own account and its terms.
Dotify has no server of its own and collects no telemetry.
See [SECURITY.md](SECURITY.md).

## Contributing

Issues, pull requests, and forks are welcome.
[CONTRIBUTING.md](CONTRIBUTING.md) explains how to build and test, and the
rules the code follows.

## License

Apache License 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE). Bundled
third-party components keep their own licenses; see
[Windows/installer/THIRD-PARTY-NOTICES.md](Windows/installer/THIRD-PARTY-NOTICES.md).
