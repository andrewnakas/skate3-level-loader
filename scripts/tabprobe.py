#!/usr/bin/env python3
"""Find the frontend word that tracks the CHALLENGE-MAP TAB.

Why: `rt` x3 is supposed to reach the Locations tab, and it is not reliable -
sometimes the run confirms a CHALLENGE instead (a live score/LINE counter on
screen), which teleports the player somewhere unrelated. No log line exposes
which tab is selected, so the macro is pressing blind. Give the engine a word it
can read and the press can be confirmed the way the opening `start` already is.

Two things this does that the first version did not:

  * It follows `mgr+0x264` into the CURRENT SCREEN OBJECT. The manager's own
    first 2 KiB holds no cursor - stepping the Locations list 13 rows moved only
    fade ramps and alpha there - so a window over the manager alone cannot see a
    tab. The engine's own `fe-debug` watcher already looks one level down for
    exactly this reason.
  * It correlates candidates against WHEN the presses actually happened, read
    out of the game's log, instead of assuming they landed on schedule. A word
    that steps once per press and holds still in between is a tab index; a word
    that drifts on its own is animation.

    python3 scripts/tabprobe.py [world] [--presses 5] [--gap 3000]
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loader import catalog, config, guestmem, launch  # noqa: E402

MANAGER_WORDS = 512
SCREEN_WORDS = 256
# Where the manager keeps a pointer to the screen that is up. Both are watched:
# the engine's fe-debug defaults to +0x264 and notes +0x254 alongside it.
SCREEN_PTR_OFFSETS = (0x254, 0x264)
SAMPLE_HZ = 8.0

RE_INJECT = re.compile(
    r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})\].*"
    r"injected gameplay input (\d+)/(\d+) '([a-z0-9]+)'")
RE_STEPPED = re.compile(r"fe-debug: screen ([0-9A-F]{8}) stepped \d+: (.*)")


def log_time(stamp: str) -> float:
    from datetime import datetime
    return datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S.%f").timestamp()


def sample(memory: guestmem.GuestMemory, manager: int) -> dict:
    """One observation: the manager window plus whatever screens it points at."""
    out = {"manager": memory.words(manager, MANAGER_WORDS), "screens": {}}
    for offset in SCREEN_PTR_OFFSETS:
        pointer = memory.u32(manager + offset)
        if pointer and guestmem.looks_like_guest_pointer(pointer):
            words = memory.words(pointer, SCREEN_WORDS)
            if words:
                out["screens"][pointer] = words
    return out


def series_for(samples: list[tuple[float, dict]], where: str, key, index: int):
    """The value of one word over time, and the times it was readable."""
    times, values = [], []
    for when, snap in samples:
        block = snap["manager"] if where == "manager" else snap["screens"].get(key)
        if block and index < len(block):
            times.append(when)
            values.append(block[index])
    return times, values


WINDOW_S = 1.5


def score(times: list[float], values: list[int], presses: list[float]) -> tuple | None:
    """How well a word's changes line up with the presses.

    Deliberately makes NO assumption about how a tab is represented. The first
    version of this required small integers stepping by exactly +/-1 and found
    nothing at all across five presses - but a selected tab can just as easily
    be a pointer to the tab's object, a name hash, or a float animating to a
    new resting value. What is common to all of those is the TIMING: the word
    changes just after a press and holds still in between.

    Ranked by (fraction of changes explained by a press, presses covered), so a
    word that changes once per press and never otherwise sorts to the top and a
    free-running animation sorts to the bottom.
    """
    if len(values) < 4 or len(presses) < 2:
        return None
    changes = [times[i] for i in range(1, len(values)) if values[i] != values[i - 1]]
    if len(changes) < 2:
        return None
    explained = sum(1 for when in changes
                    if any(0 <= when - press <= WINDOW_S for press in presses))
    covered = sum(1 for press in presses
                  if any(0 <= when - press <= WINDOW_S for when in changes))
    # A word that changes on every sample is animation, not state.
    if len(changes) > len(presses) * 3:
        return None
    distinct = sorted(set(values))
    return (explained / len(changes), covered, len(changes),
            distinct[:8], values[0], values[-1])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("world", nargs="?", default="sk8itspillway")
    parser.add_argument("--presses", type=int, default=5)
    parser.add_argument("--gap", type=int, default=3000, help="ms between rt presses")
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

    # Reach the challenge map and then just tab, slowly, with no confirm - the
    # run must not teleport anywhere while we are reading.
    macro = "start,a," + ",".join([f"rt:{args.gap}"] * args.presses)
    print(f":: macro {macro}")
    session = launch.launch(pack, entry, macro, windowed=True, settle_ms=2500,
                            extra_cvars={"skate3_fe_debug": "true",
                                         # The loading overlay exists to HIDE the menu
                                         # automation, which is the thing being watched.
                                         "skate3_loader_overlay": "false"})
    log = Path(session.log_file)

    samples: list[tuple[float, dict]] = []
    manager = 0
    try:
        deadline = time.monotonic() + args.timeout
        # Wait for the challenge map to be up before sampling.
        while time.monotonic() < deadline:
            try:
                with guestmem.GuestMemory() as memory:
                    mgr = memory.frontend_manager()
                    if mgr:
                        manager = mgr
                        if any(e.screen_id == 17 for e in memory.screen_stack()):
                            break
            except (guestmem.NotRunning, OSError):
                pass
            if session.process.poll() is not None:
                print("game exited before the challenge map opened", file=sys.stderr)
                return 2
            time.sleep(0.25)

        if not manager:
            print("never found the frontend manager", file=sys.stderr)
            return 2
        print(f":: manager 0x{manager:08X}; sampling {args.presses} tab presses")

        end = time.monotonic() + (args.presses + 2) * args.gap / 1000.0
        while time.monotonic() < end:
            try:
                with guestmem.GuestMemory() as memory:
                    samples.append((time.monotonic(), sample(memory, manager)))
            except (guestmem.NotRunning, OSError):
                pass
            time.sleep(1.0 / SAMPLE_HZ)
    finally:
        session.stop()
        try:
            session.process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            pass
        launch.kill_running_game(timeout=30)

    text = log.read_text(errors="replace") if log.exists() else ""
    kept = config.SPOTS_DIR / "tabprobe.log"
    kept.parent.mkdir(parents=True, exist_ok=True)
    kept.write_text(text)

    # Anchor log timestamps to the monotonic clock the samples used, via the
    # last sample taken: both are wall-clock-adjacent within a run.
    injects = [(log_time(m.group(1)), m.group(4)) for m in RE_INJECT.finditer(text)]
    if not injects or not samples:
        print("no injected inputs in the log" if not injects else "no samples")
        return 2
    offset = samples[-1][0] - injects[-1][0]
    presses = [t + offset for t, name in injects if name == "rt"]
    print(f":: {len(injects)} inputs injected, {len(presses)} of them rt; "
          f"{len(samples)} samples")
    if not presses:
        return 2

    seen_screens = sorted({addr for _, s in samples for addr in s["screens"]})
    print(f":: screen objects seen: {', '.join(f'0x{a:08X}' for a in seen_screens)}")

    results = []
    for where, key, count in ([("manager", None, MANAGER_WORDS)]
                              + [("screen", a, SCREEN_WORDS) for a in seen_screens]):
        for index in range(count):
            times, values = series_for(samples, where, key, index)
            got = score(times, values, presses)
            if got:
                results.append((got[0], got[1], where, key, index * 4, got[2:]))

    results.sort(reverse=True)
    print(f"\n{len(results)} word(s) that change in step with the presses, best first:")
    print(f"   {'where':>18s}  {'hit':>4s} {'cov':>3s} {'chg':>3s}  values")
    for explained, covered, where, key, offset_bytes, extra in results[:20]:
        who = "mgr" if where == "manager" else f"0x{key:08X}"
        values = ", ".join(f"{v:08X}" if v > 0xFFFF else str(v) for v in extra[1])
        print(f"   {who}+{offset_bytes:03X}  {explained*100:3.0f}% "
              f"{covered}/{len(presses)} {extra[0]:3d}  [{values}]")
    if not results:
        print("   (none)")

    stepped = RE_STEPPED.findall(text)
    print(f"\nengine fe-debug reported {len(stepped)} 'screen ... stepped' line(s)")
    for addr, diff in stepped[:15]:
        print(f"   {addr}  {diff}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
