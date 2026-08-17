#!/usr/bin/env python3
"""Sample per-thread CPU of the running game, so a boot phase can be classified
as 'the guest is computing' or 'the guest is waiting'.

    python3 scripts/cpuwatch.py [--interval 0.25] [--out FILE]

Waits for the process to appear, samples until it exits, and prints one line per
interval: wall clock, total busy cores, and the three busiest threads. A phase
where nothing is near 100% of a core is a phase spent blocked - the thing worth
attacking - while a pegged thread is real work.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

HZ = 100.0  # kernel USER_HZ; getconf CLK_TCK is 100 on Linux/x86-64


def pid_of() -> int | None:
    out = subprocess.run(["pgrep", "-x", "skate3"], capture_output=True, text=True)
    pids = [int(p) for p in out.stdout.split()]
    return pids[0] if pids else None


def sample(pid: int) -> dict[str, float]:
    """Cumulative busy seconds per thread name."""
    busy: dict[str, float] = {}
    task = Path(f"/proc/{pid}/task")
    try:
        entries = list(task.iterdir())
    except OSError:
        return busy
    for tid in entries:
        try:
            fields = (tid / "stat").read_text().rsplit(") ", 1)[1].split()
            name = (tid / "comm").read_text().strip()
        except (OSError, IndexError):
            continue
        # utime + stime, fields 11 and 12 after the ") " split (0-based 11/12).
        busy[f"{name}#{tid.name}"] = (int(fields[11]) + int(fields[12])) / HZ
    return busy


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=float, default=0.25)
    parser.add_argument("--out", default=None)
    parser.add_argument("--wait", type=float, default=120)
    args = parser.parse_args()

    deadline = time.monotonic() + args.wait
    pid = None
    while pid is None and time.monotonic() < deadline:
        pid = pid_of()
        time.sleep(0.1)
    if pid is None:
        print("no skate3 process appeared", file=sys.stderr)
        return 1

    sink = open(args.out, "w") if args.out else sys.stdout
    start = time.monotonic()
    prev = sample(pid)
    print(f"# pid {pid}  t=0 at {time.strftime('%H:%M:%S')}", file=sink, flush=True)
    while True:
        time.sleep(args.interval)
        now = sample(pid)
        if not now:
            break
        delta = {k: now[k] - prev.get(k, 0.0) for k in now}
        total = sum(delta.values()) / args.interval
        # Every busy thread, not a top-N: the question these samples answer is
        # "was the GUEST thread working or waiting", and a busy renderer pushes
        # the guest out of any top-N exactly when it matters.
        top = sorted(delta.items(), key=lambda kv: -kv[1])
        detail = "  ".join(f"{k}={v / args.interval:.2f}" for k, v in top
                           if v / args.interval > 0.05)
        print(f"{time.monotonic() - start:6.2f}  cores={total:5.2f}  {detail}",
              file=sink, flush=True)
        prev = now
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
