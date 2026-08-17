#!/usr/bin/env python3
"""Measure whether a screenshot fingerprint can actually tell maps apart.

`spotcheck` decides "did we land in the right map" by hamming distance between
perceptual hashes, with a fixed same-world threshold. That only works if the
gap between "two shots of one map" and "shots of two different maps" is wide.
The thresholds in `spotcheck` were set from a handful of Spillway runs; before
trusting them across 37 maps, measure the gap on every shot we have.

    python3 scripts/hashcheck.py [shot-dir ...] [--label-from-filename]

Shots are grouped into maps by the `<label>__<world>.png` naming `verifyspot`
writes. For each candidate fingerprint it reports:

    intra   the spread within a group (same map, different runs)
    cross   the closest pair drawn from two different groups
    ratio   cross / intra - the headroom a threshold has to sit in

A ratio near 1 means the fingerprint cannot separate maps and any threshold is
guesswork. The bar this project uses is 4x.

Caveat worth keeping in mind while reading the output: a small cross-map
distance has two possible causes - a fingerprint too coarse to tell the maps
apart, or two runs that genuinely landed in the SAME place. Only looking at the
images distinguishes those, so `--show` prints the closest pairs by name.
"""

from __future__ import annotations

import argparse
import itertools
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loader import fingerprint  # noqa: E402


def load_groups(dirs: list[Path], override: Path | None) -> dict[str, str]:
    """Filename -> confirmed group, from a `groups.json` beside the shots.

    A shot's filename records the map that was REQUESTED, which is exactly the
    thing under test - eleven shots in the historical corpus are named
    `__sk8itspillway` and landed in several different places. Grouping those
    together measures nothing. `groups.json` carries what a human actually
    confirmed; anything not listed becomes its own group, so it contributes
    cross-map evidence without polluting the intra-map spread.
    """
    mapping: dict[str, str] = {}
    candidates = [override] if override else [d / "groups.json" for d in dirs]
    for path in candidates:
        if path and path.is_file():
            import json
            mapping.update(json.loads(path.read_text()))
    return mapping


def group_of(path: Path, confirmed: dict[str, str] | None = None) -> str:
    """The map a shot belongs to.

    `verifyspot` names shots `<label>__<key>.png`; the label is the run, the
    key is the map. Anything without the separator is its own group, so a stray
    file cannot silently merge into another map's cluster.
    """
    if confirmed and path.name in confirmed:
        return confirmed[path.name]
    if confirmed:
        # Unconfirmed shots stand alone rather than being grouped on a name
        # that only records what was asked for.
        return f"?{path.stem}"
    stem = path.stem
    if "__" in stem:
        return stem.split("__", 1)[1]
    return stem


def report(name: str, sigs: dict[Path, object], groups: dict[str, list[Path]],
           show: int, confirmed: dict[str, str]) -> float:
    """Score one fingerprint.

    Three buckets, not two. A pair where either shot is UNCONFIRMED cannot be
    called same-map or different-map, so it must not enter the statistic - and
    it especially must not enter the cross-map floor, because two unconfirmed
    runs that landed in the same place would drag it to zero and make every
    fingerprint look useless. Those pairs are reported separately, where a tiny
    distance is a positive finding: two runs that landed together.
    """
    intra: list[tuple[int, Path, Path]] = []
    cross: list[tuple[int, Path, Path]] = []
    unknown: list[tuple[int, Path, Path]] = []
    for a, b in itertools.combinations(sorted(sigs), 2):
        d = fingerprint.distance(sigs[a], sigs[b])
        ga, gb = group_of(a, confirmed), group_of(b, confirmed)
        if confirmed and (ga.startswith("?") or gb.startswith("?")):
            unknown.append((d, a, b))
        elif ga == gb:
            intra.append((d, a, b))
        else:
            cross.append((d, a, b))
    intra.sort()
    cross.sort()
    unknown.sort()

    multi = {g: p for g, p in groups.items() if len(p) > 1}
    worst_intra = intra[-1][0] if intra else 0
    best_cross = cross[0][0] if cross else 0
    bits = fingerprint.bit_count(next(iter(sigs.values())))
    ratio = (best_cross / worst_intra) if worst_intra else float("inf")

    print(f"\n=== {name} ({bits} bits) ===")
    print(f"  confirmed groups    : {len(multi)} with repeats"
          f"  ({', '.join(sorted(multi)) or 'none'})")
    print(f"  max intra-map       : {worst_intra}"
          + (f"   ({intra[-1][1].stem} / {intra[-1][2].stem})" if intra else "   (no repeats)"))
    print(f"  min cross-map       : {best_cross}"
          + (f"   ({cross[0][1].stem} / {cross[0][2].stem})" if cross else "   (no pairs)"))
    print(f"  separation ratio    : {ratio:.1f}x")
    if show and cross:
        print(f"  closest {show} cross-map pairs:")
        for d, a, b in cross[:show]:
            print(f"    {d:4d}  {group_of(a, confirmed):22s} {group_of(b, confirmed)}")
    if show and unknown:
        print(f"  closest {show} UNCONFIRMED pairs (small = landed together):")
        for d, a, b in unknown[:show]:
            print(f"    {d:4d}  {a.stem}  /  {b.stem}")
    return ratio


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("dirs", nargs="*", type=Path,
                        help="directories of shots (default: the spots dir)")
    parser.add_argument("--show", type=int, default=6,
                        help="list this many closest cross-map pairs")
    parser.add_argument("--glob", default="*.png")
    parser.add_argument("--groups", type=Path, default=None,
                        help="json of {filename: confirmed-group}")
    args = parser.parse_args()

    dirs = args.dirs
    if not dirs:
        from loader import config
        dirs = [config.SPOTS_DIR]

    shots: list[Path] = []
    for directory in dirs:
        if not directory.is_dir():
            print(f"no such directory: {directory}", file=sys.stderr)
            return 1
        shots.extend(sorted(directory.glob(args.glob)))
    if len(shots) < 2:
        print("need at least two shots to compare", file=sys.stderr)
        return 1

    confirmed = load_groups(list(dirs), args.groups)
    groups: dict[str, list[Path]] = defaultdict(list)
    for shot in shots:
        groups[group_of(shot, confirmed)].append(shot)
    print(f":: {len(shots)} shots in {len(groups)} groups from "
          f"{', '.join(str(d) for d in dirs)}")

    ratios = {}
    for name, build in fingerprint.CANDIDATES.items():
        sigs = {shot: build(shot) for shot in shots}
        ratios[name] = report(name, sigs, groups, args.show, confirmed)

    best = max(ratios, key=lambda k: ratios[k])
    print(f"\n:: best separation: {best} at {ratios[best]:.1f}x "
          f"(in use: {fingerprint.ACTIVE})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
