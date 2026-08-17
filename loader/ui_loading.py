"""The fullscreen loading screen shown while a map boots.

Wayland does not let a client raise itself above another app, so this runs on
X11 (GDK_BACKEND=x11, set for both the launcher and the game) and uses GTK3's
set_keep_above. Verified in a spike: a keep-above window stays above a
fullscreen window that maps later and takes focus -- which is exactly what the
game does -- and dropping keep-above then hiding hands the screen over cleanly.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from . import art  # noqa: E402

STYLE = b"""
.loading-root { background-color: #101014; }
.loading-title { color: #ffffff; font-size: 40px; font-weight: bold; }
.loading-pack { color: rgba(255,255,255,0.72); font-size: 16px; }
.loading-detail { color: rgba(255,255,255,0.85); font-size: 14px; }
.loading-hint { color: rgba(255,255,255,0.45); font-size: 12px; }
progressbar trough { min-height: 8px; background-color: rgba(255,255,255,0.18); }
progressbar progress { min-height: 8px; background-color: #ffffff; }
"""


class LoadingScreen(Gtk.Window):
    def __init__(self, on_cancel=None):
        super().__init__(title="Loading")
        self.on_cancel = on_cancel
        self._handed_over = False
        self._world_id = ""
        self._raise_id = 0

        self.set_decorated(False)
        self.connect("key-press-event", self._on_key)
        self.add_events(Gdk.EventMask.KEY_PRESS_MASK)

        provider = Gtk.CssProvider()
        provider.load_from_data(STYLE)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        self.overlay = Gtk.Overlay()
        self.get_style_context().add_class("loading-root")
        self.background = Gtk.Image()
        self.overlay.add(self.background)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        content.set_valign(Gtk.Align.END)
        content.set_halign(Gtk.Align.FILL)
        content.set_margin_start(64)
        content.set_margin_end(64)
        content.set_margin_bottom(56)

        self.title_label = Gtk.Label(xalign=0)
        self.title_label.get_style_context().add_class("loading-title")
        self.pack_label = Gtk.Label(xalign=0)
        self.pack_label.get_style_context().add_class("loading-pack")

        self.progress = Gtk.ProgressBar()
        self.progress.set_margin_top(22)

        self.detail_label = Gtk.Label(xalign=0)
        self.detail_label.get_style_context().add_class("loading-detail")

        hint = Gtk.Label(xalign=0, label="Esc cancels")
        hint.get_style_context().add_class("loading-hint")

        for widget in (
            self.title_label,
            self.pack_label,
            self.progress,
            self.detail_label,
            hint,
        ):
            content.add(widget)
        self.overlay.add_overlay(content)
        self.add(self.overlay)

    # -- lifecycle ---------------------------------------------------------

    def begin(self, world_id: str, title: str, subtitle: str) -> None:
        self._world_id = world_id
        self._handed_over = False
        self.title_label.set_text(title)
        self.pack_label.set_text(subtitle)
        self.detail_label.set_text("staging content")
        self.progress.set_fraction(0.0)

        width, height = _screen_size()
        self.background.set_from_pixbuf(art.card_pixbuf(world_id, width, height))

        self.set_keep_above(True)
        self.fullscreen()
        self.show_all()
        self.present()
        if not self._raise_id:
            self._raise_id = GLib.timeout_add(400, self._tick_on_top)

    def update(self, fraction: float, detail: str) -> None:
        self.progress.set_fraction(max(0.0, min(1.0, fraction)))
        self.detail_label.set_text(f"{detail}   {int(fraction * 100)}%")

    def keep_on_top(self) -> None:
        """Re-assert ourselves above the game.

        Setting keep-above once is not enough: the game maps a FULLSCREEN window
        after we do, and Mutter stacks fullscreen windows in a layer that can sit
        above ordinary always-on-top windows. Re-raising on a timer wins that
        race without needing any external window-management tool.
        """
        if self._handed_over:
            return
        window = self.get_window()
        if window is None:
            return
        self.set_keep_above(True)
        window.raise_()

    def _tick_on_top(self) -> bool:
        if self._handed_over or not self.get_visible():
            self._raise_id = 0
            return False
        self.keep_on_top()
        return True

    def hand_over(self) -> None:
        """Give the screen to the game: stop floating, then get out of the way."""
        if self._handed_over:
            return
        self._handed_over = True
        if self._raise_id:
            GLib.source_remove(self._raise_id)
            self._raise_id = 0
        self.set_keep_above(False)
        self.hide()
        # Make sure the unmap reaches the X server before the game's next frame.
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)

    # -- input -------------------------------------------------------------

    def _on_key(self, _widget, event) -> bool:
        if event.keyval == Gdk.KEY_Escape and self.on_cancel:
            self.on_cancel()
            return True
        return False


def _screen_size() -> tuple[int, int]:
    display = Gdk.Display.get_default()
    if display is None:
        return 1920, 1080
    monitor = display.get_primary_monitor() or display.get_monitor(0)
    if monitor is None:
        return 1920, 1080
    geometry = monitor.get_geometry()
    return max(640, geometry.width), max(480, geometry.height)


def ensure_x11() -> bool:
    """True when the launcher is on X11, where keep-above actually binds."""
    display = Gdk.Display.get_default()
    return display is not None and "x11" in type(display).__name__.lower()
