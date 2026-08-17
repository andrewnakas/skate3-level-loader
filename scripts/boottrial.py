#!/usr/bin/env python3
"""Run one boot with an arbitrary macro/settle/cvar set and print the phases.

    python3 scripts/boottrial.py <label> [--world W] [--macro M] [--settle MS]
                                 [--cvar K=V]... [--runs N] [--fullscreen]

Exists because smoke_launch.py fixes the macro from navigate.build_macro(), and
boot-time work is mostly about varying exactly that. Every trial keeps its log,
so a regression can be re-read rather than re-run.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loader import catalog, config, launch, logwatch  # noqa: E402
from loader import navigate  # noqa: E402

from boottime import PHASES, parse  # noqa: E402

TRIALS = config.TRIALS_DIR


def find(world: str):
    for pack in catalog.load_all(config.CATALOG_DIR):
        entry = pack.map_by_world(world)
        if entry is not None:
            return pack, entry
    raise SystemExit(f"no map {world!r} in any imported pack")


def one_run(label: str, run: int, args) -> dict[str, float]:
    pack, entry = find(args.world)
    macro = args.macro or navigate.build_macro(pack.maps.index(entry))
    extra = dict(item.split("=", 1) for item in args.cvar)

    session = launch.launch(
        pack, entry, macro,
        windowed=not args.fullscreen,
        settle_ms=args.settle,
        extra_cvars=extra,
    )
    watcher = logwatch.LogWatcher(
        session.log_file, [m.world_id for m in pack.maps], entry.world_id
    )
    def rendered_after_macro() -> bool:
        """A takeover logged AFTER the macro finished - the playable one.

        Both halves have to be judged on the SAME split. Testing "macro done"
        and "a takeover exists anywhere" separately is not the same test: on
        maps where the boot warp trips a takeover of its own, the pair goes
        true the moment the macro completes, and the run gets killed a tenth of
        a second before the real takeover would have landed. That reported Rio
        as broken for an hour.
        """
        # The log does not exist for the first second or so after launch.
        try:
            text = session.log_file.read_text(errors="replace")
        except OSError:
            return False
        _, sep, after = text.partition("gameplay input sequence complete")
        return bool(sep) and "taking over natively" in after

    start = time.monotonic()
    try:
        # 'taking over natively' is a better playable marker than the watcher's
        # own READY (which is inferred from streaming lines and is just as happy
        # on a run that streamed the right world and never drew it) - but it is
        # not sufficient on its own. On some maps the BOOT WARP trips it before
        # the menu confirm has run, with items in the scene and a black screen
        # on the glass. So the run is only over once the macro has finished AND
        # a takeover has been logged after it.
        while time.monotonic() - start < args.timeout:
            watcher.poll()
            if rendered_after_macro():
                break
            if session.process.poll() is not None:
                break
            time.sleep(0.4)
    finally:
        # Keep the log BEFORE tearing down: a teardown that throws must not cost
        # the measurement that has already been taken.
        TRIALS.mkdir(parents=True, exist_ok=True)
        kept = TRIALS / f"{label}.{run}.log"
        if session.log_file.exists():
            shutil.copy(session.log_file, kept)
        else:
            kept.write_text("")
        session.stop()
        # Reap it. Session.stop() kills without waiting, and a zombie still
        # matches `pgrep -x skate3` - which made kill_running_game spin for its
        # whole timeout and then declare a process that no longer exists
        # unkillable.
        try:
            session.process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            pass
        launch.kill_running_game(timeout=40)

    times = parse(kept)
    times["_landed"] = 1.0 if watcher.landed_correctly else 0.0
    times["_takeovers"] = float(
        subprocess.run(["grep", "-c", "taking over natively", str(kept)],
                       capture_output=True, text=True).stdout.strip() or 0
    )
    return times


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("label")
    parser.add_argument("--world", default="sk8itspillway")
    parser.add_argument("--macro", default=None)
    # Defaults to the loader's own settle so a trial with no --settle measures
    # what actually ships, not a value frozen into this script.
    parser.add_argument("--settle", type=int, default=launch.DEFAULT_SETTLE_MS)
    parser.add_argument("--cvar", action="append", default=[])
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=150)
    parser.add_argument("--fullscreen", action="store_true")
    args = parser.parse_args()

    width = max(len(name) for name, _, _ in PHASES)
    totals = []
    for run in range(1, args.runs + 1):
        times = one_run(args.label, run, args)
        print(f":: {args.label} run {run}")
        for name, begin, end in PHASES:
            if begin in times and end in times:
                print(f"   {name:<{width}}  {times[end] - times[begin]:6.2f}s")
            else:
                print(f"   {name:<{width}}       -")
        total = times.get("playable")
        landed = "LANDED" if times["_landed"] else "MISSED"
        rendered = int(times["_takeovers"])
        if total is None:
            print(f"   {'TOTAL':<{width}}   NEVER RENDERED   ({landed})")
        else:
            print(f"   {'TOTAL':<{width}}  {total:6.2f}s   {landed}  takeovers={rendered}")
            totals.append(total)
        sys.stdout.flush()
    if len(totals) > 1:
        print(f":: {args.label} best {min(totals):.2f}s  mean "
              f"{sum(totals) / len(totals):.2f}s  n={len(totals)}")
    return 0 if totals else 2


if __name__ == "__main__":
    raise SystemExit(main())
