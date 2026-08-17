#!/usr/bin/env python3
"""Break a boot log into phases, so a timing change is attributed, not guessed.

    python3 scripts/boottime.py [log] [--csv]

Every marker below was picked because it is emitted exactly once per boot and
sits on the critical path. `playable` is the FIRST `taking over natively`: the
loader's own RESULT: LANDED can be true with a black screen (the streaming lines
name the right world even when nothing ever renders), so time-to-playable has to
come from the renderer engaging, not from the world being seen.
"""

from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path

TS = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})\]")

# (label, substring) - first match wins.
MARKERS = [
    ("start", "skate3 starting"),
    # Windowed boots only ever make the 1280x720 swapchain; fullscreen makes
    # that one and then a 1920x1080 one. Match the first either way.
    ("vulkan", "VulkanPresenter: Created"),
    ("guest_launch", "KernelState: Preparing module launch"),
    ("fe_language", "BootFlow LanguageSelectState event=0"),
    ("movie_done", "forcing frontend intro movie complete"),
    ("press_start", "BootFlow ShowPressStartMode event=1"),
    ("fe_exit", "XGIUserSetContextEx(user=0, context=0x8001, value=0x0)"),
    ("gameplay", "gameplay reached; injecting"),
    ("macro_first", "injected gameplay input 1/"),
    ("macro_last", "gameplay input sequence complete"),
    ("playable", "native-scene: taking over natively"),
]

PHASES = [
    ("host init", "start", "vulkan"),
    ("guest boot", "vulkan", "fe_language"),
    ("fe/movie", "fe_language", "movie_done"),
    ("press-start", "movie_done", "fe_exit"),
    ("boot world load", "fe_exit", "gameplay"),
    ("macro settle", "gameplay", "macro_first"),
    ("macro", "macro_first", "macro_last"),
    ("activate", "macro_last", "playable"),
]


def parse(path: Path) -> dict[str, float]:
    """Absolute seconds for each marker, relative to process start."""
    times: dict[str, float] = {}
    takeovers: list[float] = []
    base: datetime | None = None
    with path.open(errors="replace") as handle:
        for line in handle:
            match = TS.match(line)
            if not match:
                continue
            when = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S.%f")
            if base is None:
                base = when
            offset = (when - base).total_seconds()
            if "native-scene: taking over natively" in line:
                takeovers.append(offset)
            for label, needle in MARKERS:
                if label not in times and needle in line:
                    times[label] = offset
    # The FIRST takeover is not always the playable one. On some maps the boot
    # warp trips it before the menu confirm - items in the scene, black on the
    # glass - so once a macro has run, only a takeover after it counts.
    if "macro_last" in times:
        after = [t for t in takeovers if t >= times["macro_last"]]
        if after:
            times["playable"] = after[0]
        else:
            times.pop("playable", None)
    return times


def report(path: Path, csv: bool = False) -> int:
    times = parse(path)
    if "start" not in times:
        print(f"no timestamped lines in {path}", file=sys.stderr)
        return 1
    if csv:
        print(",".join(label for label, _, _ in PHASES) + ",total")
        row = []
        for _, begin, end in PHASES:
            row.append(
                f"{times[end] - times[begin]:.2f}"
                if begin in times and end in times
                else ""
            )
        row.append(f"{times.get('playable', float('nan')):.2f}")
        print(",".join(row))
        return 0

    print(f":: {path}")
    width = max(len(label) for label, _, _ in PHASES)
    for label, begin, end in PHASES:
        if begin not in times or end not in times:
            print(f"   {label:<{width}}   (missing {begin if begin not in times else end})")
            continue
        span = times[end] - times[begin]
        bar = "#" * int(span * 4)
        print(f"   {label:<{width}}  {span:6.2f}s  @{times[end]:6.2f}  {bar}")
    if "playable" in times:
        print(f"   {'TOTAL':<{width}}  {times['playable']:6.2f}s to playable")
    else:
        print("   never rendered (no 'taking over natively')")
    return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    log = Path(args[0]) if args else Path(
        "/home/nakas/Documents/skate3/freeskate/runtime/logs/freeskate.log"
    )
    raise SystemExit(report(log, csv="--csv" in sys.argv))
