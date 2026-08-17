"""Read-only inspection of a Skate 3 BIG archive.

We only need three things out of a map pack, and all three are recoverable from
a plain string scan without unpacking a single entry:

  * the pack's display name        -- "<Name> freeskate maps."
  * each map's display name        -- "<Name> freeskate location."
  * each map's internal world id   -- DIST_<world>, which is what the log names
                                      when the world actually streams

Deliberately NOT using `freeskate extract --list` for this: it resolves entry
names by djb2 hash and refuses SkateIT outright (only 1188 of 1954 records
match), because the hash does not fully fit that archive. The strings we want
sit in the payload regardless of whether the directory can be named.

Nothing here writes, extracts, or mutates anything.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# 8 MiB at a time, with an overlap so a string straddling a boundary is still
# seen whole by the next window.
CHUNK = 8 << 20
OVERLAP = 512

# "Barcelona freeskate location." / "Skate IT freeskate maps."
RE_LOCATION = re.compile(rb"([\x20-\x7e]{1,48}?) freeskate location\.")
RE_PACK = re.compile(rb"([\x20-\x7e]{1,48}?) freeskate maps\.")

# World ids may contain spaces: SkateIT has DIST_sk8itrio, DHS has "DIST_DHS 32221".
RE_WORLD = re.compile(rb"DIST_([A-Za-z0-9][A-Za-z0-9 _]{1,40}?)(?=[_.\\/\x00]|$)")

# Slugs that pair a world id with a display name, e.g.
# "dist_sk8itxgames_sanfranciscodlc" or "freeskate_dlc_dhs_32221_dhsbydh13".
RE_SLUG = re.compile(rb"[A-Za-z0-9][A-Za-z0-9_ ]{6,80}")

# Per-world suffixes that are asset streams, not map-name variants.
ASSET_VARIANTS = {"pres", "sim", "tex", "global", "aud", "audio"}

# The spawn node for a location: "Z_sk8itspillway_spillway_Start". This is the
# authoritative list of places a pack can actually put you - the confirm handler
# looks a location up by this name, so a world id that appears in no spawn node
# cannot be reached, whatever the DIST_ folders suggest.
RE_SPAWN_NODE = re.compile(rb"Z_([A-Za-z0-9][A-Za-z0-9_ ]{2,60}?)_Start")


def normalize(text: str) -> str:
    """Lowercase, alphanumerics only.

    'Community Center' -> 'communitycenter', 'DHS 32221' -> 'dhs32221'. This is
    what makes the pairing work: the slug 'dist_sk8itcommcenter_communitycenterdlc'
    normalizes to a string containing both the world id and the display name.
    """
    return re.sub(r"[^a-z0-9]", "", text.lower())


@dataclass
class ScannedMap:
    world_id: str  # e.g. "sk8itbareclona" - what the log prints when it streams
    name: str  # e.g. "Barcelona" - what the Locations menu shows


@dataclass
class ScanResult:
    pack_name: str | None = None
    maps: list[ScannedMap] = field(default_factory=list)
    unpaired_names: list[str] = field(default_factory=list)
    unpaired_worlds: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.maps)


def _iter_chunks(path: Path):
    with path.open("rb") as handle:
        tail = b""
        while True:
            block = handle.read(CHUNK)
            if not block:
                break
            yield tail + block
            tail = block[-OVERLAP:]


def _decode(raw: bytes) -> str:
    return raw.decode("ascii", errors="replace").strip()


def scan(path: Path) -> ScanResult:
    """Scan a .big for pack/map names and world ids."""
    names: list[str] = []
    pack_names: list[str] = []
    worlds: list[str] = []
    slugs: set[str] = set()

    seen_names: set[str] = set()
    seen_worlds: set[str] = set()

    for chunk in _iter_chunks(path):
        for match in RE_LOCATION.finditer(chunk):
            name = _decode(match.group(1))
            if name and name not in seen_names:
                seen_names.add(name)
                names.append(name)
        for match in RE_PACK.finditer(chunk):
            pack_names.append(_decode(match.group(1)))
        for match in RE_WORLD.finditer(chunk):
            world = _decode(match.group(1)).strip()
            if world and world not in seen_worlds:
                seen_worlds.add(world)
                worlds.append(world)
        for match in RE_SLUG.finditer(chunk):
            slugs.add(normalize(_decode(match.group(0))))

    return _pair(names, pack_names, worlds, slugs)


def _pair(
    names: list[str],
    pack_names: list[str],
    worlds: list[str],
    slugs: set[str],
) -> ScanResult:
    """Match each display name to the world id that shares a slug with it.

    Positional pairing (Nth name -> Nth world) happens to be correct for SkateIT,
    but only by luck of alphabetical ordering, and it is silently wrong the moment
    a pack orders things differently. Slug evidence is checked first; ordering is
    only the last resort.
    """
    result = ScanResult(pack_name=pack_names[0] if pack_names else None)

    norm_worlds = {world: normalize(world) for world in worlds}
    taken: set[str] = set()

    for name in names:
        norm_name = normalize(name)
        if not norm_name:
            continue
        match = _find_world_for(norm_name, norm_worlds, slugs, taken)
        if match:
            taken.add(match)
            result.maps.append(ScannedMap(world_id=match, name=name))
        else:
            result.unpaired_names.append(name)

    # Some packs are ONE world holding many named locations (San Vanelona is a
    # single DIST_ world with 18 freeskate spots) rather than one world per map.
    # When exactly one real world exists, every name belongs to it.
    real_worlds = [
        w for w in worlds
        if normalize(w) not in {"skybox"} and not normalize(w).startswith("skybox")
    ]
    longest = max(real_worlds, key=len) if real_worlds else None
    if longest and len({normalize(w) for w in real_worlds if normalize(longest).startswith(normalize(w))}) == len(real_worlds):
        result.maps = [ScannedMap(world_id=longest, name=name) for name in names]
        result.unpaired_names.clear()
        taken = {longest}

    # Last resort: equal counts and nothing paired -> fall back to file order.
    elif result.unpaired_names and len(result.unpaired_names) == len(
        [w for w in worlds if w not in taken]
    ):
        leftovers = [w for w in worlds if w not in taken]
        for name, world in zip(result.unpaired_names, leftovers):
            taken.add(world)
            result.maps.append(ScannedMap(world_id=world, name=name))
        result.unpaired_names.clear()

    # Scanning raw payload turns up truncated fragments of real ids ("sk8itba"
    # for "sk8itbareclona"). Anything that is a strict prefix of a world we did
    # pair is noise, not a missed map.
    paired = {normalize(m.world_id) for m in result.maps}
    result.unpaired_worlds = [
        w
        for w in worlds
        if w not in taken
        and not any(p != normalize(w) and p.startswith(normalize(w)) for p in paired)
    ]
    result.maps.sort(key=lambda m: m.name.lower())
    return result


def _find_world_for(
    norm_name: str,
    norm_worlds: dict[str, str],
    slugs: set[str],
    taken: set[str],
) -> str | None:
    """A world id pairs with a name when some slug contains both."""
    best: tuple[int, str] | None = None
    for world, norm_world in norm_worlds.items():
        if world in taken or not norm_world:
            continue
        for slug in slugs:
            if norm_world in slug and norm_name in slug and slug != norm_world:
                # Prefer the most specific world id, so a pack containing both
                # "DIST_Foo" and "DIST_FooBar" does not mis-bind the shorter one.
                if best is None or len(norm_world) > best[0]:
                    best = (len(norm_world), world)
                break
    return best[1] if best else None


def variant_slugs(world_id: str, slugs: set[str]) -> list[str]:
    """The non-asset variant tokens for a world (diagnostic helper)."""
    norm = normalize(world_id)
    out = []
    for slug in slugs:
        if slug.startswith("dist" + norm + "_"):
            tail = slug[len("dist" + norm) + 1 :]
            if tail not in ASSET_VARIANTS:
                out.append(tail)
    return sorted(out)


def spawn_nodes(path: Path) -> list[str]:
    """Every `Z_<location>_Start` node name in a pack, sorted.

    This is what makes a world id checkable. `scan()` derives world ids from
    `DIST_` folder names, and for several official DLC packs that picks up the
    CONTENT package name rather than the location: Maloof scans as
    `MaloofMoneyCupDLC` while its only spawn node is `Z_MaloofMCNYC_Start`, and
    Danny Way scans as `DW_MegaCompund` against `Z_DWMegaCompound_Start`. The
    warp then substitutes a spawn node that does not exist, the world's geometry
    never arrives, and you spawn in the skybox and fall - which is exactly what
    that looks like on screen.
    """
    found: set[str] = set()
    for block in _iter_chunks(path):
        for match in RE_SPAWN_NODE.finditer(block):
            found.add(_decode(match.group()))
    return sorted(found)
