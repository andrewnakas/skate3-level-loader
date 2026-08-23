"""First-run setup: point the loader at your own Skate 3 files.

Two ways out of this screen, and the first one is free: if a Skate3Recomp
install is already on this machine, adopt its game folder. Otherwise pick the
ISO (and the Title Update 3 package) and let the ENGINE extract them - see
loader/setup.py for why the loader does no extraction of its own.
"""

from __future__ import annotations

from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from . import setup  # noqa: E402

STYLE = b"""
.setup-root { background-color: #101014; }
.setup-title { color: #ffffff; font-size: 26px; font-weight: bold; }
.setup-lead { color: rgba(255,255,255,0.72); font-size: 14px; }
.setup-label { color: rgba(255,255,255,0.55); font-size: 11px; letter-spacing: 1px; }
.setup-value { color: #ffffff; font-size: 13px; }
.setup-detail { color: rgba(255,255,255,0.60); font-size: 12px; }
.setup-error { color: #ff6b5e; font-size: 13px; }
"""


def _heading(text: str) -> Gtk.Label:
    label = Gtk.Label(label=text.upper(), xalign=0.0)
    label.get_style_context().add_class("setup-label")
    return label


class SetupWindow(Gtk.ApplicationWindow):
    """Collects an ISO (or an existing install) and runs the engine's installer.

    `on_done(game_root)` fires once default.xex exists, and the application
    swaps this window for the library.
    """

    def __init__(self, application, on_done):
        super().__init__(application=application, title="Skate 3 Level Loader")
        self.on_done = on_done
        self.iso: Path | None = None
        self.title_update: Path | None = None
        self._installing = False

        self.set_default_size(720, 520)
        provider = Gtk.CssProvider()
        provider.load_from_data(STYLE)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        self.get_style_context().add_class("setup-root")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        box.set_margin_top(40)
        box.set_margin_bottom(32)
        box.set_margin_start(48)
        box.set_margin_end(48)
        self.add(box)

        title = Gtk.Label(label="Set up your game files", xalign=0.0)
        title.get_style_context().add_class("setup-title")
        box.pack_start(title, False, False, 0)

        lead = Gtk.Label(xalign=0.0, wrap=True, label=(
            "The launcher needs the files from your own copy of Skate 3. Nothing "
            "is downloaded - the game's own installer extracts your disc image, "
            "once, and the result is reused for every map after that."
        ))
        lead.get_style_context().add_class("setup-lead")
        box.pack_start(lead, False, False, 0)

        # -- the free path: an install already here ------------------------
        found = setup.existing_installs()
        if found:
            box.pack_start(_heading("already on this machine"), False, False, 8)
            for path in found:
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
                label = Gtk.Label(label=str(path), xalign=0.0, ellipsize=3)
                label.get_style_context().add_class("setup-value")
                row.pack_start(label, True, True, 0)
                button = Gtk.Button(label="Use this")
                button.connect("clicked", self._on_use_existing, path)
                row.pack_start(button, False, False, 0)
                box.pack_start(row, False, False, 0)

        # -- the ISO path ---------------------------------------------------
        box.pack_start(_heading("or extract from your disc image" if found
                                else "extract from your disc image"), False, False, 8)
        self.iso_row, self.iso_label = self._chooser_row(
            box, "Skate 3 ISO", self._on_pick_iso)
        self.tu_row, self.tu_label = self._chooser_row(
            box, "Title Update 3 package - the TU_… file from your console update",
            self._on_pick_tu)

        self.install_button = Gtk.Button(label="Extract game files")
        self.install_button.set_sensitive(False)
        self.install_button.set_halign(Gtk.Align.START)
        self.install_button.connect("clicked", self._on_install)
        box.pack_start(self.install_button, False, False, 8)

        self.progress = Gtk.ProgressBar(show_text=False)
        self.progress.set_no_show_all(True)
        box.pack_start(self.progress, False, False, 0)

        self.detail = Gtk.Label(label="", xalign=0.0, ellipsize=3)
        self.detail.get_style_context().add_class("setup-detail")
        box.pack_start(self.detail, False, False, 0)

        self.error = Gtk.Label(label="", xalign=0.0, wrap=True)
        self.error.get_style_context().add_class("setup-error")
        box.pack_start(self.error, False, False, 0)

        from . import config

        if not config.default_binary().is_file():
            self.install_button.set_sensitive(False)
            self.error.set_text(
                "No game engine found. The launcher drives a patched build of "
                "skate3recomp: unpack the engine archive into a folder named "
                f"\u201cengine\u201d beside the launcher ({config.app_dir()}), then "
                "reopen this window."
            )

        # Showing a CHILD does not show its ancestors - the library window came
        # up empty for two days over exactly this.
        self.show_all()
        self.progress.hide()

    # -- widgets -----------------------------------------------------------

    def _chooser_row(self, box, label_text, handler):
        box.pack_start(_heading(label_text), False, False, 6)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        value = Gtk.Label(label="not chosen", xalign=0.0, ellipsize=3)
        value.get_style_context().add_class("setup-value")
        row.pack_start(value, True, True, 0)
        button = Gtk.Button(label="Choose…")
        button.connect("clicked", handler)
        row.pack_start(button, False, False, 0)
        box.pack_start(row, False, False, 0)
        return row, value

    def _pick_file(self, title: str, patterns: list[str]) -> Path | None:
        dialog = Gtk.FileChooserDialog(
            title=title, transient_for=self, action=Gtk.FileChooserAction.OPEN)
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Choose", Gtk.ResponseType.ACCEPT)
        if patterns:
            file_filter = Gtk.FileFilter()
            file_filter.set_name(" ".join(patterns))
            for pattern in patterns:
                file_filter.add_pattern(pattern)
            dialog.add_filter(file_filter)
        everything = Gtk.FileFilter()
        everything.set_name("All files")
        everything.add_pattern("*")
        dialog.add_filter(everything)
        chosen = None
        if dialog.run() == Gtk.ResponseType.ACCEPT:
            chosen = Path(dialog.get_filename())
        dialog.destroy()
        return chosen

    # -- actions -----------------------------------------------------------

    def _on_pick_iso(self, _button) -> None:
        chosen = self._pick_file("Select your Skate 3 ISO", ["*.iso", "*.ISO"])
        if chosen:
            self.iso = chosen
            self.iso_label.set_text(str(chosen))
            self.install_button.set_sensitive(True)

    def _on_pick_tu(self, _button) -> None:
        chosen = self._pick_file("Select the Title Update 3 package", ["*"])
        if chosen:
            self.title_update = chosen
            self.tu_label.set_text(str(chosen))

    def _on_use_existing(self, _button, path: Path) -> None:
        try:
            setup.use_existing(path)
        except setup.SetupError as exc:
            self.error.set_text(str(exc))
            return
        self.on_done(path)

    def _on_install(self, _button) -> None:
        if self._installing or not self.iso:
            return
        self._installing = True
        self.error.set_text("")
        self.install_button.set_sensitive(False)
        self.progress.show()
        self.progress.set_fraction(0.0)
        self.detail.set_text("starting the game's installer…")
        self._pulse_id = GLib.timeout_add(120, self._pulse)

        setup.run_install_async(
            setup.Install(iso=self.iso, title_update=self.title_update),
            on_done=lambda root, error: GLib.idle_add(self._on_finished, root, error),
            on_line=lambda line: GLib.idle_add(self.detail.set_text, line[-160:]),
        )

    def _pulse(self) -> bool:
        self.progress.pulse()
        return self._installing

    def _on_finished(self, game_root, error) -> bool:
        self._installing = False
        if error:
            self.progress.hide()
            self.install_button.set_sensitive(True)
            self.error.set_text(error)
            return False
        self.progress.set_fraction(1.0)
        self.detail.set_text(f"game files at {game_root}")
        self.on_done(game_root)
        return False
