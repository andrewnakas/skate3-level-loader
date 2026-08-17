#!/usr/bin/env python3
"""Bucket a `first`-mode guest trace by wall-clock boot phase.

    python3 scripts/tracephase.py <trace> <log> [--window BEGIN:END]

A `first` trace records the first call of each guest function with a cycle
stamp. Anchoring those cycles to two known wall-clock events in the log turns
them into boot-relative seconds, and the functions whose FIRST call lands inside
a stalled phase are the ones that phase is made of. Without the anchor the
stamps are only orderings, which cannot say which phase a function belongs to.

The anchors are the arm (process start, trace entry 0) and the dump, both of
which the log timestamps.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from boottime import MARKERS, TS, parse  # noqa: E402
from tracediff import load  # noqa: E402
from datetime import datetime  # noqa: E402


def dump_time(log: Path) -> float | None:
    """Boot-relative seconds at which the trace was dumped."""
    base = None
    for line in log.read_text(errors="replace").splitlines():
        match = TS.match(line)
        if not match:
            continue
        when = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S.%f")
        if base is None:
            base = when
        if "skate3 trace: DUMPED" in line:
            return (when - base).total_seconds()
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("trace")
    parser.add_argument("log")
    parser.add_argument("--window", default=None, help="BEGIN:END in boot-relative seconds")
    parser.add_argument("--limit", type=int, default=60)
    args = parser.parse_args()

    entries = load(Path(args.trace))
    if not entries:
        print("empty trace", file=sys.stderr)
        return 1
    log = Path(args.log)
    times = parse(log)
    dumped = dump_time(log)
    if dumped is None:
        print("no 'skate3 trace: DUMPED' line in the log - cannot anchor cycles",
              file=sys.stderr)
        return 1

    # Entry 0 is the arm. In 'boot' mode that is process start, near enough:
    # the first guest call follows within milliseconds of the module launch.
    armed_at = times.get("guest_launch", 0.0)
    span_cycles = entries[-1].dcycles - entries[0].dcycles
    span_seconds = dumped - armed_at
    if span_cycles <= 0 or span_seconds <= 0:
        print("degenerate anchor", file=sys.stderr)
        return 1
    per_cycle = span_seconds / span_cycles

    def at(entry) -> float:
        return armed_at + (entry.dcycles - entries[0].dcycles) * per_cycle

    print(f"anchor: guest_launch {armed_at:.2f}s .. dump {dumped:.2f}s "
          f"({span_cycles} cycles, {1 / per_cycle / 1e9:.2f} GHz)")
    for label, _ in MARKERS:
        if label in times:
            print(f"  marker {label:<14} {times[label]:6.2f}s")

    if args.window:
        begin, end = (float(x) for x in args.window.split(":"))
    else:
        begin, end = 0.0, span_seconds + armed_at

    seen: set[str] = set()
    shown = 0
    print(f"\nfirst calls in [{begin:.2f}, {end:.2f}]s:")
    for entry in entries:
        if entry.fn in seen:
            continue
        seen.add(entry.fn)
        when = at(entry)
        if not (begin <= when <= end):
            continue
        print(f"  {when:6.2f}s  {entry.thread:<16} {entry.fn} "
              f"r3={entry.r3:08X} r4={entry.r4:08X} r5={entry.r5:08X} {entry.text}")
        shown += 1
        if shown >= args.limit:
            print(f"  ... ({args.limit} shown; raise --limit)")
            break
    if shown == 0:
        print("  (nothing - the guest called no NEW function in this window, "
              "so it was spinning in code it had already run)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
