"""Perceptual fingerprints for "which map is this a screenshot of".

Split out of `spotcheck` so the hash can be measured and swapped without
touching the judging logic. `scripts/hashcheck.py` scores every candidate here
against a corpus of real shots and reports the separation ratio between "two
runs of one map" and "two different maps"; whichever wins is what `ACTIVE`
names.

A signature is a bitmask plus its length, so distance is one XOR and a popcount
- a full sweep is ~110 shots, which is 6000 pairs, and the list-of-ints form
this replaced spent most of its time in Python-level loops.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Signature:
    """Either a bitmask (hamming) or a real vector (scaled L1).

    Both kinds live behind one `distance`, so a histogram fingerprint - which
    cannot be a bit hash without throwing away what makes it useful - can be
    scored against the bit hashes on the same axis.
    """
    bits: int = 0
    length: int = 0
    vector: tuple[float, ...] | None = None


def bit_count(signature: Signature) -> int:
    return signature.length


def distance(a: Signature, b: Signature) -> int:
    if (a.vector is None) != (b.vector is None):
        raise ValueError("comparing a bit signature with a vector signature")
    if a.vector is not None and b.vector is not None:
        if len(a.vector) != len(b.vector):
            raise ValueError("comparing vectors of different length")
        # Normalised histograms each sum to 1, so L1 lands in 0..2. Scale to
        # 0..1000 so the numbers read like the hamming ones.
        return round(500 * sum(abs(x - y) for x, y in zip(a.vector, b.vector)))
    if a.length != b.length:
        raise ValueError(f"comparing {a.length}-bit and {b.length}-bit signatures")
    return (a.bits ^ b.bits).bit_count()


def _pack(values: list[int]) -> int:
    bits = 0
    for index, value in enumerate(values):
        if value:
            bits |= 1 << index
    return bits


def _grayscale(path: Path, width: int, height: int):
    from PIL import Image

    return Image.open(path).convert("L").resize((width, height), Image.BILINEAR)


def average_hash(path: Path, side: int) -> Signature:
    """Each cell brighter than the frame mean. Sensitive to overall exposure."""
    pixels = list(_grayscale(path, side, side).getdata())
    mean = sum(pixels) / len(pixels)
    return Signature(_pack([1 if v > mean else 0 for v in pixels]), side * side)


def difference_hash(path: Path, side: int) -> Signature:
    """Each cell brighter than its right neighbour.

    A gradient hash rather than a level one, so a map does not change identity
    when the sky is brighter - which matters here because the shot is taken a
    few seconds after a takeover and the world is still popping in.
    """
    image = _grayscale(path, side + 1, side)
    pixels = list(image.getdata())
    values = []
    for row in range(side):
        base = row * (side + 1)
        for column in range(side):
            values.append(1 if pixels[base + column] > pixels[base + column + 1] else 0)
    return Signature(_pack(values), side * side)


def color_hash(path: Path, side: int) -> Signature:
    """Per-channel average hash.

    Worlds in this game separate on palette as much as on geometry - a night
    map, a desert map and a green park read almost identically once flattened
    to luminance, which is the failure mode a grayscale hash has here.
    """
    from PIL import Image

    image = Image.open(path).convert("RGB").resize((side, side), Image.BILINEAR)
    values: list[int] = []
    for channel in image.split():
        pixels = list(channel.getdata())
        mean = sum(pixels) / len(pixels)
        values.extend(1 if v > mean else 0 for v in pixels)
    return Signature(_pack(values), side * side * 3)


def colour_histogram(path: Path, bins: int = 12, grid: int = 1) -> Signature:
    """Normalised RGB histogram, optionally per cell of a grid.

    Invariant to where the camera is pointing, which is the point: two runs of
    one map can differ by a large camera pan (measured at 77 on dhash16, against
    86 for two genuinely different worlds), and a histogram does not care. What
    it keeps is the world's palette and lighting, which is exactly what differs
    between maps.

    `grid=1` is fully translation-invariant; `grid=2` keeps a little layout
    (sky up, ground down) at some cost in pan tolerance.
    """
    from PIL import Image

    image = Image.open(path).convert("RGB").resize((96, 96), Image.BILINEAR)
    width, height = image.size
    cell_w, cell_h = width // grid, height // grid
    counts: list[float] = []
    for row in range(grid):
        for column in range(grid):
            cell = image.crop((column * cell_w, row * cell_h,
                               (column + 1) * cell_w, (row + 1) * cell_h))
            per_channel = [[0] * bins for _ in range(3)]
            for pixel in cell.getdata():
                for channel in range(3):
                    per_channel[channel][min(pixel[channel] * bins // 256, bins - 1)] += 1
            total = cell_w * cell_h * 3 or 1
            for channel in per_channel:
                counts.extend(value / total for value in channel)
    scale = sum(counts) or 1.0
    return Signature(vector=tuple(value / scale for value in counts))


def combined(path: Path) -> Signature:
    """Gradient structure plus colour, concatenated into one bitmask."""
    structure = difference_hash(path, 16)
    colour = color_hash(path, 12)
    bits = structure.bits | (colour.bits << structure.length)
    return Signature(bits, structure.length + colour.length)


CANDIDATES = {
    # The historical one, kept so a regression in the corpus is visible.
    "ahash16": lambda p: average_hash(p, 16),
    "ahash32": lambda p: average_hash(p, 32),
    "dhash16": lambda p: difference_hash(p, 16),
    "dhash32": lambda p: difference_hash(p, 32),
    "color12": lambda p: color_hash(p, 12),
    "combined": combined,
    "hist1x12": lambda p: colour_histogram(p, 12, 1),
    "hist2x12": lambda p: colour_histogram(p, 12, 2),
    "hist3x8": lambda p: colour_histogram(p, 8, 3),
}

# Which fingerprint `spotcheck` judges with. Chosen by measurement, not taste:
# run `scripts/hashcheck.py` and take the best separation ratio.
#
# Measured 2026-08-14 with `scripts/hashcheck.py`, under two regimes, as
# ratio = (closest different-map pair) / (widest same-map pair). Bigger is more
# room for a threshold to sit in; the bar is 4x.
#
#   regime A  every same-map pair shot at the spawn, skater stationary
#   regime B  regime A plus one same-map pair where the CAMERA HAS PANNED and
#             the skater has pushed away from the spawn
#
#                    A       B
#     ahash16      4.0x    3.1x     <- what this project used to use
#     ahash32      2.7x    3.1x
#     dhash16      7.4x    1.1x
#     dhash32      3.1x    1.1x
#     color12      3.0x    3.5x
#     combined     6.4x    2.3x
#     hist1x12    10.4x   11.3x     <- chosen
#
# Two things decided this. First, ahash16's closest pair was commcenter/matrix
# at 16, four bits above its own same-world threshold of 12 - and those shots
# are a hex-paved skatepark and a skyscraper plaza, not remotely the same place.
# Every frame in this game is a black-hooded skater centred against ground
# filling the lower half, so a luminance hash largely measures the skater.
#
# Second, and the reason the gradient hashes are out despite winning regime A:
# under camera drift dhash16 rates two shots of ONE place at 77 while rating
# Spillway against Rio at 86. That is not a threshold anyone can set. Drift is
# not controllable across a hundred unattended runs - the shot is taken a fixed
# hold after the renderer engages, and how far the skater has moved by then
# varies - so the fingerprint has to survive it. A colour histogram is invariant
# to where the camera is pointing and keeps what actually identifies a world:
# its palette and lighting.
#
# Known weakness: being colour-only, two maps with genuinely similar palettes
# could collide. Nothing in a 12-map corpus does (closest 145 against a 75
# threshold), but re-run hashcheck on the 37-map sweep before trusting it there.
ACTIVE = "hist1x12"


def signature(path: Path) -> Signature:
    return CANDIDATES[ACTIVE](path)
