# -*- mode: python ; coding: utf-8 -*-
"""One spec, three platforms.

Kept as a single file on purpose: the excludes, the hiddenimports, the
hooksconfig and the catalog seed are identical everywhere, and three copies of
that drift - you tighten an exclude on Linux and Windows keeps shipping Pillow
for six months. What actually differs is about twenty lines: where the GTK
prefix is, the second console executable on Windows, and the macOS .app.

Build with:

    pyinstaller --noconfirm --clean packaging/skate3loader.spec

SKATE3_GTK_PREFIX overrides prefix discovery (MSYS2's /ucrt64, `brew --prefix`).
"""

import glob
import os
import subprocess
import sys
from pathlib import Path

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

ROOT = Path(SPECPATH).parent
sys.path.insert(0, str(ROOT))
from loader._version import __version__ as VERSION  # noqa: E402


def gtk_prefix() -> Path:
    """Where the GTK stack we are bundling lives."""
    override = os.environ.get("SKATE3_GTK_PREFIX")
    if override:
        return Path(override)
    if IS_WIN:
        # MSYS2 UCRT64. MINGW64 was deprecated in March 2026.
        return Path(os.environ.get("MINGW_PREFIX", "/ucrt64"))
    if IS_MAC:
        try:
            found = subprocess.run(["brew", "--prefix"], capture_output=True,
                                   text=True, check=True).stdout.strip()
            return Path(found)
        except Exception:
            return Path("/opt/homebrew")
    return Path("/usr")


PREFIX = gtk_prefix()

# -- data ------------------------------------------------------------------

# The catalog that ships is the curation with the machine-local file pointers
# stripped out - see packaging/make_seed_catalog.py. Generated here rather than
# committed so it cannot drift from the real catalog.
sys.path.insert(0, str(ROOT / "packaging"))
import make_seed_catalog  # noqa: E402

SEED = Path(os.environ.get("SKATE3_SEED_DIR", ROOT / "build" / "seed-catalog"))
_packs, _stripped = make_seed_catalog.build(ROOT / "catalog", SEED)
print(f"seed catalog: {_packs} packs, {_stripped} machine-local paths stripped")

datas = [(str(SEED), "catalog")]

# GSettings schemas. No PyInstaller hook collects these on ANY platform, and
# without them Gtk.FileChooserDialog aborts the process (a g_error, uncatchable)
# the first time the user clicks Import. PyInstaller compiles whatever schema
# XML it finds in datas into gschemas.compiled during assembly.
schema_dir = PREFIX / "share" / "glib-2.0" / "schemas"
for pattern in ("*.xml", "*.gschema.override"):
    datas += [(f, "share/glib-2.0/schemas") for f in glob.glob(str(schema_dir / pattern))]

# The Gio hook collects share/mime on non-Windows only.
if IS_WIN:
    mime_cache = PREFIX / "share" / "mime" / "mime.cache"
    if mime_cache.is_file():
        datas.append((str(mime_cache), "share/mime"))

# -- what does NOT ship ----------------------------------------------------

# The harness is Linux-only by design (X11 capture, /dev/shm, GNOME gsettings)
# and is not part of the application. Excluding it here is what makes "app
# parity only" a fact about the artifact rather than an intention.
EXCLUDES = [
    "loader.display",
    "loader.spotcheck",
    "loader.fingerprint",
    "scripts",
    "PIL",
    "numpy",
    "tkinter",
    "unittest",
    "pydoc",
    "doctest",
    "setuptools",
    "pip",
    "pkg_resources",
]

# gi.repository members are resolved dynamically, so nothing static finds them.
HIDDEN = [f"gi.repository.{name}" for name in
          ("Gtk", "Gdk", "Gio", "GLib", "GObject", "GdkPixbuf", "Pango", "PangoCairo")]

a = Analysis(
    [str(ROOT / "loader" / "__main__.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=HIDDEN,
    hookspath=[],
    runtime_hooks=[str(ROOT / "packaging" / "rthook_skate3loader.py")],
    excludes=EXCLUDES,
    noarchive=False,
    # Without this the gi hooks collect EVERY icon theme and EVERY translation
    # on the build machine: a 600-800 MB bundle instead of ~150 MB.
    hooksconfig={
        "gi": {
            "icons": ["Adwaita", "hicolor"],
            "themes": ["Adwaita"],
            "languages": ["en", "en_US"],
            "module-versions": {"Gtk": "3.0", "Gdk": "3.0"},
        }
    },
)

pyz = PYZ(a.pure)

console_exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="skate3loader",
    console=True,
    icon=None,
    debug=False,
    strip=False,
    upx=False,
)

collected = [console_exe]

if IS_WIN:
    # Two entry points from one build: the subcommands (import, list, doctor,
    # play) are terminal programs and a windowed binary would swallow their
    # output, while double-clicking the console one flashes a shell.
    collected.append(EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="skate3loader-gui",
        console=False,
        icon=None,
        debug=False,
        strip=False,
        upx=False,
    ))

coll = COLLECT(
    *collected,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="skate3loader",
)

if IS_MAC:
    app = BUNDLE(
        coll,
        name="Skate 3 Level Loader.app",
        icon=None,
        # Must match LoaderApp's application_id, or GTK and macOS disagree about
        # what this program is.
        bundle_identifier="dev.skate3.loader",
        version=VERSION,
        info_plist={
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "15.0",
            "NSRequiresAquaSystemAppearance": False,
        },
    )
