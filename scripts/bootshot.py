#!/usr/bin/env python3
"""Boot a pack with NO menu macro and photograph wherever the game lands.

For most packs the map is reached by driving the pause menu. But an official DLC
world can be the world the game boots into all by itself, and then the macro is
not just unnecessary - it navigates away from the map and confirms something
else. Danny Way looks exactly like that: its content streams at boot (one
`taking over natively` BEFORE the macro runs, and none after), where a working
pack like Maloof takes over twice, the second time after the confirm.

This is deliberately a separate script from `verifyspot` rather than a flag on
it, so it can be written and run while a sweep is using `verifyspot` as a
subprocess.

    python3 scripts/bootshot.py <world>|<pack>:<index> [label]
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from loader import config, launch, spotcheck  # noqa: E402

from verifyspot import find  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("target")
    parser.add_argument("label", nargs="?", default="boot")
    parser.add_argument("--cvar", action="append", default=[])
    parser.add_argument("--hold", type=float, default=10.0)
    parser.add_argument("--burst", type=int, default=5)
    parser.add_argument("--burst-gap", type=float, default=2.0, dest="burst_gap")
    parser.add_argument("--timeout", type=float, default=200)
    args = parser.parse_args()

    pack, entry = find(args.target)
    key = spotcheck.reference_key(pack.id, entry.sub_index)
    extra = {"skate3_loader_overlay": "false"}
    extra.update(item.split("=", 1) for item in args.cvar)

    print(f":: {entry.name} [{entry.world_id}] from {pack.name}")
    print(f":: NO MACRO - waiting for whatever the game boots into")
    print(f":: warp_safe={pack.warp_safe}")

    # Empty macro: the demo path injects nothing at all.
    session = launch.launch(pack, entry, "", windowed=True, extra_cvars=extra)
    log = Path(session.log_file)
    shot = config.SPOTS_DIR / f"{args.label}__{key}.png"
    burst_dir = config.SPOTS_DIR / "burst"
    burst_dir.mkdir(parents=True, exist_ok=True)

    ok = False
    try:
        start = time.monotonic()
        while time.monotonic() - start < args.timeout:
            try:
                if "taking over natively" in log.read_text(errors="replace"):
                    ok = True
                    break
            except OSError:
                pass
            if session.process.poll() is not None:
                print(":: the game exited before rendering anything")
                break
            time.sleep(0.5)
        if ok:
            print(f":: rendered after {time.monotonic() - start:.0f}s; "
                  f"holding {args.hold}s")
            time.sleep(args.hold)
            frames = []
            for index in range(args.burst):
                frame = burst_dir / f"{args.label}__{key}.{index}.png"
                subprocess.run(
                    [sys.executable, str(Path(__file__).parent / "capture.py"),
                     str(frame), "--pid", str(session.process.pid)],
                    capture_output=True, text=True)
                if frame.exists():
                    frames.append(frame)
                if index + 1 < args.burst:
                    time.sleep(args.burst_gap)
            spreads = sorted(((spotcheck.frame_spread(f), f) for f in frames),
                             reverse=True)
            print(":: frames " + " ".join(f"{s:.0f}" for s, _ in spreads))
            if spreads:
                shutil.copyfile(spreads[0][1], shot)
    finally:
        try:
            (config.SPOTS_DIR / f"{args.label}__{key}.log").write_text(
                log.read_text(errors="replace"))
        except OSError:
            pass
        session.stop()
        try:
            session.process.wait(timeout=25)
        except subprocess.TimeoutExpired:
            pass
        launch.kill_running_game(timeout=40)

    if not (ok and shot.exists()):
        return 2
    print(f":: SHOT {shot} ({shot.stat().st_size} bytes)")
    unrendered = spotcheck.looks_unrendered(shot)
    if unrendered:
        print(f":: NOT A MAP  {unrendered}")
        return 2
    verdict = spotcheck.check(shot, key, config.REFERENCES_DIR,
                              fallback_key=entry.world_id)
    print(f":: VERDICT {verdict}")
    return 0 if verdict.ok is True else (3 if verdict.ok is False else 4)


if __name__ == "__main__":
    raise SystemExit(main())
