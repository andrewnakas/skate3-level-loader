#!/usr/bin/env python3
"""End-to-end smoke test: stage a pack, boot it, drive the macro, report where we
actually landed. No GUI -- this exists to prove the pipeline before the loading
screen is layered on top.

    python3 scripts/smoke_launch.py <world-id> [--windowed] [--timeout 200]
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loader import catalog, config, launch, logwatch, navigate  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("world", nargs="?", default=None)
    parser.add_argument("--windowed", action="store_true")
    parser.add_argument("--timeout", type=int, default=220)
    parser.add_argument("--index", type=int, default=None, help="sub-list index within the pack")
    parser.add_argument(
        "--cvar",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="extra engine flag, repeatable (e.g. --cvar skate3_trace=true)",
    )
    parser.add_argument(
        "--keep-open",
        action="store_true",
        help="leave the game running after the target world lands (the trace dump "
        "arrives seconds later)",
    )
    args = parser.parse_args()
    extra_cvars = dict(item.split("=", 1) for item in args.cvar)

    packs = catalog.load_all(config.CATALOG_DIR)
    if not packs:
        print("no packs imported yet", file=sys.stderr)
        return 1
    # More than one pack is imported now, so pick the one that owns the
    # requested world rather than assuming it is the first.
    pack = packs[0]
    entry = None
    if args.world:
        for candidate in packs:
            found = candidate.map_by_world(args.world)
            if found is not None:
                pack, entry = candidate, found
                break
    else:
        entry = pack.maps[-1]
    if entry is None:
        print(f"no map {args.world!r} in any imported pack", file=sys.stderr)
        for candidate in packs:
            print(f"  {candidate.id}: " + ", ".join(m.world_id for m in candidate.maps))
        return 1

    index = args.index if args.index is not None else pack.maps.index(entry)
    macro = navigate.build_macro(index, row_patch=not pack.spots_share_world)

    print(f":: pack   {pack.name} ({pack.package})")
    print(f":: target {entry.name} [{entry.world_id}]")
    print(f":: index  {index}")
    print(f":: macro  {macro}")
    print(f":: binary {config.default_binary()}")

    if extra_cvars:
        print(f":: cvars  {extra_cvars}")

    session = launch.launch(pack, entry, macro, windowed=args.windowed, extra_cvars=extra_cvars)
    watcher = logwatch.LogWatcher(
        session.log_file, [m.world_id for m in pack.maps], entry.world_id
    )

    print(f":: log    {session.log_file}")
    start = time.monotonic()
    last = ""
    try:
        while time.monotonic() - start < args.timeout:
            status = watcher.poll()
            line = f"{status.fraction * 100:5.1f}%  {status.stage.value:<16} {status.detail}"
            if line != last:
                print(f"   [{time.monotonic() - start:6.1f}s] {line}")
                last = line
            if status.stage is logwatch.Stage.READY or status.failed_reason:
                break
            if session.process.poll() is not None:
                print(f"   game exited with code {session.process.returncode}")
                break
            time.sleep(0.5)
        if args.keep_open:
            # The trace controller dumps some seconds after the macro finishes,
            # which can be after the world has already landed - killing the game
            # at that point loses the file.
            print("   [keep-open] holding 25s so any pending trace dump lands")
            time.sleep(25)
    finally:
        watcher.poll()
        status = watcher.status
        print()
        print(f":: world seen : {status.world_seen}")
        print(f":: target     : {entry.world_id}")
        print(f":: RESULT     : {'LANDED' if watcher.landed_correctly else 'MISSED'}")
        if watcher.wrong_world:
            print(f":: wrong map  : {watcher.wrong_world}")
        session.stop()
        # A traced run has more to tear down than a plain one and can take
        # well over the 10 s default, which then reports a spurious failure
        # after a perfectly good capture.
        launch.kill_running_game(timeout=40)
    return 0 if watcher.landed_correctly else 2


if __name__ == "__main__":
    raise SystemExit(main())
