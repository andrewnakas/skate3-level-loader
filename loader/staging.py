"""Staging, done in-process against the vendored freeskate.

The loader used to run `freeskate play _loader --dry-run` and scrape the argv
out of its stdout. That cannot work off Linux: a shebang script is not
executable on Windows, and a frozen bundle has no python3 to fall back on -
`sys.executable` is the loader itself.

So freeskate is vendored (loader/vendor/freeskate.py) and called directly.
`build_command()` stays the single source of truth for the game's argv; we just
stop going through a pipe to reach it.

Its module-level paths (HERE, RUNTIME, MAPS_DIR, ...) are rebound here, because
vendored they would otherwise resolve relative to loader/vendor/.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from . import config
from .vendor import freeskate as fs


class StagingError(RuntimeError):
    pass


def _bind_paths() -> None:
    """Point the vendored module at the freeskate tree we actually use."""
    fs.HERE = config.FREESKATE_DIR
    fs.RUNTIME = config.RUNTIME
    fs.MAPS_DIR = config.FREESKATE_DIR / "maps"
    fs.MACROS_FILE = config.FREESKATE_DIR / "macros.toml"
    fs.CONFIG_FILE = config.FREESKATE_DIR / "config.toml"


def make_config(binary: Path | None = None) -> "fs.Config":
    """freeskate's resolved paths, with the engine and game data ours to choose.

    freeskate hardcodes `install/skate3` and a Linux-branded install directory.
    The loader already knows where the binary is (bundled, dev build or
    configured) and where the ISO installer put the game files, so both are
    overridden after construction rather than left to its defaults.
    """
    _bind_paths()
    cfg = fs.Config()
    cfg.binary = Path(binary) if binary else config.default_binary()
    cfg.src_game = config.game_data_root()
    cfg.install = cfg.src_game.parent
    cfg.game_root = config.RUNTIME / "game"
    cfg.user_root = config.RUNTIME / "user"
    cfg.logs = config.RUNTIME / "logs"
    return cfg


def _args(macro: str, settle_ms: int, delay_ms: int, windowed: bool) -> SimpleNamespace:
    """The subset of freeskate's argparse namespace that build_command reads."""
    return SimpleNamespace(
        macro=macro,
        settle=settle_ms,
        delay=delay_ms,
        probe=False,
        windowed=windowed,
        signed_out=False,
        signed_in=False,
        extra=[],
        map=config.LOADER_PROFILE,
        dry_run=True,
    )


def stage(macro: str, settle_ms: int, delay_ms: int, windowed: bool = False,
          binary: Path | None = None) -> list[str]:
    """Stage the scratch profile and return the argv freeskate would have run.

    The single-instance guard and the shm sweep freeskate does in `cmd_play` are
    deliberately skipped: `launch.kill_running_game()` has already done both, and
    freeskate's versions are Linux-only (pgrep, /dev/shm).
    """
    try:
        cfg = make_config(binary)
        cfg.check()
        game_map = fs.load_map(config.LOADER_PROFILE)
        fs.stage(cfg, game_map)
        return fs.build_command(cfg, game_map, _args(macro, settle_ms, delay_ms, windowed))
    except fs.Fail as exc:
        raise StagingError(str(exc)) from exc
