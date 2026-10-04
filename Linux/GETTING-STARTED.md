# Dotify on Linux (or WSL)

On Linux, Dotify drives the braille display through BRLTTY's BrlAPI, so any
display BRLTTY supports should work. Only the HumanWare NLS eReader (20
cells, USB) has been tested end to end. Every speech engine on Linux is a
cloud provider and needs that provider's API key; the offline Nemotron
engine is only wired up in the Windows build.

## 1. Install the prerequisites (Debian/Ubuntu)

```bash
sudo apt update
sudo apt install brltty python3-brlapi liblouis-bin python3-louis python3 python3-pip nodejs npm
pip3 install -r core/text-to-braille/requirements.txt
(cd core/speech-to-text && npm install)
```

The speech server needs a current Node.js (the Windows build bundles
Node 24); if your distribution's `nodejs` is old, install one from
nodejs.org.

**WSL only:** WSL can't see USB devices by default. Install
[usbipd-win](https://github.com/dorssel/usbipd-win) on Windows
(`winget install usbipd`), then from an elevated PowerShell:

```powershell
usbipd list                         # find the display's bus id
usbipd bind --busid <BUSID>         # once
usbipd attach --busid <BUSID> --wsl # after every plug-in or reboot
```

Bluetooth displays don't work under WSL, which has no access to the Windows
Bluetooth radio.

## 2. Launch

Plug in the display, then from the repository root:

```bash
sudo bash Linux/launch-dotify.sh
```

This starts one BRLTTY with the display, the speech server on port 8788,
and the braille engine in the foreground. Open http://localhost:8788 in
Chrome or Edge (from Windows, under WSL), add a key under
**Settings › API keys**, press **Start transcription**, and speak.

In the terminal running the engine: `f` next page, `space` jump to live,
`S` summarize the backlog, `r` auto/manual paging, `Tab` type text straight
to the display, `q` quit. The display's own keys work too; see
[core/text-to-braille/README.md](../core/text-to-braille/README.md).

**Note:** `Linux/hardware/start_display.sh` stops and masks the system
BRLTTY services (`brltty.service`, `brltty-udev.service`) so that only one
BRLTTY owns the display. If you rely on BRLTTY for your console, unmask and
restart those services when you're done:

```bash
sudo systemctl unmask brltty.service brltty-udev.service
sudo systemctl start brltty.service
```

If your display isn't detected, BRLTTY may need its driver named
explicitly (`-b <driver>`; see the comments in `start_display.sh`).

## Without the speech server

The braille engine can replay a file or piped text, which is the quickest
hardware check and needs no API key:

```bash
cd core/text-to-braille
sudo bash ../../Linux/hardware/start_display.sh
sudo python3 run.py --file sample.txt --sink brlapi
sudo python3 run.py --file grade_demo.txt --sink brlapi --grade 1
echo "hello from the keyboard" | sudo python3 run.py --source stdin --sink brlapi
```

From the repository root, `sudo python3 Linux/hardware/display_test.py
"HELLO" 30` writes one line and holds it for 30 seconds, which checks BRLTTY
and BrlAPI without the engine.

macOS is untested. BRLTTY and liblouis are available through Homebrew, but
the BrlAPI Python bindings may need BRLTTY built from source.
