#!/usr/bin/env python3
"""Grab the game window's contents to a PNG.

This machine was assumed to have no way to take screenshots -- the in-game
capture is Windows-only and GNOME's D-Bus screenshot API returns AccessDenied --
but the game runs on XWayland, and an X11 client can read another X11 window's
pixels directly. That makes visual checks possible without a human.

    python3 scripts/capture.py out.png --pid 12345     one shot of that process
    python3 scripts/capture.py out --every 3 --for 60  a numbered series

**Say which window you mean.** This used to pick the largest toplevel on the
screen and call it the game, with no check of any kind. The launcher's own
loading screen is fullscreen, so it outranks a windowed game and gets
photographed instead -- and the verifier then hashes the launcher and reports a
verdict about it. Pass `--pid` (the launcher knows `session.process.pid`) or
`--title`. Without either this still falls back to the largest window, but says
so loudly, because a silent wrong answer here invalidates everything downstream.
"""

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

os.environ.setdefault("GDK_BACKEND", "x11")

import gi  # noqa: E402

gi.require_version("Gdk", "3.0")
gi.require_version("GdkX11", "3.0")
from gi.repository import Gdk, GdkX11  # noqa: E402

# Properties are read with xprop rather than Gdk.property_get. The GDK call
# returns nothing at all for these foreign windows - the game window carries a
# perfectly good _NET_WM_PID, WM_CLASS and WM_NAME, and PyGObject reported None
# for every one of them, which silently defeated the whole point of matching by
# pid. xprop is installed and answers correctly.
_PROP_CACHE: dict[int, dict[str, str]] = {}
_PROP_LINE = re.compile(r"^([A-Za-z0-9_]+)\([A-Z_]+\)\s*[:=]\s*(.*)$")


def _xprops(xid: int) -> dict[str, str]:
    cached = _PROP_CACHE.get(xid)
    if cached is not None:
        return cached
    props: dict[str, str] = {}
    try:
        out = subprocess.run(["xprop", "-id", hex(xid)],
                             capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        out = ""
    for line in out.splitlines():
        match = _PROP_LINE.match(line)
        if match:
            props[match.group(1)] = match.group(2).strip()
    _PROP_CACHE[xid] = props
    return props


def _xid(window) -> int:
    try:
        return GdkX11.X11Window.get_xid(window)
    except Exception:
        return 0


def _window_pid(window) -> int | None:
    xid = _xid(window)
    if not xid:
        return None
    raw = _xprops(xid).get("_NET_WM_PID", "")
    try:
        return int(raw)
    except ValueError:
        return None


def _window_title(window) -> str:
    xid = _xid(window)
    if not xid:
        return ""
    props = _xprops(xid)
    for name in ("_NET_WM_NAME", "WM_NAME", "WM_CLASS"):
        value = props.get(name, "")
        if value:
            return value.strip('"')
    return ""


def _descends_from(child: int | None, ancestor: int, depth: int = 6) -> bool:
    """Whether `child` is `ancestor` or one of its descendants."""
    if not child:
        return False
    for _ in range(depth):
        if child == ancestor:
            return True
        try:
            with open(f"/proc/{child}/stat", "r") as handle:
                child = int(handle.read().split(") ", 1)[1].split()[1])
        except (OSError, IndexError, ValueError):
            return False
        if child <= 1:
            return False
    return False


def describe(window) -> str:
    geometry = window.get_geometry()
    return (f"pid={_window_pid(window)} {geometry[2]}x{geometry[3]} "
            f"title={_window_title(window)!r}")


def game_window(pid: int | None = None, title: str | None = None):
    """The window to photograph, and why it was chosen.

    Returns (window, reason). `window` is None when nothing matched, which is a
    hard error for the caller rather than something to paper over.
    """
    screen = Gdk.Screen.get_default()
    if screen is None:
        return None, "no X11 screen (is DISPLAY set?)"
    windows = list(screen.get_window_stack() or [])
    if not windows:
        return None, "no mapped toplevel windows"

    if pid is not None:
        matches = [w for w in windows if _window_pid(w) == pid]
        if not matches:
            # The window may belong to a child of the process we launched.
            matches = [w for w in windows
                       if _descends_from(_window_pid(w), pid)]
        if not matches:
            seen = ", ".join(str(_window_pid(w)) for w in windows)
            return None, f"no window belongs to pid {pid} (saw pids: {seen})"
        # A process can own more than one toplevel; the game's is the big one.
        best = max(matches, key=lambda w: w.get_geometry()[2] * w.get_geometry()[3])
        return best, f"pid {pid}"

    if title:
        needle = title.lower()
        matches = [w for w in windows if needle in _window_title(w).lower()]
        if not matches:
            seen = ", ".join(repr(_window_title(w)) for w in windows)
            return None, f"no window title contains {title!r} (saw: {seen})"
        best = max(matches, key=lambda w: w.get_geometry()[2] * w.get_geometry()[3])
        return best, f"title ~ {title!r}"

    best = max(windows, key=lambda w: w.get_geometry()[2] * w.get_geometry()[3])
    return best, "LARGEST WINDOW (no --pid/--title given - this may not be the game)"


def capture(path: Path, pid: int | None = None, title: str | None = None,
            quiet: bool = False) -> bool:
    window, reason = game_window(pid, title)
    if window is None:
        print(f"capture: {reason}", file=sys.stderr)
        return False
    if not quiet:
        print(f"capture: chose {describe(window)} by {reason}", file=sys.stderr)
    geometry = window.get_geometry()
    pixbuf = Gdk.pixbuf_get_from_window(window, 0, 0, geometry[2], geometry[3])
    if pixbuf is None:
        print("capture: window gave no pixels (unmapped or obscured?)", file=sys.stderr)
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    pixbuf.savev(str(path), "png", [], [])
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("out", nargs="?")
    parser.add_argument("--pid", type=int, default=None,
                        help="only photograph a window owned by this process")
    parser.add_argument("--title", default=None,
                        help="only photograph a window whose title contains this")
    parser.add_argument("--every", type=float, default=0.0, help="seconds between shots")
    parser.add_argument("--for", dest="duration", type=float, default=0.0)
    parser.add_argument("--list", action="store_true",
                        help="describe every toplevel and exit")
    args = parser.parse_args()

    if args.list:
        screen = Gdk.Screen.get_default()
        windows = (screen.get_window_stack() or []) if screen else []
        for window in windows:
            print(describe(window))
        if not windows:
            print("no mapped toplevel windows")
        return 0
    if not args.out:
        parser.error("an output path is required unless --list is given")

    if args.every <= 0:
        ok = capture(Path(args.out), args.pid, args.title)
        print("captured" if ok else "no window found")
        return 0 if ok else 1

    start = time.monotonic()
    index = 0
    captured = 0
    while time.monotonic() - start < args.duration:
        elapsed = time.monotonic() - start
        path = Path(f"{args.out}_{index:02d}_{elapsed:05.1f}s.png")
        if capture(path, args.pid, args.title, quiet=index > 0):
            print(f"[{elapsed:5.1f}s] {path.name}", flush=True)
            index += 1
            captured += 1
        time.sleep(args.every)
    # A series that captured nothing is a failure, not an empty success.
    return 0 if captured else 1


if __name__ == "__main__":
    raise SystemExit(main())
