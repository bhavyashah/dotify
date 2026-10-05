"""Braille ticker for the documentation screenshots.

Runs the staged Windows ticker (windows_run.py) on the simulated display,
named like a real one so the window says "Connected to APH Mantis Q40."
instead of the simulator's class name. Everything else is the shipping
code path. Started by capture.js; not part of the app.

Usage: python capture_display.py <display name> <windows_run.py> [ticker args]
"""

import runpy
import sys

from braille_engine.sinks.simulated import SimulatedSink

SimulatedSink.display_name = sys.argv[1]
sys.argv = sys.argv[2:]
runpy.run_path(sys.argv[0], run_name="__main__")
