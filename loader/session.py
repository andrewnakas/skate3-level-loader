"""Driving one play session: launch, watch, hand over, and come back.

The state machine runs on the GTK main loop via a single periodic tick: each
tick polls the log watcher and the child process and moves things along. The one
exception is staging, which shells out to freeskate and takes seconds, so it runs
on a worker thread and reports back through GLib.idle_add.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path

from gi.repository import GLib

from . import guestmem, launch, logwatch, navigate
from .catalog import MapEntry, Pack

TICK_MS = 250
# Give up on a boot that never reaches gameplay.
#
# Without this the GUI hangs on the loading screen FOREVER on a map that does
# not load: the in-engine cover only hides once the world is up, so a broken
# pack leaves you staring at a progress bar with no way back. The CLI
# supervisor always had a boot timeout; the GUI did not.
#
# 200 s is well clear of a real boot - the slowest verified map takes ~78 s, and
# a cold shader cache pushes that to ~90 s - so this only fires on a genuine
# failure.
BOOT_TIMEOUT_SECONDS = 200.0
# How long to wait past "ready" before handing the screen to the game, so the
# first gameplay frame is up rather than the tail of the load.
HANDOVER_DELAY_MS = 1200


class Phase(Enum):
    IDLE = auto()
    LOADING = auto()
    PLAYING = auto()
    FAILED = auto()


@dataclass
class Callbacks:
    on_switch: object = None  # (pack, entry) - relaunching on a different pack
    on_window_up: object = None  # () - the game's window exists; stop covering
    on_progress: object = None  # (fraction, detail)
    on_ready: object = None  # ()
    on_finished: object = None  # (exit_code)
    on_failed: object = None  # (message)
    on_retry: object = None  # (attempt, reason) - reason is a phrase, not a world


class SessionController:
    """One map at a time: start it, follow it, report what happened."""

    MAX_ATTEMPTS = 2

    def __init__(self, callbacks: Callbacks, packs_provider=None):
        self.callbacks = callbacks
        # Lets the in-game picker offer maps from every imported pack, not just
        # the one staged this session.
        self.packs_provider = packs_provider
        self.phase = Phase.IDLE
        self.session: launch.Session | None = None
        self.watcher: logwatch.LogWatcher | None = None
        self.pack: Pack | None = None
        self.entry: MapEntry | None = None
        self._tick_id = 0
        self._attempt = 0
        self._handover_pending = 0
        self._boot_started = 0.0
        self._windowed = False
        self._window_announced = False
        # Bumped whenever a session is abandoned, so a staging thread that
        # finishes after a cancel does not spawn a game nobody is watching.
        self._generation = 0

    # -- control -----------------------------------------------------------

    def start(self, pack: Pack, entry: MapEntry, windowed: bool = False) -> None:
        self.pack = pack
        self.entry = entry
        self._attempt = 0
        self._launch(windowed)

    def _launch(self, windowed: bool) -> None:
        """Stage and spawn on a worker thread.

        Staging shells out to freeskate, which takes seconds. Doing that on the
        main loop freezes the loading screen before it has painted -- it shows as
        a blank grey rectangle until staging finishes.
        """
        assert self.pack and self.entry
        self._attempt += 1
        self._windowed = windowed
        self._window_announced = False
        self.phase = Phase.LOADING
        self._handover_pending = 0
        self._boot_started = time.monotonic()
        # A pack whose maps share one world id has to walk to its row: the item
        # patch cannot tell those spots apart and collapses them onto one.
        macro = navigate.macro_for(self.pack, self.entry)
        pack, entry = self.pack, self.entry
        all_packs = self.packs_provider() if self.packs_provider else None
        generation = self._generation

        def worker():
            try:
                started = launch.launch(
                    pack, entry, macro, all_packs=all_packs, windowed=windowed
                )
            except launch.LaunchError as exc:
                GLib.idle_add(self._on_launched, generation, None, str(exc))
            except Exception as exc:  # noqa: BLE001 - surfaced to the user
                message = f"{type(exc).__name__}: {exc}"
                GLib.idle_add(self._on_launched, generation, None, message)
            else:
                GLib.idle_add(self._on_launched, generation, started, None)

        threading.Thread(target=worker, daemon=True).start()

    def _on_launched(
        self, generation: int, started: "launch.Session | None", error: str | None
    ) -> bool:
        if generation != self._generation:
            # Abandoned while staging: throw away whatever we just started.
            if started is not None:
                started.stop()
                launch.kill_running_game()
            return False
        if error or started is None:
            self._fail(error or "the game could not be started")
            return False
        self.session = started
        self.watcher = logwatch.LogWatcher(
            started.log_file,
            [m.world_id for m in self.pack.maps],
            self.entry.world_id,
        )
        if not self._tick_id:
            self._tick_id = GLib.timeout_add(TICK_MS, self._tick)
        return False

    def stop(self) -> None:
        self._generation += 1
        if self.session:
            self.session.stop()
        launch.kill_running_game()
        self._cancel_tick()
        self.session = None
        self.watcher = None
        self.phase = Phase.IDLE

    def _cancel_tick(self) -> None:
        if self._tick_id:
            GLib.source_remove(self._tick_id)
            self._tick_id = 0

    # -- the loop ----------------------------------------------------------

    def _tick(self) -> bool:
        """One step of the state machine, on the GTK main loop.

        Wrapped so a raise can never just stop the loop. It happened: an
        in-game map switch hit `LaunchError` inside `_handle_switch_request`,
        the exception escaped this tick, the timeout source died, and the
        loading screen sat on "staging content" forever with nothing shown to
        the user and no way back. A failure has to become an on_failed, which
        at least says what went wrong and returns the library.
        """
        try:
            return self._tick_inner()
        except Exception as exc:  # noqa: BLE001 - surfaced to the user
            self._fail(f"{type(exc).__name__}: {exc}")
            return False

    def _tick_inner(self) -> bool:
        if not self.session or not self.watcher:
            self._tick_id = 0
            return False

        status = self.watcher.poll()
        exited = self.session.process.poll()

        if self.phase is Phase.LOADING:
            self._emit("on_progress", status.fraction, status.detail)

            waited = time.monotonic() - self._boot_started
            if waited > BOOT_TIMEOUT_SECONDS:
                name = self.entry.name if self.entry else "that map"
                self._retry_or_fail(
                    f"{name} did not load within {BOOT_TIMEOUT_SECONDS:.0f} seconds. "
                    "Some community packs never reach gameplay in this build - the "
                    "library marks the ones already known to fail as WON'T LOAD."
                )
                return False

            # Once the game is presenting, its own overlay covers the load, so
            # the launcher's cover is no longer needed (and cannot reliably stay
            # above a fullscreen window anyway).
            if status.window_up and not self._window_announced:
                self._window_announced = True
                self._emit("on_window_up")

            if self._handle_switch_request():
                return False

            if status.failed_reason:
                self._fail(status.failed_reason)
                return False

            if exited is not None:
                self._retry_or_fail(
                    f"the game exited during loading (code {exited}) before reaching "
                    f"{self.entry.name}"
                )
                return False

            # The wrong map streaming is recoverable: relaunch behind the same
            # loading screen so a miss costs time rather than being visible.
            wrong = self.watcher.wrong_world
            if wrong and self._attempt < self.MAX_ATTEMPTS:
                self._relaunch(f"landed on {wrong}")
                return False

            if status.arrived and self._in_gameplay():
                self.watcher.mark_ready()
                self._handover_pending += TICK_MS
                if self._handover_pending >= HANDOVER_DELAY_MS:
                    self.phase = Phase.PLAYING
                    if self.entry is not None:
                        # The LOG only proves the world streamed, which the
                        # boot warp makes true whichever row was confirmed.
                        # "we landed here" is spot_verified, and only a
                        # screenshot can set that.
                        self.entry.log_seen = True
                    self._emit("on_ready")
            return True

        if self.phase is Phase.PLAYING:
            if self._handle_switch_request():
                return False
            if exited is not None:
                self._cancel_tick()
                self.phase = Phase.IDLE
                self._emit("on_finished", exited)
                return False
            return True

        return True

    # -- helpers -----------------------------------------------------------

    # Frontend screens that mean a menu is open, so we are not skating yet.
    MENU_SCREENS = {17, 56}
    # Fallback: a finished load goes silent, because the game logs nothing at
    # all when a world finishes streaming.
    QUIET_READY_SECONDS = 5.0

    def _in_gameplay(self) -> bool:
        """Whether the guest is actually skating rather than sitting in a menu.

        Read straight out of the running game's memory: the frontend push-state
        stack is empty in gameplay and holds the pause/challenge-map screens
        while the macro is navigating. The log cannot answer this -- it emits
        both of its "gameplay reached" lines within a millisecond at boot and
        says nothing when a map finishes loading.
        """
        try:
            with guestmem.GuestMemory() as memory:
                stack = memory.screen_stack()
                if stack:
                    return not any(e.screen_id in self.MENU_SCREENS for e in stack)
                # An empty stack right after arrival is gameplay; before the
                # frontend exists it is just an unbooted game, but by this point
                # we have already seen the target world stream.
                return memory.frontend_manager() is not None
        except guestmem.NotRunning:
            pass
        except OSError:
            pass
        return bool(self.watcher and self.watcher.quiet_seconds >= self.QUIET_READY_SECONDS)

    def _handle_switch_request(self) -> bool:
        """Restart on another pack if the in-game picker asked for one.

        The picker can only switch maps within the pack staged at boot, because
        the installed DLC set is fixed then. Anything else comes back here as a
        request, and the launcher -- which outlives the game -- re-stages and
        relaunches.
        """
        request = launch.read_request()
        if request is None:
            return False
        launch.clear_request()

        pack_id, sub_index = request
        packs = self.packs_provider() if self.packs_provider else []
        target = next((p for p in packs if p.id == pack_id), None)
        if target is None:
            return False
        entry = next((m for m in target.maps if m.sub_index == sub_index), None)
        if entry is None:
            return False

        self._emit("on_switch", target, entry)
        windowed = self._windowed
        self.stop()
        self.pack, self.entry = target, entry
        self._attempt = 0
        self._launch(windowed)
        return True

    def _retry_or_fail(self, message: str) -> None:
        """Try again if this boot failed in a way that is not the map's fault.

        The sweep harness has always retried a run that never rendered, and it
        recovers a boot that way somewhere between 1 run in 333 (measured on an
        idle machine) and 1 in 60 (measured 2026-08-17 under load). The GUI did
        not, so exactly the same engine flake that the harness shrugs off
        surfaced to the user as an error dialog - the launcher was measurably
        less reliable than its own test rig. Both now apply
        `logwatch.transient_boot_failure`.

        The extra gate here is `status == "boots"`. A transient reason means
        "nothing rendered", which is also true of every map that is simply
        broken, and those already cost a 200 s timeout - doubling that to 400 s
        before the user gets an explanation would be worse than the bug. So only
        a map with a run behind it proving it CAN load earns a second attempt.
        """
        reason = None
        if self.session is not None and self.entry is not None \
                and self._attempt < self.MAX_ATTEMPTS \
                and self.entry.status == "boots":
            try:
                text = Path(self.session.log_file).read_text(errors="replace")
            except OSError:
                text = ""
            reason = logwatch.transient_boot_failure(text) if text else None

        if reason is None:
            self._fail(message)
            return
        self._relaunch(reason)

    def _relaunch(self, reason: str) -> None:
        """Start this map over behind the same loading screen.

        The one place that relaunches. Deliberately does NOT bump `_generation`
        - that marks a session as abandoned, and this one is being continued.
        `_launch` resets the boot deadline, so the new attempt gets a full
        window rather than the remains of the old one's.
        """
        self._emit("on_retry", self._attempt, reason)
        if self.session:
            self.session.stop()
        # Clears the leaked 4.5 GiB /dev/shm segment too, which would otherwise
        # hang the very next startup.
        launch.kill_running_game()
        self.session = None
        self.watcher = None
        self._cancel_tick()
        self._launch(self._windowed)

    def _fail(self, message: str) -> None:
        self._generation += 1
        self._cancel_tick()
        self.phase = Phase.FAILED
        # Nothing in the teardown may raise: this IS the error path, and a
        # second exception here would strand the UI exactly as the first one did.
        try:
            if self.session:
                self.session.stop()
            launch.kill_running_game()
        except Exception as exc:  # noqa: BLE001
            message = f"{message} (cleanup also failed: {exc})"
        self._emit("on_failed", message)

    def _emit(self, name: str, *args) -> None:
        callback = getattr(self.callbacks, name, None)
        if callback:
            callback(*args)
