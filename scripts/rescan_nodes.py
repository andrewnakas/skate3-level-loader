#!/usr/bin/env python3
"""Backfill each pack's spawn-node list into the catalog.

`Pack.warp_safe` needs to know which locations a pack really contains, and that
is only in the container. Scanning is slow (~12 s for a 292 MB .big), so it is
done once here rather than per launch.

    python3 scripts/rescan_nodes.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loader import bigscan, catalog, config  # noqa: E402


def main() -> int:
    for pack in catalog.load_all(config.CATALOG_DIR):
        source = Path(pack.big)
        if not source.is_file():
            print(f"  {pack.id:20s} SKIP - {source} is missing")
            continue
        pack.spawn_nodes = bigscan.spawn_nodes(source)
        catalog.save(pack, config.CATALOG_DIR)
        state = "warp ok " if pack.warp_safe else "WARP OFF"
        print(f"  {pack.id:20s} {len(pack.spawn_nodes):2d} nodes  {state}"
              f"  {pack.spawn_nodes[:3]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
