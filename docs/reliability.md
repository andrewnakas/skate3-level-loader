# Reliability: every working map, measured

**40 of 40 playable maps passed, 120 runs, 2026-08-17.** This is the first clean
full pass over the current catalog — the three earlier clean sweeps covered the
original 37 maps, before the Meebs pack was imported.

> ## Correction, same day: "14 broken maps" was wrong
>
> Hastings Bowl is not broken. It now loads unattended in ~62 s, three runs,
> spread 5 and nearest other map 267 — the same standard as every map below.
> **The catalog is 41 playable, 13 still stuck.**
>
> **Scope, measured afterwards: one of the fourteen was ours, not fourteen.**
> Olay Holes and The Alley (Meebs) were then driven by hand with no automation
> and both hang mid-load — Olay Holes for 67 s, The Alley for six minutes, one
> takeover, renderer free-spinning at ~940 FPS with nothing to draw. Their maps
> ARE listed and selectable; loading them is what hangs. So most of the list
> looks genuinely broken, and the value of the two fixes below is that they
> stopped the list being unfalsifiable — not that they refuted it wholesale.
>
> It was found by booting the pack with no automation at all and walking the
> menus by hand. Two faults in our own tooling had hidden it:
>
> 1. **The boot warp loads worlds without rendering them** — the same
>    zero-draw-record failure as the unsolved direct warp (`records=0 views=0`).
>    Any map routed through it looks dead. Turning all five
>    `skate3_warp_substitute_*` switches off per pack, and letting the macro
>    navigate, is what fixed Hastings Bowl.
> 2. **`verifyspot` could not see a successful load.** It waited for a takeover
>    line after the macro and, on timeout, took *no picture* and reported
>    "never rendered" — printing exactly that while a person was skating the
>    map. The whole classification rested on that exit code.
>
> The `EXPECTED FAIL` verdict described below made it worse: it turned a wrong
> classification into a passing test. A gate that excuses the cases it cannot
> explain is not a gate. Both are fixed — `verifyspot` now photographs on
> timeout, and a `stalls` map that renders must still clear the distinctness
> bar (`STILL STUCK`) before it counts as recovered.
>
> **Do not read the 13 as settled.** Ten of them were retested with the warp off
> and all ten rendered the *stock world*, colliding with each other at distance
> 2–21. They reach gameplay and never confirm their map. The five single-map
> packs are now configured identically to Hastings Bowl and still fail, so the
> difference is in the pack, not the navigation — boot one with
> `./skate3loader manual <pack>` and check whether its map is listed at all.

| | |
|---|---|
| loader | `3cf012b` + the changes described below |
| engine | `skate3recomp-dev` `14371ab`, binary built 2026-08-16 |
| command | `python3 scripts/sweep.py --status boots --runs 3` |
| wall time | 2.2 h (120 runs, median 64.8 s, range 32.7–85.4 s) |
| result | `MATCH=3  OK*=37`, exit 0 |

Reproduce with `python3 scripts/sweep.py --report`, which re-judges the shots on
disk and boots nothing.

## What passing means

No log line can tell you which map you actually landed in — the boot warp
redirects the stream, so the log names the requested world whatever the menu
really did (see `loader/spotcheck.py`). The verdict is therefore taken from
pictures, three ways:

- **reproducible** — a map's three runs agree with each other. Threshold 75;
  **measured 1–7**.
- **distinct** — its shots are far from every other map's. Two maps landing
  together is the signature of the menu confirming the wrong row. Threshold 75;
  **measured 165–401**.
- **MATCH** — matches a human-confirmed reference. Only three references exist
  (Maloof, Rio, Spillway); all three matched, at distances 4–12.

Both thresholds cleared by more than 20x. Zero `NO SHOT`, `FLAKY`, `COLLIDES`
or `WRONG`.

Three maps carry an advisory `HUD?` note — `san-vanelona.14`, `san-vanelona.15`,
`skate-it.6`. That detector looks for glyph-like white in the lower left and has
a known false-positive rate on bright pavement and signage; it is not part of
the verdict. Worth one glance at `work/sweep/contact.png`.

The 14 maps recorded as `stalls` were not run. They now score `EXPECTED FAIL`
rather than dragging the suite red, which is what makes this command usable as a
regression gate at all.

## Run-level reliability

**118 of 120 runs produced a usable shot on the first attempt (98.3%).** Two
did not, and both were on maps whose other two runs were fine — neither changed
a verdict. They are worth recording because they are different failures with
different fixes:

| map | run | what happened |
|---|---|---|
| `official-maloof.0` | 3 | the macro completed and the world never followed (39.8 s) |
| `san-vanelona.5` | 3 | the world came up, then the game stopped logging and the capture got 0 of 4 frames |

Neither was retried, because the retry rule at the time asked only whether the
log contained a takeover *anywhere* — and both logs did. Both rules have since
been sharpened; see below.

Re-running these two with the new rules in place gave 3/3 clean on each
(Maloof `MATCH` spread 7; Elementary School `OK*` spread 5). **The new
capture-failure retry fired during that re-run** — Elementary School's first
run hit the same "rendered but captured no frames" condition, was retried
automatically, and passed. Under the old code that would have been another
recorded failure.

**This 1.7% is higher than the ~0.3% (1 in 333) recorded by earlier sweeps, and
the machine was not idle**: 23 Chrome processes were running throughout, which
the earlier overnight runs did not have. Treat 1.7% as an upper bound measured
under contention rather than a regression.

## The retry rules, and how they were chosen

The sweep harness has always retried a run that never rendered; the launcher did
not, so the same engine flake the harness absorbed reached the user as an error
dialog. Both now share one rule, `logwatch.transient_boot_failure`.

**A boot counts as transient when no takeover follows the macro.** Not "no
takeover at all" — the boot warp draws a world on its way to the menu, so nearly
every run logs one before anything is selected. Measured over 141 real run logs:

| | post-macro takeover |
|---|---|
| `boots` maps | 118 of 122 |
| `stalls` maps | 0 of 19 |

The four exceptions are why the rule has a second branch. Three are Danny Way,
which boots straight into its own world and gets no macro at all — there is no
sequence-complete line to be after, so its single takeover is the real one. The
fourth was the Maloof flake above.

**A run that rendered but captured nothing is retried too.** That is a
measurement failure, not a verdict about the map: there is no evidence either
way. This is the `san-vanelona.5` case.

Both are bounded twice, because a genuinely broken map looks identical in the
log to a working map that flaked:

- the sweep retries once and skips maps recorded as `stalls`
- the launcher additionally requires `status == "boots"`, so a known-broken map
  still fails fast instead of costing a second 200-second wait

## A capture guard that failed open

`display.disable_idle_blanking` compared settings with a substring test, and
`"0"` is a substring of `"uint32 3600"`. On a machine with the GNOME default of
one hour it concluded both idle timeouts were already disabled, wrote nothing,
and reported success — so the session would lock 60 minutes into an unattended
sweep and every capture after that would be pure black while the logs stayed
perfectly healthy. That is the failure that ate most of an earlier sweep.

Caught by checking the settings by hand after this sweep had already started,
which is not a thing to rely on twice. Fixed, and pinned by
`scripts/display_test.py`. `./skate3loader doctor` now reports the setting.

## A `--only` re-run destroyed the results it merged into

Re-running two maps reduced this very 40-map result set to 2 rows, and printed
the 2-row table as if it were the whole answer. The merge meant to prevent that
had been there for some time and never worked: the run loop checkpoints
`sweep.json` after every map, and the merge then read that same file back as
"the previous results" — so it only ever saw what the current run had just
written. Fixed by reading the previous results before the loop starts, and
pinned by `scripts/sweep_merge_test.py`.

This is the third time the symptom has been recorded. Keep archiving
`sweep.json` before a `--only` re-run until there is more mileage on the fix;
this run's full result survived only because it had been copied aside first.

## Offline tests

None of these need a game or a GPU; all five run in seconds — 103 checks.

| script | checks | what it protects |
|---|---|---|
| `scripts/classifier_test.py` | 63 | the retry rule, against every real log in `work/spots/` |
| `scripts/gui_failure_test.py` | 15 | the launcher's retry and failure paths |
| `scripts/display_test.py` | 13 | the capture guard actually writes what it claims |
| `scripts/switch_request_test.py` | 7 | the in-game picker's relaunch request |
| `scripts/sweep_merge_test.py` | 5 | a `--only` re-run keeps every other map's result |

`classifier_test.py` asserts two properties rather than per-map outcomes: a
broken map is never called loadable, and a working map is never called
permanently broken. Flakes in between are reported by name, so a new one shows
up as a new name instead of a red suite.
