#!/usr/bin/env python3
"""The vendored freeskate must match the one it was taken from.

`loader/vendor/freeskate.py` is the copy the app ships and imports; the
standalone script beside it is what a developer still runs by hand. They are the
same file, and a fix applied to one and not the other is the kind of divergence
this project has already been bitten by more than once.

In CI only the vendored copy exists, so this degrades to a structural check:
the module imports, and the functions the loader calls into are present. Point
`--against` at a checkout of the standalone script for the real comparison.

    python3 scripts/vendor_check.py
    python3 scripts/vendor_check.py --against ../freeskate/freeskate
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENDORED = ROOT / "loader" / "vendor" / "freeskate.py"
#: Everything loader/staging.py calls. Losing any of these silently breaks
#: staging at runtime rather than at import.
REQUIRED = ("Config", "GameMap", "load_map", "stage", "build_command", "Fail")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--against", default=None,
                        help="path to the standalone freeskate script")
    args = parser.parse_args(argv)

    if not VENDORED.is_file():
        print(f"FAIL: no vendored freeskate at {VENDORED}", file=sys.stderr)
        return 1

    sys.path.insert(0, str(ROOT))
    from loader.vendor import freeskate  # noqa: PLC0415

    missing = [name for name in REQUIRED if not hasattr(freeskate, name)]
    if missing:
        print(f"FAIL: vendored freeskate is missing {', '.join(missing)}", file=sys.stderr)
        return 1
    print(f"ok: vendored freeskate has all {len(REQUIRED)} entry points")

    if args.against:
        other = Path(args.against).expanduser()
        if not other.is_file():
            print(f"FAIL: no such file {other}", file=sys.stderr)
            return 1
        if digest(other) != digest(VENDORED):
            print(f"FAIL: {other} and {VENDORED} have diverged.\n"
                  f"      Copy whichever is newer over the other.", file=sys.stderr)
            return 1
        print(f"ok: identical to {other}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
