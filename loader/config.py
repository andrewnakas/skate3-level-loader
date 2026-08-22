"""Where everything lives. Override any of these with SKATE3LOADER_* env vars.

Three roots, and the difference between them is the whole reason this file grew:

* `bundle_root()`  read-only data that SHIPS with the app (the catalog seed).
* `app_dir()`      the directory the executable sits in; `engine/` is next to it.
* `state_root()`   everything we write. In a dev checkout that is the checkout,
                   exactly as before. Frozen, it is the per-user data directory -
                   a bundle is read-only, and on macOS writing inside the .app
                   invalidates its signature and the app then refuses to launch.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# --------------------------------------------------------------------------
# Platform
# --------------------------------------------------------------------------

IS_LINUX = sys.platform.startswith("linux")
IS_MACOS = sys.platform == "darwin"
IS_WINDOWS = os.name == "nt"

FROZEN = bool(getattr(sys, "frozen", False))

#: The game binary and the runtime library it loads, per platform.
BINARY_NAME = "skate3.exe" if IS_WINDOWS else "skate3"
RUNTIME_LIB = (
    "rexruntime.dll" if IS_WINDOWS else
    "librexruntime.dylib" if IS_MACOS else
    "librexruntime.so"
)
#: The CMake preset a dev build of the engine lands under.
BUILD_PRESET = (
    "windows-release" if IS_WINDOWS else
    "macos-release" if IS_MACOS else
    "linux-release"
)

APP_NAME = "Skate3Loader"


def bundle_root() -> Path:
    """Read-only data shipped with the app."""
    if FROZEN:
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def app_dir() -> Path:
    """The directory the executable lives in. `engine/` is looked for here."""
    if FROZEN:
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    """Per-user writable state.

    Hand-rolled rather than `GLib.get_user_data_dir()` so that `doctor`, `list`
    and `import` do not have to import GTK to work out where the catalog is.
    The answer is the same one GLib gives.
    """
    override = os.environ.get("SKATE3LOADER_DATA")
    if override:
        return Path(override).expanduser()
    if IS_WINDOWS:
        base = os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
        return Path(base) / APP_NAME
    if IS_MACOS:
        return Path.home() / "Library" / "Application Support" / APP_NAME
    base = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
    return Path(base) / "skate3loader"


def state_root() -> Path:
    """Where we write. The checkout in development, the data dir when frozen."""
    return data_dir() if FROZEN else bundle_root()


HERE = bundle_root()
SKATE3 = HERE.parent
STATE = state_root()


def _path(env: str, default: Path) -> Path:
    return Path(os.environ.get(env, default)).expanduser()


# --------------------------------------------------------------------------
# Staging
#
# freeskate is vendored (loader/vendor/freeskate.py) and driven in-process by
# loader.staging; FREESKATE_DIR now only names where its runtime tree lives.
# A dev checkout keeps using the sibling freeskate/ so an existing install,
# its config.toml and its hand-written maps/ profiles all keep working.
# --------------------------------------------------------------------------

FREESKATE_DIR = _path("SKATE3LOADER_FREESKATE", SKATE3 / "freeskate" if not FROZEN else STATE / "freeskate")
FREESKATE_BIN = FREESKATE_DIR / "freeskate"
RUNTIME = FREESKATE_DIR / "runtime"
LOG_FILE = RUNTIME / "logs" / "freeskate.log"

# The scratch map profile we regenerate on every launch. Kept separate from the
# hand-written profiles in maps/ so nothing the user authored is ever touched.
LOADER_PROFILE = "_loader"
LOADER_PROFILE_DIR = FREESKATE_DIR / "maps" / LOADER_PROFILE


# --------------------------------------------------------------------------
# The engine
# --------------------------------------------------------------------------

# The stock install (source of default.xex for the relaunch alias). Frozen, the
# game files the ISO installer extracted live under our own data dir.
INSTALL = _path(
    "SKATE3LOADER_INSTALL",
    STATE / "install" if FROZEN else SKATE3 / "Skate3Recomp-Linux",
)

# Shipped next to the executable by the release archive.
BUNDLED_BINARY = app_dir() / "engine" / BINARY_NAME


def bundled_engines() -> list[Path]:
    """`engine/<binary>` at or above the executable's directory.

    Walking up matters on macOS: `app_dir()` inside a .app is
    `Skate 3 Level Loader.app/Contents/MacOS`, and putting the engine THERE
    modifies the bundle, which invalidates its ad-hoc signature and stops it
    launching. So `engine/` beside the .app has to work too.
    """
    found: list[Path] = []
    directory = app_dir()
    for candidate in [directory, *directory.parents[:3]]:
        binary = candidate / "engine" / BINARY_NAME
        if binary not in found:
            found.append(binary)
    return found
# The dev build: it carries the renderer fixes and the crash reporter.
DEV_BINARY = SKATE3 / "skate3recomp-dev" / "out" / "build" / BUILD_PRESET / BINARY_NAME
SHIPPED_BINARY = INSTALL / BINARY_NAME


def engine_candidates() -> list[Path]:
    """Every place a game binary might be, best first."""
    from . import settings

    found: list[Path] = []
    configured = settings.load().get("engine")
    if configured:
        found.append(Path(configured).expanduser())
    found += bundled_engines()
    found += [DEV_BINARY, SHIPPED_BINARY]
    return found


def default_binary() -> Path:
    override = os.environ.get("SKATE3LOADER_BINARY")
    if override:
        return Path(override).expanduser()
    for candidate in engine_candidates():
        if candidate.is_file():
            return candidate
    return BUNDLED_BINARY if FROZEN else DEV_BINARY


def game_data_root() -> Path:
    """Where the extracted game files (default.xex and friends) live."""
    override = os.environ.get("SKATE3LOADER_GAME_DATA")
    if override:
        return Path(override).expanduser()
    from . import settings

    configured = settings.load().get("game_data_root")
    if configured:
        return Path(configured).expanduser()
    return INSTALL / "game"


def game_installed() -> bool:
    """The engine's own IsGameInstalled test, from this side of the process."""
    return (game_data_root() / "default.xex").is_file()


# --------------------------------------------------------------------------
# Loader-owned state
# --------------------------------------------------------------------------

SETTINGS_FILE = _path("SKATE3LOADER_SETTINGS", STATE / "settings.json")
CATALOG_DIR = _path("SKATE3LOADER_CATALOG", STATE / "catalog")
ART_DIR = _path("SKATE3LOADER_ART", STATE / "art")
# Screenshots confirmed to BE a given map, and confirmed not to be.
# `spotcheck` judges every run against these.
REFERENCES_DIR = _path("SKATE3LOADER_REFERENCES", HERE / "references")

# Test output. These used to be absolute paths into one session's scratchpad,
# which meant every run after that session wrote somewhere nobody was looking.
WORK_DIR = _path("SKATE3LOADER_WORK", STATE / "work")
SPOTS_DIR = _path("SKATE3LOADER_SPOTS", WORK_DIR / "spots")
TRIALS_DIR = _path("SKATE3LOADER_TRIALS", WORK_DIR / "trials")
SWEEP_DIR = _path("SKATE3LOADER_SWEEP", WORK_DIR / "sweep")
# Payloads extracted out of .zip packs, so the originals stay untouched.
CACHE_DIR = _path("SKATE3LOADER_CACHE", STATE / "cache")
# The in-game picker writes a relaunch request here when the chosen map lives
# in a pack other than the one staged at boot.
REQUEST_FILE = RUNTIME / "loader_request.txt"

#: Read-only catalog shipped inside the bundle, copied into CATALOG_DIR once.
CATALOG_SEED = bundle_root() / "catalog"


def seed_state() -> None:
    """First run: give the user a writable copy of the shipped catalog."""
    import shutil

    CATALOG_DIR.mkdir(parents=True, exist_ok=True)
    if CATALOG_SEED == CATALOG_DIR or not CATALOG_SEED.is_dir():
        return
    if any(CATALOG_DIR.glob("*.json")):
        return
    for source in CATALOG_SEED.glob("*.json"):
        shutil.copy2(source, CATALOG_DIR / source.name)
