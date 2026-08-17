#!/usr/bin/env python3
"""The in-game picker's relaunch request must actually relaunch. Needs no game.

Every pick now goes through the relaunch path, including maps in the pack that
is already staged - the in-session switch is off because it landed in the wrong
place. So the request file is no longer an edge case for cross-pack picks; it is
the ONLY way the picker does anything, and if the launcher drops one the game
just closes and nothing comes back. That is indistinguishable from a crash.

Deliberately avoids GTK: `Gtk.Application` is single-instance, so building the
real app while the launcher is open attaches to that instance instead of
starting, and the test silently does nothing.

    python3 scripts/switch_request_test.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loader import catalog, config, launch, session  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))


class FakeProcess:
    def poll(self):
        return None

    def wait(self, timeout=None):
        return 0


class FakeSession:
    def __init__(self):
        self.process = FakeProcess()
        self.log_file = "/dev/null"
        self.stopped = False

    def stop(self):
        self.stopped = True


def main() -> int:
    packs = catalog.load_all(config.CATALOG_DIR)
    staged = next((p for p in packs if len(p.maps) > 1), packs[0])
    target = staged.maps[-1]

    # Nothing may kill a real process: `stop()` calls kill_running_game, which
    # pkills EVERY skate3 on the machine. Running an earlier test without this
    # destroyed three in-flight sweep runs.
    killed = []
    launch.kill_running_game = lambda *a, **k: killed.append(1)

    switched: list[tuple] = []
    launched: list[bool] = []
    controller = session.SessionController(
        callbacks=session.Callbacks(on_switch=lambda p, e: switched.append((p.id, e.sub_index))),
        packs_provider=lambda: packs,
    )
    controller._launch = lambda windowed: launched.append(windowed)
    controller.pack, controller.entry = staged, staged.maps[0]
    controller.session = FakeSession()
    controller.phase = session.Phase.PLAYING

    # The picker writes: pack id, sub-index, name.
    config.REQUEST_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.REQUEST_FILE.write_text(f"{staged.id}\n{target.sub_index}\n{target.name}\n")
    check("request file written", config.REQUEST_FILE.is_file())

    handled = controller._handle_switch_request()

    check("same-pack request is acted on", handled,
          "a pick inside the staged pack must still relaunch")
    check("switch reported to the UI", len(switched) == 1,
          f"{switched} - this is what puts the loading cover up")
    check("relaunched on the requested map",
          switched[:1] == [(staged.id, target.sub_index)],
          f"wanted ({staged.id}, {target.sub_index}) got {switched[:1]}")
    check("a new launch was started", len(launched) == 1, f"{len(launched)} launch(es)")
    check("request file consumed", not config.REQUEST_FILE.exists(),
          "left behind it would relaunch forever")
    check("no real process killed", len(killed) > 0,
          f"{len(killed)} stubbed kill(s)")

    print()
    failed = [n for n, ok, _ in results if not ok]
    print(f"{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
