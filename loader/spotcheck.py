"""Deciding, from a screenshot, whether we landed in the right map.

No log line answers this. `RESULT: LANDED` is inferred from stream lines and the
boot warp substitutes the stream folder, so the requested map's data streams
whatever the menu really did; `taking over natively` only proves *a* world
rendered. Both were true for weeks while every map was actually loading
skate.School.

What does work is the picture. Spawn points are deterministic, so two runs of
one map frame the same scene while two different maps do not, and the gap
between those is wide enough to decide automatically. `loader/fingerprint.py`
owns the hash and `scripts/hashcheck.py` measures that gap; the thresholds below
are derived from its output rather than chosen.

    references/<key>.png      confirmed "this IS that map"
    references/_wrong/*.png   confirmed "this is NOT where we asked for"

The _wrong set is what makes this useful before every map has a reference: a run
that matches a known-bad landing is a failure no matter which map was requested.

Two independent judgements live here, and the sweep needs both:

  `check`         against a confirmed reference - "is this the right map"
  `compare_shots` between two runs of one map  - "is this reproducible"

The second needs no reference at all, which matters because references have to
be confirmed by a human once and there are 37 maps.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import fingerprint
from .fingerprint import Signature, distance, signature

# Derived from scripts/hashcheck.py on the corpus quoted in fingerprint.py.
# For `hist1x12` the observed extremes were:
#
#     widest same-map pair       42   (with a full camera pan between runs)
#     closest different-map pair 145
#
# 75 is roughly the geometric mean, so it sits ~1.8x clear of both sides rather
# than hugging either. These are for `fingerprint.ACTIVE` and only for it -
# changing the fingerprint means re-running hashcheck and re-deriving them.
#
# Re-derive from the sweep too: 37 groups of 3 is far better evidence than the
# two repeated maps these came from.
SAME_WORLD_MAX = 75
# Between SAME_WORLD_MAX and this, the shot is close enough to be suspicious -
# most likely a different spawn point in the SAME world, which is exactly what a
# half-working macro produces. Worth a human look rather than a silent pass.
AMBIGUOUS_MAX = 130

# Kept for callers that predate fingerprint.py.
HASH_SIDE = 16


def reference_key(pack_id: str, sub_index: int) -> str:
    """The filename a map's reference is stored under.

    NOT the world id. The San Vanelona pack is 18 distinct spots that all report
    `world_id: "San Vanelona"`, so a world-keyed reference collapses 18 maps
    onto one file and makes half the catalog unjudgeable.
    """
    return f"{pack_id}.{sub_index}"


@dataclass
class Verdict:
    ok: bool | None  # True / False / None = no reference to judge against
    detail: str

    def __str__(self) -> str:
        mark = {True: "MATCH", False: "WRONG", None: "UNKNOWN"}[self.ok]
        return f"{mark}: {self.detail}"


def _known_bad(shot_sig: Signature, references: Path) -> Verdict | None:
    wrong_dir = references / "_wrong"
    if not wrong_dir.is_dir():
        return None
    for bad in sorted(wrong_dir.glob("*.png")):
        d = distance(shot_sig, signature(bad))
        if d <= SAME_WORLD_MAX:
            return Verdict(False, f"landed in {bad.stem} (distance {d})")
    return None


def check(shot: Path, key: str, references: Path,
          fallback_key: str | None = None) -> Verdict:
    """Judge one screenshot against the confirmed references.

    `key` is a `reference_key`; `fallback_key` is usually the bare world id, so
    references confirmed before the keying changed still resolve.
    """
    shot_sig = signature(shot)

    # A known-bad landing is decisive regardless of what was requested.
    bad = _known_bad(shot_sig, references)
    if bad is not None:
        return bad

    reference = references / f"{key}.png"
    if not reference.is_file() and fallback_key:
        reference = references / f"{fallback_key}.png"
    if not reference.is_file():
        return Verdict(None, f"no confirmed reference for {key} yet")

    name = reference.stem
    d = distance(shot_sig, signature(reference))
    if d <= SAME_WORLD_MAX:
        return Verdict(True, f"matches the confirmed {name} (distance {d})")
    if d <= AMBIGUOUS_MAX:
        return Verdict(False, f"close to {name} but not it (distance {d}) - "
                              "likely a different spawn in the same world")
    return Verdict(False, f"does not match {name} (distance {d})")


# The challenge HUD sits in the lower-left: a multiplier ring and a big score
# over "LINE: N". Fractions of the frame so it survives a resolution change.
_HUD_BOX = (0.05, 0.72, 0.32, 0.95)
_HUD_WHITE = 245     # glyphs are near-pure white
_HUD_DARK = 150      # ...against something clearly darker
_HUD_GAP = 3         # px to either side to look for that darker thing
_HUD_ROW_HITS = 8    # edge-white pixels in a row before it counts as text
_HUD_ROWS = 10       # text rows before we call it a HUD


# Mean horizontal spread below which a frame carries no scene.
#
# Recalibrated against the full 37-map sweep, which is the only dataset big
# enough to set it. The first value (12.0) came from a 12-map corpus whose
# weakest world was 18.2 - and it REJECTED A REAL MAP: SanFrancisco renders at
# 11.20 across all three runs, in 800 KB PNGs, and was reported three times as
# "not a map".
#
# Measured over 34 maps: the lowest-contrast real world is 11.20, then 12.88,
# 14.83, rising to 49.4. A black screen is 0.11 - and 5 KB against 778 KB+ for
# the smallest real shot, a 150x gap, so the two are nowhere near each other.
# 5.0 sits 2.2x below the weakest map and 45x above black.
#
# The loading card measures ~6.6 and would now pass this check. That is
# accepted: every capture path forces `skate3_loader_overlay=false`, and a card
# that slipped through would fail the reference match anyway, looking nothing
# like a world.
UNRENDERED_MAX = 5.0


def frame_spread(shot: Path) -> float:
    """Mean absolute deviation within each row, averaged over rows."""
    from PIL import Image

    side = 64
    image = Image.open(shot).convert("L").resize((side, side), Image.BILINEAR)
    pixels = list(image.getdata())
    total = 0.0
    for y in range(side):
        row = pixels[y * side:(y + 1) * side]
        mean = sum(row) / side
        total += sum(abs(v - mean) for v in row) / side
    return total / side


def looks_unrendered(shot: Path) -> str | None:
    """Why this frame shows no world, or None if it does.

    Two ways a verification run photographs nothing:

    - `skate3_loader_overlay` covers the whole window with a flat gradient
      card. It exists precisely to hide the menu automation, so photographing
      it measures nothing - and its title text sits in the same corner as the
      challenge HUD, reading as 17 rows of glyphs, which fakes a positive.
    - The boot warp substitutes the stream folder, so until a confirm lands the
      guest submits no draw records and the screen is pure black.

    Both are flat across each row in a way no rendered world is.
    """
    spread = frame_spread(shot)
    if spread >= UNRENDERED_MAX:
        return None
    return ("a black screen - no world was ever drawn" if spread < 2.0
            else "the loading overlay card, not the game")


# Kept as the older name; `looks_unrendered` says more.
def looks_like_overlay(shot: Path) -> bool:
    return looks_unrendered(shot) is not None


def challenge_hud(shot: Path) -> int:
    """Rows of glyph-like white in the challenge-HUD corner.

    A score / LINE / multiplier HUD means a CHALLENGE is running: the macro
    confirmed on a challenges tab instead of Locations, and the challenge
    teleported the player to wherever it lives. That is the failure this whole
    verification effort exists to catch, and unlike a reference image it is the
    same tell in every map, so it needs no per-map confirmation.

    Counts ROWS of text rather than white pixels, because bright pavement fills
    this corner on plenty of maps (99% near-white on DIY) and a painted ground
    line crossing it gives a couple of hits per row. Real glyphs give dozens.

    ADVISORY ONLY - do NOT gate on this. The one confirmed challenge shot
    (V_sk8itrio) scores 40 against 0 for the whole historical corpus, which
    looked like total separation at n=1. A 37-map sweep then produced two more
    detections and BOTH were false: an "OLD SAN VAN" advertising column on a
    dark plinth (33 rows) and high-contrast mortar lines in cobblestone (27).
    Bright world texture in that corner reads the same as UI text.

    A real challenge landing is caught anyway, and more reliably: it teleports
    the player somewhere else, so it fails reproducibility, distinctness, or
    the reference match.
    """
    if looks_like_overlay(shot):
        return 0

    from PIL import Image

    image = Image.open(shot).convert("L")
    width, height = image.size
    left, top, right, bottom = _HUD_BOX
    crop = image.crop((int(left * width), int(top * height),
                       int(right * width), int(bottom * height)))
    pixels = crop.load()
    crop_w, crop_h = crop.size
    rows = 0
    for y in range(crop_h):
        hits = 0
        for x in range(_HUD_GAP, crop_w - _HUD_GAP):
            if pixels[x, y] >= _HUD_WHITE and (
                    pixels[x - _HUD_GAP, y] < _HUD_DARK
                    or pixels[x + _HUD_GAP, y] < _HUD_DARK):
                hits += 1
        if hits >= _HUD_ROW_HITS:
            rows += 1
    return rows if rows >= _HUD_ROWS else 0


def compare_shots(shots: list[Path]) -> tuple[int, bool]:
    """Spread across several runs of the SAME map, and whether they agree.

    This is the half of the sweep that needs no human. A map whose own runs
    disagree has a non-deterministic route to it, which is a failure regardless
    of where any single run landed.
    """
    if len(shots) < 2:
        return 0, True
    sigs = [signature(shot) for shot in shots]
    worst = max(distance(a, b)
                for index, a in enumerate(sigs) for b in sigs[index + 1:])
    return worst, worst <= SAME_WORLD_MAX


__all__ = [
    "AMBIGUOUS_MAX", "HASH_SIDE", "SAME_WORLD_MAX", "Verdict", "challenge_hud",
    "frame_spread", "looks_like_overlay", "looks_unrendered",
    "check", "compare_shots", "distance", "fingerprint", "reference_key",
    "signature",
]
