"""
Pytest configuration. Adds `src/` to sys.path so tests can import from
the project modules (tools, agents, etc.) without needing PYTHONPATH=src
on the command line.
"""
import sys
from pathlib import Path

# Add `src/` directory to Python's import path
SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))