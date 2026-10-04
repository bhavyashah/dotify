#!/usr/bin/env bash
# Prepare the real braille display for the ticker (Linux or WSL, with sudo).
#
#   sudo bash Linux/hardware/start_display.sh
#
# Prerequisites:
#   * WSL only: usbipd-win on Windows, the display shared once (elevated)
#       usbipd bind --busid <BUSID>
#     and attached after each plug-in or reboot
#       usbipd attach --busid <BUSID> --wsl
#     (`usbipd list` shows the bus id).
#   * brltty, python3-brlapi and liblouis installed (apt).
#
# What this script enforces (each of these breaks the display otherwise):
#   1. Ubuntu's auto-spawned BRLTTY instances stay off — two BRLTTYs fight
#      over the USB device and the display flaps ("braille driver restarted").
#   2. Exactly one BRLTTY runs, with the NoScreen driver (-x no): with a
#      screen driver, BRLTTY paints the console and a BrlAPI client bound to
#      a tty is never shown (no VT focus under WSL).
#   3. Stale BrlAPI sockets are removed so the server can bind.
set -u

# BRLTTY auto-detects any supported display (-b auto below), so a missing
# HumanWare device (USB vendor 1c71) is only a warning.
if ! lsusb | grep -qi 1c71; then
  echo "!! No HumanWare display (usb 1c71) seen. Under WSL, attach it from Windows:"
  echo "     usbipd attach --busid <BUSID> --wsl"
  echo "   Continuing anyway in case another brand of display is attached."
fi

systemctl stop brltty-udev.service brltty.service 2>/dev/null || true
systemctl mask brltty-udev.service brltty.service 2>/dev/null || true
pkill -9 -x brltty 2>/dev/null || true
sleep 1
rm -f /var/lib/BrlAPI/.0 /var/lib/BrlAPI/0 /var/run/brltty.pid

# -b auto: any BRLTTY-supported display. -d usb:,bluetooth: tries USB first,
# then Bluetooth. Bluetooth does not work under WSL2, which has no access to
# the Windows Bluetooth radio; on native Linux it needs BlueZ and usually the
# display's address (bluetooth:AA:BB:...).
brltty -b auto -d usb:,bluetooth: -x no -l warning
sleep 2
if pgrep -x brltty >/dev/null; then
  echo "display ready: brltty (NoScreen) running, pid $(pgrep -x brltty)"
  echo "next:  sudo python3 run.py --source ws --sink brlapi     (live speech)"
  echo "  or:  sudo python3 run.py --file sample.txt --sink brlapi"
else
  echo "!! brltty failed to start"; exit 1
fi
