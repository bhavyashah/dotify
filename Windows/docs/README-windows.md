# Dotify on Windows

The Windows build turns live speech into contracted (grade 2) UEB braille on
a refreshable display, using Windows' in-box HID and serial drivers. It needs
no WSL, BRLTTY, Zadig, or custom USB driver.

This file covers installing, what runs where, supported displays,
diagnostics and building. For using Dotify (the panel, keyboard shortcuts,
display chords, reading modes), see [USER-GUIDE.md](USER-GUIDE.md).

## Install and start

1. Run `DotifySetup.exe` (Windows 11, x64 or ARM64). From a source checkout,
   double-click `Windows\Launch Dotify.cmd` instead; its first run builds the
   app image and needs internet once.
2. Connect the display by USB and switch it to its terminal / screen-reader
   mode, or pair it once in Windows **Settings > Bluetooth & devices** and
   turn on the display's Bluetooth terminal connection (see
   [Bluetooth](#bluetooth)).
3. Start **Start Dotify** from the Start menu or the desktop.
4. One browser window opens and transcription starts on its own; allow
   microphone access when asked.

No API key is needed. With no keys saved, Dotify uses **Nemotron
(offline)**, a speech model that runs on the computer after a one-time
download of about 650 MB (**Settings › Transcription › Offline model**;
resumable). Until it is downloaded, the page says so instead of
transcribing. Cloud models (ElevenLabs, AssemblyAI, Deepgram, OpenAI) are
optional; add their keys under **Settings › API keys**. The page only
reports whether a provider is configured and never reads a saved key back.
**Configure Optional API Keys** in the Start menu is a console alternative
for the AssemblyAI and OpenAI keys.

## What runs

`launcher.ps1` starts these hidden processes and opens the page in an
app-mode Edge window (Chrome, then the default browser, as fallbacks):

| Port | Process | Role |
|---|---|---|
| 8788 | `node "Speech to text\server.js"` | Speech server and the web page |
| 8790 | `python "Text to Braille\windows_run.py"` | Braille engine (the "ticker") and its control bridge for the page |
| 8791 | `python "Text to Braille\local_speech_server.py"` | Offline speech (Nemotron through sherpa-onnx) |
| 8792 | `python "Speech to text\speaker-id\server.py"` | Optional speaker naming |

All of them listen on 127.0.0.1 only. Quitting Dotify, or closing its
window, stops the ticker, and the launcher then stops the rest. If an
earlier launcher was killed and left its services running, the next launch
stops them first; a launch while another Dotify session is still running
fails with a message instead.

On Windows on ARM the offline speech service runs on a native ARM64 Python
(`runtime\python-arm64`); emulated x64 decoding is too slow for live
captions there. Everything else runs on the x64 runtime under Windows'
emulation.

User data stays out of the program folder, in `%LOCALAPPDATA%\Dotify`: API
keys (`.env`), the transcript (`transcript.txt`), the offline model
(`models\`), voiceprints (`speakers\`) and logs (`logs\`). Uninstalling
leaves this folder in place; delete it to remove your keys and data.

If usbipd previously shared a supported HID display with WSL, the launcher
detaches and unbinds only that registered device (one UAC prompt) so Windows
can use it again.

## Display support

"Verified" means used on real hardware under Windows. "Implemented" means
the same protocol code, without a hardware pass yet.

| Display / transport | USB identity | Software | Hardware |
|---|---|---|---|
| HumanWare NLS eReader | `1C71:CE01` | Native HumanWare HID | Verified |
| HumanWare Brailliant BI 40X | `1C71:C131` | Native HumanWare HID | Verified over USB; can drop off the USB bus when idle (below) |
| HumanWare Brailliant BI 20X | `1C71:C141` | Native HumanWare HID | Implemented |
| APH Mantis Q40 / Chameleon 20 | `1C71:C111`, `C101` | Native HumanWare HID | Implemented |
| HumanWare Brailliant BI/B legacy HID | `1C71:C006`, `C022` | Native HumanWare HID | Implemented |
| HumanWare BrailleOne 20 | `1C71:C121` | Native HumanWare HID | Implemented |
| BrailleNote Touch | `1C71:C00A` | Native HumanWare HID | Implemented |
| BrailleNote Touch v2 / Touch Plus | `1C71:C00E` | Native HumanWare HID | Implemented |
| HumanWare Brailliant USB serial | `1C71:C005`, `C021` | Native COM, HumanWare serial protocol | Implemented |
| Standards-compliant single-row HID Braille | usage page `0x41` | Descriptor-driven `standard-hid` sink | Implemented |
| HIMS Braille Edge 3S | `045E:940A` | Native HIMS HID | Implemented |
| HIMS SyncBraille / Braille Edge 2S serial | `0403:6001`, `1A86:55D3` | Native COM, HIMS protocol | Implemented |
| HumanWare / APH family over Bluetooth | paired name (`NLS eReader`, `Brailliant BI`, `APH Mantis`, …) | Bluetooth serial, HumanWare protocol, display keys included | Implemented |
| HIMS family over Bluetooth | paired name (`BrailleSense`, `BrailleEDGE`, `SmartBeetle`, …) | Bluetooth serial, HIMS protocol, output only | Implemented |
| BrailleSense legacy USB family | `045E:930A` | Discovery only | Needs a signed driver and the exact endpoints |
| HIMS Braille EDGE 40 bulk | `045E:930B` | Discovery only | Needs a compatible signed driver |

The Brailliant BI 40X has been used in real sessions over USB. It can
suspend itself off the USB bus while the display is idle, which ends the
session's connection; pressing a key on the display wakes it. Dotify keeps
trying to reconnect, but if braille does not come back, relaunch.

The launcher auto-detects exactly one compatible display. If two are
connected, pick one by profile id (the ids are in
`Windows\hardware\display_profiles.json`):

```powershell
launcher.ps1 -Display brailliant-bi-20x
```

The standard HID sink reads the report ID, cell usage and width from the
device's HID descriptor, so any single-row usage-page `0x41` display should
work. Multi-row displays are detected but refused. To use that sink
directly:

```powershell
runtime\python\python.exe "Text to Braille\windows_run.py" --source ws --sink standard-hid
```

When a write fails mid-session, the sink makes two quick attempts to reopen
the same display and resend the frame, pinned to the original unit and
width (it never switches to a second display). After that the engine keeps
retrying in the background and the panel says what it is waiting for.

### Bluetooth

Pair the display once in Windows **Settings > Bluetooth & devices**, then
turn on its own Bluetooth terminal connection (on HumanWare displays:
Braille Display > Connected devices > Bluetooth). Windows creates a virtual
serial port for the pairing; Dotify finds it by the display's paired name
(Bluetooth ports carry no USB identity) and speaks the same serial protocol
NVDA and BRLTTY use. Display keys work on HumanWare/APH displays; HIMS over
Bluetooth is output only.

**USB wins over Bluetooth.** A paired display's serial port exists even
while the display is off or out of range, so Bluetooth is tried only when no
USB display is present, and a session that started on USB never switches to
Bluetooth. To use Bluetooth while the display is also on USB:

```powershell
launcher.ps1 -Display humanware-bluetooth
```

If two paired displays of the same family match, Dotify refuses to guess;
unpair the one not in use. A paired display that is switched off shows as
"waiting for the braille display" until it is switched on. Connecting takes
a few seconds, because opening the port is what dials the radio link.

Bluetooth support has not yet been tested with a physical display.

## Diagnostics

Installed builds include a read-only inventory:

```powershell
powershell -ExecutionPolicy Bypass -File diagnostics\device_inventory.ps1
```

It lists matching devices, HID descriptors, usbipd state, serial ports and
paired Bluetooth serial ports (with each pairing's name and whether a
profile matches it), without changing anything. Start here with any display
Dotify does not find. Logs are in `%LOCALAPPDATA%\Dotify\logs`
(`ticker-error.log` for the braille side, `speech-error.log` for offline
speech).

A source checkout also has probes that talk to a display directly:

```powershell
python Windows\hardware\hid_probe.py inspect --device all
python Windows\hardware\hid_probe.py write --device nls-ereader --protocol humanware --pattern alternating
python Windows\hardware\bluetooth_probe.py list
```

To check everything after the browser (speech server, braille engine and
display) without a microphone, post a line of text while Dotify is running:

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8788/ingest `
  -ContentType application/json -Body '{"text":"live speech is working"}'
```

To try the pipeline with no display attached, run the staged launcher with
the simulated display:

```powershell
powershell -ExecutionPolicy Bypass -File launcher.ps1 -Sim -NoBrowser -PaceMs 1
```

## Building and verifying

`Windows\installer\build.ps1` assembles the app image in
`Windows\installer\stage` from the repository plus the pinned downloads in
`vendor.lock.json` (it needs internet and a Python on PATH matching the
pinned embeddable version). `-Installer` also compiles `installer.iss` with
Inno Setup 6. The build refuses to package anything secret-shaped: `.env`,
`*.key`, `speakers\`, recordings.

`Windows\installer\verify.ps1` runs the test suites, builds the stage and
checks it:

```powershell
powershell -ExecutionPolicy Bypass -File Windows\installer\verify.ps1
powershell -ExecutionPolicy Bypass -File Windows\installer\verify.ps1 -Installer
```

It also runs the braille engine's tests under WSL (Ubuntu 22.04); pass
`-SkipWsl` without it. Add `-Hardware` only with a display in USB terminal
mode, and `-RequireSignature` for a signed build. A new display should
pass the hardware checklist in [HARDWARE-FINDINGS.md](HARDWARE-FINDINGS.md)
before it's called verified.

Development installers can be unsigned. To sign, put a CA-issued
Authenticode certificate in a Windows certificate store and pass only its
thumbprint (never commit a PFX or password):

```powershell
$env:DOTIFY_SIGNING_CERT_THUMBPRINT = '<certificate thumbprint>'
.\build.ps1 -Installer -Sign
```

Signing uses SHA-256 with an RFC 3161 timestamp, and the build verifies the
signed installer.

## Troubleshooting

| Symptom | Fix |
|---|---|
| No supported display found | Put the display in USB terminal mode, reconnect it, and run the inventory. |
| Paired over Bluetooth but not found | Run the inventory: `bluetooth_spp_ports` shows each pairing's name and whether a profile matched. Re-pair if there is no serial port; the display's Bluetooth terminal mode must be on. |
| "Waiting for the braille display" | Another program holds it. In NVDA set the braille display to "No braille"; Dotify connects as soon as the display is free. |
| Windows error 1167 | `usbipd list` must show the display as `Not shared`; re-enter terminal mode or power-cycle the display. |
| Access denied | Close NVDA, JAWS, BRLTTY, or any other program using the display. |
| More than one display found | Start with `-Display <profile-id>`. |
| Braille falls back to grade 1 | Reinstall: the bundled `vendor\liblouis` is missing or damaged. |
| Microphone meter stays at zero | Allow microphone access for `localhost` and check the Windows input device. Capture errors appear in the status line. |
| Nemotron says "not downloaded" | **Settings › Transcription › Offline model › Download**. An interrupted download resumes. |
| Offline speech did not start | See `%LOCALAPPDATA%\Dotify\logs\speech-error.log`. Cloud models with keys still work. |
| "A leftover Dotify session probably still holds port …" | Another Dotify is running: close its window, or quit it, and launch again. |

The installer and runtimes have been tested on Windows 11 ARM64 (running
the x64 package under emulation, with native ARM64 offline speech). They
have not had a full test pass on a physical x64 machine.
