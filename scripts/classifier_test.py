#!/usr/bin/env python3
"""`transient_boot_failure` must split real logs the way the catalog does.

This is the rule both the launcher and the sweep now retry on, so getting it
wrong costs either a user-visible failure that should have been retried away, or
an unbounded retry loop on a map that is simply broken. It is one line of code,
which is exactly why it is worth pinning against real data rather than a
hand-written fixture.

The two properties that matter, checked against every log in `work/spots/`:

  a broken map is never called loadable   - or a caller would trust a map that
                                            has never once rendered
  a working map is never called broken    - at least one of its runs must read
                                            as a real load, or retrying is moot

Flakes in between are expected and are reported by name rather than asserted
per-map, so a new one shows up as a new name instead of a red suite.

    python3 scripts/classifier_test.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loader import catalog, config, logwatch, spotcheck  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))


def main() -> int:
    # Synthetic cases first: the rule itself, independent of any log on disk.
    TAKE = "native-scene: taking over natively (997 items)"
    DONE = "Skate 3 demo path: gameplay input sequence complete"

    check("a takeover AFTER the macro is the real world",
          logwatch.transient_boot_failure(f"{TAKE}\n{DONE}\n{TAKE}") is None)
    check("only the boot warp's takeover means the map never arrived",
          logwatch.transient_boot_failure(f"{TAKE}\n{DONE}")
          == "the map never finished loading")
    check("no macro at all: the single takeover is the map's (Danny Way)",
          logwatch.transient_boot_failure(TAKE) is None)
    check("a null indirect call names the crash",
          logwatch.transient_boot_failure(
              "[FATAL] Call to invalid or unregistered function")
          == "the engine crashed during boot")
    check("a silent boot names the stall",
          logwatch.transient_boot_failure("BootFlow ShowPressStartMode")
          == "the game never reached gameplay")
    check("a crash after the world came up is the map's problem, not a flake",
          logwatch.transient_boot_failure(
              f"{TAKE}\n{DONE}\n{TAKE}\n[FATAL] later") is None)

    # Then every real log this project has kept.
    statuses = {spotcheck.reference_key(pack.id, entry.sub_index): entry.status
                for pack in catalog.load_all(config.CATALOG_DIR)
                for entry in pack.maps}

    logs = sorted((config.SPOTS_DIR).glob("s[0-9]__*.log"))
    if not logs:
        check("found run logs to test against", False,
              f"no s<N>__*.log in {config.SPOTS_DIR}")
        return report()

    seen: dict[str, set[str | None]] = {}
    for log in logs:
        key = log.stem.split("__", 1)[1]
        reason = logwatch.transient_boot_failure(log.read_text(errors="replace"))
        seen.setdefault(key, set()).add(reason)

    # 1. A broken map must NEVER be called loadable. If any `stalls` log came
    #    back non-transient the rule would be telling a caller to trust a map
    #    that has never once rendered.
    stalls = [k for k, s in statuses.items() if s == "stalls" and k in seen]
    check(f"{len(stalls)} known-broken maps' logs available", len(stalls) >= 10,
          f"only {len(stalls)} found")
    for key in sorted(stalls):
        check(f"{key} (stalls) never looks loaded", None not in seen[key],
              f"got {seen[key]}")

    # 2. A working map must never be called permanently broken: at least one of
    #    its runs has to read as a real load, or the retry would be pointless.
    working = [k for k, s in statuses.items() if s == "boots" and k in seen]
    check(f"{len(working)} working maps' logs available", len(working) >= 30,
          f"only {len(working)} found")
    for key in sorted(working):
        check(f"{key} (boots) has a run that loaded", None in seen[key],
              f"got {seen[key]}")

    # 3. Flakes among the working maps are expected but must stay RARE. Asserted
    #    as a rate rather than per-map, so a new flake does not fail the suite -
    #    it gets named below, which is the thing worth a human glance.
    flaky = sorted(k for k in working if seen[k] != {None})
    check(f"flaky working maps stay rare ({len(flaky)}/{len(working)})",
          len(flaky) <= max(2, len(working) // 10),
          f"{len(flaky)} of {len(working)}: {flaky}")
    if flaky:
        print(f"  note: working maps with at least one non-loading run: "
              f"{', '.join(flaky)}")

    return report()


def report() -> int:
    passed = sum(1 for _, ok, _ in results if ok)
    for name, ok, detail in results:
        if not ok:
            print(f"  FAIL  {name}" + (f"  ({detail})" if detail else ""))
    print(f":: {passed}/{len(results)} checks passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
