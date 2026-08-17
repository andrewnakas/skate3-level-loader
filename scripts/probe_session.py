#!/usr/bin/env python3
"""One boot that parks in the Locations list and steps the cursor slowly, while
reading guest memory from outside to find the selection index.

This replaces the "one boot per probe" calibration budget: instead of learning a
single suffix per launch, we watch the cursor move in real time and learn the
whole list in one session.
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loader import catalog, config, guestmem, launch, logwatch  # noqa: E402

PROBE_ROOT = Path(__file__).resolve().parent


def build_probe_macro(
    steps: int, dwell_ms: int, pattern: str = "down", pre: str = ""
) -> str:
    """Reach Locations, then step the cursor with a long dwell between presses.

    `pre` runs after the tab presses and before the stepping -- used to descend
    into a pack's sub-list before watching its cursor.

    Deliberately omits the trailing confirm presses: we want to SIT in the list
    and watch it, not teleport out of it.
    """
    tokens = ["start", "a", "rt:1500", "rt:1500", "rt:1500"]
    tokens += [t.strip() for t in pre.split(",") if t.strip()]
    moves = [m.strip() for m in pattern.split(",") if m.strip()]
    for index in range(steps):
        tokens.append(f"{moves[index % len(moves)]}:{dwell_ms}")
    return ",".join(tokens)


def wait_for_screen(target: int, timeout: float, poll: float = 0.5):
    """Wait until the frontend stack shows a screen id, returning the memory handle."""
    deadline = time.monotonic() + timeout
    memory = None
    while time.monotonic() < deadline:
        if memory is None:
            try:
                memory = guestmem.GuestMemory()
            except guestmem.NotRunning:
                time.sleep(poll)
                continue
        stack = memory.screen_stack()
        if any(entry.screen_id == target for entry in stack):
            return memory
        time.sleep(poll)
    return memory


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--steps", type=int, default=14)
    parser.add_argument("--dwell", type=int, default=2500, help="ms between cursor steps")
    parser.add_argument("--boot-timeout", type=float, default=180.0)
    parser.add_argument("--windowed", action="store_true", default=True)
    parser.add_argument("--pattern", default="down", help="comma-separated moves to cycle")
    parser.add_argument("--pre", default="", help="tokens to run before stepping (e.g. down*40 entry)")
    parser.add_argument(
        "--mode",
        choices=["hunt", "inspect"],
        default="hunt",
        help="hunt = find stepping words; inspect = read the known cursor path",
    )
    args = parser.parse_args()

    packs = catalog.load_all(config.CATALOG_DIR)
    if not packs:
        print("no packs imported", file=sys.stderr)
        return 1
    pack = packs[0]
    entry = pack.maps[0]

    macro = build_probe_macro(args.steps, args.dwell, args.pattern, args.pre)
    print(f":: pack  {pack.name}")
    print(f":: macro {macro}")

    session = launch.launch(pack, entry, macro, windowed=args.windowed)
    watcher = logwatch.LogWatcher(
        session.log_file, [m.world_id for m in pack.maps], entry.world_id
    )

    try:
        print(":: booting (waiting for the Locations screen)...")
        deadline = time.monotonic() + args.boot_timeout
        memory = None
        while time.monotonic() < deadline:
            status = watcher.poll()
            if memory is None:
                try:
                    memory = guestmem.GuestMemory()
                except guestmem.NotRunning:
                    memory = None
            if memory is not None:
                stack = memory.screen_stack()
                if any(e.screen_id == 17 for e in stack):
                    print(f":: reached Locations at {status.stage.value}")
                    break
            if session.process.poll() is not None:
                print(f":: game exited early ({session.process.returncode})")
                return 3
            time.sleep(0.5)
        else:
            print(":: timed out before reaching the Locations screen")
            return 3

        print(f":: segment {memory.path}")
        manager = memory.frontend_manager()
        print(f":: manager {manager:08X}")
        print()

        duration = (args.steps * args.dwell / 1000.0) + 5
        if args.mode == "hunt":
            child = [
                sys.executable,
                str(PROBE_ROOT / "fe_probe.py"),
                "hunt",
                "--seconds",
                str(duration),
                "--interval",
                "0.2",
            ]
        else:
            subprocess.run(
                [sys.executable, str(PROBE_ROOT / "fe_inspect.py"), "dump"], text=True
            )
            child = [
                sys.executable,
                str(PROBE_ROOT / "fe_inspect.py"),
                "follow",
                "--seconds",
                str(duration),
            ]
        result = subprocess.run(child, text=True)
        return result.returncode
    finally:
        session.stop()
        launch.kill_running_game()
        print(":: stopped")


if __name__ == "__main__":
    raise SystemExit(main())
