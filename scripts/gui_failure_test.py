#!/usr/bin/env python3
"""One failed launch must not brick the library. Needs no game.

`SessionController._fail` puts the controller in `Phase.FAILED`, and `app.play`
refuses to start unless the phase is `IDLE`. Nothing used to move it back, so
after any failure every click on every map card did nothing at all - the window
still up, the cards still highlighting, and no way to tell from the UI that the
launcher was dead. `app._on_failed` now calls `controller.stop()`.

`gui_e2e.py` cannot catch this: its own `on_failed` quits the app immediately,
so it never gets to try a second launch.

    python3 scripts/gui_failure_test.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gi  # noqa: E402

gi.require_version("Gtk", "3.0")
from gi.repository import GLib  # noqa: E402

from loader import app as loader_app  # noqa: E402
from loader import catalog, config, session  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))


def main() -> int:
    app = loader_app.LoaderApp()

    def start(_app):
        controller = app.controller
        packs = catalog.load_all(config.CATALOG_DIR)
        pack = packs[0]
        entry = pack.maps[0]

        check("starts IDLE", controller.phase is session.Phase.IDLE,
              str(controller.phase))

        # Nothing may actually launch: stub the spawn so this stays offline.
        launched = []
        controller._launch = lambda windowed: launched.append(windowed)

        # And nothing may KILL, either. `_fail` and `stop` both call
        # `launch.kill_running_game()`, which pkills every skate3 on the
        # machine - so running this while a sweep was in flight destroyed three
        # of its runs (23.9 s, 12.9 s and 4.3 s against a normal 63 s) before
        # the cause was obvious. This test is offline in BOTH directions.
        killed = []
        loader_app.launch.kill_running_game = lambda *a, **k: killed.append(1)
        session.launch.kill_running_game = lambda *a, **k: killed.append(1)

        # `_on_failed` ends in `library.show_error(...)`, which is a MODAL
        # dialog - correct for a person sitting there, and a deadlock with
        # nobody to click it. Record the message instead.
        errors = []
        app.library.show_error = lambda title, body: errors.append((title, body))

        app.play(pack, entry)
        check("play() starts a launch from IDLE", len(launched) == 1,
              f"{len(launched)} launch(es)")

        # Now fail it, exactly as a bad launch would.
        controller._fail("simulated launch failure")
        check("controller returns to IDLE after a failure",
              controller.phase is session.Phase.IDLE, str(controller.phase))

        app.play(pack, entry)
        check("play() still works after a failure", len(launched) == 2,
              f"{len(launched)} launch(es) total - a second click was accepted")

        check("the failure was surfaced to the user", len(errors) == 1,
              errors[0][0] if errors else "no error shown")
        check("no real process was ever killed", len(killed) > 0,
              f"{len(killed)} stubbed kill(s) - none reached pkill")

        controller.stop()
        app.quit()

    app.connect_after("activate", start)
    GLib.timeout_add_seconds(60, lambda: (app.quit(), False)[1])
    app.run([sys.argv[0]])

    print()
    failed = [n for n, ok, _ in results if not ok]
    if not results:
        print("no checks ran - the app never activated")
        return 2
    print(f"{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
