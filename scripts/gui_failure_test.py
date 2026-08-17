#!/usr/bin/env python3
"""What the launcher does when a launch goes wrong. Needs no game.

Two things, both of which were once broken in ways invisible from the UI:

**One failed launch must not brick the library.** `SessionController._fail` puts
the controller in `Phase.FAILED`, and `app.play` refuses to start unless the
phase is `IDLE`. Nothing used to move it back, so after any failure every click
on every map card did nothing at all - the window still up, the cards still
highlighting, and no way to tell the launcher was dead. `app._on_failed` now
calls `controller.stop()`.

**A boot that never rendered must be retried.** The sweep harness always did;
the GUI did not, so the same engine flake the harness absorbed reached the user
as an error dialog. See `retry_checks` below.

`gui_e2e.py` cannot catch either: its own `on_failed` quits the app immediately,
so it never gets to try a second launch.

    python3 scripts/gui_failure_test.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gi  # noqa: E402

gi.require_version("Gtk", "3.0")
from gi.repository import Gio, GLib  # noqa: E402

from loader import app as loader_app  # noqa: E402
from loader import catalog, config, session  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))


def main() -> int:
    app = loader_app.LoaderApp()
    # MUST be non-unique. `Gtk.Application` is single-instance by application
    # id, so with the real launcher open this process handed its command line to
    # THAT instance, activated the user's library window, ran not one check, and
    # exited 0 - a silent false pass, and the one failure mode a test must never
    # have. `switch_request_test.py` dodges it by avoiding GTK altogether; this
    # one cannot, because the wiring under test is the app's.
    app.set_flags(app.get_flags() | Gio.ApplicationFlags.NON_UNIQUE)

    def start(_app):
        controller = app.controller
        packs = catalog.load_all(config.CATALOG_DIR)
        # Must be a map that CAN launch. This used to be `packs[0].maps[0]`,
        # which is DHS by DH13 - a `stalls` map - and every "a normal click
        # starts a launch" check below silently became a test of the refusal
        # path the moment won't-load maps stopped being launchable.
        pack, entry = next((p, e) for p in packs for e in p.maps
                           if e.status == "boots")

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

        # A map that cannot load must not be launchable from anywhere. Labelling
        # it was not enough - the label is easy to miss and missing it costs a
        # 200-second wait that can only end in an error.
        broken = next(((p, e) for p in packs for e in p.maps
                       if e.status == "stalls"), None)
        if broken is None:
            check("a known-broken map exists to test with", False, "none in catalog")
        else:
            bad_pack, bad_entry = broken
            before, before_errors = len(launched), len(errors)
            app.play(bad_pack, bad_entry)
            check("clicking a won't-load map starts nothing",
                  len(launched) == before,
                  f"{len(launched) - before} launch(es) started")
            check("and says why immediately", len(errors) == before_errors + 1,
                  f"{len(errors) - before_errors} messages shown")
            check("the library is still usable afterwards",
                  controller.phase is session.Phase.IDLE, str(controller.phase))

        # The picker the game itself shows must omit them too.
        from loader import launch as launch_mod
        offered = launch_mod._level_picker_cvars(packs[0], packs)
        names = offered["skate3_loader_levels"].split("|")
        broken_names = {e.name for p in packs for e in p.maps
                        if e.status == "stalls"}
        check("the in-game picker omits won't-load maps",
              not [n for n in names if n in broken_names],
              f"offered {[n for n in names if n in broken_names]}")
        check("the picker's parallel lists stay aligned",
              len(names) == len(offered["skate3_loader_level_packs"].split("|"))
              == len(offered["skate3_loader_level_indices"].split("|"))
              == len(offered["skate3_loader_level_worlds"].split("|")),
              "a name would map to the wrong world")

        # The retry cases below synthesize failures with no matching `play()`,
        # so `_on_failed`'s `release()` would outnumber `hold()` and GLib would
        # log `use_count > 0` criticals. The real flow is balanced - one play
        # holds, exactly one of finished/failed releases, and a retry does
        # neither - so neutralize the counter rather than let noise into a
        # passing run.
        app.hold, app.release = (lambda: None), (lambda: None)

        retry_checks(controller, launched, errors)

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


def retry_checks(controller, launched, errors) -> None:
    """A boot that never rendered must be retried, but only when it can work.

    The sweep harness has always retried these; the GUI did not, so the same
    ~1-in-333 engine flake that the harness silently absorbed reached the user
    as an error dialog. The two gates that keep the retry bounded are what these
    checks are really about: the attempt count, and `status == "boots"`.
    """
    import tempfile

    from loader.catalog import MapEntry

    CRASHED = "[FATAL] Call to invalid or unregistered function\n"
    # A real macro-driven run: the boot warp's takeover, the macro finishing,
    # then the takeover that is the requested world actually coming up. The
    # last one is what makes this NOT a transient failure.
    RENDERED = (
        "native-scene: taking over natively (746 items)\n"
        "Skate 3 demo path: gameplay input sequence complete\n"
        "native-scene: taking over natively (1093 items)\n"
    )
    # The same run without that final takeover: the macro ran and the world
    # never followed. Indistinguishable from a broken map in the log, which is
    # why only a map already proven to boot is retried on it.
    NO_WORLD = (
        "native-scene: taking over natively (746 items)\n"
        "Skate 3 demo path: gameplay input sequence complete\n"
    )

    directory = Path(tempfile.mkdtemp(prefix="skate3loader-test-"))

    class FakeSession:
        def __init__(self, text: str):
            self.log_file = directory / "run.log"
            self.log_file.write_text(text)

        def stop(self):
            pass

    def attempt_failure(log_text: str, status: str, attempt: int) -> tuple[int, int]:
        """Drive one LOADING-phase failure. Returns (relaunches, errors) it caused."""
        controller.phase = session.Phase.LOADING
        controller.entry = MapEntry(world_id="w", name="Test Map", status=status)
        controller.session = FakeSession(log_text)
        controller._attempt = attempt
        before = (len(launched), len(errors))
        controller._retry_or_fail("simulated boot failure")
        return len(launched) - before[0], len(errors) - before[1]

    relaunched, failed = attempt_failure(CRASHED, "boots", 1)
    check("a working map retries after an engine crash",
          relaunched == 1 and failed == 0,
          f"{relaunched} relaunch(es), {failed} error(s)")

    relaunched, failed = attempt_failure("BootFlow ShowPressStartMode\n", "boots", 1)
    check("a working map retries after a silent stall",
          relaunched == 1 and failed == 0,
          f"{relaunched} relaunch(es), {failed} error(s)")

    relaunched, failed = attempt_failure(CRASHED, "boots", session.SessionController.MAX_ATTEMPTS)
    check("the retry is bounded - the last attempt fails",
          relaunched == 0 and failed == 1,
          f"{relaunched} relaunch(es), {failed} error(s)")

    relaunched, failed = attempt_failure(CRASHED, "stalls", 1)
    check("a known-broken map fails at once, with no second 200 s wait",
          relaunched == 0 and failed == 1,
          f"{relaunched} relaunch(es), {failed} error(s)")

    relaunched, failed = attempt_failure(RENDERED, "boots", 1)
    check("a failure after the world came up is the map's, and is not retried",
          relaunched == 0 and failed == 1,
          f"{relaunched} relaunch(es), {failed} error(s)")

    relaunched, failed = attempt_failure(NO_WORLD, "boots", 1)
    check("the macro finishing with no world is retried",
          relaunched == 1 and failed == 0,
          f"{relaunched} relaunch(es), {failed} error(s)")

    relaunched, failed = attempt_failure(NO_WORLD, "stalls", 1)
    check("the same log on a known-broken map is not retried",
          relaunched == 0 and failed == 1,
          f"{relaunched} relaunch(es), {failed} error(s)")

    relaunched, failed = attempt_failure(CRASHED, "", 1)
    check("an untested map is not retried either",
          relaunched == 0 and failed == 1,
          f"{relaunched} relaunch(es), {failed} error(s)")

    # A retry continues the session, so it must NOT bump the generation - that
    # is the marker `_on_launched` reads to throw away an abandoned launch, and
    # bumping it here would make every retry stage a game and then discard it.
    controller.phase = session.Phase.LOADING
    controller.entry = MapEntry(world_id="w", name="Test Map", status="boots")
    controller.session = FakeSession(CRASHED)
    controller._attempt = 1
    generation = controller._generation
    controller._retry_or_fail("simulated boot failure")
    check("a retry does not abandon the session",
          controller._generation == generation,
          f"generation moved {generation} -> {controller._generation}")


if __name__ == "__main__":
    raise SystemExit(main())
