# Working on Dotify

This guide is for anyone building on Dotify, here or in a fork. The people
using it read braille, so the bar for any change is: does it make reading
better, and can a braille reader tell what happened?

## Reporting problems

Open an issue with your display model, how it was connected (USB, Bluetooth,
BrlAPI), the speech engine, and what you felt under your fingers. Logs help:
on Windows they are in `%LOCALAPPDATA%\Dotify\logs` (start with
`ticker-error.log`).

## Running the tests

```bash
# speech server (Node.js; the installer bundles 24)
cd core/speech-to-text && npm install && node --test --test-concurrency=1

# braille engine (Python; the installer bundles 3.13)
cd core/text-to-braille && pip install -r requirements.txt && python -m pytest -q

# Windows shell
cd Windows/overlay && python -m pytest -q
cd Windows/hardware && python -m pytest -q
```

The speech tests open real local ports, so run them serially as shown. On
Windows, `Windows/installer/verify.ps1` runs all of these, builds the app
image and checks it (see
[Windows/docs/README-windows.md](Windows/docs/README-windows.md#building-and-verifying)).

## Ground rules for changes

- **`core/` stays portable.** No OS-specific code in `core/`; platform
  details belong in `Windows/` or `Linux/`.
- **Braille is append-only.** A cell, once shown, never changes under the
  reader's fingers. Corrections arrive as new text. Read
  [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) before touching the
  soft/revise protocol or the engine's queue.
- **Every command acknowledges on the display.** If a chord does something,
  the reader must be able to feel that it did (or why it didn't).
- **The installed layout is a contract.** Paths inside the Windows app
  image (`Speech to text`, `Text to Braille`, `runtime`) don't change, so
  upgrades work in place.
- **Never commit secrets or voice data.** `.env`, `*.key`, recordings, and
  `speakers/` stay local; the installer build refuses to package them.

Hardware changes need a test on a real display. Say in the pull request
which display you used.

By contributing, you agree your contribution is licensed under the
Apache License 2.0.
