#!/usr/bin/env python3
"""Build the catalog that SHIPS, from the catalog on this machine.

A catalog record is two things stuck together: curation - the pack's maps,
their world ids, spawn nodes, and whether each one is known to load or known to
hang - and a POINTER to a file on the machine that imported it. The first is
worth shipping and took 120 test runs to establish. The second is meaningless
anywhere else, and on this machine some of it points into a deleted scratchpad.

So the seed keeps the curation and drops `big` and `header`. The launcher shows
such a pack as needing its file located (Pack.located), and `relocate()` fills
the pointer in from the user's own copy.

    python3 packaging/make_seed_catalog.py --out build/seed-catalog
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: Machine-local pointers. Everything else in a record is portable.
STRIPPED = ("big", "header")


def build(source: Path, out: Path) -> tuple[int, int]:
    out.mkdir(parents=True, exist_ok=True)
    for stale in out.glob("*.json"):
        stale.unlink()

    packs, stripped = 0, 0
    for path in sorted(source.glob("*.json")):
        record = json.loads(path.read_text())
        for key in STRIPPED:
            if record.get(key):
                stripped += 1
            record[key] = ""
        (out / path.name).write_text(json.dumps(record, indent=2) + "\n")
        packs += 1
    return packs, stripped


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default=str(ROOT / "catalog"))
    parser.add_argument("--out", default=str(ROOT / "build" / "seed-catalog"))
    args = parser.parse_args(argv)

    source, out = Path(args.source), Path(args.out)
    if not source.is_dir():
        print(f"no catalog at {source}", file=sys.stderr)
        return 1
    packs, stripped = build(source, out)

    # Assert the post-condition rather than trusting the loop: a seed that still
    # carried a /home path would be published, and this project has been bitten
    # before by preflights that report success without having done anything.
    leaked = [p.name for p in out.glob("*.json")
              if any(record.get(key) for key in STRIPPED
                     for record in [json.loads(p.read_text())])]
    if leaked:
        print(f"REFUSING: {len(leaked)} seed records still carry a path: {leaked[:3]}",
              file=sys.stderr)
        return 2
    print(f":: {packs} packs, {stripped} paths stripped -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
