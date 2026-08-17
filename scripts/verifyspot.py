#!/usr/bin/env python3
"""Boot one map and photograph where you actually end up.

Why this exists: every log-based check the loader had is blind to the failure
that matters. `RESULT: LANDED` is inferred from STREAM lines, and the boot warp
substitutes the stream folder - so the requested map's data streams no matter
which Locations row the menu really confirmed. `taking over natively` only says
*a* world rendered. Both report success while you are standing in skate.School.

The only ground truth is the picture. This boots a map, waits for the renderer
to take over, lets the world settle, and saves a screenshot named after what was
REQUESTED - so a human can say yes or no.

    python3 scripts/verifyspot.py <world-id> [label] [--cvar K=V]...
    python3 scripts/verifyspot.py <pack-id>:<sub-index> [label]

The second form exists for packs whose maps do not have distinct world ids -
all 18 San Vanelona spots report `world_id: "San Vanelona"`, so a world id
alone cannot name which of them to boot.

Exit codes: 0 matched a confirmed reference, 2 never rendered, 3 landed
somewhere WRONG, 4 no reference to judge against. 4 is not success - it used to
share exit 0 with a real match, which meant 35 of 37 maps passed silently.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from loader import catalog, config, display, launch, navigate, spotcheck  # noqa: E402

SHOTS = config.SPOTS_DIR
REFERENCES = config.REFERENCES_DIR


def find(target: str):
    """Resolve `<world-id>` or `<pack-id>:<sub-index>` to a (pack, entry)."""
    packs = catalog.load_all(config.CATALOG_DIR)
    if ":" in target:
        pack_id, _, index = target.partition(":")
        for pack in packs:
            if pack.id != pack_id:
                continue
            for entry in pack.maps:
                if str(entry.sub_index) == index:
                    return pack, entry
            raise SystemExit(f"pack {pack_id!r} has no map at index {index}")
        raise SystemExit(f"no pack with id {pack_id!r}")
    for pack in packs:
        entry = pack.map_by_world(target)
        if entry is not None:
            # A world id that names several maps is ambiguous, and silently
            # taking the first is how 18 San Vanelona spots became one.
            same = [m for m in pack.maps if m.world_id == target]
            if len(same) > 1:
                choices = ", ".join(f"{pack.id}:{m.sub_index} ({m.name})" for m in same)
                raise SystemExit(
                    f"world {target!r} names {len(same)} maps - say which:\n  {choices}")
            return pack, entry
    raise SystemExit(f"no pack owns world {target!r}")


def rendered_after_macro(log: Path, macro: str = "x") -> bool:
    """Whether a world has rendered that the macro is responsible for.

    Partitioning at the macro's completion matters: on some maps the boot warp
    trips a takeover of its own before the menu is ever driven - items in the
    scene, black on the glass - so an unpartitioned search goes true instantly
    and the run is torn down before the real one lands. That reported Rio as
    broken for an hour.

    With NO macro the partition is wrong in the other direction: nothing ever
    logs `gameplay input sequence complete`, so the condition can never be
    satisfied and the run always times out. That is a real configuration -
    `navigate.macro_for` returns "" for a pack the game boots straight into
    (Danny Way) - and it made a working map look permanently broken.
    """
    try:
        text = log.read_text(errors="replace")
    except OSError:
        return False
    if not macro:
        return "taking over natively" in text
    _, sep, after = text.partition("gameplay input sequence complete")
    return bool(sep) and "taking over natively" in after


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("world", metavar="TARGET",
                        help="a world id, or <pack-id>:<sub-index>")
    parser.add_argument("label", nargs="?", default="")
    parser.add_argument("--cvar", action="append", default=[])
    parser.add_argument("--macro", default=None)
    parser.add_argument("--settle", type=int, default=None)
    parser.add_argument("--hold", type=float, default=6.0,
                        help="seconds to let the world settle before the shot")
    parser.add_argument("--burst", type=int, default=4,
                        help="frames to grab; the fullest one is kept")
    parser.add_argument("--burst-gap", type=float, default=1.5,
                        dest="burst_gap", help="seconds between burst frames")
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args()

    # A locked session makes every capture pure black, and this runs standalone
    # as often as it runs under sweep.py - which was the only caller doing this.
    display.ensure_capturable()

    pack, entry = find(args.world)
    macro = args.macro if args.macro is not None else navigate.macro_for(pack, entry)
    # The loading overlay covers the whole window on purpose - it exists to
    # hide the menu automation. Photographing it measures nothing, so a
    # verification run always turns it off, rather than depending on it having
    # hidden itself by the time the shot is taken.
    extra = {"skate3_loader_overlay": "false"}
    extra.update(item.split("=", 1) for item in args.cvar)
    label = args.label or "default"
    key = spotcheck.reference_key(pack.id, entry.sub_index)
    SHOTS.mkdir(parents=True, exist_ok=True)

    print(f":: requested  {entry.name} [{entry.world_id}]  from {pack.name}")
    print(f":: technique  {label}")
    print(f":: macro      {macro}")
    if extra:
        print(f":: cvars      {extra}")

    kwargs = {"windowed": True, "extra_cvars": extra}
    if args.settle is not None:
        kwargs["settle_ms"] = args.settle
    session = launch.launch(pack, entry, macro, **kwargs)
    log = Path(session.log_file)

    # Named after the reference key, not the world id, so two spots of one
    # world do not overwrite each other's shot.
    shot = SHOTS / f"{label}__{key}.png"
    burst_dir = SHOTS / "burst"
    burst_dir.mkdir(parents=True, exist_ok=True)
    ok = False
    timed_out = False
    try:
        start = time.monotonic()
        while time.monotonic() - start < args.timeout:
            if rendered_after_macro(log, macro):
                ok = True
                break
            if session.process.poll() is not None:
                break
            time.sleep(0.5)

        # PHOTOGRAPH IT ANYWAY when the wait ran out but the game is still up.
        #
        # `rendered_after_macro` waits for a takeover AFTER the macro, and that
        # line is neither sufficient nor necessary. Its insufficiency was known
        # (it appears with a black screen on screen). Its NON-necessity was
        # found the hard way: this script printed "never rendered after the
        # macro - no shot taken" and then killed the game while a person was
        # standing in a fully loaded Hastings Bowl, skating it.
        #
        # That matters far beyond one map. The entire "these packs never load"
        # classification rests on this script's exit 2, so any map that loads
        # without emitting the line was recorded as broken and then not
        # re-examined, because it was recorded as broken.
        #
        # A picture can answer "is a world on screen" and the log cannot, so
        # take the picture and let `looks_unrendered` decide. Costs one burst on
        # a genuinely dead run; buys back every map misfiled by a missing line.
        if not ok and session.process.poll() is None:
            timed_out = True
            ok = True

        if ok:
            # Let the world finish popping in; a shot taken on the takeover
            # frame catches half-streamed geometry and is unfair to judge.
            time.sleep(args.hold)
            # A BURST, not one frame. Single shots are not reliable here: an
            # otherwise-identical run photographed a completely black window
            # (5151 bytes, no FPS box) while its log matched a known-good run
            # line for line - the window read is transiently empty. A burst
            # also lets the judge prefer a frame where the skater is still at
            # the spawn, which is what keeps two runs of one map comparable.
            frames = []
            for index in range(args.burst):
                frame = burst_dir / f"{label}__{key}.{index}.png"
                subprocess.run(
                    [sys.executable, str(Path(__file__).parent / "capture.py"),
                     str(frame), "--pid", str(session.process.pid)],
                    capture_output=True, text=True)
                if frame.exists():
                    frames.append(frame)
                if index + 1 < args.burst:
                    time.sleep(args.burst_gap)
            spreads = [(spotcheck.frame_spread(f), f) for f in frames]
            spreads.sort(reverse=True)
            print(f":: capture    {len(frames)}/{args.burst} frames, spread "
                  + " ".join(f"{s:.0f}" for s, _ in spreads))
            if timed_out:
                print(f":: NO READY SIGNAL  no takeover after the macro within "
                      f"{args.timeout:.0f}s - judging the picture instead")
            if spreads:
                # The frame with the most going on is the one that actually
                # caught the world; a blank read has nothing in it by
                # definition.
                shutil.copyfile(spreads[0][1], shot)
        else:
            print(":: the game exited before anything could be photographed")
    finally:
        # Keep the log next to the shot. The picture says WHERE we landed; the
        # log is where a machine-checkable signal for it has to come from, and
        # the two are only comparable if they are from the same run.
        try:
            kept = SHOTS / f"{label}__{key}.log"
            kept.write_text(log.read_text(errors="replace"))
        except OSError:
            pass
        session.stop()
        try:
            session.process.wait(timeout=25)
        except subprocess.TimeoutExpired:
            pass
        launch.kill_running_game(timeout=40)

    if ok and shot.exists():
        print(f":: SHOT       {shot}  ({shot.stat().st_size} bytes)")
        unrendered = spotcheck.looks_unrendered(shot)
        if unrendered:
            print(f":: NOT A MAP  {unrendered} - nothing was measured")
            return 2
        hud = spotcheck.challenge_hud(shot)
        if hud:
            print(f":: CHALLENGE  score/LINE HUD on screen ({hud} text rows) - the "
                  "macro confirmed a CHALLENGE, not a location")
        verdict = spotcheck.check(shot, key, REFERENCES, fallback_key=entry.world_id)
        print(f":: VERDICT    {verdict}")
        if verdict.ok is True:
            return 0
        return 3 if verdict.ok is False else 4
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
