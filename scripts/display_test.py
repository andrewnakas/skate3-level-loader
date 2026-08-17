#!/usr/bin/env python3
"""The capture guard must actually write the settings it claims to. Needs no game.

`disable_idle_blanking` is the thing standing between an unattended sweep and
two hours of pure-black screenshots, and it failed OPEN: it compared the wanted
value against `gsettings get` output with `wanted not in current`, a substring
test. "0" is a substring of "uint32 3600", so on a machine with the GNOME
default of one hour it concluded both timeouts were already disabled, wrote
nothing, and reported success. The session then locked exactly 60 minutes in and
every capture after that was black while the logs stayed healthy - the failure
mode that has already cost this project most of one sweep.

It was caught by checking the settings by hand after a sweep had started, which
is not a thing to rely on twice.

    python3 scripts/display_test.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loader import display  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    if not ok:
        print(f"  FAIL  {name}" + (f"  ({detail})" if detail else ""))


def main() -> int:
    # Parsing `gsettings get` output.
    for raw, want in (
        ("uint32 3600", "3600"),
        ("uint32 0", "0"),
        ("  uint32 900  ", "900"),
        ("false", "false"),
        ("true", "true"),
        ("3600", "3600"),
        ("0", "0"),
        ("int32 -1", "-1"),
    ):
        got = display._value_of(raw)
        check(f"_value_of({raw!r}) == {want!r}", got == want, f"got {got!r}")

    # The regression itself, with gsettings stubbed so this touches nothing.
    reads = {
        "org.gnome.desktop.session idle-delay": "uint32 3600",
        "org.gnome.desktop.screensaver idle-activation-enabled": "true",
        "org.gnome.settings-daemon.plugins.power sleep-inactive-ac-timeout": "3600",
    }
    writes: list[tuple[str, str, str]] = []

    def fake_run(*args: str) -> str:
        if args[:1] == ("gsettings",) and args[1] == "get":
            return reads.get(f"{args[2]} {args[3]}", "")
        if args[:1] == ("gsettings",) and args[1] == "set":
            writes.append((args[2], args[3], args[4]))
            reads[f"{args[2]} {args[3]}"] = args[4]
            return ""
        return ""

    original = display._run
    try:
        display._run = fake_run
        changed = display.disable_idle_blanking()
        check("a one-hour idle-delay is actually rewritten",
              ("org.gnome.desktop.session", "idle-delay", "0") in writes,
              f"wrote {writes}")
        check("a one-hour sleep timeout is actually rewritten",
              ("org.gnome.settings-daemon.plugins.power",
               "sleep-inactive-ac-timeout", "0") in writes,
              f"wrote {writes}")
        check("the screensaver is switched off",
              ("org.gnome.desktop.screensaver",
               "idle-activation-enabled", "false") in writes,
              f"wrote {writes}")
        check("all three changes are reported", len(changed) == 3, str(changed))

        # Second call: everything is already correct, so nothing more is written.
        writes.clear()
        again = display.disable_idle_blanking()
        check("already-disabled settings are left alone",
              not writes and not again, f"wrote {writes}, reported {again}")
    finally:
        display._run = original

    passed = sum(1 for _, ok, _ in results if ok)
    print(f":: {passed}/{len(results)} checks passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
