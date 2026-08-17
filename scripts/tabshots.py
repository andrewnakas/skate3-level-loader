#!/usr/bin/env python3
"""Photograph the challenge-map screen after each `rt` press.

`tabprobe` looks for a word that tracks the tab; this answers the prior
question it depends on - does `rt` move the tab at all, and how many presses
does Locations actually take? One frame per press is ground truth, and unlike a
memory probe it needs nothing to be findable first.

Nothing is ever confirmed here, so the run cannot teleport and the frames are
all of the same screen.

    python3 scripts/tabshots.py [world] [--presses 6] [--gap 2500]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loader import catalog, config, guestmem, launch  # noqa: E402

OUT = config.SPOTS_DIR / "tabs"


def shoot(pid: int, path: Path, label: str) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [sys.executable, str(Path(__file__).parent / "capture.py"), str(path),
         "--pid", str(pid)],
        capture_output=True, text=True)
    ok = path.exists()
    print(f"   {label:22s} {'shot' if ok else 'FAILED'}  "
          f"{result.stderr.strip().splitlines()[-1] if result.stderr.strip() else ''}")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("world", nargs="?", default="sk8itspillway")
    parser.add_argument("--presses", type=int, default=6)
    parser.add_argument("--gap", type=int, default=2500)
    parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args()

    pack = entry = None
    for candidate in catalog.load_all(config.CATALOG_DIR):
        found = candidate.map_by_world(args.world)
        if found:
            pack, entry = candidate, found
            break
    if entry is None:
        print(f"no pack owns {args.world!r}", file=sys.stderr)
        return 1

    macro = "start,a," + ",".join([f"rt:{args.gap}"] * args.presses)
    print(f":: macro {macro}")
    session = launch.launch(pack, entry, macro, windowed=True, settle_ms=2500,
                            extra_cvars={
                                "skate3_fe_debug": "true",
                                # The loading overlay exists to HIDE the menu
                                # automation, which is the thing being watched.
                                "skate3_loader_overlay": "false",
                                # And the boot-stream substitution leaves the
                                # guest submitting no draw records at all, so
                                # the MENU is not drawn either - the screen is
                                # black until a confirm. Boot the stock world
                                # instead; slower, but the menus are visible.
                                "skate3_warp_substitute_folder": "false",
                                "skate3_warp_substitute_slug": "false",
                                "skate3_warp_substitute_item": "false"})
    pid = session.process.pid

    try:
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            try:
                with guestmem.GuestMemory() as memory:
                    if any(e.screen_id == 17 for e in memory.screen_stack()):
                        break
            except (guestmem.NotRunning, OSError):
                pass
            if session.process.poll() is not None:
                print("game exited before the challenge map opened", file=sys.stderr)
                return 2
            time.sleep(0.25)

        # The challenge map is up and the first rt is one delay away. Shoot
        # just before each press lands, so frame N is the state after N presses.
        print(":: challenge map up")
        shoot(pid, OUT / "tab_0_before.png", "before any rt")
        for index in range(1, args.presses + 1):
            time.sleep(args.gap / 1000.0)
            shoot(pid, OUT / f"tab_{index}.png", f"after rt #{index}")
    finally:
        session.stop()
        try:
            session.process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            pass
        launch.kill_running_game(timeout=30)
    print(f":: frames in {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
