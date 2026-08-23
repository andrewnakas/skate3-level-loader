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

from . import art, catalog, config, discover  # noqa: E402

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

        advanced_button = Gtk.Button(label="Advanced")
        advanced_button.set_tooltip_text(
            "How a map is reached, what the launch logs, and the menu timings."
        )
        advanced_button.connect("clicked", self._on_advanced_clicked)
        header.pack_end(advanced_button)

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

        # Drag and drop: the whole window is the drop target, because aiming at
        # a particular strip of it is work the user should not have to do.
        self.drag_dest_set(Gtk.DestDefaults.ALL, [], Gdk.DragAction.COPY)
        self.drag_dest_add_uri_targets()
        self.connect("drag-data-received", self._on_drop)

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
        # Only packs whose file is on THIS machine are shown. The rest stay in
        # the catalog - the launcher remembers their maps, their world ids and
        # which of them load, so dropping the file in later restores all of it -
        # but a card you cannot click is not a library, it is a list of
        # disappointments.
        here = [p for p in packs if p.located]
        elsewhere = [p for p in packs if not p.located]

        # The base game always leads. On a fresh install it is the only thing
        # anyone can do, and it is what makes the launcher useful before a
        # single pack has been imported.
        self.body.add(self._pack_section(catalog.stock_pack(),
                                         catalog.stock_pack().maps))

        shown = 0
        for pack in here:
            entries = self.visible_maps(pack)
            # A pack with nothing playable left is dropped whole, heading and
            # all - an empty section under a pack name reads as a bug.
            if not entries:
                continue
            shown += len(entries)
            self.body.add(self._pack_section(pack, entries))

        if not shown:
            self.body.add(self._drop_hint(len(elsewhere)))

        hidden = sum(len(p.maps) for p in here) - shown
        # Count what is PLAYABLE NOW. Counting every record made a fresh install
        # claim "121 playable" while not one of them had a file to stage.
        parts = [f"{shown} custom map{'' if shown == 1 else 's'}"] if shown else []
        if hidden and not self.show_broken.get_active():
            parts.append(f"{hidden} hidden (won't load)")
        self.set_status(" · ".join(parts))
        self.body.show_all()
        # show_all() expands an Expander, so the collapse has to come after it.
        if getattr(self, "_collapse_after_show", None) is not None:
            self._collapse_after_show.set_expanded(False)
            self._collapse_after_show = None

    def _drop_hint(self, known: int) -> Gtk.Widget:
        """What to do next, when there is nothing but the game itself.

        This replaces a list of packs the launcher could not start. The
        catalog still remembers them - drop the matching file and the pack
        comes back with its maps, its world ids and its tested status intact.
        """
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(48)
        box.set_halign(Gtk.Align.CENTER)

        title = Gtk.Label()
        title.set_markup('<span size="large" weight="bold">Drop a map pack here</span>')
        box.add(title)

        detail = Gtk.Label(justify=Gtk.Justification.CENTER, wrap=True)
        detail.set_max_width_chars(60)
        detail.set_text(
            "Drag a .big file - or a folder of them - onto this window and it "
            "is installed. Everything else on this screen stays playable while "
            "you do."
        )
        detail.get_style_context().add_class("pack-count")
        box.add(detail)

        if known:
            memory = Gtk.Label(justify=Gtk.Justification.CENTER, wrap=True)
            memory.set_max_width_chars(60)
            memory.set_text(
                f"The launcher already knows {known} packs by name, including "
                "which of their maps load. Drop one in and it arrives complete."
            )
            memory.get_style_context().add_class("pack-count")
            box.add(memory)
        return box

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

    # -- drag and drop -----------------------------------------------------

    def _on_drop(self, _widget, _context, _x, _y, data, _info, _time) -> None:
        """Install whatever was dropped: .big files, or folders of them.

        A folder goes through the same directory scan as `skate3loader scan`,
        which explains every rejection rather than skipping silently - dropping
        a folder of downloads and being told nothing happened would be worse
        than not accepting folders at all.
        """
        paths = []
        for uri in data.get_uris():
            try:
                paths.append(Path(GLib.filename_from_uri(uri)[0]))
            except Exception:  # noqa: BLE001 - a non-file URI is just not for us
                continue
        if not paths:
            return
        self.set_status(f"installing {len(paths)} item(s)…")
        # Let the status line paint before the scan blocks the loop: a 292 MB
        # container takes ~12 s to read its location list.
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)

        installed, failures = [], []
        for path in paths:
            try:
                if path.is_dir():
                    results = discover.import_directory(path)
                    installed += [r for r in results if r.kind == "imported"]
                    failures += [(r.path.name, r.detail) for r in results
                                 if r.kind != "imported"]
                else:
                    pack = catalog.import_pack(path, config.CATALOG_DIR)
                    installed.append(pack)
            except Exception as exc:  # noqa: BLE001 - reported, never raised at a user
                failures.append((path.name, str(exc)))

        self.reload()
        if installed and not failures:
            self.set_status(f"installed {len(installed)} pack(s)")
        elif installed:
            self.set_status(f"installed {len(installed)}, {len(failures)} skipped")
        if failures and not installed:
            first, detail = failures[0]
            self.show_error(
                f"Could not install {first}",
                detail + ("" if len(failures) == 1 else
                          f"\n\n{len(failures) - 1} other item(s) were skipped too."),
            )

    def _on_advanced_clicked(self, _button) -> None:
        from . import ui_advanced

        dialog = ui_advanced.AdvancedDialog(self)
        dialog.run()
        dialog.destroy()
        # A changed strategy or timing only takes effect on the NEXT launch, and
        # saying so beats a user watching an unchanged boot and concluding the
        # setting did nothing.
        self.set_status("advanced settings saved - they apply to the next launch")

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
