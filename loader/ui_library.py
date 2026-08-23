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
            label=("FILE MISSING" if not pack.located
                   else "WON'T LOAD" if entry.status == "stalls"
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
            + ("\nthe pack file is not on this machine - use Locate on the "
               "pack heading" if not pack.located else "")
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
        # Packs whose file is on THIS machine come first: that is what the user
        # can actually click. The rest are catalog entries the launcher knows
        # about and cannot start, and burying them under everything playable is
        # the difference between a library and a wall of dead cards.
        here = [p for p in packs if p.located]
        elsewhere = [p for p in packs if not p.located]

        shown = 0
        for pack in here:
            entries = self.visible_maps(pack)
            # A pack with nothing playable left is dropped whole, heading and
            # all - an empty section under a pack name reads as a bug.
            if not entries:
                continue
            shown += len(entries)
            self.body.add(self._pack_section(pack, entries))

        if elsewhere:
            # Open only when there is nothing else to look at. With maps ready
            # to play, this is a footnote; with none, it IS the screen.
            self.body.add(self._missing_section(elsewhere, expanded=not shown))

        hidden = sum(len(p.maps) for p in here) - shown
        # Count what is PLAYABLE NOW. Counting every record made a fresh install
        # claim "121 playable" while not one of them had a file to stage.
        parts = [f"{shown} playable"]
        if hidden and not self.show_broken.get_active():
            parts.append(f"{hidden} hidden (won't load)")
        if elsewhere:
            parts.append(f"{len(elsewhere)} packs need their file")
        self.set_status(" · ".join(parts))
        self.body.show_all()
        # show_all() expands an Expander, so the collapse has to come after it.
        if getattr(self, "_collapse_after_show", None) is not None:
            self._collapse_after_show.set_expanded(False)
            self._collapse_after_show = None

    def _missing_section(self, packs: list, expanded: bool = False) -> Gtk.Widget:
        """The packs the launcher knows about but cannot find on this machine.

        One expander, not 43 dead rows. Everything in here is curation shipped
        with the release - names, maps, which of them load - waiting for the
        user to supply their own copy of the pack.
        """
        maps = sum(len(p.maps) for p in packs)
        expander = Gtk.Expander(
            label=f"{len(packs)} more packs the launcher knows about "
                  f"({maps} maps) - locate a file to enable one"
        )
        expander.set_margin_top(18)
        self._collapse_after_show = None if expanded else expander

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        inner.set_margin_top(10)
        inner.set_margin_start(8)
        for pack in packs:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            name = Gtk.Label(xalign=0, label=pack.name)
            name.get_style_context().add_class("card-title")
            row.pack_start(name, True, True, 0)
            count = Gtk.Label(xalign=0, label=f"{len(pack.maps)} maps")
            count.get_style_context().add_class("pack-count")
            row.pack_start(count, False, False, 0)
            locate = Gtk.Button(label="Locate…")
            locate.connect("clicked", self._on_locate_clicked, pack)
            row.pack_start(locate, False, False, 0)
            inner.add(row)
        expander.add(inner)
        return expander

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
        if not pack.located:
            locate = Gtk.Button(label="Locate pack file…")
            locate.set_valign(Gtk.Align.CENTER)
            locate.connect("clicked", self._on_locate_clicked, pack)
            head_row.pack_end(locate, False, False, 0)
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

    def _on_locate_clicked(self, _button, pack) -> None:
        """Point a shipped catalog record at this machine's copy of the pack.

        The catalog that ships with a release carries the curation - names,
        world ids, spawn nodes, which maps are known to load - with the file
        paths stripped, because those pointed at the machine that did the
        importing. This is how a user supplies their own copy.
        """
        dialog = Gtk.FileChooserDialog(
            title=f"Where is {pack.name}?",
            transient_for=self,
            action=Gtk.FileChooserAction.OPEN,
        )
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Use this", Gtk.ResponseType.ACCEPT)
        pattern = Gtk.FileFilter()
        pattern.set_name("Map packs (*.big)")
        pattern.add_pattern("*.big")
        dialog.add_filter(pattern)
        response = dialog.run()
        chosen = dialog.get_filename() if response == Gtk.ResponseType.ACCEPT else None
        dialog.destroy()
        if not chosen:
            return
        try:
            pack.relocate(Path(chosen))
            catalog.save(pack, config.CATALOG_DIR)
        except Exception as exc:  # noqa: BLE001
            self.show_error(f"Could not use that file for {pack.name}", str(exc))
            return
        self.set_status(f"{pack.name}: using {chosen}")
        self.reload()

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
