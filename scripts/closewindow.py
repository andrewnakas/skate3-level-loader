#!/usr/bin/env python3
"""Ask the game to close its window, so it shuts down cleanly.

Every test run so far ended in `pkill -9`, which means the game never runs its
shutdown path -- and therefore never writes its save. That invalidated a whole
round of save-diff analysis. Sending WM_DELETE_WINDOW is what a window-manager
close button does: GTK turns it into the app's normal quit, the engine logs
"Window closing", and state gets flushed.
"""

import ctypes
import ctypes.util
import os
import sys
import time

os.environ.setdefault("GDK_BACKEND", "x11")

import gi  # noqa: E402

gi.require_version("Gdk", "3.0")
gi.require_version("GdkX11", "3.0")
from gi.repository import Gdk, GdkX11  # noqa: E402


class Display(ctypes.Structure):
    pass


class ClientMessageData(ctypes.Union):
    _fields_ = [("b", ctypes.c_char * 20), ("s", ctypes.c_short * 10),
                ("l", ctypes.c_long * 5)]


class XClientMessageEvent(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int),
        ("serial", ctypes.c_ulong),
        ("send_event", ctypes.c_int),
        ("display", ctypes.POINTER(Display)),
        ("window", ctypes.c_ulong),
        ("message_type", ctypes.c_ulong),
        ("format", ctypes.c_int),
        ("data", ClientMessageData),
    ]


class XEvent(ctypes.Union):
    _fields_ = [("type", ctypes.c_int), ("xclient", XClientMessageEvent),
                ("pad", ctypes.c_long * 24)]


CLIENT_MESSAGE = 33
SUBSTRUCTURE_NOTIFY = 1 << 19
SUBSTRUCTURE_REDIRECT = 1 << 20


def game_xid() -> int | None:
    """The largest X11 toplevel, i.e. the fullscreen game."""
    screen = Gdk.Screen.get_default()
    best, best_area = None, 0
    for window in screen.get_window_stack() or []:
        geometry = window.get_geometry()
        area = geometry[2] * geometry[3]
        if area > best_area:
            best, best_area = window, area
    return GdkX11.X11Window.get_xid(best) if best else None


def main() -> int:
    xid = game_xid()
    if xid is None:
        print("no game window found", file=sys.stderr)
        return 1

    x11 = ctypes.CDLL(ctypes.util.find_library("X11"))
    x11.XOpenDisplay.restype = ctypes.POINTER(Display)
    x11.XInternAtom.restype = ctypes.c_ulong
    x11.XInternAtom.argtypes = [ctypes.POINTER(Display), ctypes.c_char_p, ctypes.c_int]
    x11.XSendEvent.argtypes = [
        ctypes.POINTER(Display), ctypes.c_ulong, ctypes.c_int, ctypes.c_long,
        ctypes.POINTER(XEvent),
    ]

    display = x11.XOpenDisplay(None)
    if not display:
        print("could not open display", file=sys.stderr)
        return 1

    wm_protocols = x11.XInternAtom(display, b"WM_PROTOCOLS", False)
    wm_delete = x11.XInternAtom(display, b"WM_DELETE_WINDOW", False)

    event = XEvent()
    event.xclient.type = CLIENT_MESSAGE
    event.xclient.serial = 0
    event.xclient.send_event = 1
    event.xclient.display = display
    event.xclient.window = xid
    event.xclient.message_type = wm_protocols
    event.xclient.format = 32
    event.xclient.data.l[0] = wm_delete
    event.xclient.data.l[1] = 0

    # WM_DELETE_WINDOW goes straight to the client with NO event mask. The
    # substructure masks are for messages the window MANAGER should act on;
    # using them here means the client never sees it.
    x11.XSendEvent(display, ctypes.c_ulong(xid), False, 0, ctypes.byref(event))
    x11.XFlush(display)
    time.sleep(0.2)
    x11.XCloseDisplay(display)
    print(f"sent WM_DELETE_WINDOW to {xid:#x}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
