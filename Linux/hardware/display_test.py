"""Write a line of text to the real display and hold it — a quick tactile
check that the whole hardware path works, independent of the ticker.

    sudo python3 hardware/display_test.py "HELLO" 30

Uses the same connection style as the engine's BrlapiSink: a GLOBAL claim
(empty tty path). Requires hardware/start_display.sh to have been run.
"""
import sys
import time

import brlapi

msg = sys.argv[1] if len(sys.argv) > 1 else "DISPLAY TEST"
hold = float(sys.argv[2]) if len(sys.argv) > 2 else 30.0

b = brlapi.Connection()
b.enterTtyModeWithPath([])
cols, rows = b.displaySize
print(f"display: {cols} cells x {rows} row(s)")
b.writeText(msg[:cols].ljust(cols))
print(f"wrote {msg!r}; holding {hold:.0f}s — feel the display now")
time.sleep(hold)
b.writeText(" " * cols)
b.leaveTtyMode()
b.closeConnection()
print("cleared, done")
