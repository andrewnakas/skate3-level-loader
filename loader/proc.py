"""Finding and killing the game, on any of the three platforms.

This used to be `pgrep -x skate3`, `/proc/<pid>/stat` and `pkill -9 -x skate3`.
Both of the hard-won rules those encoded are kept here, because both were paid
for with a wedged launcher:

* A ZOMBIE is not running. A defunct child still matches a process scan, and
  treating it as alive made `kill_running_game` SIGKILL a corpse for its whole
  timeout and then raise - which is what wedged an in-game map switch.
* Kill hard, every time round. A game mid-teardown ignores gentler signals, and
  freeskate refuses to stage while ANY copy is alive, so a half-dead process
  turns the next relaunch into a hard failure.

psutil rather than a subprocess: `pgrep` does not exist on Windows or in a
frozen bundle's PATH, and matching our own command line was a live bug once
already (`pgrep -f skate3loader` matched the shell that launched it).
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import psutil

from . import config

#: The engine's process name, without the platform's executable suffix.
GAME_STEM = "skate3"

#: A killed game leaks a 4.5 GiB shared-memory segment on Linux. Nothing
#: equivalent exists on Windows or macOS, where the mapping dies with the
#: process, so the cleanup below is a no-op there.
SHM_DIR = Path("/dev/shm")
SHM_GLOB = "xenia_memory_*"


def _is_game(process: psutil.Process) -> bool:
    try:
        name = process.name()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False
    stem = Path(name).stem.lower()
    return stem == GAME_STEM


def game_processes() -> list[psutil.Process]:
    """Every LIVE game process. Zombies are not live - see the module docstring."""
    found = []
    for process in psutil.process_iter(["name", "status"]):
        if not _is_game(process):
            continue
        try:
            if process.status() == psutil.STATUS_ZOMBIE:
                continue
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        found.append(process)
    return found


def game_running() -> bool:
    return bool(game_processes())


def kill_game(timeout: float = 10.0) -> bool:
    """Stop every running game. True if the field is clear afterwards."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        alive = game_processes()
        if not alive:
            return True
        for process in alive:
            try:
                process.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        # Reap, so a child of ours does not linger as a zombie and get counted.
        psutil.wait_procs(alive, timeout=0.25)
    return not game_running()


def clear_leaked_memory() -> int:
    """Delete shared-memory segments a killed game left behind. Linux only."""
    if not config.IS_LINUX or not SHM_DIR.is_dir():
        return 0
    cleared = 0
    for leaked in SHM_DIR.glob(SHM_GLOB):
        try:
            leaked.unlink()
            cleared += 1
        except OSError:
            pass
    return cleared


def leaked_memory() -> tuple[int, int]:
    """(count, total bytes) of leaked segments. (0, 0) off Linux."""
    if not config.IS_LINUX or not SHM_DIR.is_dir():
        return 0, 0
    found = list(SHM_DIR.glob(SHM_GLOB))
    total = 0
    for path in found:
        try:
            total += path.stat().st_size
        except OSError:
            pass
    return len(found), total


def terminate(process, timeout: float = 5.0) -> None:
    """Stop one child we spawned ourselves, and REAP it.

    `Popen.kill()` with no following `wait()` leaves a zombie, which then reads
    as a running game and blocks the next launch. That exact omission wedged the
    launcher on 'staging content' forever.
    """
    if process is None or process.poll() is not None:
        _reap(process)
        return
    try:
        process.terminate()
        try:
            process.wait(timeout=timeout)
            return
        except Exception:
            pass
        process.kill()
        process.wait(timeout=timeout)
    except Exception:
        pass
    finally:
        _reap(process)


def _reap(process) -> None:
    if process is None:
        return
    try:
        process.poll()
    except Exception:
        pass


def name_matches(path: os.PathLike[str] | str) -> bool:
    """Whether a binary path is the game, whatever suffix it carries."""
    return Path(path).stem.lower() == GAME_STEM
