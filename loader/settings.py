"""The launcher's own settings file: what the user pointed us at.

Small on purpose. Everything in here is a PATH the loader cannot work out for
itself - where the engine is, where the game files landed, which ISO they came
from - so the setup screen writes it once and every later run reads it.

`config` reads this lazily (a cached load), which is why the two modules can
refer to each other without a cycle.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_cache: dict[str, Any] | None = None


def _file() -> Path:
    from . import config

    return config.SETTINGS_FILE


def load(reload: bool = False) -> dict[str, Any]:
    global _cache
    if _cache is not None and not reload:
        return _cache
    path = _file()
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        _cache = data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        _cache = {}
    return _cache


def save(values: dict[str, Any]) -> None:
    """Merge `values` into the settings file. Paths are stored as strings."""
    global _cache
    data = dict(load(reload=True))
    for key, value in values.items():
        data[key] = str(value) if isinstance(value, Path) else value
    path = _file()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write-then-rename: a half-written settings file would strand the user on
    # the setup screen with no way back.
    scratch = path.with_suffix(".json.tmp")
    with scratch.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(scratch, path)
    _cache = data


def get(key: str, default: Any = None) -> Any:
    return load().get(key, default)
