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

    python3 scripts/sweep.py --status boots      the maps that should work
    python3 scripts/sweep.py                     every map, 3 runs each
    python3 scripts/sweep.py --pack skate-it     one pack
    python3 scripts/sweep.py --runs 1 --limit 4  a quick shakedown
    python3 scripts/sweep.py --report            re-judge existing shots only

`--status boots` is the regression gate: it covers exactly the maps recorded as
working and exits 0 only if every one of them was reproducible and distinct. A
map recorded as `stalls` is EXPECTED to render nothing, so it scores
EXPECTED FAIL rather than dragging the run to a failure - without that the exit
code could never be 0 while a single broken pack stayed imported.

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

from loader import catalog, config, display, launch, logwatch, navigate, spotcheck  # noqa: E402
from loader.fingerprint import distance, signature  # noqa: E402

import verifyspot  # noqa: E402

OUT = config.SWEEP_DIR


def targets(packs, only_pack: str | None, limit: int | None,
            only_keys: set[str] | None = None, status: str | None = None):
    """The (pack, entry) pairs to sweep.

    `only_keys` re-runs a named handful - `<pack>.<index>`, the same key the
    references and the results table use. Re-verifying one fix should not cost
    a two-hour full sweep.

    `status` narrows to one catalog status. `--status boots` is the one that
    matters: it sweeps the maps that are supposed to work and skips the 14 that
    are known broken, which is ~40 minutes of guaranteed failures per run.
    """
    out = []
    for pack in packs:
        if only_pack and pack.id != only_pack:
            continue
        for entry in sorted(pack.maps, key=lambda m: m.sub_index):
            if only_keys and spotcheck.reference_key(pack.id, entry.sub_index) \
                    not in only_keys:
                continue
            if status and entry.status != status:
                continue
            out.append((pack, entry))
    return out[:limit] if limit else out


def status_by_key(packs) -> dict[str, str]:
    """Every map's catalog status, keyed the way the results table is.

    Read fresh rather than taken from the stored row: a map's status changes
    when a sweep proves something about it, and a merged result set can carry
    rows measured before that.
    """
    return {spotcheck.reference_key(pack.id, entry.sub_index): entry.status
            for pack, entry in targets(packs, None, None)}


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
    # the map, so retry it once. `logwatch.transient_boot_failure` holds the
    # rule and the evidence behind it; the launcher applies the same one, so a
    # flake the harness shrugs off is a flake the user never sees either.
    #
    # Not for a map the catalog already knows is broken: those never render by
    # definition, and retrying each one costs another full timeout for nothing.
    if retries > 0 and result.returncode == 2 and entry.status != "stalls":
        log = config.SPOTS_DIR / f"{label}__{key}.log"
        text = log.read_text(errors="replace") if log.exists() else ""
        why = logwatch.transient_boot_failure(text) if text else None
        if why is None and not run_frames(key, run):
            # The world came up and the harness STILL got no picture, so there
            # is no evidence either way - a measurement failure, not a verdict
            # about the map. Seen on san-vanelona.5: verifyspot saw the
            # post-macro takeover, held its 6 s, and then captured 0 of 4
            # frames, the game having stopped logging 5.4 s earlier after
            # dumping ~940 mesh warnings in under a second (the pre-existing
            # engine hang, which no crash handler ever sees).
            why = "rendered but captured no frames"
        if why:
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


# Verdicts that mean something went wrong that was not expected to. Everything
# else - including a known-broken map failing exactly as recorded - is a pass.
BAD_VERDICTS = {"NO SHOT", "FLAKY", "COLLIDES", "WRONG"}


def verdict_of(row: dict) -> str:
    """The map's verdict.

    A map the catalog records as `stalls` is EXPECTED to render nothing, so it
    must not drag the whole sweep to a failure - without this the exit code
    could never be 0 while a single broken pack stayed imported, which made it
    useless as a regression gate. The opposite case gets a verdict of its own:
    a `stalls` map that DOES render is not a failure, it is news.

    Deliberately does NOT use the challenge-HUD flag. That detector looks for
    glyph-like white in the lower-left, and this sweep showed it firing on
    ordinary scenery: an "OLD SAN VAN" advertising column on a dark plinth, and
    high-contrast mortar lines in cobblestone. Two of its three lifetime
    detections are false. It stays as a NOTE worth a human glance, but a map is
    judged on reproducibility, distinctness and reference match - all of which
    a real challenge landing would also fail, since it teleports you elsewhere.
    """
    known_bad = row.get("status") == "stalls"
    if not row["shots"]:
        return "EXPECTED FAIL" if known_bad else "NO SHOT"
    if known_bad:
        # A shot is NOT recovery. `verifyspot` now photographs on timeout
        # rather than reporting nothing, so a map whose macro confirmed
        # nothing still yields a picture - of the STOCK world it was left
        # sitting in. Eleven such maps came back "rendered" together and were
        # all within distance 2-21 of each other: one place, not eleven.
        #
        # Recovery means landing somewhere of its OWN, so it has to clear the
        # same distinctness bar as any other map.
        if row.get("distinct") is False:
            return "STILL STUCK"
        return "RECOVERED?"
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
    parser.add_argument("--status", default=None,
                        help="only maps with this catalog status "
                             "(`boots` sweeps the 40 that are supposed to work)")
    args = parser.parse_args()

    if not args.report:
        display.ensure_capturable()
    packs = catalog.load_all(config.CATALOG_DIR)
    statuses = status_by_key(packs)
    only = ({k.strip() for k in args.only.split(',')} if args.only else None)
    todo = targets(packs, args.pack, args.limit, only, args.status)
    if only and not todo:
        print(f'no maps match --only {sorted(only)}', file=sys.stderr)
        return 1
    if args.status and not todo:
        print(f'no maps have status {args.status!r}', file=sys.stderr)
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    results_path = OUT / "sweep.json"

    # Read what was already measured BEFORE the run loop starts.
    #
    # The loop checkpoints sweep.json after every map, so reading this file
    # afterwards reads back what THIS run just wrote - the merge below then has
    # nothing older to merge with and quietly throws the rest away. That is the
    # bug the merge was added to prevent, and it never worked: a 40-map result
    # set was reduced to the 2 maps a `--only` re-run had touched, with a
    # 2-row table printed as if it were the whole answer.
    previous: list[dict] = []
    if not args.report and results_path.exists():
        try:
            previous = json.loads(results_path.read_text())["maps"]
        except (OSError, ValueError, KeyError):
            previous = []

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
                   "status": entry.status, "runs": []}
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
    # `--only` re-run is a fix being re-verified, not a new sweep, and three
    # times now it silently reduced a full result set to the maps it re-ran.
    if not args.report and previous:
        fresh = {r["key"] for r in rows}
        rows = [r for r in previous if r["key"] not in fresh] + rows
        # UNFILTERED on purpose. Threading `args.status` in here would leave
        # every filtered-out row without a sort key, and `1 << 30` would dump
        # them all at the end of the table in arbitrary order.
        order = {spotcheck.reference_key(p.id, e.sub_index): i
                 for i, (p, e) in enumerate(targets(packs, None, None))}
        rows.sort(key=lambda r: order.get(r["key"], 1 << 30))

    # Stamp the CURRENT status on every row, including ones merged in from an
    # earlier sweep or reloaded by --report. A map's status is the thing the
    # verdict is measured against, so it has to come from the catalog as it is
    # now, not from whatever it was when the row was written.
    for row in rows:
        row["status"] = statuses.get(row["key"], row.get("status", ""))

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

    # Only UNEXPECTED outcomes fail the run, so this is usable as a gate: a
    # known-broken map failing the way it is recorded as failing is a pass.
    # `RECOVERED?` is not a failure either - it means a map got better - but it
    # does want a human to look, so say so out loud.
    recovered = [r["key"] for r in rows if verdict_of(r) == "RECOVERED?"]
    if recovered:
        print(f":: {len(recovered)} map(s) marked `stalls` RENDERED this time: "
              f"{', '.join(recovered)}\n   Check the contact sheet; if they are "
              f"real, clear their status in catalog/.")
    bad = sum(count for verdict, count in tally.items() if verdict in BAD_VERDICTS)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
