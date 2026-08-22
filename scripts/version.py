#!/usr/bin/env python3
"""Print the loader's version. The release tag must be `v` + this.

A one-liner in the workflow got this wrong in a way worth keeping a file over:
`re.search('"(.*)"')` matched the first quoted thing in _version.py, which is
the docstring's opening triple quote, so the tag check compared v0.1.0 against
a single `"` and failed a build that was otherwise finished.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

SOURCE = Path(__file__).resolve().parent.parent / "loader" / "_version.py"


def version() -> str:
    match = re.search(r'^__version__\s*=\s*"([^"]+)"', SOURCE.read_text(), re.M)
    if not match:
        raise SystemExit(f"no __version__ in {SOURCE}")
    return match.group(1)


if __name__ == "__main__":
    print(version())
    sys.exit(0)
