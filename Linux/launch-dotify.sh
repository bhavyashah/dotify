#!/usr/bin/env bash
# One-command Dotify launch on Linux/WSL:
#   braille display service + speech server + live braille ticker (foreground).
# The display service needs root:  sudo bash launch-dotify.sh
cd "$(dirname "$0")" || exit 1
exec bash hardware/start_appliance.sh
