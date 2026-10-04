import os
import sys

# Ensure the project root is importable so tests can `import run` and
# `import braille_engine` when pytest is invoked from anywhere.
sys.path.insert(0, os.path.dirname(__file__))
