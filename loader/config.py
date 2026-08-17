"""Where everything lives. Override any of these with SKATE3LOADER_* env vars."""

from __future__ import annotations

import os
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
SKATE3 = HERE.parent


def _path(env: str, default: Path) -> Path:
    return Path(os.environ.get(env, default)).expanduser()


# The freeskate stager we delegate all content staging to.
FREESKATE_DIR = _path("SKATE3LOADER_FREESKATE", SKATE3 / "freeskate")
FREESKATE_BIN = FREESKATE_DIR / "freeskate"
RUNTIME = FREESKATE_DIR / "runtime"
LOG_FILE = RUNTIME / "logs" / "freeskate.log"

# The scratch map profile we regenerate on every launch. Kept separate from the
# hand-written profiles in maps/ so nothing the user authored is ever touched.
LOADER_PROFILE = "_loader"
LOADER_PROFILE_DIR = FREESKATE_DIR / "maps" / LOADER_PROFILE

# The stock install (source of default.xex for the relaunch alias).
INSTALL = _path("SKATE3LOADER_INSTALL", SKATE3 / "Skate3Recomp-Linux")

# Default to the dev build: it carries the renderer fixes and the crash reporter.
# Falls back to the shipped binary when the dev tree has not been built.
DEV_BINARY = SKATE3 / "skate3recomp-dev" / "out" / "build" / "linux-release" / "skate3"
SHIPPED_BINARY = INSTALL / "skate3"


def default_binary() -> Path:
    override = os.environ.get("SKATE3LOADER_BINARY")
    if override:
        return Path(override).expanduser()
    return DEV_BINARY if DEV_BINARY.is_file() else SHIPPED_BINARY


# Loader-owned state.
CATALOG_DIR = _path("SKATE3LOADER_CATALOG", HERE / "catalog")
ART_DIR = _path("SKATE3LOADER_ART", HERE / "art")
# Screenshots confirmed to BE a given map, and confirmed not to be.
# `spotcheck` judges every run against these.
REFERENCES_DIR = _path("SKATE3LOADER_REFERENCES", HERE / "references")

# Test output. These used to be absolute paths into one session's scratchpad,
# which meant every run after that session wrote somewhere nobody was looking.
# Under the repo by default, and out of the way of the catalog.
WORK_DIR = _path("SKATE3LOADER_WORK", HERE / "work")
SPOTS_DIR = _path("SKATE3LOADER_SPOTS", WORK_DIR / "spots")
TRIALS_DIR = _path("SKATE3LOADER_TRIALS", WORK_DIR / "trials")
SWEEP_DIR = _path("SKATE3LOADER_SWEEP", WORK_DIR / "sweep")
# Payloads extracted out of .zip packs, so the originals stay untouched.
CACHE_DIR = _path("SKATE3LOADER_CACHE", HERE / "cache")
# The in-game picker writes a relaunch request here when the chosen map lives
# in a pack other than the one staged at boot.
REQUEST_FILE = RUNTIME / "loader_request.txt"
