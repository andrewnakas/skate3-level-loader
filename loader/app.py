"""The application: library window, loading screen, and the loop between them."""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

# Both the launcher and the game must be on X11 for the loading screen's
# keep-above to bind; Wayland does not let a client raise itself. Set before GTK
# is imported so it takes effect for this process too.
os.environ.setdefault("GDK_BACKEND", "x11")

import gi  # noqa: E402

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from . import art, catalog, config, launch, session, ui_library, ui_loading  # noqa: E402


class LoaderApp(Gtk.Application):
    def __init__(self):
        super().__init__(
            application_id="dev.skate3.loader",
            flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE,
        )
        self.library: ui_library.LibraryWindow | None = None
        self.loading: ui_loading.LoadingScreen | None = None
        self.controller: session.SessionController | None = None

    # -- lifecycle ---------------------------------------------------------

    def do_command_line(self, command_line):
        self.activate()
        return 0

    def do_activate(self):
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
        self.library.present()

    def do_shutdown(self):
        if self.controller:
            self.controller.stop()
        Gtk.Application.do_shutdown(self)

    # -- playing -----------------------------------------------------------

    def play(self, pack: catalog.Pack, entry: catalog.MapEntry) -> None:
        if self.controller.phase is not session.Phase.IDLE:
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
        self.library.reload()
        self.library.set_status("")
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
