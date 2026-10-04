#!/usr/bin/env bash
# Start Dotify on Linux or WSL with one command:
#   braille display service + speech server + the live ticker.
#
#   sudo bash Linux/hardware/start_appliance.sh   (or: sudo bash Linux/launch-dotify.sh)
#
# Ends with the ticker in the FOREGROUND so the live keys work
# (f = next page, space = jump to live, S = summarize the backlog,
# r = auto/manual, q = quit, Tab = human mode). Quitting the ticker leaves the
# display service and speech server running; rerun this script to get back.
#
# Under WSL, attach the display after each plug-in or reboot
# (usbipd attach --busid <BUSID> --wsl) and open http://localhost:8788 in a
# Windows browser (WSL forwards the port).
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"          # Linux/hardware
REPO="$(cd "$HERE/../.." && pwd)"              # repo root
SPEECH_DIR="$REPO/core/speech-to-text"
TICKER_DIR="$REPO/core/text-to-braille"

echo "== 1/3 braille display =="
bash "$HERE/start_display.sh" || exit 1

echo "== 2/3 speech server =="
if curl -s -o /dev/null --max-time 2 http://localhost:8788/; then
  echo "   already running on :8788"
else
  if [ ! -d "$SPEECH_DIR" ]; then
    echo "!! speech-to-text folder not found next to this folder"; exit 1
  fi
  ( cd "$SPEECH_DIR" && nohup node server.js > server.log 2>&1 & )
  sleep 2
  if curl -s -o /dev/null --max-time 2 http://localhost:8788/; then
    echo "   started (log: speech-to-text/server.log)"
  else
    echo "!! server did not come up; see speech-to-text/server.log"; exit 1
  fi
fi

echo "== 3/3 live braille ticker (foreground; f = next page, space = live, S = summarize, q = quit, Tab = type) =="
echo "   In a browser (under WSL, a Windows one): http://localhost:8788 -> Start transcription -> speak."
cd "$TICKER_DIR"
exec python3 run.py --source ws --sink brlapi
