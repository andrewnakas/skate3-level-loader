#!/usr/bin/env python3
"""A `--only` re-run must not throw away the rest of the results. Needs no game.

Re-running one map is a fix being re-verified, not a new sweep, so `main()`
merges its rows into whatever was measured before. That merge existed and did
not work: the run loop checkpoints `sweep.json` after every map, and the merge
then read that same file back as "the previous results" - so it only ever saw
what the current run had just written, and everything else was dropped.

The symptom is quiet and convincing. A 40-map sweep that had passed was reduced
to the 2 maps a `--only` re-run touched, and a 2-row table was printed as if it
were the whole answer. Recorded twice before that as "silently reduced a full
37-map result set to the one map it re-ran".

Verified here against the real `main()`, with the booting stubbed out.

    python3 scripts/sweep_merge_test.py
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import sweep  # noqa: E402
from loader import catalog, config  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    if not ok:
        print(f"  FAIL  {name}" + (f"  ({detail})" if detail else ""))


def main() -> int:
    packs = catalog.load_all(config.CATALOG_DIR)
    keys = [sweep.spotcheck.reference_key(p.id, e.sub_index)
            for p, e in sweep.targets(packs, None, None)]
    if len(keys) < 5:
        check("enough maps in the catalog to test a merge", False, str(len(keys)))
        return report()

    out = Path(tempfile.mkdtemp(prefix="skate3loader-merge-"))
    results_path = out / "sweep.json"

    # A previous full sweep: every map, each with one run that produced a shot.
    prior = {"maps": [{"key": k, "pack": k.rsplit(".", 1)[0], "name": k,
                       "world": "w", "sub_index": 0, "status": "boots",
                       "shots": [], "runs": [{"run": 1, "exit": 4, "shot": None,
                                              "seconds": 60.0, "note": "",
                                              "challenge": False}]}
                      for k in keys]}
    results_path.write_text(json.dumps(prior))

    # Now re-run exactly one of them. Everything that touches a game, the
    # screen or the disk outside `out` is stubbed.
    target = keys[1]
    stubs = {
        "OUT": sweep.OUT,
        "one_run": sweep.one_run,
        "kill": sweep.launch.kill_running_game,
        "ensure": sweep.display.ensure_capturable,
        "sheet": sweep.contact_sheet,
        "argv": sys.argv,
    }
    try:
        sweep.OUT = out
        sweep.one_run = lambda pack, entry, run, hold, timeout, retries=1: {
            "run": run, "exit": 4, "shot": None, "seconds": 61.0,
            "note": "", "challenge": False}
        sweep.launch.kill_running_game = lambda *a, **k: None
        sweep.display.ensure_capturable = lambda *a, **k: True
        sweep.contact_sheet = lambda *a, **k: None
        sys.argv = ["sweep.py", "--only", target, "--runs", "1"]
        # main() prints the whole results table; it is not what is under test.
        with contextlib.redirect_stdout(io.StringIO()):
            sweep.main()
    finally:
        sweep.OUT = stubs["OUT"]
        sweep.one_run = stubs["one_run"]
        sweep.launch.kill_running_game = stubs["kill"]
        sweep.display.ensure_capturable = stubs["ensure"]
        sweep.contact_sheet = stubs["sheet"]
        sys.argv = stubs["argv"]

    after = json.loads(results_path.read_text())["maps"]
    check(f"every map survives a --only re-run ({len(after)} of {len(keys)})",
          len(after) == len(keys),
          f"{len(keys)} before, {len(after)} after - the rest were dropped")
    check("the re-run map is present exactly once",
          [r["key"] for r in after].count(target) == 1,
          f"{[r['key'] for r in after].count(target)} copies")
    check("the re-run map carries the FRESH run",
          any(r["key"] == target and r["runs"][0]["seconds"] == 61.0
              for r in after),
          "still holding the old 60.0 s run")
    check("untouched maps keep their ORIGINAL run",
          all(r["runs"][0]["seconds"] == 60.0
              for r in after if r["key"] != target),
          "an untouched map was overwritten")
    check("catalog order is preserved",
          [r["key"] for r in after] == keys,
          "rows came back in a different order")

    return report()


def report() -> int:
    passed = sum(1 for _, ok, _ in results if ok)
    print(f":: {passed}/{len(results)} checks passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
