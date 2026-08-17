"""Keep a session alive across relaunches, driven by the in-game picker.

The in-game picker can switch maps within the pack staged at boot, because the
installed DLC set is fixed then. Anything else is written to a request file --
and something has to be alive to act on it. That is this: a small loop that owns
the game process, watches for requests, and re-stages and relaunches on the
chosen pack. Without it a cross-pack pick looks like the game has frozen, since
the request is written and nobody answers.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from . import catalog, config, launch, logwatch, navigate
from .catalog import MapEntry, Pack


@dataclass
class Report:
    pack: Pack
    entry: MapEntry
    arrived: bool
    world_seen: str | None
    seconds: float


class Supervisor:
    """Runs sessions back to back until the game exits without a request."""

    # A boot that never rendered gets one more go. Same policy as the GUI's
    # `SessionController`; see `logwatch.transient_boot_failure`.
    MAX_ATTEMPTS = 2

    def __init__(self, boot_timeout: float = 260.0, on_event=None):
        self.boot_timeout = boot_timeout
        self.on_event = on_event or (lambda message: None)
        self.history: list[Report] = []

    def _packs(self) -> list[Pack]:
        return catalog.load_all(config.CATALOG_DIR)

    def _retry_reason(self, session, target: MapEntry, attempt: int) -> str | None:
        """Whether this dead session is worth simply starting over.

        Gated on `status == "boots"` for the same reason the GUI is: "nothing
        rendered" is equally true of a map that is merely broken, and those have
        already cost a full `boot_timeout`.
        """
        if attempt >= self.MAX_ATTEMPTS or target.status != "boots":
            return None
        try:
            text = Path(session.log_file).read_text(errors="replace")
        except OSError:
            return None
        return logwatch.transient_boot_failure(text) if text else None

    def run(
        self,
        pack: Pack,
        entry: MapEntry | None,
        open_picker: bool = False,
        windowed: bool = False,
    ) -> int:
        """Play `entry`, then keep serving relaunch requests until the game quits."""
        launch.clear_request()
        attempt = 0
        while True:
            packs = self._packs()
            target = entry or pack.maps[0]
            attempt += 1
            if open_picker:
                self.on_event(f"starting at the map picker ({pack.name} staged)")
            else:
                self.on_event(f"launching {pack.name} / {target.name}")

            # No map chosen yet: boot to the default world and wait at the
            # picker rather than navigating anywhere.
            macro = "" if open_picker else navigate.macro_for(pack, target)
            session = launch.launch(
                pack,
                target,
                macro,
                all_packs=packs,
                windowed=windowed,
                open_picker=open_picker,
            )
            watcher = logwatch.LogWatcher(
                session.log_file, [m.world_id for m in pack.maps], target.world_id
            )

            request = self._watch(session, watcher, pack, target, awaiting_pick=open_picker)
            if request is None:
                if not watcher.status.arrived:
                    reason = self._retry_reason(session, target, attempt)
                    if reason:
                        self.on_event(f"{reason} - retrying {target.name}")
                        session.stop()
                        launch.kill_running_game()
                        continue
                return session.process.poll() or 0

            pack_id, sub_index = request
            next_pack = next((p for p in packs if p.id == pack_id), None)
            next_entry = (
                next((m for m in next_pack.maps if m.sub_index == sub_index), None)
                if next_pack
                else None
            )
            if next_pack is None or next_entry is None:
                self.on_event(f"unknown pack in request: {pack_id}")
                return 1

            self.on_event(f"switching to {next_pack.name} / {next_entry.name}")
            session.stop()
            launch.kill_running_game()
            pack, entry = next_pack, next_entry
            # A new map gets its own attempt budget; the count above is per map,
            # not per session.
            attempt = 0
            # After the first hop the picker should not reopen; we are aiming at
            # a specific map.
            open_picker = False

    def _watch(
        self, session, watcher, pack, target, awaiting_pick: bool = False
    ) -> tuple[str, int] | None:
        """Follow one session. Returns a relaunch request, or None when it ends."""
        start = time.monotonic()
        last_detail = ""
        arrived_at = None

        while True:
            status = watcher.poll()
            if status.detail != last_detail:
                self.on_event(f"{status.stage.value}: {status.detail}")
                last_detail = status.detail
            if awaiting_pick and arrived_at is None and status.gameplay_count:
                arrived_at = time.monotonic() - start
                self.on_event("waiting at the map picker - press Esc if it is not showing")

            if not awaiting_pick and status.arrived and arrived_at is None:
                arrived_at = time.monotonic() - start
                self.on_event(f"arrived in {target.name} after {arrived_at:.0f}s")
                target.log_seen = True
                catalog.save(pack, config.CATALOG_DIR)

            request = launch.read_request()
            if request is not None:
                launch.clear_request()
                return request

            if session.process.poll() is not None:
                self.history.append(
                    Report(pack, target, bool(arrived_at), status.world_seen,
                           time.monotonic() - start)
                )
                return None

            # Only the pre-arrival phase is time-limited; once you are skating the
            # session lasts as long as you want it to.
            if arrived_at is None and time.monotonic() - start > self.boot_timeout:
                self.on_event("timed out before the map loaded")
                session.stop()
                return None

            time.sleep(0.25)
