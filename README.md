# Skate 3 Level Loader

Pick a map, watch it load, skate. The launcher owns map selection, so the game
never has to manage DLC — you keep unlimited packs imported and it stages
exactly one per launch.

Built against [skate3recomp](https://github.com/mchughalex/skate3recomp) with a
few engine-side patches (see [Engine changes](#engine-changes)).

![the library](docs/library.png)

---

## Quick start

```bash
./skate3loader doctor     # check the environment first
./skate3loader            # open the library, click a map
```

That's the whole workflow: click a card, the loading screen covers the boot, and
you drop into the map with no menus. Quit the game and the library comes back.

### Command line

```
./skate3loader                     open the library (default)
./skate3loader doctor              check freeskate, the game binary, disk, packs
./skate3loader list                list imported packs and their maps
./skate3loader import <path.big>   import one pack
./skate3loader scan <dir>          import every usable pack in a folder
./skate3loader play <world-id>     boot straight into one map, no GUI
./skate3loader run [world-id]      play, starting at the in-game map picker
./skate3loader manual <pack-id>    stage a pack, no automation — you navigate
```

---

## Adding maps

Drop your downloaded packs in a folder and point `scan` at it:

```bash
./skate3loader scan ~/Downloads/packs
```

It explains every rejection rather than silently skipping. Two formats work:

| Format | What it is | Notes |
|---|---|---|
| **STFS container** | signed `LIVE`/`CON`/`PIRS` package | Preferred — installs itself, no header needed |
| **`.big` + `.header`** | raw archive plus its content header | The header must be present |

### Packs with no `.header`

Plenty of community packs ship a bare `<name>_00000000.big`. Generate the header:

```bash
python3 scripts/makeheader.py "path/to/pack_00000000.big"
./skate3loader scan ~/Downloads/packs        # re-scan to pick it up
```

The header is a 328-byte descriptor with nothing signed in it; the only rule
that matters is that its content id equals the package directory name, which the
generator guarantees. Verified working — the game mounts packs carrying one.

### Packs that won't import

- **Loose `DIST_` folders** — world geometry with no DLC package. There's no
  Locations menu entry for the game to select, so these can't be loaded.
- **"found no freeskate locations"** — the archive has no location strings, so
  it isn't a map pack in the format the loader understands.

---

## Diagnosing a map that won't load

```bash
./skate3loader manual <pack-id> --windowed
```

Stages the DLC and gets out of the way: no macro, no warp, no loading cover. You
navigate the game's own menus.

This is how the "14 broken maps" turned out to be wrong. Every hypothesis ruled
out before it had been tested with the loader's automation still driving, so
"the loader breaks these" and "these are broken" were never separated. Hastings
Bowl loaded by hand on the first try, and now loads unattended in ~62 s with the
boot warp disabled for that pack:

```json
"settings": {
  "skate3_warp_substitute_item":   false,
  "skate3_warp_substitute_folder": false,
  "skate3_warp_substitute_slug":   false,
  "skate3_warp_substitute_node":   false,
  "skate3_warp_substitute_lookup": false
}
```

All **five** switches matter — `_node` and `_lookup` default to true and aren't
in the usual disable list, which is why an earlier workaround that turned off
only the first three never actually stopped the substitution.

---

## In-game map picker

Press **backtick** (or the **Xbox Guide** button) while skating to switch maps
without going back to the library.

Every entry is tagged **RESTARTS**, because switching relaunches the game — the
installed DLC set is fixed at boot. Expect ~27 s. The launcher stays alive
across the restart and covers it with the loading screen.

Keys: d-pad/stick to move, **A** to pick, **B** to cancel. Moving the mouse
takes over the selection; leaving it parked over the list doesn't.

---

## Which maps actually work

Cards are labelled from real test runs, not guesswork:

- **normal card** — verified to load
- **CONFIRMED** — additionally matched against a reference screenshot
- **WON'T LOAD** — tested, and it hangs or never renders

Some community packs simply don't load in this build. They're labelled so you
don't click into a hang. Current catalog: **41 of 54 maps playable.**

40 were verified together on 2026-08-17 — 120 runs, every map reproducible
across its runs and distinct from every other, zero failures. Both thresholds
cleared by more than 20x. See [Reliability](docs/reliability.md) for the numbers,
the two flakes that turned up, and the retry rules they produced.

If a boot flakes, the launcher retries it once behind the loading screen rather
than showing you an error — but only for maps already proven to load, so a
known-broken one still fails fast.

---

## Verifying maps yourself

No log line can tell you which map you actually landed in — the boot warp
redirects the stream, so the log names the requested world whatever the menu
really did. The only ground truth is a screenshot.

```bash
python3 scripts/verifyspot.py <world-id>            # boot one map, photograph it
python3 scripts/verifyspot.py <pack-id>:<index>     # when a world id is ambiguous
python3 scripts/sweep.py --status boots             # the maps that should work (~2.2h)
python3 scripts/sweep.py                            # every map, 3 runs, contact sheet
python3 scripts/sweep.py --only skate-it.13 --runs 3
python3 scripts/sweep.py --report                   # re-judge existing shots, boots nothing
```

`--status boots` is the regression gate: it covers exactly the maps recorded as
working, and exits 0 only if every one was reproducible and distinct. A map
recorded as `stalls` scores `EXPECTED FAIL` instead of failing the run, so
keeping a broken pack imported doesn't make the suite permanently red.

**A `stalls` map that renders is not automatically recovered.** `verifyspot` now
photographs on timeout instead of reporting "never rendered" with no picture, so
a map whose macro confirmed nothing still yields a shot — of the stock world it
was left in. Eleven such maps came back "rendered" and all eleven were within
distance 2–21 of each other: one place, not eleven. Recovery has to clear the
distinctness bar too, which is what the `STILL STUCK` verdict is for.

`sweep.py` writes `work/sweep/sweep.md`, `sweep.json` and a labelled
`contact.png`. It judges three ways:

- **reproducible** — a map's runs agree with each other (needs no reference)
- **distinct** — its shots are far from every other map's (needs no reference)
- **MATCH** — matches a confirmed reference in `references/`

To promote a shot to a reference once you've eyeballed the contact sheet:

```bash
cp work/sweep/<pack>.<index>__s1.png references/<pack>.<index>.png
```

Exit codes: `0` match, `2` nothing rendered, `3` wrong place, `4` no reference.

### Offline tests

These need no game and run in seconds:

```bash
python3 scripts/classifier_test.py       # the retry rule, against every real log
python3 scripts/gui_failure_test.py      # the launcher's retry and failure paths
python3 scripts/display_test.py          # the capture guard writes what it claims
python3 scripts/switch_request_test.py   # the in-game picker's relaunch request
python3 scripts/sweep_merge_test.py      # a --only re-run keeps the other results
```

---

## Layout

```
loader/           the launcher
  app.py          GTK application and window flow
  session.py      launch -> watch -> hand over -> come back
  launch.py       staging (delegates to freeskate) and process control
  catalog.py      packs and maps on disk, and their verified status
  navigate.py     the pad macro that reaches a map
  spotcheck.py    judging a screenshot
  fingerprint.py  the perceptual hash, chosen by measurement
  display.py      keeps the desktop in a state where capture works
scripts/          tools: verifyspot, sweep, makeheader, capture, hashcheck, ...
catalog/          one JSON per imported pack
references/       confirmed screenshots, and known-bad ones in _wrong/
work/             test output (gitignored)
```

---

## Requirements

- Linux, Python 3.11+, PyGObject (GTK 3), Pillow
- [freeskate](../freeskate) for staging
- A built `skate3recomp` binary
- `xprop` for window capture; `unrar`/`7z` if you're extracting pack archives

`./skate3loader doctor` checks all of it.

---

## Gotchas

**A locked screen makes every screenshot black.** An unattended sweep has no
input, the session locks, and captures come back pure black while the logs look
perfectly healthy. `loader/display.py` handles this automatically; if you see
inexplicable black shots, that's the first thing to check.

**Never run two sweeps at once.** Each run kills any live game, so concurrent
batches silently truncate each other.

**Leaked shared memory hangs the next start.** A killed game leaves a 4.5 GiB
`/dev/shm/xenia_memory_*` segment. The loader cleans these up; by hand it's
`rm -f /dev/shm/xenia_memory_*`.

---

## Engine changes

The loader depends on patches in a `skate3recomp` fork, mainly:

- the in-engine loading overlay and level picker (`skate3_loader_overlay.cpp`)
- warp/selection support (`skate3_warp.cpp`)
- boot automation in `skate3_demo_path.cpp`
- a synthetic auto-tap fix in the SDK, so the title-screen `START` press isn't
  clobbered by the dialog `A` press

Build with `cmake --build out/build/linux-release --target skate3` — that
compiles the SDK change and deploys `librexruntime.so` in one step.
