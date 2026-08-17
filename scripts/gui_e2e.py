#!/usr/bin/env python3
"""End-to-end GUI test: drive the real app through one map load.

Checks the thing the spike could only approximate -- that the loading screen
stays above the *actual* game window (a Vulkan fullscreen surface), not just
above a stand-in GTK window -- by reading _NET_CLIENT_LIST_STACKING while the
game boots.
"""

import subprocess
import sys
import time
from pathlib import Path

import os

# Must run before ANY gi import: importing gi.repository.Gdk opens the
# default display, and the backend chosen there cannot be changed later.
# Wayland gives no way for a client to raise itself above the game.
os.environ.setdefault("GDK_BACKEND", "x11")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gi  # noqa: E402

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkX11", "3.0")
from gi.repository import Gdk, GdkX11, GLib  # noqa: E402

from loader import app as loader_app  # noqa: E402
from loader import catalog, config, session  # noqa: E402

TARGET_WORLD = sys.argv[1] if len(sys.argv) > 1 else "sk8itrio"
DEADLINE = 260


def stacking_report(loading_window):
    """Where the loading screen sits in the X11 stack, bottom -> top."""
    gdk_window = loading_window.get_window()
    if gdk_window is None:
        return "no gdk window"
    try:
        own = GdkX11.X11Window.get_xid(gdk_window)
    except Exception as exc:
        return f"xid failed: {type(exc).__name__}: {exc}"
    stack = Gdk.Screen.get_default().get_window_stack() or []
    xids = []
    for window in stack:
        try:
            xids.append(GdkX11.X11Window.get_xid(window))
        except Exception:
            continue
    if own not in xids:
        return f"not in stack ({len(xids)} windows)"
    position = xids.index(own)
    return f"{position + 1}/{len(xids)}" + (" TOP" if position == len(xids) - 1 else " COVERED")


def main() -> int:
    app = loader_app.LoaderApp()
    state = {"start": 0.0, "events": [], "stack": [], "done": False}

    def log(message):
        elapsed = time.monotonic() - state["start"]
        line = f"[{elapsed:6.1f}s] {message}"
        print(line, flush=True)
        state["events"].append(line)

    original_progress = app._on_progress
    original_ready = app._on_ready
    original_finished = app._on_finished
    original_failed = app._on_failed

    def on_progress(fraction, detail):
        original_progress(fraction, detail)
        key = (round(fraction, 2), detail)
        if key not in state.get("seen", set()):
            state.setdefault("seen", set()).add(key)
            report = stacking_report(app.loading)
            if report:
                state["stack"].append(report)
            log(f"{fraction * 100:5.1f}%  {detail:<34} loading screen: {report}")

    def on_ready():
        log("READY - handing the screen to the game")
        original_ready()
        log(f"after handover, loading screen visible: {app.loading.get_visible()}")
        state["ready"] = True
        # Quit the game a few seconds later, the way a player would, so the
        # test covers the whole loop rather than stopping at "it booted":
        # game exits -> on_finished -> library comes back.
        GLib.timeout_add_seconds(6, quit_the_game)

    def on_finished(code):
        log(f"game exited with code {code}")
        original_finished(code)
        state["returned"] = app.library.get_visible()
        log(f"library visible again: {state['returned']}")
        state["done"] = True
        app.quit()

    def quit_the_game():
        log("closing the game window (simulating the player quitting)")
        subprocess.run(["pkill", "-x", "skate3"], capture_output=True)
        return False

    def on_failed(message):
        log(f"FAILED: {message}")
        state["done"] = True
        # Skip the modal dialog; just record and quit.
        app.release()
        app.loading.hand_over()
        app.quit()

    def start(_app):
        state["start"] = time.monotonic()
        app.callbacks_patched = True
        app.controller.callbacks.on_progress = on_progress
        app.controller.callbacks.on_ready = on_ready
        app.controller.callbacks.on_finished = on_finished
        app.controller.callbacks.on_failed = on_failed

        # Search EVERY pack, not packs[0]: with the official DLC imported the
        # first pack alphabetically is a one-map pack that owns no target world,
        # and the old `or pack.maps[0]` fallback silently tested the wrong map.
        packs = catalog.load_all(config.CATALOG_DIR)
        pack = entry = None
        for candidate in packs:
            found = candidate.map_by_world(TARGET_WORLD)
            if found is not None:
                pack, entry = candidate, found
                break
        if entry is None:
            log(f"no pack owns world {TARGET_WORLD!r}")
            app.quit()
            return
        log(f"playing {entry.name} [{entry.world_id}] index {entry.sub_index}")
        GLib.timeout_add(300, lambda: (app.play(pack, entry), False)[1])
        GLib.timeout_add(1000, watchdog)

    def watchdog():
        if state["done"]:
            return False
        if time.monotonic() - state["start"] > DEADLINE:
            log("deadline reached; stopping")
            app.controller.stop()
            app.quit()
            return False
        return True

    # connect_after: the default `do_activate` handler is what builds the
    # controller and windows, and plain `connect` would run before it.
    app.connect_after("activate", start)
    app.run([sys.argv[0]])

    print()
    print(f"reached gameplay : {state.get('ready', False)}")
    print(f"library returned : {state.get('returned', False)}")
    covered = [s for s in state["stack"] if "COVERED" in s]
    print(f"stacking samples: {len(state['stack'])}, covered: {len(covered)}")
    if state["stack"]:
        verdict = "PASS - stayed on top" if not covered else "FAIL - was covered"
        print(f"loading screen stacking: {verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
