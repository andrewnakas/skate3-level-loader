"""First run: get the user's own game files onto disk.

The loader does NOT extract anything. The engine already carries a full ISO
installer (`skate3_iso_installer.cpp`) and a title-update installer
(`skate3_title_update_installer.cpp`), and both take their input from the
environment when driven rather than clicked:

    SKATE3_INSTALL_ISO=<path to the Skate 3 ISO>
    SKATE3_INSTALL_TU=<path to the Title Update 3 package>

`skate3_app_common.cpp` runs the wizard whenever `IsGameInstalled(game_root)`
is false, so all this module does is spawn the engine with those variables set,
watch for the process to finish, and check whether default.xex arrived.

Reusing the engine's installer rather than re-implementing XISO parsing is the
whole point: it is the same code path the upstream release uses, so a file that
works there works here.
"""

from __future__ import annotations

import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

from . import config, launch, settings


class SetupError(RuntimeError):
    pass


@dataclass
class Install:
    """What the setup screen collected."""

    iso: Path | None = None
    title_update: Path | None = None


def needs_setup() -> bool:
    return not config.game_installed()


def existing_installs() -> list[Path]:
    """Game roots already on this machine, best first.

    A user who already runs a Skate3Recomp release should not have to extract
    their ISO a second time - the files are identical.
    """
    seen: list[Path] = []
    candidates = [
        config.game_data_root(),
        config.app_dir() / "engine" / "game",
        config.INSTALL / "game",
        config.SKATE3 / "Skate3Recomp-Linux" / "game",
        config.state_root() / "install" / "game",
    ]
    for path in candidates:
        try:
            resolved = path.expanduser().resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        if (resolved / "default.xex").is_file():
            seen.append(resolved)
    return seen


def use_existing(game_root: Path) -> None:
    """Adopt an install that is already on disk."""
    game_root = Path(game_root).expanduser()
    if not (game_root / "default.xex").is_file():
        raise SetupError(f"no default.xex under {game_root}")
    settings.save({"game_data_root": game_root})


def install_command(install: Install, binary: Path | None = None) -> tuple[list[str], dict[str, str]]:
    """The engine invocation that performs the install, and its environment."""
    binary = Path(binary) if binary else config.default_binary()
    if not binary.is_file():
        raise SetupError(
            f"no game engine at {binary}. The release ships one in engine/; "
            "set SKATE3LOADER_BINARY to point at another."
        )
    game_root = config.state_root() / "install" / "game"
    game_root.mkdir(parents=True, exist_ok=True)

    env = launch.child_env()
    if install.iso:
        env["SKATE3_INSTALL_ISO"] = str(Path(install.iso).expanduser())
    if install.title_update:
        env["SKATE3_INSTALL_TU"] = str(Path(install.title_update).expanduser())
    argv = [
        str(binary),
        f"--game_data_root={game_root}",
        f"--user_data_root={config.RUNTIME / 'user'}",
        f"--log_file={config.RUNTIME / 'logs' / 'install.log'}",
    ]
    return argv, env


def run_install(install: Install, binary: Path | None = None,
                on_line=None, timeout: float = 3600) -> Path:
    """Install the game, blocking. Returns the game root. Raises on failure.

    Meant to be called off the UI thread - see `run_install_async`.
    """
    argv, env = install_command(install, binary)
    game_root = Path(argv[1].split("=", 1)[1])
    for path in (config.RUNTIME / "logs", config.RUNTIME / "user"):
        path.mkdir(parents=True, exist_ok=True)

    process = subprocess.Popen(
        argv, cwd=config.RUNTIME, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        if process.stdout is not None:
            for line in process.stdout:
                if on_line:
                    on_line(line.rstrip())
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        raise SetupError("the installer did not finish within an hour") from exc

    if not (game_root / "default.xex").is_file():
        raise SetupError(
            "the installer finished but no default.xex arrived. Check that the "
            "ISO is a Skate 3 disc image; the engine's log is at "
            f"{config.RUNTIME / 'logs' / 'install.log'}"
        )
    settings.save({
        "game_data_root": game_root,
        "iso": str(install.iso) if install.iso else "",
        "title_update": str(install.title_update) if install.title_update else "",
    })
    return game_root


def run_install_async(install: Install, on_done, on_line=None,
                      binary: Path | None = None) -> threading.Thread:
    """Install on a worker thread. `on_done(game_root, error)` when finished."""

    def work() -> None:
        try:
            root = run_install(install, binary=binary, on_line=on_line)
        except Exception as exc:  # noqa: BLE001 - reported to the caller
            on_done(None, str(exc))
        else:
            on_done(root, None)

    thread = threading.Thread(target=work, daemon=True, name="skate3-install")
    thread.start()
    return thread
