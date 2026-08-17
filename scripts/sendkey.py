#!/usr/bin/env python3
"""Send a keystroke to the focused X11 window via XTEST.

Used to drive the game's host-side key binds (F3 opens the in-game level
picker) during automated testing, without needing xdotool installed -- libXtst
is already on the system, so ctypes is enough.

    python3 scripts/sendkey.py F3
"""

import ctypes
import ctypes.util
import os
import sys
import time

os.environ.setdefault("GDK_BACKEND", "x11")


class Display(ctypes.Structure):
    pass


REVERT_TO_PARENT = 1


def _focus_game(x11, display):
    """Give X11 input focus to the largest toplevel, i.e. the fullscreen game."""
    import gi

    gi.require_version("Gdk", "3.0")
    gi.require_version("GdkX11", "3.0")
    from gi.repository import Gdk, GdkX11

    screen = Gdk.Screen.get_default()
    best, best_area = None, 0
    for window in screen.get_window_stack() or []:
        geometry = window.get_geometry()
        area = geometry[2] * geometry[3]
        if area > best_area:
            best, best_area = window, area
    if best is None:
        return False
    xid = GdkX11.X11Window.get_xid(best)
    x11.XSetInputFocus(display, ctypes.c_ulong(xid), REVERT_TO_PARENT, 0)
    x11.XFlush(display)
    time.sleep(0.15)
    return True


def main() -> int:
    key = sys.argv[1] if len(sys.argv) > 1 else "F3"

    x11 = ctypes.CDLL(ctypes.util.find_library("X11"))
    xtst = ctypes.CDLL(ctypes.util.find_library("Xtst"))

    x11.XOpenDisplay.restype = ctypes.POINTER(Display)
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XStringToKeysym.restype = ctypes.c_ulong
    x11.XStringToKeysym.argtypes = [ctypes.c_char_p]
    x11.XKeysymToKeycode.restype = ctypes.c_ubyte
    x11.XKeysymToKeycode.argtypes = [ctypes.POINTER(Display), ctypes.c_ulong]
    xtst.XTestFakeKeyEvent.argtypes = [
        ctypes.POINTER(Display),
        ctypes.c_uint,
        ctypes.c_int,
        ctypes.c_ulong,
    ]

    display = x11.XOpenDisplay(None)
    if not display:
        print("could not open display", file=sys.stderr)
        return 1

    # Under XWayland the "active" X11 window is a 1x1 focus proxy parked
    # offscreen, so a bare XTEST keystroke never reaches the game. Point input
    # focus at the real game window first.
    if not _focus_game(x11, display):
        print("warning: could not focus the game window", file=sys.stderr)

    keysym = x11.XStringToKeysym(key.encode())
    if keysym == 0:
        print(f"unknown key {key!r}", file=sys.stderr)
        return 1
    keycode = x11.XKeysymToKeycode(display, keysym)

    xtst.XTestFakeKeyEvent(display, keycode, 1, 0)
    x11.XFlush(display)
    time.sleep(0.05)
    xtst.XTestFakeKeyEvent(display, keycode, 0, 0)
    x11.XFlush(display)
    x11.XCloseDisplay(display)
    print(f"sent {key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
