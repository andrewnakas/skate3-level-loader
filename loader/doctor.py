"""Environment check: everything that has silently broken a run at least once."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from . import config


def _check(label: str, ok: bool, detail: str = "") -> tuple[bool, str]:
    mark = "ok  " if ok else "FAIL"
    return ok, f"[{mark}] {label}" + (f"\n         {detail}" if detail else "")


def run() -> tuple[bool, list[str]]:
    results: list[tuple[bool, str]] = []

    # Card art is rendered with pure pycairo and handed to GTK as a pixbuf, so
    # the python3-gi-cairo bridge is NOT required. Report it, do not demand it.
    try:
        import cairo  # noqa: F401

        results.append(_check("pycairo (card rendering)", True))
    except ImportError:
        results.append(
            _check("pycairo (card rendering)", False, "install with: sudo apt install -y python3-cairo")
        )

    # X11. The loading screen's keep-above does not bind under Wayland - and
    # the whole question only exists on Linux, where a stacking hint is the only
    # lever a client has. Windows and macOS get the in-engine cover and nothing
    # to check.
    if config.IS_LINUX:
        session_type = os.environ.get("XDG_SESSION_TYPE", "?")
        has_x = bool(os.environ.get("DISPLAY"))
        results.append(
            _check(
                f"X11 available (session is {session_type})",
                has_x,
                "" if has_x else "no DISPLAY; the loading screen cannot stay above the game",
            )
        )

    # The pieces we drive.
    # freeskate is vendored and driven in-process now (loader/staging.py); what
    # still has to exist on disk is the tree it stages into.
    from . import staging

    try:
        staged = staging.make_config()
        stage_ok, stage_detail = True, f"runtime {staged.game_root.parent}"
    except Exception as exc:  # noqa: BLE001
        stage_ok, stage_detail = False, str(exc)
    results.append(_check("staging (vendored freeskate)", stage_ok, "" if stage_ok else stage_detail))

    binary = config.default_binary()
    results.append(
        _check(
            f"game binary at {binary}",
            binary.is_file(),
            "" if binary.is_file() else "set SKATE3LOADER_BINARY",
        )
    )
    xex = config.game_data_root() / "default.xex"
    results.append(
        _check(f"game files at {config.game_data_root()}", xex.is_file(),
               "" if xex.is_file() else
               "no default.xex; the loader offers the ISO installer on first run")
    )

    # Screen locking, which is invisible until it has already ruined a run: a
    # locked session makes every window capture come back pure black while the
    # capture succeeds and the logs stay healthy. Only reported, not changed -
    # `display.ensure_capturable()` does that at the start of a sweep. GNOME
    # only, so Linux only; the capture harness does not ship off Linux anyway.
    # The guard itself lives in the test harness, which is Linux-only and is not
    # part of the frozen app - so its absence is a fact to report, not an error.
    try:
        from . import display
    except ImportError:
        display = None
    if config.IS_LINUX and display is not None:
        idle = display._value_of(
            subprocess.run(["gsettings", "get", "org.gnome.desktop.session", "idle-delay"],
                           capture_output=True, text=True).stdout)
        results.append(
            _check(
                f"screen will not blank during a long run (idle-delay {idle or '?'})",
                idle == "0",
                "" if idle == "0" else
                "captures go BLACK once the session locks; sweeps disable this themselves",
            )
        )

    # Nothing else holding the game's resources.
    from . import proc

    running = proc.game_running()
    results.append(
        _check("no game already running", not running,
               "" if not running else "a running game blocks staging; the loader will kill it")
    )
    if config.IS_LINUX:
        count, total = proc.leaked_memory()
        results.append(
            _check(
                f"no leaked shm segments ({count} found)",
                not count,
                "" if not count else f"{total / 2**30:.1f} GiB in /dev/shm; the loader clears these",
            )
        )
        free = shutil.disk_usage(proc.SHM_DIR).free
        results.append(
            _check(f"/dev/shm has room ({free / 2**30:.1f} GiB free)", free > 5 * 2**30,
                   "" if free > 5 * 2**30 else "the game reserves ~4.5 GiB and will hang without it")
        )

    # Content.
    from . import catalog

    packs = catalog.load_all(config.CATALOG_DIR)
    maps = sum(len(p.maps) for p in packs)
    results.append(
        _check(f"{len(packs)} pack(s), {maps} map(s) imported", bool(packs),
               "" if packs else "import one with: ./skate3loader import <path.big>")
    )

    ok = all(result for result, _ in results)
    return ok, [line for _, line in results]
