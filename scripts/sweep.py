#!/usr/bin/env python3
"""Boot every map several times and judge where each one actually landed.

The verdict has to come from pictures - see `loader/spotcheck.py` for why no log
line can answer "which map am I in". But references have to be confirmed by a
human once each, and there are 37 maps, so the sweep judges two ways and only
one of them needs references:

  REPRODUCIBLE  a map's runs agree with each other        (no reference needed)
  DISTINCT      its cluster is far from every other map's (no reference needed)
  MATCH         it matches a confirmed reference          (needs one)

Reproducibility catches a non-deterministic route; distinctness catches two maps
landing in the same place, which is the signature of the menu confirming the
wrong thing. Together they are enough to call the loader correct before a single
reference exists - and the contact sheet then turns one human pass into 37
references, after which MATCH carries it.

    python3 scripts/sweep.py                     every map, 3 runs each
    python3 scripts/sweep.py --pack skate-it     one pack
    python3 scripts/sweep.py --runs 1 --limit 4  a quick shakedown
    python3 scripts/sweep.py --report            re-judge existing shots only

Run ONE of these at a time. Each run kills any live game, so two sweeps at once
silently truncate each other's runs.
"""

from __future__ import annotations

import argparse
import itertools
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from loader import catalog, config, display, launch, navigate, spotcheck  # noqa: E402
from loader.fingerprint import distance, signature  # noqa: E402

import verifyspot  # noqa: E402

OUT = config.SWEEP_DIR


def targets(packs, only_pack: str | None, limit: int | None,
            only_keys: set[str] | None = None):
    """The (pack, entry) pairs to sweep.

    `only_keys` re-runs a named handful - `<pack>.<index>`, the same key the
    references and the results table use. Re-verifying one fix should not cost
    a two-hour full sweep.
    """
    out = []
    for pack in packs:
        if only_pack and pack.id != only_pack:
            continue
        for entry in sorted(pack.maps, key=lambda m: m.sub_index):
            if only_keys and spotcheck.reference_key(pack.id, entry.sub_index) \
                    not in only_keys:
                continue
            out.append((pack, entry))
    return out[:limit] if limit else out


def one_run(pack, entry, run: int, hold: float, timeout: float,
            retries: int = 1) -> dict:
    """Boot once and keep the shot. Returns what happened."""
    key = spotcheck.reference_key(pack.id, entry.sub_index)
    label = f"s{run}"

    # Clear this run's OLD frames first. `judge` reads whatever burst frames are
    # on disk, so a re-run that renders nothing silently inherits the previous
    # attempt's evidence - and the previous attempt may be exactly what a fix
    # was meant to change. Four Meebs maps were reported COLLIDES at distance 0
    # against pre-fix frames while their fresh runs had actually timed out.
    for stale in (config.SPOTS_DIR / "burst").glob(f"{label}__{key}.*.png"):
        stale.unlink(missing_ok=True)
    # ALL three artifacts, not just the burst: verifyspot's own kept shot in
    # SPOTS_DIR is what `one_run` copies forward, so leaving it behind let a
    # run that rendered nothing still hand `judge` the previous attempt's
    # picture. That produced "COLLIDES @ 0" for maps whose fresh runs had both
    # timed out - twice, because the first fix only cleared two of the three.
    (config.SPOTS_DIR / f"{label}__{key}.png").unlink(missing_ok=True)
    (config.SWEEP_DIR / f"{key}__{label}.png").unlink(missing_ok=True)

    began = time.monotonic()
    result = subprocess.run(
        [sys.executable, str(Path(__file__).parent / "verifyspot.py"),
         f"{pack.id}:{entry.sub_index}", label,
         "--hold", str(hold), "--timeout", str(timeout)],
        capture_output=True, text=True)
    got_seconds = time.monotonic() - began
    shot = config.SPOTS_DIR / f"{label}__{key}.png"
    kept = None
    if shot.exists():
        OUT.mkdir(parents=True, exist_ok=True)
        kept = OUT / f"{key}__{label}.png"
        shutil.copyfile(shot, kept)
    # verifyspot's exit codes: 0 match, 2 nothing rendered, 3 wrong, 4 no ref.
    note = ""
    for line in result.stdout.splitlines():
        if line.startswith(":: VERDICT") or line.startswith(":: NOT A MAP") \
                or line.startswith(":: CHALLENGE") or line.startswith(":: never"):
            note = line.split("  ", 1)[-1].strip()
    # A run where NOTHING EVER RENDERED is a boot failure, not a verdict about
    # the map, so retry it once. Two distinct flavours were seen in ~220 runs,
    # and neither says anything about where the map goes:
    #
    #   4.3 s   "[FATAL] Call to invalid or unregistered function at guest
    #           address 0x00000000" - a null indirect call during boot, the
    #           known intermittent corruption in this title.
    #   160.6 s reached press-start, enabled the auto-tap, and then sat there.
    #           No "gameplay reached", no inputs injected, frontend never left
    #           screen 0. Maloof, whose other two runs MATCHed the same session.
    #
    # Both show up as "no takeover in the log at all", which a map that renders
    # the wrong world does not. A genuinely broken map just fails twice.
    if retries > 0 and result.returncode == 2:
        log = config.SPOTS_DIR / f"{label}__{key}.log"
        text = log.read_text(errors="replace") if log.exists() else ""
        if text and "taking over natively" not in text:
            why = "engine crashed" if "[FATAL]" in text else "never reached gameplay"
            print(f"      ({why} after {got_seconds:.0f}s - retrying)")
            launch.kill_running_game(timeout=30)
            subprocess.run("rm -f /dev/shm/xenia_memory_*", shell=True)
            return one_run(pack, entry, run, hold, timeout, retries - 1)

    if kept is not None and result.returncode == 2 \
            and spotcheck.looks_unrendered(kept):
        # A black frame is what a LOCKED SESSION looks like from here, and the
        # log of such a run is identical to a good one. Check that before
        # believing the map failed.
        if retries > 0 and display.screensaver_active():
            print("      (black frame + locked session - unlocking and retrying)")
            display.ensure_capturable()
            # Bounded: if unlocking does not take, do not spin all night.
            return one_run(pack, entry, run, hold, timeout, retries - 1)
    return {
        "run": run,
        "exit": result.returncode,
        "shot": str(kept) if kept else None,
        "seconds": round(time.monotonic() - began, 1),
        "note": note,
        "challenge": bool(kept and spotcheck.challenge_hud(kept)),
    }


def run_frames(key: str, run: int) -> list[Path]:
    """Every burst frame kept for one run of one map."""
    return sorted((config.SPOTS_DIR / "burst").glob(f"s{run}__{key}.*.png"))


def judge(rows: list[dict]) -> list[dict]:
    """Add reproducibility and cross-map distinctness to each map's row.

    Compares whole BURSTS, not one frame per run. Two runs of a map are the
    same place if ANY of their frames align, because the burst spans a few
    seconds during which the camera drifts - and a colour histogram is very
    sensitive to how much bright ground versus dark architecture is in frame.

    Cathedral is the case that forced this: three runs that land in visibly the
    same arcade (same colonnade, same planter, same building at the end) scored
    142 apart on first frames and were reported FLAKY. Min-over-burst puts them
    at 5-6 while leaving the nearest DIFFERENT map at 143 - the separation goes
    from nothing to ~24x, with no threshold change.
    """
    sigs: dict[str, list] = {}
    for row in rows:
        shots = [Path(r["shot"]) for r in row["runs"] if r["shot"]]
        row["shots"] = [str(s) for s in shots]

        per_run = []
        for r in row["runs"]:
            frames = run_frames(row["key"], r["run"])
            if frames:
                per_run.append([signature(f) for f in frames])
            elif r["shot"]:
                per_run.append([signature(Path(r["shot"]))])
        # Only maps that actually produced frames go in. A map with no shots
        # used to land here as an EMPTY list, and the cross-map comparison then
        # did min() over an empty product and took the whole sweep down with
        # `ValueError: min() iterable argument is empty` - losing 14 of 17 runs
        # after the first failure.
        flat = [s for group in per_run for s in group]
        if flat:
            sigs[row["key"]] = flat

        worst = 0
        for a, b in itertools.combinations(per_run, 2):
            worst = max(worst, min(distance(x, y)
                                   for x in a for y in b))
        row["spread"] = worst
        row["reproducible"] = (worst <= spotcheck.SAME_WORLD_MAX
                               if len(per_run) > 1 else None)

    for row in rows:
        mine = sigs.get(row["key"])
        if not mine:
            row["nearest"] = None
            row["nearest_distance"] = None
            continue
        best, best_key = None, None
        for other_key, theirs in sigs.items():
            if other_key == row["key"] or not theirs:
                continue
            d = min(distance(a, b) for a, b in itertools.product(mine, theirs))
            if best is None or d < best:
                best, best_key = d, other_key
        row["nearest"] = best_key
        row["nearest_distance"] = best
        row["distinct"] = None if best is None else best > spotcheck.SAME_WORLD_MAX
    return rows


def verdict_of(row: dict) -> str:
    """The map's verdict.

    Deliberately does NOT use the challenge-HUD flag. That detector looks for
    glyph-like white in the lower-left, and this sweep showed it firing on
    ordinary scenery: an "OLD SAN VAN" advertising column on a dark plinth, and
    high-contrast mortar lines in cobblestone. Two of its three lifetime
    detections are false. It stays as a NOTE worth a human glance, but a map is
    judged on reproducibility, distinctness and reference match - all of which
    a real challenge landing would also fail, since it teleports you elsewhere.
    """
    if not row["shots"]:
        return "NO SHOT"
    if row.get("reproducible") is False:
        return "FLAKY"
    if row.get("distinct") is False:
        return "COLLIDES"
    if any(r["exit"] == 3 for r in row["runs"]):
        return "WRONG"
    if any(r["exit"] == 0 for r in row["runs"]):
        return "MATCH"
    return "OK*"


def contact_sheet(rows: list[dict], path: Path, columns: int = 6) -> None:
    """One labelled thumbnail per map, for the single human pass."""
    from PIL import Image, ImageDraw

    cell_w, cell_h, bar = 320, 180, 22
    shown = [r for r in rows if r["shots"]]
    if not shown:
        return
    cols = min(columns, len(shown))
    rows_n = (len(shown) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * cell_w, rows_n * (cell_h + bar)), (16, 16, 20))
    draw = ImageDraw.Draw(sheet)
    for index, row in enumerate(shown):
        x = (index % cols) * cell_w
        y = (index // cols) * (cell_h + bar)
        thumb = Image.open(row["shots"][0]).convert("RGB").resize((cell_w, cell_h))
        sheet.paste(thumb, (x, y))
        draw.text((x + 4, y + cell_h + 5),
                  f"{row['name']}  [{row['key']}]  {verdict_of(row)}",
                  fill=(235, 235, 235))
    sheet.save(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pack", default=None)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--only", default=None,
                        help="comma-separated <pack>.<index> keys to re-run")
    parser.add_argument("--hold", type=float, default=6.0)
    parser.add_argument("--timeout", type=float, default=150)
    parser.add_argument("--report", action="store_true",
                        help="re-judge the shots already on disk, boot nothing")
    args = parser.parse_args()

    if not args.report:
        display.ensure_capturable()
    packs = catalog.load_all(config.CATALOG_DIR)
    only = ({k.strip() for k in args.only.split(',')} if args.only else None)
    todo = targets(packs, args.pack, args.limit, only)
    if only and not todo:
        print(f'no maps match --only {sorted(only)}', file=sys.stderr)
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    results_path = OUT / "sweep.json"

    if args.report:
        rows = json.loads(results_path.read_text())["maps"]
    else:
        print(f":: {len(todo)} maps x {args.runs} runs "
              f"(~{len(todo) * args.runs * 45 / 60:.0f} min)")
        rows = []
        for number, (pack, entry) in enumerate(todo, 1):
            key = spotcheck.reference_key(pack.id, entry.sub_index)
            row = {"key": key, "pack": pack.id, "name": entry.name,
                   "world": entry.world_id, "sub_index": entry.sub_index,
                   "runs": []}
            for run in range(1, args.runs + 1):
                # Never inherit a wedged process or a leaked 4.5 GiB segment
                # from the previous run; either one hangs the next startup.
                launch.kill_running_game(timeout=30)
                subprocess.run("rm -f /dev/shm/xenia_memory_*", shell=True)
                got = one_run(pack, entry, run, args.hold, args.timeout)
                row["runs"].append(got)
                print(f"   [{number:2d}/{len(todo)}] {entry.name:24s} run {run} "
                      f"exit={got['exit']} {got['seconds']:5.1f}s  {got['note'][:60]}",
                      flush=True)
            rows.append(row)
            results_path.write_text(json.dumps({"maps": judge(rows)}, indent=2))

    # MERGE into whatever was already measured, rather than replacing it. A
    # `--only` re-run is a fix being re-verified, not a new sweep, and twice it
    # silently reduced a full 37-map result set to the one map it re-ran.
    if not args.report and results_path.exists():
        try:
            previous = json.loads(results_path.read_text())["maps"]
        except (OSError, ValueError, KeyError):
            previous = []
        fresh = {r["key"] for r in rows}
        rows = [r for r in previous if r["key"] not in fresh] + rows
        order = {spotcheck.reference_key(p.id, e.sub_index): i
                 for i, (p, e) in enumerate(targets(packs, None, None))}
        rows.sort(key=lambda r: order.get(r["key"], 1 << 30))

    rows = judge(rows)
    results_path.write_text(json.dumps({"maps": rows}, indent=2))

    lines = ["| map | key | verdict | spread | nearest other map | secs |",
             "|---|---|---|---|---|---|"]
    for row in rows:
        secs = "/".join(str(r["seconds"]) for r in row["runs"])
        near = (f"{row['nearest']} @ {row['nearest_distance']}"
                if row.get("nearest") else "-")
        note = " HUD?" if any(r["challenge"] for r in row["runs"]) else ""
        lines.append(f"| {row['name']} | {row['key']} | {verdict_of(row)}{note} | "
                     f"{row.get('spread', '-')} | {near} | {secs} |")
    table = "\n".join(lines)
    (OUT / "sweep.md").write_text(table + "\n")
    print("\n" + table)

    tally: dict[str, int] = {}
    for row in rows:
        tally[verdict_of(row)] = tally.get(verdict_of(row), 0) + 1
    print("\n:: " + "  ".join(f"{k}={v}" for k, v in sorted(tally.items())))

    contact_sheet(rows, OUT / "contact.png")
    print(f":: contact sheet {OUT / 'contact.png'}")
    print(f":: table         {OUT / 'sweep.md'}")
    return 0 if tally.get("MATCH", 0) + tally.get("OK*", 0) == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
