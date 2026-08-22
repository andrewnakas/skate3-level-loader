"""The application: library window, loading screen, and the loop between them."""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

# Both the launcher and the game must be on X11 for the loading screen's
# keep-above to bind; Wayland does not let a client raise itself. Set before GTK
# is imported so it takes effect for this process too.
#
# LINUX ONLY. GDK on Windows is built with the win32 backend and on macOS with
# quartz - neither has an x11 backend compiled in, so asking for one there makes
# Gdk.Display.get_default() return None and GTK never initialises at all.
if sys.platform.startswith("linux"):
    os.environ.setdefault("GDK_BACKEND", "x11")
else:
    os.environ.pop("GDK_BACKEND", None)

import gi  # noqa: E402

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from . import (  # noqa: E402
    art, catalog, config, launch, session, setup, ui_library, ui_loading, ui_setup,
)


class LoaderApp(Gtk.Application):
    def __init__(self):
        super().__init__(
            application_id="dev.skate3.loader",
            flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE,
        )
        self.library: ui_library.LibraryWindow | None = None
        self.setup_window: ui_setup.SetupWindow | None = None
        self.loading: ui_loading.LoadingScreen | None = None
        self.controller: session.SessionController | None = None

    # -- lifecycle ---------------------------------------------------------

    def do_command_line(self, command_line):
        self.activate()
        return 0

    def do_activate(self):
        self._build()
        # No game files yet: what the user SEES is the setup screen, not a
        # library of maps none of which could possibly load. The wiring above is
        # built either way - gating it too left `self.controller` as None on any
        # machine without an install, which is every CI runner.
        if setup.needs_setup():
            # LibraryWindow shows itself on construction (see the note about
            # show_all in the README history), so it has to be told to go away.
            self.library.hide()
            if self.setup_window is None:
                self.setup_window = ui_setup.SetupWindow(self, on_done=self._on_setup_done)
            self.setup_window.present()
            return
        self.library.present()

    def _build(self):
        if self.library is None:
            self.library = ui_library.LibraryWindow(
                self, on_play=self.play, on_import=self.import_pack
            )
            self.loading = ui_loading.LoadingScreen(on_cancel=self.cancel_loading)
            self.controller = session.SessionController(
                packs_provider=lambda: catalog.load_all(config.CATALOG_DIR),
                callbacks=session.Callbacks(
                    on_switch=self._on_switch,
                    on_window_up=self._on_window_up,
                    on_progress=self._on_progress,
                    on_ready=self._on_ready,
                    on_finished=self._on_finished,
                    on_failed=self._on_failed,
                    on_retry=self._on_retry,
                ),
            )
            if not ui_loading.ensure_x11():
                self.library.set_status("not on X11 - loading screen may sit behind the game")

    def _on_setup_done(self, game_root) -> None:
        """Game files are in place; retire the setup window and open the library."""
        if self.setup_window is not None:
            self.setup_window.destroy()
            self.setup_window = None
        self.do_activate()

    def do_shutdown(self):
        if self.controller:
            self.controller.stop()
        Gtk.Application.do_shutdown(self)

    # -- playing -----------------------------------------------------------

    def play(self, pack: catalog.Pack, entry: catalog.MapEntry) -> None:
        if self.controller.phase is not session.Phase.IDLE:
            return
        # A map that has been TESTED and never reaches gameplay is not started
        # at all. Labelling it "WON'T LOAD" was not enough: the label is easy to
        # miss, and the cost of missing it is a 200-second wait behind a loading
        # screen that can only end in an error. Say so in the moment instead.
        #
        # This is the backstop rather than the only guard - the library hides
        # these and the in-game picker omits them - because it is the one place
        # every route to a launch passes through.
        if entry.status == "stalls":
            self.library.show_error(
                f"{entry.name} does not load",
                "This map was tested and never reaches gameplay in this build - "
                "the frontend hangs before the world is ever drawn. It is not a "
                "problem with your install, and nothing the launcher does can "
                "work around it, so it is not offered.\n\n"
                "Tick 'Show won't-load' in the header if you want to see the "
                "tested-and-broken maps anyway.",
            )
            return
        # A shipped catalog record carries curation, not the pack file itself.
        # Launching would fail deep inside staging with "dlc package not found";
        # say it here, where the user can act on it.
        if not pack.located:
            self.library.show_error(
                f"{pack.name} is not on this machine",
                "The launcher knows this pack - its maps, which of them load - "
                "but not where your copy of the file is. Use "
                "\u201cLocate pack file\u2026\u201d on the pack heading to point it at "
                "your .big, or import the pack from scratch.",
            )
            return
        windowed = self.library.windowed_toggle.get_active()
        self.library.set_status("")
        self.hold()
        self.loading.begin(entry.world_id, entry.name, pack.name)
        self.library.hide()
        # Force a first paint before anything else happens, so the screen is
        # never shown blank.
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        self.controller.start(pack, entry, windowed=windowed)

    def cancel_loading(self) -> None:
        if self.controller.phase is session.Phase.LOADING:
            self.controller.stop()
            self.release()
            self.loading.hand_over()
            self.library.set_status("cancelled")
            self.library.present()

    # -- controller callbacks ---------------------------------------------

    def _on_switch(self, pack: catalog.Pack, entry: catalog.MapEntry) -> None:
        # The in-game picker chose a map in another pack; cover the restart.
        self.loading.begin(entry.world_id, entry.name, pack.name)
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)

    def _on_window_up(self) -> None:
        # The game is presenting now and draws its own full-screen loading cover
        # (skate3_loader_overlay), so ours steps aside. Trying to stay above a
        # fullscreen game window is a fight we cannot win on this compositor.
        self.loading.hand_over()

    def _on_progress(self, fraction: float, detail: str) -> None:
        self.loading.update(fraction, detail)

    def _on_retry(self, attempt: int, reason: str) -> None:
        # Stays on the loading screen rather than raising an error dialog: a
        # recovered flake should cost the user time, not an explanation.
        self.loading.update(0.05, f"{reason} - retrying")

    def _on_ready(self) -> None:
        self.loading.update(1.0, "ready")
        self.loading.hand_over()
        if self.controller.pack:
            catalog.save(self.controller.pack, config.CATALOG_DIR)

    def _on_finished(self, exit_code: int) -> None:
        self.release()
        launch.clear_profile()
        # `reload` sets its own status (the playable/hidden count), so clearing
        # it afterwards would blank the line it just wrote.
        self.library.reload()
        self.library.present()

    def _on_failed(self, message: str) -> None:
        # Put the controller back in IDLE. `_fail` leaves it in Phase.FAILED and
        # nothing else ever resets it, while `play` refuses to start unless the
        # phase is IDLE - so before this call one failed launch silently swallowed
        # every later click on every card, with the library looking perfectly
        # normal. The window is up, the cards highlight, and nothing happens.
        self.controller.stop()
        self.release()
        self.loading.hand_over()
        self.library.present()
        self.library.show_error("That map did not load", message)
        self.library.set_status("last launch failed")

    # -- importing ---------------------------------------------------------

    def import_pack(self, path: Path) -> None:
        self.library.set_status(f"scanning {path.name}…")

        def worker():
            try:
                pack = catalog.import_pack(path, config.CATALOG_DIR)
                GLib.idle_add(done, pack, None)
            except catalog.ImportError_ as exc:
                GLib.idle_add(done, None, str(exc))
            except Exception as exc:  # noqa: BLE001 - surfaced to the user
                GLib.idle_add(done, None, f"{type(exc).__name__}: {exc}")

        def done(pack, error):
            if error:
                self.library.set_status("")
                self.library.show_error("Could not import that pack", error)
            else:
                self.library.set_status(f"imported {pack.name} ({len(pack.maps)} maps)")
                self.library.reload()
            return False

        threading.Thread(target=worker, daemon=True).start()


def main(argv=None) -> int:
    return LoaderApp().run(argv if argv is not None else sys.argv)
