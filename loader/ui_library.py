"""The library window: pick a map, manage packs.

This is where DLC management lives now. Because exactly one pack is staged per
launch, "installed" is not a destructive on-disk state any more -- it is just
which card you clicked, so there is nothing to install or uninstall.
"""

from __future__ import annotations

from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402

from . import art, catalog, config  # noqa: E402

CARD_WIDTH = 240
CARD_HEIGHT = 135


# Grip-tape black, hard edges, shouty condensed type - the same language as the
# in-game picker, so the launcher and the overlay read as one product rather
# than a tool that launches a game.
CARD_STYLE = b"""
window, .lib-root       { background: #0d0d0f; }
.card-title             { color: #ffffff; font-size: 15px; font-weight: 800;
                          letter-spacing: 0.3px; }
.card-note              { color: rgba(255,255,255,0.62); font-size: 10px;
                          font-weight: 600; letter-spacing: 1.2px; }
.pack-heading           { color: #ffffff; font-size: 13px; font-weight: 800;
                          letter-spacing: 2.4px; }
.pack-count             { color: rgba(255,255,255,0.35); font-size: 11px;
                          font-weight: 600; letter-spacing: 1.4px; }
.rail                   { background: #ff5a1f; }
.map-card               { background: transparent; border: 0; padding: 0;
                          box-shadow: none; }
.map-card:hover         { background: rgba(255,255,255,0.06); }
.status-line            { color: rgba(255,255,255,0.55); font-size: 11px; }
"""


class MapCard(Gtk.Button):
    """A map tile: generated art with the name laid over it."""

    def __init__(self, pack: catalog.Pack, entry: catalog.MapEntry, on_play):
        super().__init__()
        self.pack = pack
        self.entry = entry
        self.set_relief(Gtk.ReliefStyle.NONE)
        self.get_style_context().add_class("map-card")
        self.connect("clicked", lambda *_: on_play(pack, entry))

        overlay = Gtk.Overlay()
        image = Gtk.Image.new_from_pixbuf(
            art.card_pixbuf(entry.world_id, CARD_WIDTH, CARD_HEIGHT)
        )
        overlay.add(image)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        text.set_valign(Gtk.Align.END)
        text.set_halign(Gtk.Align.START)
        text.set_margin_start(12)
        text.set_margin_bottom(10)
        text.set_margin_end(12)

        title = Gtk.Label(xalign=0, label=entry.name)
        title.get_style_context().add_class("card-title")
        title.set_ellipsize(Pango.EllipsizeMode.END)
        note = Gtk.Label(
            xalign=0,
            label=("WON'T LOAD" if entry.status == "stalls"
                   else "CONFIRMED" if entry.spot_verified
                   else pack.name.upper()),
        )
        note.get_style_context().add_class("card-note")
        note.set_ellipsize(Pango.EllipsizeMode.END)
        text.add(title)
        text.add(note)
        overlay.add_overlay(text)

        self.add(overlay)
        self.set_tooltip_text(
            f"{entry.name}\nworld: {entry.world_id}\nlist position: {entry.sub_index}"
            + ("\nconfirmed by screenshot" if entry.spot_verified
               else "\nseen streaming" if entry.log_seen else "")
            + ("\nthis pack never reaches gameplay - tested, it hangs at the "
               "title screen" if entry.status == "stalls" else "")
        )


class LibraryWindow(Gtk.ApplicationWindow):
    def __init__(self, application, on_play, on_import):
        super().__init__(application=application, title="Skate 3 Level Loader")
        self.on_play = on_play
        self.on_import = on_import
        self.set_default_size(1060, 720)

        header = Gtk.HeaderBar(title="Skate 3 Level Loader", show_close_button=True)
        header.set_subtitle("pick a spot - it boots straight in")
        self.set_titlebar(header)

        import_button = Gtk.Button(label="Import pack…")
        import_button.connect("clicked", self._on_import_clicked)
        header.pack_start(import_button)

        self.windowed_toggle = Gtk.CheckButton(label="Windowed")
        self.windowed_toggle.set_tooltip_text(
            "Run the game in a window instead of fullscreen."
        )
        header.pack_end(self.windowed_toggle)

        # Off by default: a map that cannot load is not worth a click. The
        # toggle exists so the information is not lost - which packs were
        # tested and found broken is worth being able to see - but the default
        # library is the maps you can actually skate.
        self.show_broken = Gtk.CheckButton(label="Show won't-load")
        self.show_broken.set_tooltip_text(
            "Also list the maps that were tested and never reach gameplay."
        )
        self.show_broken.connect("toggled", lambda _b: self.reload())
        header.pack_end(self.show_broken)

        self.status = Gtk.Label(label="")
        self.status.set_ellipsize(Pango.EllipsizeMode.END)
        self.status.get_style_context().add_class("status-line")
        header.pack_end(self.status)

        provider = Gtk.CssProvider()
        provider.load_from_data(CARD_STYLE)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self.body.set_border_width(18)
        scroller.add(self.body)
        self.add(scroller)

        self.reload()
        # Show the whole tree, not just the cards. reload() below calls
        # show_all() on `body`, which shows the cards and everything under them
        # but NOT their ancestors - the scroller, the header buttons and the
        # status label stay hidden, and present() then maps a window with
        # nothing visible in it. The loading screen always looked right because
        # it does call show_all() on itself.
        self.show_all()

    # -- content -----------------------------------------------------------

    def visible_maps(self, pack: catalog.Pack) -> list[catalog.MapEntry]:
        """The maps of `pack` worth offering, in list order."""
        entries = sorted(pack.maps, key=lambda m: m.sub_index)
        if self.show_broken.get_active():
            return entries
        return [e for e in entries if e.status != "stalls"]

    def reload(self) -> None:
        for child in self.body.get_children():
            self.body.remove(child)

        packs = catalog.load_all(config.CATALOG_DIR)
        if not packs:
            self.body.add(self._empty_state())
        shown = 0
        for pack in packs:
            entries = self.visible_maps(pack)
            # A pack with nothing playable left is dropped whole, heading and
            # all - an empty section under a pack name reads as a bug.
            if not entries:
                continue
            shown += len(entries)
            self.body.add(self._pack_section(pack, entries))
        hidden = sum(len(p.maps) for p in packs) - shown
        if hidden and not self.show_broken.get_active():
            self.set_status(f"{shown} playable · {hidden} hidden (won't load)")
        self.body.show_all()

    def _empty_state(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_valign(Gtk.Align.CENTER)
        box.set_margin_top(80)
        title = Gtk.Label()
        title.set_markup('<span size="x-large" weight="bold">No map packs yet</span>')
        hint = Gtk.Label(
            label=(
                "Import a pack's .big file to get started. The pack must ship its "
                "own .header - a synthesized one is silently rejected by the game."
            )
        )
        hint.set_line_wrap(True)
        hint.set_max_width_chars(64)
        hint.set_justify(Gtk.Justification.CENTER)
        button = Gtk.Button(label="Import pack…")
        button.set_halign(Gtk.Align.CENTER)
        button.connect("clicked", self._on_import_clicked)
        box.add(title)
        box.add(hint)
        box.add(button)
        return box

    def _pack_section(self, pack: catalog.Pack, entries: list) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        head_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        rail = Gtk.Box()
        rail.set_size_request(4, 16)
        rail.set_valign(Gtk.Align.CENTER)
        rail.get_style_context().add_class("rail")
        head_row.pack_start(rail, False, False, 0)
        heading = Gtk.Label(xalign=0, label=pack.name.upper())
        heading.get_style_context().add_class("pack-heading")
        head_row.pack_start(heading, False, False, 0)
        count = Gtk.Label(xalign=0, label=f"{len(entries)} SPOTS")
        count.get_style_context().add_class("pack-count")
        count.set_valign(Gtk.Align.CENTER)
        head_row.pack_start(count, False, False, 0)
        box.add(head_row)

        flow = Gtk.FlowBox()
        flow.set_valign(Gtk.Align.START)
        flow.set_max_children_per_line(8)
        flow.set_min_children_per_line(2)
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_column_spacing(12)
        flow.set_row_spacing(12)
        for entry in entries:
            flow.add(MapCard(pack, entry, self.on_play))
        box.add(flow)
        return box

    # -- actions -----------------------------------------------------------

    def set_status(self, text: str) -> None:
        self.status.set_text(text)

    def _on_import_clicked(self, _button) -> None:
        dialog = Gtk.FileChooserDialog(
            title="Choose a map pack (.big)",
            transient_for=self,
            action=Gtk.FileChooserAction.OPEN,
        )
        dialog.add_buttons(
            "Cancel", Gtk.ResponseType.CANCEL, "Import", Gtk.ResponseType.ACCEPT
        )
        dialog.set_current_folder(str(Path.home() / "Downloads"))
        big_filter = Gtk.FileFilter()
        big_filter.set_name("BIG archives")
        big_filter.add_pattern("*.big")
        dialog.add_filter(big_filter)
        any_filter = Gtk.FileFilter()
        any_filter.set_name("All files")
        any_filter.add_pattern("*")
        dialog.add_filter(any_filter)

        if dialog.run() == Gtk.ResponseType.ACCEPT:
            path = Path(dialog.get_filename())
            dialog.destroy()
            self.on_import(path)
        else:
            dialog.destroy()

    def show_error(self, title: str, detail: str) -> None:
        dialog = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.CLOSE,
            text=title,
        )
        dialog.format_secondary_text(detail)
        dialog.run()
        dialog.destroy()
