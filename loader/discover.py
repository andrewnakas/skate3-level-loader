"""Finding every map pack on disk and importing what can actually be used.

Community packs arrive in a lot of shapes and most of them are NOT usable by
this build. Rather than let the game fail at boot, everything is classified up
front and anything unusable is reported with the specific reason.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path

from . import catalog, config

# Files that are never a map pack, so we do not waste minutes scanning them.
SKIP_NAMES = {"skate3recomp-linux.zip"}
SKIP_SUFFIXES = {".header", ".txt", ".rtf", ".log", ".png", ".jpg", ".md", ".xcp", ".iso"}
# A park-save pack is skater content, not a world.
PARK_MARKER = "SPARK_SKATER/"
MIN_PACK_BYTES = 4 << 20


@dataclass
class Finding:
    path: Path
    kind: str  # "imported", "skipped", "failed"
    detail: str
    pack: catalog.Pack | None = None


def _zip_payload(path: Path) -> Path | None:
    """Extract a map payload out of a .zip, if it holds one.

    Only .zip is handled: this machine has unzip but no unrar or 7z, so RAR and
    7z packs have to be unpacked by hand first.
    """
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if any(PARK_MARKER in name for name in names):
                return None
            candidates = [
                name
                for name in names
                if not name.endswith("/")
                and archive.getinfo(name).file_size > MIN_PACK_BYTES
            ]
            if not candidates:
                return None
            candidates.sort(key=lambda n: archive.getinfo(n).file_size, reverse=True)
            target_dir = config.CACHE_DIR / path.stem
            target_dir.mkdir(parents=True, exist_ok=True)
            out = target_dir / Path(candidates[0]).name
            if not out.exists():
                with archive.open(candidates[0]) as src, out.open("wb") as dst:
                    while chunk := src.read(1 << 20):
                        dst.write(chunk)
            return out
    except (zipfile.BadZipFile, OSError):
        return None


def candidates(directory: Path) -> list[Path]:
    found = []
    for path in sorted(directory.iterdir()):
        if path.is_dir():
            found.extend(sorted(path.rglob("*.big")))
            continue
        if not path.is_file():
            continue
        if path.name.lower() in SKIP_NAMES or path.suffix.lower() in SKIP_SUFFIXES:
            continue
        if path.stat().st_size < MIN_PACK_BYTES:
            continue
        found.append(path)
    return found


def import_directory(directory: Path, on_progress=None) -> list[Finding]:
    """Try to import every pack in a directory; explain each one that cannot be."""
    results: list[Finding] = []
    for path in candidates(directory):
        if on_progress:
            on_progress(path)

        source = path
        if path.suffix.lower() == ".zip":
            payload = _zip_payload(path)
            if payload is None:
                results.append(Finding(path, "skipped", "no map payload inside this zip"))
                continue
            source = payload

        try:
            pack = catalog.import_pack(source, config.CATALOG_DIR)
        except catalog.ImportError_ as exc:
            results.append(Finding(path, "skipped", str(exc)))
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            results.append(Finding(path, "failed", f"{type(exc).__name__}: {exc}"))
        else:
            results.append(
                Finding(path, "imported", f"{pack.name}: {len(pack.maps)} map(s)", pack)
            )
    return results
