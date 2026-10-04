# Dotify — live speech on a braille display

Dotify turns live speech into braille on a refreshable braille display, at
the reader's own pace. Someone talks; the words arrive under a deafblind
reader's fingers a second or two later, as contracted (grade 2) or
uncontracted (grade 1) UEB braille. The reader can slow it down, pause, pan
back, jump to the live edge, or ask for a short summary of what they missed,
all from the display's own keys.

It runs on a Windows PC (the primary platform) or Linux, with a USB or
Bluetooth braille display. On Windows, speech recognition runs fully offline
by default; cloud engines are optional and use your own API key.

> **Status:** working software, used in real conversations and tested on a
> handful of displays. Known rough edges are listed in
> [KNOWN-ISSUES.md](KNOWN-ISSUES.md).

## Try it on Windows

1. Download **`DotifySetup.exe`** from the
   [latest release](https://github.com/bhavyashah/dotify/releases/latest) and
   run it. The installer isn't code-signed, so Windows SmartScreen may say
   "Windows protected your PC": choose **More info**, then **Run anyway**.
2. Plug in your braille display over USB, or pair it in **Settings ›
   Bluetooth & devices** and turn on the display's Bluetooth terminal mode.
3. Launch **Start Dotify** from the Start menu. A browser window opens and
   transcription starts on its own. Speak, and the words arrive on the
   display.

To run from a source checkout instead, double-click
[`Windows/Launch Dotify.cmd`](Windows/Launch%20Dotify.cmd); the first run
builds a self-contained app image (needs internet once).

Offline speech needs a one-time model download (about 650 MB, NVIDIA
Nemotron via sherpa-onnx) from **Settings › Transcription › Offline model**.
To use a cloud engine instead, open **Settings › API keys** and paste a key
for ElevenLabs, Deepgram, AssemblyAI, or OpenAI.

Building from source needs Node.js and Python 3.13 on PATH. More:
[Windows/docs/USER-GUIDE.md](Windows/docs/USER-GUIDE.md) (using Dotify) and
[Windows/docs/README-windows.md](Windows/docs/README-windows.md) (displays,
diagnostics, and building the `DotifySetup.exe` installer).

## Try it on Linux

See [Linux/GETTING-STARTED.md](Linux/GETTING-STARTED.md). Linux drives the
display through BRLTTY's BrlAPI, so any display BRLTTY supports should work.
The offline engine isn't wired up on Linux yet, so you'll need an API key
for one of the cloud engines.

## Try it with no hardware

The braille engine has a simulated display that prints cells in the terminal:

```bash
cd core/text-to-braille
pip install -r requirements.txt
python run.py --file sample.txt          # replay a file
python run.py --source ws                # follow a running speech server
```

## Displays

| Display | Status |
|---|---|
| HumanWare NLS eReader (20 cells) | Verified end to end, by touch, over native HID on Windows and BrlAPI on Linux |
| HumanWare Brailliant BI 40X | Used in real sessions; drops off the USB bus when idle (see KNOWN-ISSUES) |
| Other HumanWare, APH (Mantis Q40, Chameleon 20), and HIMS displays | Device profiles exist in [display_profiles.json](Windows/hardware/display_profiles.json); not yet tested on hardware |
| Displays that speak the USB HID Braille standard | Generic sink exists; lightly tested |
| Anything BRLTTY drives (Linux) | Should work; untested beyond the eReader |

If you try a display that isn't listed as verified, an issue saying what
happened helps the next person who tries it.

## How it fits together

```
microphone ─► speech server ─► /finalized WebSocket ─► braille engine ─► display
 (browser)    core/speech-to-text   (text segments)     core/text-to-braille   (HID / serial /
                                                                              Bluetooth / BrlAPI)
```

- [`core/speech-to-text/`](core/speech-to-text/) — a Node.js server that
  streams microphone audio to a recognizer and publishes finished text.
- [`core/text-to-braille/`](core/text-to-braille/) — a Python engine that
  translates with liblouis and paces cells across the display.
- [`Windows/`](Windows/) and [`Linux/`](Linux/) — thin platform shells:
  launchers, native display drivers, the installer.

[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) covers the moving parts in
more detail. [docs/LESSONS.md](docs/LESSONS.md) collects what was learned
building this: braille hardware on Windows, pacing text for touch reading,
and choosing a streaming speech engine. It's the part most likely to save
you time if you're building something similar.

## Privacy

With the offline engine, audio never leaves your computer. With a cloud
engine, audio goes directly to that provider under your own account and
their terms. Dotify has no server of its own and collects no telemetry.
See [SECURITY.md](SECURITY.md).

## Contributing

Issues, pull requests, and forks are welcome.
[CONTRIBUTING.md](CONTRIBUTING.md) explains how to build and test, and the
rules the code follows.

## License

Apache License 2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE). Bundled
third-party components keep their own licenses; see
[Windows/installer/THIRD-PARTY-NOTICES.md](Windows/installer/THIRD-PARTY-NOTICES.md).
