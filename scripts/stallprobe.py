#!/usr/bin/env python3
"""Find out where a stalling pack's guest is parked.

Several downloaded packs hang the frontend at the title screen: the game runs,
frames keep coming, but the guest never advances. `cpuwatch` shows Main XThread
idle while the host renderer free-spins, so the guest is BLOCKED - and the
question is what on.

The crash reporter dumps guest registers per thread, and `lr` names the guest
caller. But a plain `kill -ABRT` lands on whichever thread the kernel picks -
usually the host GTK main loop, which reports "no bound ThreadState" and tells
us nothing. This targets a specific guest thread with tgkill(2) so the reporter
binds to it and dumps the guest state we actually want.

    python3 scripts/stallprobe.py <world>|<pack>:<index> [--thread "Main XThread"]
"""

from __future__ import annotations

import argparse
import ctypes
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from loader import config, launch  # noqa: E402
from verifyspot import find  # noqa: E402

SYS_tgkill = 234  # x86-64


def threads_of(pid: int) -> dict[int, str]:
    out = {}
    for task in Path(f"/proc/{pid}/task").iterdir():
        try:
            out[int(task.name)] = (task / "comm").read_text().strip()
        except (OSError, ValueError):
            pass
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("target")
    parser.add_argument("--thread", default="Main XThread")
    parser.add_argument("--wait", type=float, default=95.0,
                        help="seconds to let it reach the stall")
    args = parser.parse_args()

    pack, entry = find(args.target)
    crash = Path(str(config.LOG_FILE) + ".crash")
    crash.unlink(missing_ok=True)

    session = launch.launch(pack, entry, "", windowed=True,
                            extra_cvars={"skate3_loader_overlay": "false"})
    pid = session.process.pid
    print(f":: {entry.name} pid={pid}; waiting {args.wait:.0f}s for the stall")
    try:
        time.sleep(args.wait)
        if session.process.poll() is not None:
            print(":: the game exited on its own - not the stall we are after")
            return 2

        tasks = threads_of(pid)
        matches = [t for t, name in tasks.items() if args.thread.lower() in name.lower()]
        print(f":: {len(tasks)} threads; {len(matches)} match {args.thread!r}")
        if not matches:
            print("   names: " + ", ".join(sorted(set(tasks.values()))))
            return 2

        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        tid = matches[0]
        print(f":: tgkill({pid}, {tid}, SIGABRT)  [{tasks[tid]}]")
        if libc.syscall(SYS_tgkill, pid, tid, int(signal.SIGABRT)) != 0:
            print(f"   tgkill failed: {os.strerror(ctypes.get_errno())}")
            return 2
        time.sleep(8)
    finally:
        session.stop()
        try:
            session.process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            pass
        launch.kill_running_game(timeout=30)

    if not crash.is_file():
        print(":: no crash report written")
        return 2
    kept = config.SPOTS_DIR / f"stallprobe__{pack.id}.{entry.sub_index}.crash"
    kept.parent.mkdir(parents=True, exist_ok=True)
    kept.write_text(crash.read_text(errors="replace"))
    print(f":: report {kept} ({kept.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
