"""Turning "I want to play Barcelona" into a pad macro.

The menu path was measured directly, by reading the frontend's selection cursor
out of guest memory while the macro drove it (see loader/guestmem.py):

    start          -> pause root (screen 56)
    a              -> challenge map (screen 17)
    rt, rt, rt     -> three tabs right, reaching Locations
    down * 40      -> the list CLAMPS rather than wrapping. It holds 6 entries
                      (cursor 0..5) and the LAST one is the installed DLC pack,
                      so this parks on the pack whatever else is installed.
    a              -> open that pack's map list
    down * K       -> select the Kth map inside the pack
    a, a           -> confirm, then teleport

The important correction over the original guess: the Locations list does not
contain the individual maps at all. It contains one entry per PACK, and the maps
live in a sub-list behind it. That is why the old `down*40,a,a,a` macro always
produced Barcelona -- it was selecting sub-list index 0, not "the last map".

Because the loader stages exactly one DLC pack per launch, the pack is always
the last Locations entry, which makes the clamp reliable without knowing how
many stock locations exist.

The sub-list index is now a BACKSTOP rather than the mechanism. The engine also
repoints every map row at the requested world (`skate3_warp_substitute_item`,
wired up in launch.py): a Locations row carries its world as a pair of hashes at
item+0x18/+0x1C plus a per-world object at +0x00, and copying those from the
wanted map's item onto every row makes any row load it. Measured: navigating to
index 5 while targeting index 13 still lands on index 13's map.

Keeping both is deliberate. If a pack ever ships items with a different layout
the patch declines (it shape-checks before writing) and the navigation below
still lands the right map on its own.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# The tokens skate3_demo_path.cpp accepts. An unknown token does not get skipped
# -- it disables the ENTIRE sequence with a warning -- so every synthesized macro
# is validated against this set before it is allowed near a command line.
VALID_TOKENS = {
    "a", "b", "x", "y",
    "start", "back",
    "lb", "rb", "lt", "rt",
    "up", "down", "left", "right",
    "l3", "r3",
}

# Reaching the Locations tab from gameplay.
#
# DO NOT TRIM THESE. They were cut twice (1500 -> 400 -> 170 ms on the tabs) on
# the strength of the loader reporting LANDED, and both cuts were wrong: LANDED
# is inferred from STREAM lines, and the boot warp substitutes the stream folder,
# so the requested map's data streams no matter which menu row is really
# confirmed. Every trimmed run reported success while actually teleporting the
# player to a challenge in skate.School or Black Box Skatepark.
#
# Verified by SCREENSHOT, which is the only ground truth here: with these
# timings, requesting Spillway puts you in Spillway. A short tab wait leaves the
# menu on a challenges tab, and the confirm then starts a challenge instead of
# changing location.
#
# THE TAB STRIP CLAMPS, so press it more times than needed (2026-08-14).
# Three presses is the exact count to reach Locations, which means a single
# swallowed press leaves the macro on a challenges tab - and confirming there
# starts a challenge, which teleports the player somewhere unrelated. That is
# the whole wrong-map bug.
#
# Measured, requesting Spillway, each landing confirmed by screenshot AND by
# eye: 3 presses -> Spillway, 6 -> Spillway, 10 -> Spillway. Removing them
# entirely lands somewhere else (distance 692 against the reference), so the
# presses do matter - they are just idempotent past the end.
#
# That is proof of clamping rather than three lucky samples: if the strip
# wrapped, 3, 6 and 10 presses could only agree if the tab count divided both 3
# and 4, which is impossible. Locations is the last tab and the strip stops
# there - the same property that makes the `down` clamp below work.
#
# So over-pressing is free and swallowed presses stop mattering. This is the
# fix for the wrong-map bug, and it needs no engine change.
#
# Why not read the tab index and press until it says Locations, the way the
# opening `start` is confirmed against the frontend stack? Because there is
# nothing to read: the tab is absent from the frontend manager's first 2 KiB
# and from both screen objects at mgr+0x254/+0x264, and a diff of the whole
# 128 MB guest heap across every press turns up only animation. This build
# also never draws the menus to the framebuffer, so a screenshot cannot see the
# tab either. See scripts/tabprobe.py and scripts/tabscan.py.
#
# The delay is an EXPERIMENT KNOB (SKATE3LOADER_TAB_DELAY_MS) because it is the
# single largest slice of the menu phase - ten presses at 1500 ms is 15 s on
# every custom map - and the only honest way to shorten it is to try a value and
# judge the resulting SCREENSHOT. Never trust a log line here: a run that tabs
# too fast lands on a challenges tab, streams the right world and reports LANDED
# while you stand somewhere else entirely.
#
# 300 ms, not 1500 (2026-08-23). Ten presses at 1500 ms was 15 s on every custom
# map - over a third of the whole boot - and the clamping property above is what
# makes the wait unnecessary: a press swallowed at 300 ms costs nothing, because
# the ones after it still land and the strip stops at Locations either way.
#
# An earlier attempt at this DID break (see [[skate3-boot-time]]: every map came
# up in skate.School) and the difference matters. That trim was 170 ms with only
# THREE presses, so a single swallowed press left the macro on a challenges tab
# and the confirm started a challenge. The margin, not the delay, was what was
# missing - and the run was judged by log lines that cannot see which world you
# are in, so it read as a win.
#
# Measured this time by SCREENSHOT, which is the only thing that can tell:
#   Maloof Money Cup   3/3 MATCH against its confirmed reference (distance 2, 5, 8)
#   GTA Rot            lands where the 1500 ms macro lands (distance 3; a
#                      genuinely different map scores 386)
TAB_PRESSES = int(os.environ.get("SKATE3LOADER_TAB_PRESSES", 10))
TAB_DELAY_MS = int(os.environ.get("SKATE3LOADER_TAB_DELAY_MS", 300))
PREFIX = ["start", "a"] + [f"rt:{TAB_DELAY_MS}"] * TAB_PRESSES
# Step onto the last Locations entry, which is the staged DLC pack.
#
# The list holds one row per pack and, with exactly one custom pack staged, is
# always six rows -- five stock districts (Downtown, Industrial, University,
# skate.School, skate.Park) then the pack, confirmed on screen. Six presses
# reach the end from any starting row, and the list clamps rather than wrapping
# so the extra press is harmless. This used to be forty presses: ten seconds of
# scrolling and thirty-odd pointless menu clicks past the end of the list.
#
# freeskate's expand_macro turns `down:130*6` into six downs; the game's own
# parser has no `*`.
#
# Ten, not forty: the comment above has said "six presses reach the end" for a
# while but the constant never followed, so every boot spent 10.4 s scrolling
# and thirty-odd presses past the end of a six-row list. Ten keeps a four-press
# margin for a swallowed input, on the same clamping argument as the tabs.
CLAMP_PRESSES = int(os.environ.get("SKATE3LOADER_CLAMP_PRESSES", 8))
# 260 ms, not less: a synthetic press is held for 8 guest input polls (~130 ms
# at 60 Hz), so a 130 ms gap means presses run back-to-back with no release
# between them and the menu swallows some. That produced a cursor short of the
# end of the list, which selected a stock district instead of the pack.
CLAMP_DELAY_MS = int(os.environ.get("SKATE3LOADER_CLAMP_DELAY_MS", 260))
CLAMP = f"down:{CLAMP_DELAY_MS}*{CLAMP_PRESSES}"
# Open the pack's map list.
ENTER_PACK = f"a:{int(os.environ.get('SKATE3LOADER_ENTER_MS', 600))}"
# Confirm the highlighted map, then teleport. Without that last press you are
# left sitting in the menu.
#
# The delay after the LAST press buys nothing on the clock - the world load that
# follows is far longer - but it is what the loading overlay waits on before it
# starts looking for the streaming dip, so it stays in step with the others.
# 600/800, not 1200/1500. A confirm that outruns the list animation is DROPPED,
# which leaves you sitting in the menu - a visible, retryable failure, unlike a
# mistimed tab press, which silently starts a challenge somewhere else. That
# asymmetry is why these could be trimmed on measurement rather than on faith.
CONFIRM = [f"a:{int(os.environ.get('SKATE3LOADER_CONFIRM_MS', 600))}",
           f"a:{int(os.environ.get('SKATE3LOADER_LAST_MS', 800))}"]

# How long to dwell on each step inside the sub-list. The clamp presses can run
# fast, but a selection press that outruns the list's own animation is dropped.
STEP_DELAY_MS = 320


class MacroError(Exception):
    pass


def validate(macro: str) -> None:
    """Reject a macro before launching, since the failure mode is silent."""
    for token in macro.split(","):
        token = token.strip()
        if not token:
            continue
        # freeskate splits the `*N` repeat off FIRST and hands `base:delay` to the
        # game, so `down:320*3` is three presses with a 320 ms delay. Parse in the
        # same order or a legal macro looks malformed here.
        base, _, repeat = token.partition("*")
        base, _, delay = base.partition(":")
        base = base.strip()
        if base not in VALID_TOKENS:
            raise MacroError(
                f"{base!r} is not a pad token. The game disables the whole input "
                f"sequence on an unknown token. Valid: {', '.join(sorted(VALID_TOKENS))}"
            )
        if delay:
            if not delay.isdigit() or int(delay) <= 0:
                raise MacroError(f"{token!r}: delay must be a positive integer in ms")
        if repeat and not repeat.isdigit():
            raise MacroError(f"{token!r}: repeat count must be an integer")


def build_macro(sub_index: int | None, row_patch: bool = True) -> str:
    """The full macro for the map at `sub_index` inside the staged pack's list."""
    tokens = list(PREFIX) + [CLAMP, ENTER_PACK]
    # The per-map row walk is no longer needed: the engine repoints every row at
    # the requested world (see the module docstring), so ANY row loads it. That
    # walk cost up to 4.2 s on a 14-map pack - the single largest slice of the
    # menu phase. Passing an explicit sub_index still emits it, which is how a
    # pack with an unfamiliar item layout can be driven the old way.
    if sub_index and not row_patch:
        tokens.append(f"down:{STEP_DELAY_MS}*{int(sub_index)}")
    tokens += list(CONFIRM)
    macro = ",".join(tokens)
    validate(macro)
    return macro


@dataclass(frozen=True)
class Probe:
    """One calibration attempt: a suffix, and what it is meant to discover."""

    suffix: list[str]
    purpose: str

    @property
    def label(self) -> str:
        return "+".join(self.suffix) if self.suffix else "clamp"


def calibration_probe_suffixes() -> list[Probe]:
    """The opening probes that establish the grid's shape.

    `down*40` alone lands on the last entry. Stepping `left` should move one
    place within the bottom row; stepping `up` jumps a whole row (measured
    earlier: `up` from the clamped entry landed back on stock content, which is
    what says "grid" rather than "list"). Comparing which worlds these reach
    against the pack's known map list gives the column count.
    """
    probes = [Probe([], "the clamped last entry")]
    for n in range(1, 6):
        probes.append(Probe(["left"] * n, f"{n} place(s) left of the end"))
    probes.append(Probe(["up"], "one row up from the end"))
    probes.append(Probe(["up", "left"], "one row up, one left"))
    return probes


def suffix_for_index(index_from_end: int, columns: int | None) -> list[str]:
    """Step back `index_from_end` places from the clamped last entry.

    With a known column count, stepping back N places is N%columns lefts plus
    N//columns ups. Without one, fall back to pure lefts -- correct within the
    bottom row, which is all we can honestly claim before calibration.
    """
    if index_from_end <= 0:
        return []
    if not columns or columns <= 0:
        return ["left"] * index_from_end
    rows, cols = divmod(index_from_end, columns)
    return ["up"] * rows + ["left"] * cols


def macro_for(pack, entry) -> str:
    """The pad sequence that reaches `entry`, or "" when none is wanted.

    Three cases, and the first two are easy to get wrong:

    * The pack's world is what the game boots into (`boots_at_start`). Driving
      the menu then navigates AWAY from the map - Danny Way never rendered in
      3/3 macro runs and was on screen in 11 s with no macro.
    * The pack's maps are spots inside one world (`spots_share_world`), so the
      item patch cannot tell them apart and the row walk has to.
    * Otherwise the item patch handles it and the walk is skipped.
    """
    if getattr(pack, "boots_at_start", False):
        return ""
    # Walk the rows unless the ITEM PATCH is actually going to pick the map.
    # It only does that when the warp is enabled for this pack AND the pack's
    # maps have distinct world ids.
    #
    # This used to test `spots_share_world` alone, which silently broke every
    # warp-unsafe multi-map pack: Meebs and Brassy Ports has six distinct world
    # ids, so the row walk was skipped - but ids like `SOV` and
    # `Industrial Zone` match no spawn node, so `warp_safe` is false and the
    # item patch never runs either. Nothing selected the map, all six confirmed
    # sub-list index 0, and the sweep duly reported COLLIDES=6: every map
    # landing in Auburn.
    #
    # And it has to respect a pack that has TURNED THE WARP OFF in its settings.
    # `warp_safe` is derived from spawn nodes and knows nothing about that, so a
    # warp-off pack still looked like "the item patch will handle it" and the
    # row walk was skipped - leaving nothing at all to select the map. Sleepen's
    # five maps all came out with an identical macro that way.
    item_patch_selects = (
        pack.warp_safe
        and not pack.spots_share_world
        and (pack.settings or {}).get("skate3_warp_substitute_item") is not False
    )
    return build_macro(entry.sub_index, row_patch=item_patch_selects)
