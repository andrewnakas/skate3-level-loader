#!/usr/bin/env python3
"""Moved into the package as loader/cli_play.py - see the note there.

Kept as a shim because scripts and notes across this repo invoke it by path.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loader.cli_play import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
