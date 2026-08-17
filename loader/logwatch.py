"""Reading the game's own log to find out how the load is going.

This is the only feedback channel available from outside the process, and it is
a good one: the log names the world that actually streams, so the loader can
tell whether the macro landed on the right map instead of assuming it did.

Two things the caller must get right or this sees nothing:
  * the game needs --log_flush_interval=1, or lines sit in a buffer
  * the STOCK world loads first, every time, before the custom map streams in.
    Only world ids belonging to the staged pack count as "we arrived".
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class Stage(Enum):
    STAGING = "staging"
    BOOTING = "booting"
    MOUNTING = "mounting"
    FRONTEND = "frontend"
    IN_WORLD = "in world"
    NAVIGATING = "navigating menus"
    STREAMING = "streaming map"
    READY = "ready"
    FAILED = "failed"


# Fraction of the bar each stage has reached when it begins.
STAGE_FLOOR = {
    Stage.STAGING: 0.02,
    Stage.BOOTING: 0.08,
    Stage.MOUNTING: 0.16,
    Stage.FRONTEND: 0.30,
    Stage.IN_WORLD: 0.48,
    Stage.NAVIGATING: 0.58,
    Stage.STREAMING: 0.82,
    Stage.READY: 1.0,
    Stage.FAILED: 0.0,
}
# How far a stage is allowed to creep on elapsed time before its next milestone.
STAGE_CEILING = {
    Stage.STAGING: 0.07,
    Stage.BOOTING: 0.15,
    Stage.MOUNTING: 0.29,
    Stage.FRONTEND: 0.47,
    Stage.IN_WORLD: 0.57,
    Stage.NAVIGATING: 0.81,
    Stage.STREAMING: 0.97,
}
# Seconds a stage is expected to take, used only to pace the creep.
STAGE_PACE = {
    Stage.BOOTING: 12.0,
    Stage.MOUNTING: 14.0,
    Stage.FRONTEND: 18.0,
    Stage.IN_WORLD: 8.0,
    Stage.NAVIGATING: 30.0,
    Stage.STREAMING: 25.0,
}

RE_MOUNT = re.compile(r"Mounted .* at .*Partition1")
RE_PATCH = re.compile(r"XEX patch applied successfully")
RE_HOOKS = re.compile(r"demo path: frontend probe hooks installed")
RE_PRESS_START = re.compile(r"BootFlow ShowPressStartMode|press-start auto-tap")
RE_INTRO = re.compile(r"forcing frontend intro movie complete|intro movie completion override")
RE_GAMEPLAY = re.compile(r"demo path: gameplay reached")
RE_INJECT = re.compile(r"inject(?:ing|ed) .*?(\d+)\s*/\s*(\d+)")
RE_SEQUENCE_DONE = re.compile(r"gameplay input sequence complete")
RE_FE_STACK = re.compile(r"fe-debug: stack .*?n=\d+ \[(.*)")
# Each stack entry prints as `<id>{w w w w}`; take the id, not a prefix of it.
RE_FE_ID = re.compile(r"(\d+)\{")
RE_CLEAN_EXIT = re.compile(r"Window closing|Execution complete")
# The moment the game's own window exists and starts presenting. From here the
# in-game overlay covers the screen, so the launcher's own cover can go away.
RE_WINDOW_UP = re.compile(r"VulkanPresenter: Created \d+x\d+ swapchain")

# "dist_sk8itbareclona_barcelonadlc_Pres.xml" and the stream directory form
# "content\world\stream\DIST_sk8itmegapark".
#
# Custom packs name their worlds DIST_<world>; the official Xbox DLC uses a
# DLC_ prefix instead ("stream\DLC_DW_MegaCompund" is Danny Way). Matching only
# DIST_ reported a perfectly good Danny Way boot as MISSED.
RE_WORLD_XML = re.compile(
    r"\b(?:dist|dlc)_([A-Za-z0-9 ]+?)_[A-Za-z0-9]+_(?:Pres|Sim|Tex)\.xml", re.I)
RE_WORLD_DIR = re.compile(
    r"stream[\\/](?:DIST|DLC)_([A-Za-z0-9_ ]+?)(?:[\\/.\"']|$)", re.I)

# The pause challenge-map screen; reaching it means the macro is navigating.
SCREEN_CHALLENGE_MAP = "17"


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


@dataclass
class Status:
    stage: Stage = Stage.STAGING
    fraction: float = 0.0
    detail: str = "staging content"
    world_seen: str | None = None
    arrived: bool = False
    failed_reason: str | None = None
    gameplay_count: int = 0
    window_up: bool = False
    inject_progress: tuple[int, int] | None = None
    screens: list[str] = field(default_factory=list)


class LogWatcher:
    """Incrementally reads a growing log file and maintains a Status."""

    def __init__(self, log_file: Path, pack_world_ids: list[str], target_world: str):
        self.log_file = Path(log_file)
        self.pack_worlds = {_normalize(w): w for w in pack_world_ids}
        self.target = _normalize(target_world)
        self.status = Status()
        self._offset = 0
        self._stage_since = time.monotonic()
        self._max_fraction = 0.0
        self._last_line_at = time.monotonic()

    # -- stage transitions -------------------------------------------------

    def _enter(self, stage: Stage, detail: str) -> None:
        if stage is self.status.stage:
            self.status.detail = detail
            return
        # Never walk the bar backwards: a late boot line must not undo progress.
        if STAGE_FLOOR[stage] < STAGE_FLOOR[self.status.stage] and stage is not Stage.FAILED:
            return
        self.status.stage = stage
        self.status.detail = detail
        self._stage_since = time.monotonic()

    def _creep(self) -> float:
        """Advance the bar within a stage so it never looks frozen."""
        floor = STAGE_FLOOR[self.status.stage]
        ceiling = STAGE_CEILING.get(self.status.stage, floor)
        pace = STAGE_PACE.get(self.status.stage, 0.0)
        if ceiling <= floor or pace <= 0:
            return floor
        elapsed = time.monotonic() - self._stage_since
        # Asymptotic: fast at first, never quite reaching the ceiling.
        share = 1.0 - pow(2.718281828, -elapsed / pace)
        return floor + (ceiling - floor) * share

    # -- reading -----------------------------------------------------------

    def poll(self) -> Status:
        lines = self._new_lines()
        if lines:
            self._last_line_at = time.monotonic()
        for line in lines:
            self._consume(line)

        fraction = max(self._creep(), STAGE_FLOOR[self.status.stage])
        if self.status.stage is Stage.NAVIGATING and self.status.inject_progress:
            done, total = self.status.inject_progress
            if total:
                lo, hi = STAGE_FLOOR[Stage.NAVIGATING], STAGE_CEILING[Stage.NAVIGATING]
                fraction = max(fraction, lo + (hi - lo) * (done / total))
        if self.status.stage is Stage.READY:
            fraction = 1.0

        self._max_fraction = max(self._max_fraction, fraction)
        self.status.fraction = min(1.0, self._max_fraction)
        return self.status

    def _new_lines(self):
        if not self.log_file.exists():
            return []
        try:
            with self.log_file.open("r", errors="replace") as handle:
                handle.seek(self._offset)
                data = handle.read()
                self._offset = handle.tell()
        except OSError:
            return []
        if not data:
            return []
        return data.splitlines()

    # -- interpretation ----------------------------------------------------

    def _consume(self, line: str) -> None:
        status = self.status

        if RE_MOUNT.search(line):
            self._enter(Stage.MOUNTING, "mounting game data")
        elif RE_PATCH.search(line):
            self._enter(Stage.MOUNTING, "applying title update")
        elif RE_HOOKS.search(line):
            self._enter(Stage.FRONTEND, "skipping the frontend")
        elif RE_PRESS_START.search(line):
            self._enter(Stage.FRONTEND, "skipping press start")
        elif RE_INTRO.search(line):
            self._enter(Stage.FRONTEND, "skipping the intro movie")
        elif self._offset and status.stage is Stage.STAGING:
            self._enter(Stage.BOOTING, "starting the engine")

        if RE_GAMEPLAY.search(line):
            # NOTE: the game logs TWO "gameplay reached" variants in the same
            # millisecond at boot ("boot automation complete" and "injecting N
            # inputs"), and none at all when a map finishes loading -- the log
            # simply goes quiet. So this only marks the initial world, and
            # readiness is decided elsewhere (see mark_ready).
            status.gameplay_count += 1
            if status.gameplay_count == 1:
                self._enter(Stage.IN_WORLD, "loaded; opening the map list")

        match = RE_INJECT.search(line)
        if match:
            status.inject_progress = (int(match.group(1)), int(match.group(2)))
            self._enter(Stage.NAVIGATING, "selecting the map")

        if RE_SEQUENCE_DONE.search(line):
            status.inject_progress = None

        match = RE_FE_STACK.search(line)
        if match:
            screens = match.group(1).split()
            status.screens = screens
            if any(s.startswith(SCREEN_CHALLENGE_MAP) for s in screens):
                self._enter(Stage.NAVIGATING, "in the Locations list")

        self._check_world(line)

        if RE_WINDOW_UP.search(line):
            status.window_up = True

        if RE_CLEAN_EXIT.search(line) and not status.arrived:
            status.failed_reason = (
                "the game exited before the map loaded - the content package was "
                "probably rejected at the content scan"
            )
            self._enter(Stage.FAILED, "the game exited early")

    def _check_world(self, line: str) -> None:
        """Note when a world belonging to the staged pack starts streaming."""
        for pattern in (RE_WORLD_XML, RE_WORLD_DIR):
            for raw in pattern.findall(line):
                key = _normalize(raw)
                if key not in self.pack_worlds:
                    continue  # the stock world always loads first; ignore it
                self.status.world_seen = self.pack_worlds[key]
                if key == self.target:
                    self.status.arrived = True
                    if self.status.stage is not Stage.READY:
                        self._enter(Stage.STREAMING, f"streaming {self.pack_worlds[key]}")
                else:
                    self.status.arrived = False
                    self._enter(
                        Stage.STREAMING, f"streaming {self.pack_worlds[key]} (wrong map)"
                    )
                return

    # -- results -----------------------------------------------------------

    @property
    def quiet_seconds(self) -> float:
        """How long the log has had nothing new to say.

        A finished map load is silent: the last streaming line lands and then
        nothing follows, so quiet time is the fallback readiness signal when the
        guest-memory check is unavailable.
        """
        return time.monotonic() - self._last_line_at

    def mark_ready(self) -> None:
        self._enter(Stage.READY, "ready")

    @property
    def landed_correctly(self) -> bool:
        return self.status.arrived

    @property
    def wrong_world(self) -> str | None:
        seen = self.status.world_seen
        if seen and _normalize(seen) != self.target:
            return seen
        return None
