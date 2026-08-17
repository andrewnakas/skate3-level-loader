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

    # X11. The loading screen's keep-above does not bind under Wayland.
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
    results.append(
        _check(
            f"freeskate at {config.FREESKATE_BIN}",
            config.FREESKATE_BIN.is_file(),
            "" if config.FREESKATE_BIN.is_file() else "set SKATE3LOADER_FREESKATE",
        )
    )
    binary = config.default_binary()
    results.append(
        _check(
            f"game binary at {binary}",
            binary.is_file(),
            "" if binary.is_file() else "set SKATE3LOADER_BINARY",
        )
    )
    xex = config.INSTALL / "game" / "default.xex"
    results.append(
        _check(f"stock game data at {config.INSTALL}", xex.is_file(),
               "" if xex.is_file() else "set SKATE3LOADER_INSTALL")
    )

    # Screen locking, which is invisible until it has already ruined a run: a
    # locked session makes every window capture come back pure black while the
    # capture succeeds and the logs stay healthy. Only reported, not changed -
    # `display.ensure_capturable()` does that at the start of a sweep.
    from . import display

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
    running = subprocess.run(["pgrep", "-x", "skate3"], capture_output=True).returncode == 0
    results.append(
        _check("no game already running", not running,
               "" if not running else "a running game blocks staging; the loader will kill it")
    )
    leaked = list(Path("/dev/shm").glob("xenia_memory_*"))
    total = sum(p.stat().st_size for p in leaked if p.exists())
    results.append(
        _check(
            f"no leaked shm segments ({len(leaked)} found)",
            not leaked,
            "" if not leaked else f"{total / 2**30:.1f} GiB in /dev/shm; the loader clears these",
        )
    )
    free = shutil.disk_usage("/dev/shm").free
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
