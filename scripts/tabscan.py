#!/usr/bin/env python3
"""Find the tab state by diffing the whole guest heap across each `rt` press.

`tabprobe` watches the frontend manager and the screen object it points at, and
across five presses found nothing at all - so the selected tab is not stored in
either. Rather than guess how much deeper to chase pointers, scan everything.

The method: snapshot the heap, press `rt`, snapshot again, five times. A word
that holds the tab MUST change on every press. Almost nothing else does - most
of the heap is static, and the animation that is not is filtered by requiring a
change in EVERY interval plus a small set of distinct values.

That `rt` really is load-bearing is established separately: the same macro with
the three `rt` presses removed lands in a different world (verified by
screenshot, distance 692 against a confirmed reference).

    python3 scripts/tabscan.py [world] [--presses 5] [--gap 4000]
"""

from __future__ import annotations

import argparse
import array
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loader import catalog, config, guestmem, launch  # noqa: E402

# The guest heap the frontend lives in. The manager sits at 0x43A88D80 and the
# menu item arrays at 0x40B9xxxx / 0x4188xxxx, so this span covers them all.
HEAP_BEGIN = 0x40000000
HEAP_END = 0x48000000
CHUNK = 8 << 20


def snapshot(memory: guestmem.GuestMemory) -> array.array:
    """The heap as big-endian u32s. ~7 s of pread on this machine."""
    words = array.array("I")
    for base in range(HEAP_BEGIN, HEAP_END, CHUNK):
        raw = memory.read(base, min(CHUNK, HEAP_END - base))
        block = array.array("I")
        block.frombytes(raw.ljust(min(CHUNK, HEAP_END - base), b"\0"))
        words.extend(block)
    if sys.byteorder == "little":
        words.byteswap()
    return words


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("world", nargs="?", default="sk8itspillway")
    parser.add_argument("--presses", type=int, default=5)
    parser.add_argument("--gap", type=int, default=4000,
                        help="ms between rt presses; must exceed the scan time")
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
                            extra_cvars={"skate3_fe_debug": "true",
                                         "skate3_loader_overlay": "false"})

    shots: list[array.array] = []
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
            time.sleep(0.2)

        print(":: challenge map up; scanning")
        for index in range(args.presses + 1):
            began = time.monotonic()
            with guestmem.GuestMemory() as memory:
                shots.append(snapshot(memory))
            print(f"   snapshot {index} in {time.monotonic() - began:.1f}s", flush=True)
            if index < args.presses:
                # Wait out the rest of this press interval.
                time.sleep(max(0.0, args.gap / 1000.0 - (time.monotonic() - began)))
    finally:
        session.stop()
        try:
            session.process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            pass
        launch.kill_running_game(timeout=30)

    if len(shots) < 3:
        print("not enough snapshots", file=sys.stderr)
        return 2

    # Words that changed across EVERY interval.
    count = len(shots[0])
    changed = None
    for before, after in zip(shots, shots[1:]):
        now = {i for i in range(count) if before[i] != after[i]}
        changed = now if changed is None else (changed & now)
        print(f"   {len(now):>9,} changed this interval; "
              f"{len(changed):>9,} changed in all so far", flush=True)
        if not changed:
            break

    print(f"\n{len(changed or [])} word(s) changed on every one of "
          f"{args.presses} presses")
    rows = []
    for index in sorted(changed or []):
        series = [shot[index] for shot in shots]
        distinct = sorted(set(series))
        if len(distinct) > args.presses + 2:
            continue
        rows.append((len(distinct), HEAP_BEGIN + index * 4, series))
    rows.sort()
    print(f"of those, {len(rows)} have a small value set:")
    for _, address, series in rows[:40]:
        text = " ".join(f"{v:08X}" if v > 0xFFFF else str(v) for v in series)
        print(f"   0x{address:08X}  {text}")
    if not rows:
        print("   (none)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
