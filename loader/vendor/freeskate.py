#!/usr/bin/env python3
"""freeskate - boot Skate 3 Recomp straight into gameplay with custom content.

Everything this tool produces lives under freeskate/runtime/. The Skate 3
install, the ISO, and ~/.local/share/skate3 are only ever read.

The recomp binary already knows how to do the hard parts:
  --game_data_root / --user_data_root    fully relocate game + user state
  --skate3_demo_path                     auto-clears language select, press
                                         start and the intro movie, then
                                         replays a pad macro once gameplay
                                         presence is reached
so this is a stager plus a launcher, not a patcher.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNTIME = HERE / "runtime"
MAPS_DIR = HERE / "maps"
MACROS_FILE = HERE / "macros.toml"
CONFIG_FILE = HERE / "config.toml"

# Content-manager layout, from skate3_app_common.cpp InstalledSavedGamePath /
# InstalledMarketplaceContentPath.
TITLE_ID = "454108E6"
SAVE_TYPE = "00000001"  # XContentType::kSavedGame
DLC_TYPE = "00000002"  # XContentType::kMarketplaceContent
DLC_XUID = "0000000000000000"

# Defaults matching config/skate3.example.toml's profile block.
DEFAULT_XUID = "B13E07DFF9AB6772"

# Save containers the game expects inside <user>/<xuid>/<title>/00000001/.
# Values are the UTF-16BE display names stored in the matching .header.
SAVE_CONTAINERS = {"ALIAS_SKATER": "skater", "CFOTO_SKATER": "CareerPhoto"}
HEADER_SIZE = 328


class Fail(Exception):
    """A user-facing error; printed without a traceback."""


def info(msg: str) -> None:
    print(f"\033[36m::\033[0m {msg}")


def warn(msg: str) -> None:
    print(f"\033[33m!!\033[0m {msg}", file=sys.stderr)


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------


def load_toml(path: Path) -> dict:
    if not path.is_file():
        return {}
    with path.open("rb") as handle:
        return tomllib.load(handle)


class Config:
    """Resolved host paths. Everything writable is under RUNTIME."""

    def __init__(self) -> None:
        raw = load_toml(CONFIG_FILE)
        home_share = Path.home() / ".local" / "share" / "skate3"

        self.install = Path(
            raw.get("install", os.environ.get("FREESKATE_INSTALL", HERE.parent / "Skate3Recomp-Linux"))
        ).expanduser()
        self.real_user_root = Path(raw.get("real_user_root", home_share)).expanduser()
        self.parkpacks = Path(raw.get("parkpacks", self.real_user_root / "parkpacks")).expanduser()
        self.xuid = raw.get("xuid", DEFAULT_XUID)

        self.binary = self.install / "skate3"
        self.src_game = self.install / "game"

        self.game_root = RUNTIME / "game"
        self.user_root = RUNTIME / "user"
        self.logs = RUNTIME / "logs"

    def check(self) -> None:
        if not self.binary.is_file():
            raise Fail(f"skate3 binary not found at {self.binary}")
        if not (self.src_game / "default.xex").is_file():
            raise Fail(f"no default.xex under {self.src_game} - is the game installed?")


# --------------------------------------------------------------------------
# Shadow game tree
#
# A deep mirror: real directories, symlinked files. The game tree is ~105
# files so rebuilding is instant, and every individual file stays overridable.
# The guest cannot write here (allow_game_relative_writes defaults false).
# --------------------------------------------------------------------------


IS_WINDOWS = os.name == "nt"


def _is_linked(path: Path) -> bool:
    """Whether this entry is one WE made, rather than a real file to protect.

    On Windows the farm is built from HARDLINKS (see link_file), and a hardlink
    is indistinguishable from a real file except by its link count - so the
    symlink test alone would refuse to rebuild a tree freeskate itself wrote.
    """
    if path.is_symlink():
        return True
    if not IS_WINDOWS:
        return False
    try:
        return path.stat().st_nlink > 1
    except OSError:
        return False


def assert_is_farm(root: Path) -> None:
    """Refuse to delete anything that is not a tree of dirs and links."""
    if not root.exists():
        return
    for path in root.rglob("*"):
        if not _is_linked(path) and not path.is_dir():
            raise Fail(
                f"refusing to rebuild {root}: {path} is a real file, not a link.\n"
                "Move it aside; freeskate only manages link farms here."
            )


def mirror_tree(src: Path, dst: Path, hard: bool = False) -> int:
    """Recreate src's directory structure under dst, linking every file.

    `hard` uses hardlinks, which are indistinguishable from real files to any
    reader. The guest's content VFS does not appear to resolve symlinks, so
    DLC content has to be mirrored this way; it costs nothing on the same
    filesystem and stays read-only in practice.
    """
    count = 0
    for entry in sorted(src.iterdir()):
        target = dst / entry.name
        if entry.is_dir() and not entry.is_symlink():
            target.mkdir(parents=True, exist_ok=True)
            count += mirror_tree(entry, target, hard)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_symlink() or target.exists():
                target.unlink()
            link_file(entry.resolve(), target, hard)
            count += 1
    return count


def link_file(source: Path, target: Path, hard: bool) -> None:
    """Link target at source, by whatever mechanism this platform allows.

    Windows only creates symlinks with Developer Mode or elevation, so there the
    order is reversed: hardlink first (free on NTFS, same volume), symlink next,
    and a copy as the last resort so staging never simply fails.
    """
    if hard or IS_WINDOWS:
        try:
            os.link(source, target)
            return
        except OSError:
            pass  # different filesystem; a symlink is the best we can do
    try:
        target.symlink_to(source)
    except OSError:
        if not IS_WINDOWS:
            raise
        shutil.copy2(source, target)


def link_override(source: Path, target: Path) -> int:
    """Point target at source, replacing whatever the mirror put there.

    A directory source is mirrored file-by-file so it merges with (rather than
    hides) the stock contents of the target directory - that is what makes
    loose-file overrides of a streamed world work.
    """
    if not source.exists():
        raise Fail(f"override source does not exist: {source}")
    if source.is_dir():
        target.mkdir(parents=True, exist_ok=True)
        return mirror_tree(source, target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink() or target.exists():
        target.unlink()
    target.symlink_to(source.resolve())
    return 1


def stage_game_tree(cfg: Config, overrides: list[tuple[Path, str]], removals: list[str]) -> None:
    assert_is_farm(cfg.game_root)
    if cfg.game_root.exists():
        shutil.rmtree(cfg.game_root)
    cfg.game_root.mkdir(parents=True)

    # Hardlinks on Windows: symlinks there need Developer Mode or elevation,
    # and the tree is ~105 files on one volume, so linking costs nothing.
    linked = mirror_tree(cfg.src_game, cfg.game_root, hard=IS_WINDOWS)
    info(f"shadow game tree: {linked} files linked from {cfg.src_game}")

    for rel in removals:
        target = cfg.game_root / rel
        if not (target.is_symlink() or target.exists()):
            raise Fail(f"cannot remove '{rel}': not present in the game tree")
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        else:
            target.unlink()
        info(f"  removed {rel}")

    for source, rel in overrides:
        target = cfg.game_root / rel
        n = link_override(source, target)
        info(f"  override {rel} <- {source.name} ({n} file{'s' if n != 1 else ''})")


# --------------------------------------------------------------------------
# Isolated user root
#
# Saves are real copies because the guest writes them. DLC content is
# symlinked because it is read-only in practice, which avoids re-installing
# ~190 MB of packages on every launch.
# --------------------------------------------------------------------------


def make_header(display_name: str, content_type: int = 1, size: int = HEADER_SIZE) -> bytes:
    """Build a content .header: BE u32 version, BE u32 type, UTF-16BE name.

    Type 1 is a saved game (328 bytes as the game writes them); type 2 is
    marketplace content (332). Community map packages are usually distributed
    as the .big alone, without the header the content manager needs, so it has
    to be synthesized.
    """
    blob = (1).to_bytes(4, "big") + content_type.to_bytes(4, "big") + display_name.encode("utf-16-be")
    if len(blob) > size:
        raise Fail(f"header name too long: {display_name}")
    return blob.ljust(size, b"\0")


DLC_HEADER_SIZE = 332
# Field layout recovered from the stock Danny Way / Maloof headers.
DLC_CONTENT_ID_OFFSET = 0x108
DLC_CONTENT_ID_LENGTH = 42
DLC_TITLE_ID_OFFSET = 0x140
DLC_TRAILER_OFFSET = 0x148


def make_dlc_header(display_name: str, content_id: str) -> bytes:
    """Build a marketplace content header.

    A name-only header is not enough: the stock ones also carry the content id
    - the same 42-character string as the package directory - and the title id.
    Without those the content manager never registers the package, which is
    why an earlier synthesized header left the map invisible in-game.
    """
    if not 0 < len(content_id) <= DLC_CONTENT_ID_LENGTH:
        raise Fail(f"content id must be 1..{DLC_CONTENT_ID_LENGTH} chars, got {len(content_id)}")

    blob = bytearray(DLC_HEADER_SIZE)
    blob[0:4] = (1).to_bytes(4, "big")
    blob[4:8] = (2).to_bytes(4, "big")

    name = display_name.encode("utf-16-be")
    if len(name) > DLC_CONTENT_ID_OFFSET - 8:
        raise Fail(f"display name too long: {display_name}")
    blob[8 : 8 + len(name)] = name

    blob[DLC_CONTENT_ID_OFFSET : DLC_CONTENT_ID_OFFSET + DLC_CONTENT_ID_LENGTH] = content_id.encode("ascii")
    blob[DLC_TITLE_ID_OFFSET : DLC_TITLE_ID_OFFSET + 4] = bytes.fromhex(TITLE_ID)
    blob[DLC_TRAILER_OFFSET : DLC_TRAILER_OFFSET + 4] = (1).to_bytes(4, "big")
    return bytes(blob)


def make_content_id(seed: str) -> str:
    """Mint a stock-shaped content id: 40 uppercase hex chars plus '45'."""
    import hashlib

    return hashlib.sha1(seed.encode()).hexdigest().upper() + "45"


def stage_dlc_packages(cfg: Config, packages: list[dict]) -> None:
    """Install a bare .big as marketplace DLC content.

    Mirrors what the game's own installer produces:
      <user>/0000000000000000/454108E6/00000002/<PKG>/<file>.big
      <user>/0000000000000000/454108E6/Headers/00000002/<PKG>.header
    """
    if not packages:
        return
    root = cfg.user_root / DLC_XUID / TITLE_ID
    content = root / DLC_TYPE
    headers = root / "Headers" / DLC_TYPE
    content.mkdir(parents=True, exist_ok=True)
    headers.mkdir(parents=True, exist_ok=True)

    for spec in packages:
        source = Path(spec["source"]).expanduser()
        if not source.is_file():
            raise Fail(f"dlc package not found: {source}")
        display = spec.get("name", source.stem)
        # The directory name and the id inside the header have to agree, so
        # derive both from one seed unless the map pins an explicit id.
        package = spec.get("package") or make_content_id(spec.get("seed", source.name))

        target_dir = content / package
        target_dir.mkdir(parents=True, exist_ok=True)
        link = target_dir / source.name
        if link.is_symlink() or link.exists():
            link.unlink()
        link_file(source.resolve(), link, hard=True)

        # Stock packages carry an XDBF spa.bin of title metadata beside the
        # content; borrow one so the package looks complete.
        spa = spec.get("spa")
        if spa:
            spa_path = Path(spa).expanduser()
            if spa_path.is_file():
                dest = target_dir / "spa.bin"
                if dest.is_symlink() or dest.exists():
                    dest.unlink()
                link_file(spa_path.resolve(), dest, hard=True)

        # Prefer a header shipped with the map. Generating one is a fallback:
        # the trailing fields are anchored to the end of the file (title id at
        # size-12), so a synthesized header of the wrong length puts them in
        # the wrong place and the content manager quietly rejects the package.
        header_path = spec.get("header")
        dest_header = headers / f"{package}.header"
        if header_path:
            supplied = Path(header_path).expanduser()
            if not supplied.is_file():
                raise Fail(f"dlc header not found: {supplied}")
            dest_header.write_bytes(supplied.read_bytes())
            info(f"  dlc header {dest_header.name} <- {supplied.name} ({supplied.stat().st_size} bytes)")
        else:
            dest_header.write_bytes(make_dlc_header(display, package))
        info(f"  dlc package {package[:12]}... <- {source.name} ({source.stat().st_size} bytes)")


def stage_save_container(cfg: Config, name: str, source: Path | None) -> None:
    """Copy a save container (and its header) into the isolated user root."""
    save_dir = cfg.user_root / cfg.xuid / TITLE_ID / SAVE_TYPE / name
    header_dir = cfg.user_root / cfg.xuid / TITLE_ID / "Headers" / SAVE_TYPE

    if save_dir.exists():
        shutil.rmtree(save_dir)
    save_dir.mkdir(parents=True)
    header_dir.mkdir(parents=True, exist_ok=True)

    if source is not None:
        for entry in sorted(source.iterdir()):
            if entry.is_file():
                shutil.copy2(entry, save_dir / entry.name)
        info(f"  save {name} <- {source} ({len(list(save_dir.iterdir()))} files)")

    # Prefer the real install's header so we stay byte-faithful; regenerate if
    # the user has never created that container.
    real_header = cfg.real_user_root / cfg.xuid / TITLE_ID / "Headers" / SAVE_TYPE / f"{name}.header"
    dest = header_dir / f"{name}.header"
    if real_header.is_file():
        shutil.copy2(real_header, dest)
    else:
        dest.write_bytes(make_header(SAVE_CONTAINERS.get(name, name)))


def stage_dlc(cfg: Config, enabled: bool, overrides: list[tuple[Path, str]]) -> None:
    """Symlink already-installed DLC content so it needs no re-install.

    The stock boot resumes into DLC_DW_MegaCompund, served out of the Danny Way
    package, so overriding an archive here is the zero-menu route to booting
    directly into custom world content.
    """
    # No farm assertion here: unlike the game tree, this subtree is entirely
    # generated by freeskate and mixes symlinked content with real generated
    # .header files, so "everything must be a symlink" does not hold.
    dst = cfg.user_root / DLC_XUID
    if dst.exists():
        shutil.rmtree(dst)

    if not enabled:
        info("  dlc: disabled for this map")
        return

    src = cfg.real_user_root / DLC_XUID
    if not src.is_dir():
        if overrides:
            raise Fail(f"map declares dlc_files but no installed DLC found at {src}")
        return

    dst.mkdir(parents=True)
    n = mirror_tree(src, dst, hard=True)
    info(f"  dlc: {n} installed content files linked")

    for source, target in overrides:
        dest = resolve_dlc_target(dst, target)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.is_symlink() or dest.exists():
            dest.unlink()
        link_file(source.expanduser().resolve(), dest, hard=True)
        info(f"  dlc override {dest.relative_to(dst)} <- {source.name}")


def stage_dlc_dropin(cfg: Config, sources: list[str]) -> None:
    """Put files in <user>/dlc/ and let the game's installer register them."""
    dropin = cfg.user_root / "dlc"
    if dropin.exists():
        shutil.rmtree(dropin)
    if not sources:
        return
    dropin.mkdir(parents=True)
    for spec in sources:
        source = Path(spec).expanduser()
        if not source.is_file():
            raise Fail(f"dlc dropin source not found: {source}")
        link_file(source.resolve(), dropin / source.name, hard=True)
        info(f"  dlc dropin {source.name} ({source.stat().st_size} bytes)")


def resolve_dlc_target(dlc_root: Path, target: str) -> Path:
    """Locate a DLC file to replace.

    A bare filename is matched by basename anywhere in the staged tree, so a
    map can say `dway_park_00000000.big` without naming the package hash.
    """
    if "/" in target:
        return dlc_root / target
    matches = [p for p in dlc_root.rglob(target)]
    if not matches:
        available = sorted({p.name for p in dlc_root.rglob("*.big")})
        raise Fail(f"no DLC file named '{target}'. Present: {', '.join(available) or '(none)'}")
    if len(matches) > 1:
        joined = ", ".join(str(m.relative_to(dlc_root)) for m in matches)
        raise Fail(f"'{target}' is ambiguous, use a path: {joined}")
    return matches[0]


def stage_overlay(cfg: Config, overlay_files: list[tuple[Path, str]]) -> None:
    """Seed the BIG-directory VFS overlay.

    InstallRecipeOverlay() re-points d:\\data\\scene, d:\\data\\content\\recipe
    and two livingworld subtrees at <user>/cache/vfs_big_directory_aliases, so
    anything dropped here is served to the guest in place of the disc copy.
    """
    root = cfg.user_root / "cache" / "vfs_big_directory_aliases"
    # Rebuilt every launch so files from a previously played map cannot leak
    # into this one. Only this subtree is cleared - the rest of cache/ holds
    # the shader cache, which is expensive to regenerate.
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    for source, rel in overlay_files:
        n = link_override(source, root / rel)
        info(f"  overlay {rel} <- {source.name} ({n} file{'s' if n != 1 else ''})")


SETTINGS_DROP = {
    # Written by the in-game overlay but meaningless to seed, and we set these
    # ourselves on the command line.
    "game_data_root",
    "user_data_root",
}


def stage_settings(cfg: Config, overrides: dict) -> None:
    """Seed settings.toml from the user's real one, plus per-map overrides.

    Copied rather than shared: the settings overlay rewrites this file on exit,
    and that must not touch the user's real configuration.
    """
    values: dict[str, object] = {}
    real = cfg.real_user_root / "settings.toml"
    if real.is_file():
        values.update({k: v for k, v in load_toml(real).items() if k not in SETTINGS_DROP})
    values.update(overrides)

    lines = ["# Generated by freeskate - safe to delete, regenerated each launch."]
    for key in sorted(values):
        lines.append(f"{key} = {toml_value(values[key])}")
    (cfg.user_root / "settings.toml").write_text("\n".join(lines) + "\n")


def toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    return "'" + str(value).replace("'", "") + "'"


# --------------------------------------------------------------------------
# EA BIG archives
#
# Layout (big-endian), recovered from the shipped world archives:
#   0x00  magic 'EB\0\3'
#   0x04  entry count
#   0x0c  offset of the name table
#   0x1c  total archive size
#   0x30  entry records, 16 bytes each, sorted by id:
#           u32 offset in 16-byte granules, u32 offset_hi, u32 size, u32 id
#   ...   name table: fixed-width NUL-padded slots, then the archive's root
#         path (the only string containing a separator)
#
# The id is djb2 (h*33 + c, seed 5381) over "<root>/<name>", which is what
# links a record to its filename - records carry no name of their own. This
# reproduces every id in every archive tested, and extraction refuses to run
# unless it still does.
# --------------------------------------------------------------------------

BIG_MAGIC = b"EB\x00\x03"
BIG_RECORDS_OFFSET = 0x30
BIG_RECORD_SIZE = 16


def djb2(text: str) -> int:
    h = 5381
    for byte in text.encode():
        h = ((h * 33) + byte) & 0xFFFFFFFF
    return h


class BigEntry:
    def __init__(self, name: str, offset: int, size: int, ident: int) -> None:
        self.name = name
        self.offset = offset
        self.size = size
        self.id = ident


class BigArchive:
    """Read-only view of an EA BIG archive."""

    def __init__(self, path: Path) -> None:
        self.path = path
        file_size = path.stat().st_size
        with path.open("rb") as handle:
            head = handle.read(4)
            if head != BIG_MAGIC:
                raise Fail(f"{path.name}: not an EA BIG archive (magic {head!r})")

            handle.seek(0)
            header = handle.read(0x10000)
            be = lambda off: int.from_bytes(header[off : off + 4], "big")
            count = be(0x04)
            self.total_size = be(0x1C)
            name_table = be(0x0C)
            if not 0 < count < 1_000_000:
                raise Fail(f"{path.name}: implausible entry count {count}")

            records = []
            record_end = BIG_RECORDS_OFFSET + count * BIG_RECORD_SIZE
            if record_end > len(header):
                handle.seek(0)
                header = handle.read(record_end + 0x1000)
                be = lambda off: int.from_bytes(header[off : off + 4], "big")
            for i in range(count):
                off = BIG_RECORDS_OFFSET + i * BIG_RECORD_SIZE
                records.append((be(off) * 16, be(off + 8), be(off + 12)))

            # The name table runs from its declared offset up to the first
            # byte of file data. Large packs push it well past any fixed-size
            # header read, so bound it by the earliest record offset instead
            # of guessing a buffer size.
            first_data = min((o for o, _s, _i in records if o > 0), default=file_size)
            names_end = max(first_data, name_table + 1)
            if not 0 < name_table < names_end <= file_size:
                raise Fail(f"{path.name}: implausible name table (0x{name_table:x}..0x{names_end:x})")
            handle.seek(name_table)
            name_blob = handle.read(names_end - name_table)

        strings = scan_strings(name_blob, 0)
        # Roots are directory paths ("data/content/world/stream/DIST_X"); a
        # bare "/" test also catches stray slashes inside scanned noise.
        roots = [s for s in strings if s.startswith("data/")]
        names = [s for s in strings if not s.startswith("data/")]
        if not roots:
            raise Fail(f"{path.name}: no root path found in the name table")
        self.root = roots[0]
        self.roots = roots

        # A package built with `bigfile.exe data/* -r` spans many roots, and
        # nothing says which name belongs to which. The id hash settles it:
        # try each root per name and keep the pairing that matches a record.
        by_id = {ident: (offset, size) for offset, size, ident in records}
        self.entries: list[BigEntry] = []
        for raw in names:
            # Some packs store names with a leading separator; joining blindly
            # would produce "root//name" and hash to nothing.
            name = raw.lstrip("/")
            if not name:
                continue
            for root in roots:
                ident = djb2(f"{root}/{name}")
                if ident in by_id:
                    offset, size = by_id.pop(ident)
                    self.entries.append(BigEntry(f"{root}/{name}", offset, size, ident))
                    break

        # Only unmatched *records* matter. The scan reaches past the name
        # table into file data, so it also returns ASCII noise that hashes to
        # nothing - harmless, and filtered out by the id lookup above.
        self.unmatched_records = len(by_id)
        self.record_count = count

    def read(self, entry: BigEntry) -> bytes:
        with self.path.open("rb") as handle:
            handle.seek(entry.offset)
            return handle.read(entry.size)


def scan_strings(blob: bytes, start: int, minimum: int = 4) -> list[str]:
    """Collect printable ASCII runs from the name table onward.

    Slot widths differ between archives, so the table is scanned rather than
    strided; NUL padding separates the entries either way.
    """
    out: list[str] = []
    current = bytearray()
    for byte in blob[start:]:
        if 0x20 <= byte < 0x7F:
            current.append(byte)
            continue
        if len(current) >= minimum:
            out.append(current.decode("ascii"))
        current.clear()
    if len(current) >= minimum:
        out.append(current.decode("ascii"))
    return out


def cmd_extract(cfg: Config, args) -> int:
    source = Path(args.archive).expanduser()
    if not source.is_file():
        candidate = cfg.src_game / "data" / "content" / source.name
        if not candidate.is_file():
            raise Fail(f"archive not found: {source}")
        source = candidate

    archive = BigArchive(source)
    info(
        f"{source.name}: {len(archive.entries)}/{archive.record_count} entries named, "
        f"root '{archive.root}'"
    )
    if archive.unmatched_records:
        raise Fail(
            f"{archive.unmatched_records} of {archive.record_count} records could not be "
            "matched to a filename. Extraction would produce mislabelled files, so it is "
            "refused - the id hash does not fully fit this archive."
        )

    if args.list:
        for entry in sorted(archive.entries, key=lambda e: e.name):
            print(f"  {entry.size:>10}  {entry.name}")
        return 0

    # Entry names are full guest paths, so the output tree mirrors the guest
    # layout and can be dropped straight in as loose-file overrides.
    out_root = Path(args.out).expanduser() if args.out else (HERE / "extracted" / source.stem)
    written = 0
    for entry in archive.entries:
        dest = out_root / entry.name.replace("/", os.sep)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(archive.read(entry))
        written += entry.size
    info(f"wrote {len(archive.entries)} files ({written} bytes) to {out_root}")

    if len(archive.roots) == 1:
        print(f"\n  use as a loose-file override:\n")
        print(f"    [[game_files]]")
        print(f'    source = "{out_root / archive.root.replace("/", os.sep)}"')
        print(f'    target = "{archive.root}"')
    else:
        print(f"\n  spans {len(archive.roots)} roots:")
        for root in sorted(archive.roots):
            print(f"    {root}")
    return 0


# --------------------------------------------------------------------------
# Maps
# --------------------------------------------------------------------------


class GameMap:
    """A staged content bundle plus the boot macro that reaches it."""

    def __init__(self, name: str, directory: Path, data: dict) -> None:
        self.name = name
        self.dir = directory
        self.data = data
        meta = data.get("map", {})
        self.title = meta.get("name", name)
        self.description = meta.get("description", "")
        self.macro = meta.get("macro", "none")

    def resolve(self, rel: str, cfg: Config) -> Path:
        """Resolve a source path: relative to the map dir unless absolute."""
        path = Path(rel).expanduser()
        return path if path.is_absolute() else (self.dir / path)

    def game_overrides(self, cfg: Config) -> list[tuple[Path, str]]:
        out = []
        for item in self.data.get("game_files", []):
            out.append((self.resolve(item["source"], cfg), item["target"]))
        return out

    def overlay_files(self, cfg: Config) -> list[tuple[Path, str]]:
        out = []
        for item in self.data.get("overlay_files", []):
            out.append((self.resolve(item["source"], cfg), item["target"]))
        return out

    def dlc_files(self, cfg: Config) -> list[tuple[Path, str]]:
        out = []
        for item in self.data.get("dlc_files", []):
            out.append((self.resolve(item["source"], cfg), item["target"]))
        return out

    def dlc_enabled(self) -> bool:
        return bool(self.data.get("dlc", {}).get("enabled", True))

    def dlc_packages(self) -> list[dict]:
        return list(self.data.get("dlc_packages", []))

    def dlc_dropin(self) -> list[str]:
        """Files to drop in <user>/dlc/ for the game's own installer to pick up.

        Several community maps are distributed with the instruction "place
        this into your portable\\dlc", i.e. they expect the installer to
        register them rather than being hand-placed into the content tree.
        """
        return list(self.data.get("dlc", {}).get("dropin", []))

    def dlc_auto_install(self) -> bool:
        return bool(self.data.get("dlc", {}).get("auto_install", False))

    def removals(self) -> list[str]:
        """Files to leave out of the shadow tree entirely.

        Starving the engine of an archive makes it probe every loose path it
        would otherwise satisfy internally, which is the only way to discover
        the filenames a world wants - the archives have no file table.

        Accepted at the top level or inside [map]: a bare `remove` written
        below any [section] header would otherwise silently become that
        section's key, which is easy to do by accident and hard to notice.
        """
        return list(self.data.get("remove", [])) + list(self.data.get("map", {}).get("remove", []))

    def park_pack(self, cfg: Config) -> Path | None:
        park = self.data.get("park", {})
        pack = park.get("pack")
        if not pack:
            return None
        candidate = Path(pack).expanduser()
        for path in (candidate if candidate.is_absolute() else None, self.dir / pack, cfg.parkpacks / pack):
            if path and path.is_dir():
                return path
        raise Fail(f"park pack '{pack}' not found (looked in {self.dir} and {cfg.parkpacks})")

    def skater_save(self, cfg: Config) -> Path | None:
        park = self.data.get("park", {})
        name = park.get("skater", "00_original_skater_save")
        candidate = cfg.parkpacks / name
        if candidate.is_dir():
            return candidate
        real = cfg.real_user_root / cfg.xuid / TITLE_ID / SAVE_TYPE / "ALIAS_SKATER"
        return real if real.is_dir() else None

    def settings(self) -> dict:
        return dict(self.data.get("settings", {}))

    def launch(self) -> dict:
        return dict(self.data.get("launch", {}))


def load_map(name: str) -> GameMap:
    directory = MAPS_DIR / name
    manifest = directory / "map.toml"
    if not manifest.is_file():
        raise Fail(f"no map '{name}' (expected {manifest})")
    return GameMap(name, directory, load_toml(manifest))


def empty_map() -> GameMap:
    return GameMap("(none)", MAPS_DIR, {})


def resolve_macro(value: str) -> str:
    """A macro is either a name in macros.toml or a literal token string."""
    macros = load_toml(MACROS_FILE).get("macros", {})
    seen = set()
    while value in macros and value not in seen:
        seen.add(value)
        value = macros[value]
    return "" if value == "none" else expand_macro(value)


def expand_macro(value: str) -> str:
    """Expand `token*N` repeats into N copies of token.

    Menu lists clamp at their ends rather than wrapping, so "press down forty
    times" is a reliable way to land on the last entry without knowing how
    long the list is - but only if it is writable without forty commas.
    """
    out: list[str] = []
    for token in value.split(","):
        token = token.strip()
        if not token:
            continue
        base, sep, count = token.partition("*")
        if sep and count.isdigit():
            out.extend([base.strip()] * int(count))
        else:
            out.append(token)
    return ",".join(out)


# --------------------------------------------------------------------------
# Launching
# --------------------------------------------------------------------------


def guard_single_instance() -> None:
    result = subprocess.run(["pgrep", "-x", "skate3"], capture_output=True, text=True)
    if result.returncode == 0:
        pids = " ".join(result.stdout.split())
        raise Fail(
            f"Skate 3 is already running (pid {pids}).\n"
            "Close it first - running two copies corrupts saves and causes crashes."
        )


def clear_stale_shm() -> None:
    """Each leaked segment is 4.5 GiB; a full /dev/shm hangs startup forever."""
    stale = sorted(Path("/dev/shm").glob("xenia_memory_*"))
    if not stale:
        return
    info(f"clearing {len(stale)} stale guest-memory segment(s) from earlier crashes")
    for path in stale:
        try:
            path.unlink()
        except OSError as exc:
            warn(f"could not remove {path}: {exc}")


def stage(cfg: Config, game_map: GameMap) -> None:
    cfg.logs.mkdir(parents=True, exist_ok=True)
    cfg.user_root.mkdir(parents=True, exist_ok=True)

    # The app appends to --log_file, so roll the previous session out of the
    # way; otherwise `freeskate logs` reports on a mix of runs.
    current = cfg.logs / "freeskate.log"
    if current.is_file():
        current.replace(cfg.logs / "freeskate.prev.log")

    stage_game_tree(cfg, game_map.game_overrides(cfg), game_map.removals())

    info("user root:")
    stage_save_container(cfg, "ALIAS_SKATER", game_map.skater_save(cfg))
    stage_save_container(cfg, "CFOTO_SKATER", game_map.park_pack(cfg))
    stage_dlc(cfg, game_map.dlc_enabled(), game_map.dlc_files(cfg))
    stage_dlc_packages(cfg, game_map.dlc_packages())
    stage_dlc_dropin(cfg, game_map.dlc_dropin())
    stage_overlay(cfg, game_map.overlay_files(cfg))
    stage_settings(cfg, game_map.settings())


def build_command(cfg: Config, game_map: GameMap, args) -> list[str]:
    launch = game_map.launch()
    macro = resolve_macro(args.macro if args.macro is not None else game_map.macro)
    settle = args.settle if args.settle is not None else launch.get("settle_ms", 2500)
    delay = args.delay if args.delay is not None else launch.get("delay_ms", 600)

    # Default signed out: with no profile there is no career save to resume, so
    # the game drops into free play on a default skater - the mode that makes
    # sense on a custom map, where career state may not apply at all. Maps that
    # need the profile (park saves) set signed_in = true.
    signed_in = launch.get("signed_in", False)
    if args.signed_out:
        signed_in = False
    if args.signed_in:
        signed_in = True

    cmd = [
        str(cfg.binary),
        f"--game_data_root={cfg.game_root}",
        f"--user_data_root={cfg.user_root}",
        # Logs default to a logs/ dir beside the executable, i.e. inside the
        # real install. Redirect so a freeskate session leaves nothing there.
        f"--log_file={cfg.logs / 'freeskate.log'}",
        "--skate3_demo_path=true",
        f"--skate3_demo_path_signed_in={'true' if signed_in else 'false'}",
        f"--skate3_demo_path_input_settle_ms={settle}",
        f"--skate3_demo_path_input_delay_ms={delay}",
        # Off by default because staged DLC is already installed and a rescan
        # would only re-copy it; maps using the dropin folder turn it back on
        # so the installer actually registers what was placed there.
        f"--skate3_auto_install_dlc={'true' if game_map.dlc_auto_install() else 'false'}",
    ]
    if macro:
        cmd.append(f"--skate3_demo_path_gameplay_inputs={macro}")
    if args.probe:
        cmd.append("--skate3_demo_path_probe=true")
    if args.windowed:
        cmd.append("--fullscreen=false")
    cmd.extend(args.extra)
    return cmd


def run(cfg: Config, cmd: list[str], dry_run: bool) -> int:
    print()
    info("launching:")
    for part in cmd:
        print(f"    {part}")
    print()
    if dry_run:
        info("dry run - not launching")
        return 0

    # cwd=RUNTIME keeps any app-relative scratch out of the real install.
    child = subprocess.Popen(cmd, cwd=RUNTIME)

    # Without this the game outlives a Ctrl+C or a `timeout` on freeskate and
    # keeps holding its 4.5 GiB /dev/shm segment.
    def forward(signum, _frame):
        try:
            child.terminate()
        except ProcessLookupError:
            pass

    previous = {sig: signal.signal(sig, forward) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        return child.wait()
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


# --------------------------------------------------------------------------
# Log inspection
# --------------------------------------------------------------------------

LOG_PATTERNS = [
    (re.compile(r"Skate 3 demo path:.*"), "demo"),
    (re.compile(r"Mounted .* at .*Partition1"), "mount"),
    (re.compile(r"Installed Skate 3 BIG-directory VFS overlay.*"), "overlay"),
    (re.compile(r"VFS: entry not found for '([^']*)'"), "miss"),
    # The filename-level signal: the VFS line often names only the directory,
    # while NtCreateFile names the exact file the engine wanted.
    (re.compile(r"\[NtCreateFile\] FAILED: path='([^']*)'"), "miss"),
    (re.compile(r"XEX patch applied successfully.*"), "patch"),
]


def newest_log(cfg: Config) -> Path | None:
    current = cfg.logs / "freeskate.log"
    if current.is_file():
        return current
    logs = [p for p in cfg.logs.glob("*.log") if p.is_file()]
    return max(logs, key=lambda p: p.stat().st_mtime) if logs else None


def cmd_logs(cfg: Config, args) -> int:
    log = Path(args.file) if args.file else newest_log(cfg)
    if log is None or not log.is_file():
        raise Fail(f"no log found under {cfg.logs}")
    info(f"reading {log}")
    misses: list[str] = []
    for line in log.read_text(errors="replace").splitlines():
        for pattern, kind in LOG_PATTERNS:
            match = pattern.search(line)
            if not match:
                continue
            if kind == "miss":
                misses.append(match.group(1))
            else:
                print(f"  {match.group(0).strip()}")
            break
    if not misses:
        return 0
    distinct = sorted(set(misses))
    summary = f"{len(distinct)} distinct loose paths probed ({len(misses)} probes total)"
    if args.misses:
        print(f"\n  {summary} - each is an override candidate:")
        for path in distinct:
            print(f"    {path}")
    else:
        print(f"\n  {summary}; pass --misses to list them")
    return 0


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


def cmd_play(cfg: Config, args) -> int:
    cfg.check()
    game_map = empty_map() if args.map is None else load_map(args.map)
    info(f"map: {game_map.title}")
    if game_map.description:
        print(f"    {game_map.description}")

    guard_single_instance()
    clear_stale_shm()
    stage(cfg, game_map)
    return run(cfg, build_command(cfg, game_map, args), args.dry_run)


def cmd_list(cfg: Config, args) -> int:
    maps = sorted(p.parent for p in MAPS_DIR.glob("*/map.toml"))
    if not maps:
        info(f"no maps yet - add one under {MAPS_DIR}/<name>/map.toml")
    for directory in maps:
        game_map = GameMap(directory.name, directory, load_toml(directory / "map.toml"))
        print(f"  {game_map.name:<24} {game_map.title}")
        if game_map.description:
            print(f"  {'':<24} {game_map.description}")

    packs = sorted(p for p in cfg.parkpacks.glob("*") if p.is_dir()) if cfg.parkpacks.is_dir() else []
    if packs:
        print(f"\n  park packs available in {cfg.parkpacks}:")
        for pack in packs:
            parks = len(list(pack.glob("*.B")))
            print(f"    {pack.name:<28} {parks} park{'s' if parks != 1 else ''}")

    macros = load_toml(MACROS_FILE).get("macros", {})
    if macros:
        print("\n  macros:")
        for name in sorted(macros):
            print(f"    {name:<28} {macros[name]}")
    return 0


def cmd_clean(cfg: Config, args) -> int:
    assert_is_farm(cfg.game_root)
    for target in (cfg.game_root, cfg.user_root, cfg.logs):
        if target.exists():
            shutil.rmtree(target)
            info(f"removed {target}")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="freeskate",
        description="Boot Skate 3 Recomp straight into gameplay with custom content.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_launch_flags(p):
        p.add_argument("--macro", help="macro name from macros.toml, or literal pad tokens")
        p.add_argument("--settle", type=int, help="ms to wait after gameplay before injecting inputs")
        p.add_argument("--delay", type=int, help="ms between injected inputs")
        p.add_argument("--windowed", action="store_true", help="run windowed instead of fullscreen")
        p.add_argument(
            "--signed-out",
            action="store_true",
            help="boot with no profile, so there is no career save to resume (free skate)",
        )
        p.add_argument("--signed-in", action="store_true", help="force the profile to be loaded")
        p.add_argument("--dry-run", action="store_true", help="stage everything but do not launch")
        p.add_argument("extra", nargs="*", help="extra args passed through to skate3")

    play = sub.add_parser("play", help="stage content and boot into it")
    play.add_argument("map", nargs="?", help="map name under maps/ (omit for stock game)")
    play.add_argument("--probe", action="store_true", help="also log frontend state transitions")
    add_launch_flags(play)
    play.set_defaults(func=cmd_play)

    probe = sub.add_parser("probe", help="boot with frontend state logging, for authoring macros")
    probe.add_argument("map", nargs="?", help="map name under maps/")
    add_launch_flags(probe)
    probe.set_defaults(func=cmd_play, probe=True)

    listing = sub.add_parser("list", help="list maps, park packs and macros")
    listing.set_defaults(func=cmd_list)

    logs = sub.add_parser("logs", help="summarise the last run's log")
    logs.add_argument("--file", help="a specific log file")
    logs.add_argument("--misses", action="store_true", help="list every loose path the guest probed")
    logs.set_defaults(func=cmd_logs)

    clean = sub.add_parser("clean", help="remove the staged runtime tree")
    clean.set_defaults(func=cmd_clean)

    extract = sub.add_parser("extract", help="unpack a .big archive to named loose files")
    extract.add_argument("archive", help="path to a .big, or just its filename in data/content")
    extract.add_argument("--out", help="output directory (default: extracted/<archive>)")
    extract.add_argument("--list", action="store_true", help="list contents instead of extracting")
    extract.set_defaults(func=cmd_extract)

    args = parser.parse_args(argv)
    if not hasattr(args, "probe"):
        args.probe = False

    try:
        return args.func(Config(), args)
    except Fail as exc:
        print(f"\033[31merror:\033[0m {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
