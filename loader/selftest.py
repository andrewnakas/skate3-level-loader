"""Prove a build is intact, without a game and (mostly) without a display.

Every check here stands for a failure that has actually happened, or that a
frozen GTK bundle fails at in a way no unit test would notice:

* a typelib the bundle forgot -> the import block
* a broken gdk-pixbuf loaders.cache -> card art rendering
* missing gschemas.compiled -> the file chooser aborts the process the first
  time a user clicks Import. Checked with SettingsSchemaSource.lookup(), which
  returns None, rather than Gio.Settings.new(), which is a g_error and takes
  the process down with it.
* the frozen path bug -> reading the catalog back out of the data dir

`--headless` needs no display and is what gates a release on every platform.
`--gui` additionally realizes the two real windows; it needs a display server.
"""

from __future__ import annotations

import argparse
import sys
import traceback

from . import config

TYPELIBS = ("Gtk", "Gdk", "Gio", "GLib", "GObject", "GdkPixbuf", "Pango")


class Check:
    def __init__(self) -> None:
        self.failures = 0

    def run(self, label, fn) -> None:
        try:
            detail = fn()
        except Exception as exc:  # noqa: BLE001 - a selftest reports, never raises
            self.failures += 1
            print(f"[FAIL] {label}\n       {exc.__class__.__name__}: {exc}")
            traceback.print_exc(limit=3)
            return
        print(f"[ ok ] {label}" + (f"  ({detail})" if detail else ""))


def _import_typelibs() -> str:
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gtk  # noqa: F401

    for name in TYPELIBS:
        __import__(f"gi.repository.{name}")
    import cairo  # noqa: F401

    return f"{len(TYPELIBS)} typelibs + pycairo"


def _card_art() -> str:
    """Renders a card with pycairo and converts it to a pixbuf.

    A bundle whose loaders.cache still points at the build machine's paths
    fails right here, and nowhere earlier.
    """
    from . import art, catalog

    packs = catalog.load_all(config.CATALOG_DIR)
    world = packs[0].maps[0].world_id if packs and packs[0].maps else "selftest"
    pixbuf = art.card_pixbuf(world, 320, 180)
    return f"{pixbuf.get_width()}x{pixbuf.get_height()} for {world}"


def _schemas() -> str:
    from gi.repository import Gio

    source = Gio.SettingsSchemaSource.get_default()
    if source is None:
        raise RuntimeError("no GSettings schema source at all (gschemas.compiled missing)")
    schema = source.lookup("org.gtk.Settings.FileChooser", True)
    if schema is None:
        raise RuntimeError(
            "org.gtk.Settings.FileChooser is not installed; the Import button "
            "would abort the process. Ship share/glib-2.0/schemas."
        )
    return "org.gtk.Settings.FileChooser found"


def _catalog() -> str:
    from . import catalog

    packs = catalog.load_all(config.CATALOG_DIR)
    if not packs:
        raise RuntimeError(f"no packs under {config.CATALOG_DIR} (seed did not run?)")
    return f"{len(packs)} packs, {sum(len(p.maps) for p in packs)} maps from {config.CATALOG_DIR}"


def _gtk_init() -> str:
    from gi.repository import Gtk

    ok, _ = Gtk.init_check([])
    return "display available" if ok else "no display (expected headless)"


def _gui() -> str:
    """Realize the two real windows, then quit.

    Deliberately NOT through LoaderApp: its application_id is single-instance,
    so a selftest run while the launcher is open would silently attach to it and
    test nothing.
    """
    from gi.repository import Gio, GLib, Gtk

    from . import ui_library, ui_loading

    if not Gtk.init_check([])[0]:
        raise RuntimeError("no display; run under xvfb-run or skip --gui")

    # NON_UNIQUE and a distinct id: the launcher's own application_id is
    # single-instance, so a selftest run while the library is open would attach
    # to that process and test nothing at all.
    app = Gtk.Application(application_id="dev.skate3.loader.selftest",
                          flags=Gio.ApplicationFlags.NON_UNIQUE)
    app.register(None)

    library = ui_library.LibraryWindow(app, lambda *a: None, lambda *a: None)
    library.show_all()
    loading = ui_loading.LoadingScreen()
    loading.show_all()
    dialog = Gtk.FileChooserDialog(title="selftest", action=Gtk.FileChooserAction.OPEN)
    dialog.destroy()

    GLib.idle_add(Gtk.main_quit)
    Gtk.main()
    loading.destroy()
    library.destroy()
    return "library + loading screen realized"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="skate3loader selftest")
    parser.add_argument("--headless", action="store_true",
                        help="checks that need no display (the default)")
    parser.add_argument("--gui", action="store_true",
                        help="also realize the real windows; needs a display")
    args = parser.parse_args(argv)

    print(f":: {'frozen bundle' if config.FROZEN else 'checkout'} at {config.bundle_root()}")
    print(f":: state {config.state_root()}")
    print(f":: platform {sys.platform}")

    checks = Check()
    checks.run("gi typelibs import", _import_typelibs)
    checks.run("GSettings schemas", _schemas)
    checks.run("catalog readable", _catalog)
    checks.run("card art renders", _card_art)
    checks.run("Gtk.init_check", _gtk_init)
    if args.gui:
        checks.run("windows realize", _gui)

    print()
    print("intact" if not checks.failures else f"{checks.failures} check(s) FAILED")
    return 0 if not checks.failures else 1
