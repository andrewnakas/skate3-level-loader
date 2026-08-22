"""Runtime hook: the handful of GTK environment facts PyInstaller does not set.

PyInstaller's own gi hooks already point GI_TYPELIB_PATH, GDK_PIXBUF_MODULE_FILE,
GIO_MODULE_DIR, GTK_PATH and XDG_DATA_DIRS at the bundle. Three things are left:

* GSETTINGS_SCHEMA_DIR - no hook collects share/glib-2.0/schemas, and without
  the compiled schemas the first Gtk.FileChooserDialog is a g_error, which is an
  ABORT rather than an exception. The spec ships the XML; this points at it.
* FONTCONFIG_PATH - on macOS a bundle with no fontconfig config renders every
  label BLANK, with no error anywhere.
* GDK_BACKEND - inherited from an outer shell, an x11 value would stop GTK
  starting at all on Windows and macOS, whose GDK has no x11 backend.
"""

import os
import sys


def _configure():
    base = getattr(sys, "_MEIPASS", None)
    if not base:
        return

    schemas = os.path.join(base, "share", "glib-2.0", "schemas")
    if os.path.isdir(schemas):
        os.environ.setdefault("GSETTINGS_SCHEMA_DIR", schemas)

    fonts = os.path.join(base, "etc", "fonts")
    if os.path.isdir(fonts):
        os.environ.setdefault("FONTCONFIG_PATH", fonts)

    if not sys.platform.startswith("linux"):
        os.environ.pop("GDK_BACKEND", None)


_configure()
