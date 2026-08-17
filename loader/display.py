"""Keeping the desktop in a state where a window can actually be photographed.

An unattended sweep runs for hours with no keyboard or mouse activity, so the
session locks - and **a locked session makes every window capture come back pure
black**. The capture succeeds, the PNG is well-formed, and its contents are
worthless. That is indistinguishable, from the harness's side, from a map that
genuinely failed to render.

It cost most of one sweep. Sixty minutes in - exactly `idle-delay` - runs began
producing all-black bursts while their logs stayed identical to the passing
runs: same two `taking over natively` lines, same mesh counts, same durations.
The maps were rendering perfectly the whole time.

Two things are needed, and the first is not enough on its own: turning the idle
timeout off does NOT dismiss a lock that has already engaged, and
`org.gnome.ScreenSaver.SetActive false` does not either. `loginctl
unlock-session` does.
"""

from __future__ import annotations

import subprocess


def _run(*args: str) -> str:
    try:
        return subprocess.run(args, capture_output=True, text=True,
                              timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def screensaver_active() -> bool:
    out = _run("gdbus", "call", "--session", "--dest", "org.gnome.ScreenSaver",
               "--object-path", "/org/gnome/ScreenSaver",
               "--method", "org.gnome.ScreenSaver.GetActive")
    return "true" in out.lower()


def session_id() -> str:
    for line in _run("loginctl", "list-sessions", "--no-legend").splitlines():
        parts = line.split()
        # seat sessions only; the manager row has no tty and cannot be unlocked
        if len(parts) >= 4 and parts[3].startswith("seat"):
            return parts[0]
    return ""


def unlock(settle: float = 6.0) -> bool:
    """Dismiss an active lock. Returns whether the screen ended up usable.

    Polls for a few seconds rather than checking once: GNOME does not clear
    `org.gnome.ScreenSaver.GetActive` synchronously, so an immediate check
    reported "could not unlock" on a session that had in fact just unlocked.

    Tries every seat session, not just the first - `loginctl` also lists a
    manager session that cannot be unlocked and is not the one that matters.
    """
    import time as _time
    sessions = [s for s in _all_sessions()] or [session_id()]
    for sid in sessions:
        if sid:
            _run("loginctl", "unlock-session", sid)
    deadline = _time.monotonic() + settle
    while _time.monotonic() < deadline:
        if not screensaver_active():
            return True
        _time.sleep(0.5)
    return not screensaver_active()


def _all_sessions() -> list[str]:
    out = []
    for line in _run("loginctl", "list-sessions", "--no-legend").splitlines():
        parts = line.split()
        if parts:
            out.append(parts[0])
    return out


def _value_of(raw: str) -> str:
    """The value out of a `gsettings get`, without its type annotation.

    `gsettings get` prints integers as `uint32 3600`, booleans as bare `false`.
    """
    text = raw.strip()
    for prefix in ("uint32 ", "uint64 ", "int32 ", "int64 "):
        if text.startswith(prefix):
            return text[len(prefix):].strip()
    return text


def disable_idle_blanking() -> list[str]:
    """Stop the session locking again. Returns what was changed.

    The comparison has to be EXACT. It used to be `wanted not in current` - a
    substring test - and "0" is a substring of "uint32 3600", so a machine with
    the GNOME default of one hour read as already-disabled and nothing was
    written. Both timeouts silently stayed at 3600 while this function reported
    success, which is the worst possible outcome: it puts the lock back exactly
    60 minutes into an unattended sweep, and from there every capture is pure
    black while the logs stay perfectly healthy. Caught with a sweep in flight.
    """
    changed = []
    for schema, key, value in (
        ("org.gnome.desktop.session", "idle-delay", "0"),
        ("org.gnome.desktop.screensaver", "idle-activation-enabled", "false"),
        ("org.gnome.settings-daemon.plugins.power",
         "sleep-inactive-ac-timeout", "0"),
    ):
        current = _run("gsettings", "get", schema, key)
        if current and _value_of(current) != value:
            _run("gsettings", "set", schema, key, value)
            changed.append(f"{key}: {_value_of(current)} -> {value}")
    return changed


def ensure_capturable(log=print) -> bool:
    """Preflight for anything that photographs a window."""
    for note in disable_idle_blanking():
        log(f":: display: {note}")
    if screensaver_active():
        log(":: display: session is LOCKED - every capture would be black; unlocking")
        if not unlock():
            log(":: display: could not unlock. Captures will be black until "
                "someone unlocks the session by hand.")
            return False
        log(":: display: unlocked")
    return True
