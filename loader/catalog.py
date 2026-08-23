"""The map catalog: what packs exist, what maps are in them, how to reach each one.

One JSON file per pack under `catalog/`. A pack is imported once (the .big scan
is the slow part, ~12 s for SkateIT's 292 MB) and everything after that reads
the cached JSON.

The catalog is also where calibration results live -- `macro_suffix` and
`log_seen` / `spot_verified` per map -- so a pack that has been calibrated
once stays calibrated.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import bigscan

# DIST_ entries that are not playable worlds.
NON_WORLD_IDS = {"skybox"}

TITLE_ID = bytes.fromhex("454108E6")
CONTENT_ID_OFFSET = 0x108
CONTENT_ID_LENGTH = 42


class ImportError_(Exception):
    """A pack could not be imported, with a reason worth showing the user."""


@dataclass
class MapEntry:
    world_id: str
    name: str
    # Position in the pack's in-game map list, which is ordered by DISPLAY NAME.
    # Measured, not guessed: index 0 loads Barcelona and index 13 loads Spillway,
    # which is the alphabetical-by-name order and not the order the strings appear
    # in the .big (that would have put SanFrancisco at 13).
    sub_index: int = 0
    # True once this map's world has been observed STREAMING in the log.
    #
    # This is a weak signal and must not be read as "this map works". The boot
    # warp substitutes the stream folder, so the requested world's data streams
    # whichever menu row was really confirmed - the log says the right thing
    # while the player stands somewhere else entirely. It was called `verified`
    # and shown in the UI as "CONFIRMED", which is what this rename is for.
    log_seen: bool = False
    # True once a SCREENSHOT of this map matched its confirmed reference.
    # The only signal that actually answers "did we land in the right map".
    spot_verified: bool = False
    # "boots": a run of this map reached gameplay and drew a world.
    # "stalls": it deterministically never reaches gameplay - several
    #           downloaded packs park the frontend at press-start forever, with
    #           the guest blocked (Main XThread idle while the host renderer
    #           free-spins). Not caused by any loader setting: it survives
    #           disabling the boot-hold skip and a 10-minute timeout, and
    #           several such packs are built for other platforms (BLUS is PS3).
    # "":       never tested.
    status: str = ""
    art: str | None = None

    @property
    def slug(self) -> str:
        return re.sub(r"[^a-z0-9]+", "-", self.world_id.lower()).strip("-")


def _migrate_map(raw: dict) -> dict:
    """Bring an on-disk map entry up to the current field names.

    `verified` became `log_seen`, because it only ever meant "the log showed
    this world streaming" - which the boot warp makes true regardless of where
    the player ended up. Old catalogs carry the old key, and `MapEntry(**raw)`
    would raise on it.
    """
    out = dict(raw)
    if "verified" in out:
        out["log_seen"] = bool(out.pop("verified"))
    return out


@dataclass
class Pack:
    id: str
    name: str
    # "big-dlc": a raw .big that must be hand-placed with its shipped .header.
    # "stfs":    a signed container (LIVE/CON/PIRS) the game installs itself.
    kind: str
    big: str
    header: str = ""
    package: str = ""
    maps: list[MapEntry] = field(default_factory=list)
    # Per-pack cvar overrides merged into the generated map.toml [settings].
    settings: dict = field(default_factory=dict)
    # Every `Z_<location>_Start` in the pack: the authoritative list of places
    # it can put you. Populated at import; empty means "not scanned yet".
    spawn_nodes: list[str] = field(default_factory=list)
    # This pack's world is what the game BOOTS INTO when it is the staged DLC,
    # so it needs no menu navigation at all - and driving the menu actively
    # breaks it, because the macro navigates away from the map and confirms
    # something else. Danny Way is the case: with a macro it never renders
    # (3/3, full 160 s timeout); with no macro it is on screen in 11 s.
    boots_at_start: bool = False

    @property
    def source_paths(self) -> list[str]:
        """The files this pack needs on disk to stage: the .big and its header."""
        return [path for path in (self.big, self.header) if path]

    @property
    def located(self) -> bool:
        """Whether this pack's files are where the record says they are.

        A catalog record is curation - map names, world ids, spawn nodes, and
        whether each map is known to load - plus a POINTER to a file on the
        machine that imported it. The shipped catalog carries the curation with
        the pointers stripped, so a fresh install knows what Skate It is without
        pretending to have it. `relocate()` fills the pointer back in.
        """
        if self.kind in ("stock-game", "stock"):
            # Nothing to locate: the base game and the official DLC are already
            # under the game data root the setup screen established.
            return True
        return bool(self.big) and all(Path(p).expanduser().is_file()
                                      for p in self.source_paths)

    def relocate(self, big: Path) -> None:
        """Point this record at a copy of the pack found on THIS machine.

        The header is taken from beside the .big when it is there, which is how
        every pack that ships one is laid out.
        """
        big = Path(big).expanduser()
        if not big.is_file():
            raise ImportError_(f"no such file: {big}")
        self.big = str(big)
        sibling = big.with_suffix(".header")
        self.header = str(sibling) if sibling.is_file() else ""

    def map_by_world(self, world_id: str) -> MapEntry | None:
        want = bigscan.normalize(world_id)
        for entry in self.maps:
            if bigscan.normalize(entry.world_id) == want:
                return entry
        return None

    @property
    def warp_safe(self) -> bool:
        """Whether `skate3_warp_world` can be trusted for this pack.

        The warp takes a world id and builds a spawn-node name from it
        (`Z_<world>_..._Start`). `scan()` derives world ids from `DIST_` folder
        names, which for several official DLC packs is the CONTENT package name
        and not the location:

            Maloof Money Cup   scans MaloofMoneyCupDLC  node Z_MaloofMCNYC_Start
            Danny Way          scans DW_MegaCompund     node Z_DWMegaCompound_Start
            After Dark         scans SanitariumDLC      node Z_Sanitarium_Start

        The warp then substitutes a node that does not exist. The log says
        `skate3 warp: no 'Z_<world>_..._Start'`, the world's geometry never
        arrives, and on screen you get the skybox and fall through it.

        Note this is NOT what the existing per-pack `settings` workaround fixed:
        Danny Way's settings turn off substitute_folder/slug/item, but
        `skate3_warp_substitute_node` and `_lookup` default to TRUE and are not
        in that list, so the bad node substitution happened anyway.

        Unknown (nothing scanned) counts as safe, so a pack that has not been
        re-imported behaves as it always did.
        """
        if not self.spawn_nodes:
            return True
        # Literal prefix, case-insensitive - NOT `bigscan.normalize`. The engine
        # builds the node name by concatenation and looks for it as written, so
        # a difference in separators is fatal even though it normalizes away:
        # San Vanelona's world id is "San Vanelona" while its nodes are
        # `Z_san_vanelona_artgallery_Start`, and the log duly says
        # `no 'Z_San Vanelona_..._Start'`. Normalizing made that pack look safe.
        lowered = [node.lower() for node in self.spawn_nodes]
        return all(
            any(node.startswith(f"z_{m.world_id.lower()}_") for node in lowered)
            for m in self.maps
        )

    @property
    def spots_share_world(self) -> bool:
        """Whether this pack's maps are SPOTS inside one world.

        The San Vanelona pack is 18 entries that all report
        `world_id: "San Vanelona"` and differ only by `sub_index`. That breaks
        the two mechanisms the loader normally selects a map with:

        - `skate3_warp_substitute_item` finds its template row by the wanted
          world's pack record, so with one world id shared 18 ways it repoints
          every row at whichever spot matched first, and all 18 entries load
          the same place.
        - `navigate.build_macro` drops the row walk when the item patch is on,
          so nothing else is left to distinguish them.

        For such a pack the row walk is the ONLY thing that can pick a spot,
        and the item patch has to stay off. Callers use this to decide both.
        """
        worlds = {bigscan.normalize(entry.world_id) for entry in self.maps}
        return len(worlds) < len(self.maps)

    def to_json(self) -> str:
        payload = asdict(self)
        payload["maps"] = [asdict(m) for m in self.maps]
        return json.dumps(payload, indent=2) + "\n"

    @classmethod
    def from_json(cls, text: str) -> "Pack":
        raw = json.loads(text)
        maps = [MapEntry(**_migrate_map(m)) for m in raw.pop("maps", [])]
        return cls(maps=maps, **raw)


# --------------------------------------------------------------------------
# Header discovery and validation
# --------------------------------------------------------------------------


def read_header_content_id(header: Path) -> str:
    """The 42-byte ASCII content id at 0x108, trailing padding stripped."""
    data = header.read_bytes()
    if len(data) < CONTENT_ID_OFFSET + CONTENT_ID_LENGTH:
        raise ImportError_(f"{header.name} is too small to be a content header ({len(data)} bytes)")
    raw = data[CONTENT_ID_OFFSET : CONTENT_ID_OFFSET + CONTENT_ID_LENGTH]
    return raw.decode("ascii", errors="replace").strip().strip("\x00")


def validate_header(header: Path, package: str) -> None:
    """Fail loudly on the header mistakes that otherwise fail *silently*.

    A rejected package does not produce an error at boot -- the content scan just
    drops it and the game quits with "Execution complete", which looks like an
    unrelated crash. Checking here is what makes that debuggable.
    """
    data = header.read_bytes()
    size = len(data)
    # Real headers vary in length -- SkateIT's is 328 bytes, stock DLC is 332 --
    # so the floor is the end of the content-id field, not a fixed struct size.
    minimum = CONTENT_ID_OFFSET + CONTENT_ID_LENGTH
    if size < minimum:
        raise ImportError_(
            f"{header.name} is only {size} bytes; a content header needs at least {minimum}"
        )

    version = int.from_bytes(data[0:4], "big")
    ctype = int.from_bytes(data[4:8], "big")
    if version != 1:
        raise ImportError_(f"{header.name}: version is {version}, expected 1")
    if ctype != 2:
        raise ImportError_(
            f"{header.name}: content type is {ctype}, expected 2 (marketplace content)"
        )

    # The trailing fields are anchored to the END of the file, which is why a
    # 332-byte stand-in cannot substitute for a 328-byte original.
    if data[size - 12 : size - 8] != TITLE_ID:
        found = data[size - 12 : size - 8].hex().upper()
        raise ImportError_(
            f"{header.name}: title id at size-12 is {found}, expected 454108E6. "
            "Use the header the pack ships with; do not synthesize one."
        )

    content_id = read_header_content_id(header)
    if content_id != package:
        raise ImportError_(
            f"{header.name}: content id is {content_id!r} but the package directory "
            f"would be {package!r}. They must match exactly or the content manager "
            "silently rejects the package."
        )


def header_display_name(header: Path) -> str | None:
    data = header.read_bytes()
    raw = data[8:0x108]
    text = raw.decode("utf-16-be", errors="replace").split("\x00")[0].strip()
    return text or None


def find_header(big: Path, package: str, extra_dirs: list[Path] | None = None) -> Path | None:
    """Locate the pack's shipped .header.

    Packs are inconsistent about this: DHS ships none at all, and SkateIT's sits
    beside the download rather than beside the .big, so search outward.
    """
    candidates: list[Path] = []
    search_dirs = [big.parent, big.parent.parent, big.parent.parent.parent]
    search_dirs += extra_dirs or [Path.home() / "Downloads"]
    for directory in search_dirs:
        if not directory or not directory.is_dir():
            continue
        candidates.append(directory / f"{package}.header")
        try:
            candidates.extend(sorted(directory.glob("*.header")))
        except OSError:
            continue

    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            if read_header_content_id(candidate) == package:
                return candidate
        except (ImportError_, OSError):
            continue
    return None


# --------------------------------------------------------------------------
# Source classification
# --------------------------------------------------------------------------

# Magic bytes that identify a file as something other than Skate 3 content.
FOREIGN_MAGIC = {
    b"Unit": "a Unity asset bundle, not Skate 3 content",
    b"\x7fELF": "an executable, not a map pack",
    b"PK\x03\x04": "a zip; extract it first",
}

UNSUPPORTED = {
    ".edat": (
        "PS3 content (.edat). This is the RPCS3 build of the map; the Xbox "
        "recompilation cannot load it. Look for an XBOX/RECOMP release instead."
    ),
    ".rar": (
        "RAR archive. There is no unrar/7z on this machine (only unzip), so it "
        "has to be extracted by hand first, then import the .big inside it."
    ),
    ".7z": (
        "7z archive. There is no 7z on this machine, so it has to be extracted "
        "by hand first, then import the .big inside it."
    ),
}


def is_stfs(path: Path) -> bool:
    """A signed Xbox content container: LIVE (marketplace), CON, or PIRS."""
    try:
        return path.is_file() and path.open("rb").read(4) in (b"LIVE", b"CON ", b"PIRS")
    except OSError:
        return False


def classify(path: Path) -> str:
    """Return the pack kind, or raise ImportError_ explaining why it cannot be used."""
    suffix = path.suffix.lower()
    if suffix in UNSUPPORTED:
        raise ImportError_(f"{path.name}: {UNSUPPORTED[suffix]}")
    # Checked before the .big test: STFS is the easier path, because the game's
    # own DLC installer writes a correct header for it and we never have to find
    # or validate one ourselves.
    if is_stfs(path):
        return "stfs"
    if suffix == ".big":
        return "big-dlc"
    if path.is_dir():
        bigs = sorted(path.rglob("*.big"))
        if bigs:
            return "big-dlc"
        raise ImportError_(f"{path}: no .big archive found inside")
    try:
        magic = path.open("rb").read(4) if path.is_file() else b""
    except OSError:
        magic = b""
    for prefix, why in FOREIGN_MAGIC.items():
        if magic.startswith(prefix):
            raise ImportError_(f"{path.name}: {why}")
    if path.name.endswith(".download"):
        raise ImportError_(
            f"{path.name}: this looks like an unfinished browser download "
            "(.download). Finish or re-download it first."
        )
    raise ImportError_(f"{path.name}: unrecognised map pack format")


def find_big(path: Path) -> Path:
    if path.is_file() and (path.suffix.lower() == ".big" or is_stfs(path)):
        return path
    bigs = sorted(path.rglob("*.big"), key=lambda p: p.stat().st_size, reverse=True)
    if not bigs:
        raise ImportError_(f"{path}: no .big archive found")
    return bigs[0]


# --------------------------------------------------------------------------
# Import
# --------------------------------------------------------------------------


def default_package_name(big: Path, scan: bigscan.ScanResult) -> str:
    """A package directory name, used only when no shipped header pins one."""
    base = scan.pack_name or big.stem
    name = re.sub(r"[^A-Za-z0-9]", "", base).upper()
    return name[:42] or "CUSTOMMAP"


def _guess_header(big: Path, scan: bigscan.ScanResult) -> Path | None:
    """Find the pack's shipped header when the caller named neither it nor the package.

    The rule that works in practice: a header belongs to this pack when its
    content id normalizes to the same string as the pack's own name. SkateIT's
    header says "SKATEIT" and its pack name is "Skate IT" -- both normalize to
    "skateit". Packs that ship no header (DHS) correctly find nothing here.
    """
    wanted = {bigscan.normalize(default_package_name(big, scan))}
    if scan.pack_name:
        wanted.add(bigscan.normalize(scan.pack_name))
    wanted.add(bigscan.normalize(big.stem))
    wanted.discard("")

    # Packs ship the header in one of two shapes: loose beside the .big (or beside
    # the download), or in the canonical DLC tree the game itself uses:
    #   <root>/0000000000000000/454108E6/00000002/<PKG>/<pack>.big
    #   <root>/0000000000000000/454108E6/Headers/00000002/<PKG>.header
    # So walk up a few levels and look for Headers/ as well as loose files.
    candidates: list[Path] = []
    directory = big.parent
    for _ in range(5):
        if not directory or directory == directory.parent:
            break
        candidates.extend(sorted(directory.glob("*.header")))
        candidates.extend(sorted(directory.glob("Headers/*/*.header")))
        directory = directory.parent
    downloads = Path.home() / "Downloads"
    if downloads.is_dir():
        candidates.extend(sorted(downloads.glob("*.header")))

    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            content_id = read_header_content_id(candidate)
        except (ImportError_, OSError):
            continue
        # Either the header names this pack, or it is the only one in the pack's
        # own tree -- both are unambiguous enough to use.
        if bigscan.normalize(content_id) in wanted or bigscan.normalize(
            content_id
        ) == bigscan.normalize(big.parent.name):
            return candidate
    return None


def import_pack(
    source: Path,
    catalog_dir: Path,
    package: str | None = None,
    header: Path | None = None,
    pack_id: str | None = None,
) -> Pack:
    """Scan a map pack and write its catalog entry. Read-only except that write."""
    source = source.expanduser()
    if not source.exists():
        raise ImportError_(f"{source}: no such file or directory")

    kind = classify(source)
    big = find_big(source)

    scan = bigscan.scan(big)
    if not scan.ok:
        raise ImportError_(
            f"{big.name}: found no freeskate locations in this archive, so it does "
            "not look like a map pack."
        )

    # scan.maps is already sorted by display name, which IS the in-game order.
    maps = [
        MapEntry(world_id=m.world_id, name=m.name, sub_index=index)
        for index, m in enumerate(
            m for m in scan.maps if bigscan.normalize(m.world_id) not in NON_WORLD_IDS
        )
    ]

    if kind == "stfs":
        # No header hunting: the engine installs the container itself, deriving
        # the content id and header from the container's own metadata. This is
        # preferred over the same pack's raw .big for exactly that reason.
        name = scan.pack_name or big.stem
        pack = Pack(
            id=pack_id or re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-"),
            name=name,
            kind=kind,
            big=str(big),
            maps=maps,
        )
        catalog_dir.mkdir(parents=True, exist_ok=True)
        (catalog_dir / f"{pack.id}.json").write_text(pack.to_json())
        return pack

    # The header is authoritative about the package name, because the content id
    # inside it must equal the package directory name on disk.
    if header is None and package is not None:
        header = find_header(big, package)
    elif header is None:
        header = _guess_header(big, scan)

    if header is not None:
        package = read_header_content_id(header)

    if header is None:
        wanted = f" for package {package!r}" if package else ""
        raise ImportError_(
            f"{big.name}: no .header found{wanted}. Generate one with "
            "`python3 scripts/makeheader.py <pack.big>` and re-scan, or pass an "
            "existing one with --header.\n"
            "            A SYNTHESIZED HEADER IS FINE - measured 2026-08-16: the game "
            "mounted a pack carrying one and opened its world files, with no content-scan "
            "rejection. Nothing in the 328-byte format is signed; the only rule that "
            "matters is that the content id equals the package directory name, which the "
            "generator guarantees. The older claim that the content manager silently "
            "rejects a synthesized header came from a case where those two did not match."
        )

    validate_header(header, package)

    name = header_display_name(header) or scan.pack_name or big.stem
    pack = Pack(
        id=pack_id or re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-"),
        name=name,
        kind=kind,
        big=str(big),
        header=str(header),
        package=package,
        maps=maps,
    )

    # The authoritative location list, read once here because it needs a full
    # scan of the container (~12 s for a 292 MB .big). `Pack.warp_safe` depends
    # on it, and without it a re-import silently re-breaks every pack whose
    # world ids do not match their spawn nodes.
    try:
        source = Path(pack.big)
        if source.is_file():
            pack.spawn_nodes = bigscan.spawn_nodes(source)
    except OSError:
        pass

    _carry_over(pack, catalog_dir)
    catalog_dir.mkdir(parents=True, exist_ok=True)
    (catalog_dir / f"{pack.id}.json").write_text(pack.to_json())
    return pack


def reindex_from_spawn_nodes(pack: "Pack") -> bool:
    """Fix `sub_index` using the spawn nodes, which are the real location list.

    `sub_index` is a position in the in-game map list, and the loader walks that
    many rows to reach a map - so if it is off by one, you land on somebody
    else's map. It is derived from the display names `scan()` pairs up, and that
    pairing can MISS a location: Meebs and Brassy Ports has seven
    `Z_<world>_<name>_Start` nodes but only six paired maps, the unpaired one
    being "Bralington Beach by Bralunit WIP". Everything sorting after it was
    then indexed one row short.

    A node names both halves - `Z_the_alley4_thealleybyovannywip_Start` is world
    `the_alley4`, display name `thealleybyovannywip` - so the full list and its
    display-name order are recoverable even for locations `scan()` did not pair.

    NOT called automatically, and that is deliberate. A pack can carry spawn
    nodes that are NOT in its Locations list: skate-it has 15 nodes for 14 maps,
    the extra one being `Z_h4_harrisburghskatepark_Start` - a map that belongs
    to the Sleepen pack. Structurally that is indistinguishable from Meebs'
    genuinely-missing Bralington Beach, so running this on node count alone
    rewrote skate-it's indices, which three 111-run sweeps had already proven
    correct. Use it as a hypothesis to TEST against a boot, never as an
    automatic import step.

    Returns whether anything moved.
    """
    if not pack.spawn_nodes or not pack.maps:
        return False

    # (world, normalised display name) per node, ordered as the game orders the
    # list: by display name.
    parsed = []
    for node in pack.spawn_nodes:
        body = node[2:-6] if node.lower().startswith("z_") else node
        world, _, shown = body.rpartition("_")
        if world and shown:
            parsed.append((bigscan.normalize(shown), bigscan.normalize(world)))
    if len(parsed) < len(pack.maps):
        return False
    order = [name for name, _ in sorted(parsed)]

    moved = False
    for entry in pack.maps:
        target = bigscan.normalize(entry.name)
        if target in order:
            index = order.index(target)
            if entry.sub_index != index:
                entry.sub_index = index
                moved = True
    return moved


def _carry_over(pack: "Pack", catalog_dir: Path) -> None:
    """Keep per-map state that re-importing should not discard.

    `log_seen` and `spot_verified` are evidence gathered by actually loading a
    map; the same pack arriving again in a different container (a .big and its
    STFS both being imported, say) must not silently erase it.
    """
    existing_path = catalog_dir / f"{pack.id}.json"
    if not existing_path.is_file():
        return
    # Pack-level facts a fresh scan cannot know are carried over below too:
    # `boots_at_start` was established by running the game, and `spawn_nodes`
    # is kept if this scan somehow produced none.
    try:
        existing = Pack.from_json(existing_path.read_text())
    except (json.JSONDecodeError, TypeError, ValueError):
        return
    for entry in pack.maps:
        previous = existing.map_by_world(entry.world_id)
        if previous is not None:
            entry.log_seen = entry.log_seen or previous.log_seen
            entry.spot_verified = entry.spot_verified or previous.spot_verified
            entry.art = entry.art or previous.art
            # `status` is the verdict of a sweep - boots, or tested and hangs.
            # A scan cannot re-derive it, and dropping a pack the launcher
            # already knows about must not throw it away.
            entry.status = entry.status or previous.status
    if not pack.settings:
        pack.settings = existing.settings
    # Established by RUNNING the game, so a rescan can never re-derive it:
    # Danny Way boots into its own world and must get no macro at all.
    pack.boots_at_start = pack.boots_at_start or existing.boots_at_start
    if not pack.spawn_nodes:
        pack.spawn_nodes = existing.spawn_nodes

    # The same pack often exists on disk twice -- a raw .big and the signed
    # container it came from. Keep the container: it installs itself, so it
    # cannot fail on a missing or mismatched .header.
    if existing.kind == "stfs" and pack.kind != "stfs":
        pack.kind = existing.kind
        pack.big = existing.big
        pack.header = existing.header
        pack.package = existing.package


#: The id reserved for the game as it ships, with no custom content staged.
STOCK_ID = "skate3"


def stock_pack() -> Pack:
    """Skate 3 itself: no DLC staged, no menu automation, no warp.

    The launcher exists to load custom maps, but the first thing anyone with a
    fresh install can do - and the only thing they can do before they have a
    single pack - is play the game. It is a Pack so that every code path that
    already handles packs (the profile writer, the session, the picker) handles
    it without a special case at every level.
    """
    return Pack(
        id=STOCK_ID,
        name="Skate 3",
        kind="stock-game",
        big="",
        maps=[MapEntry(world_id=STOCK_ID, name="Port Carverton", sub_index=0,
                       status="boots")],
        # It boots into its own world; driving the menu would navigate away.
        boots_at_start=True,
    )


def load_all(catalog_dir: Path) -> list[Pack]:
    if not catalog_dir.is_dir():
        return []
    packs = []
    for path in sorted(catalog_dir.glob("*.json")):
        try:
            packs.append(Pack.from_json(path.read_text()))
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
    return packs


def save(pack: Pack, catalog_dir: Path) -> None:
    catalog_dir.mkdir(parents=True, exist_ok=True)
    (catalog_dir / f"{pack.id}.json").write_text(pack.to_json())
