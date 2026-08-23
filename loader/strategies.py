"""How a launch is driven: the knobs, and the experiments behind them.

The default path is the only one that is verified end to end. Everything else
here is an experiment kept switchable rather than commented out, because the
useful thing about a failed approach is being able to re-run it later on a
different map, a different machine, or a fixed engine.

Each entry says what it does, whether it is known to work, and what it costs.
The library's Advanced panel renders this list; nothing else needs to know the
options exist.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Strategy:
    id: str
    name: str
    detail: str
    #: Engine cvars this adds to the launch.
    cvars: dict[str, str] = field(default_factory=dict)
    #: Verified working, or an experiment the user is opting into.
    verified: bool = False


#: How the game gets from its title screen into a map.
BOOT_STRATEGIES: list[Strategy] = [
    Strategy(
        id="macro",
        name="Standard",
        detail=(
            "Boot to gameplay, then drive the pause menu with a timed pad macro. "
            "The only path proven to land the right map - 120 sweep runs, and every "
            "timing in it screenshot-verified. About 25 s to playable."
        ),
        verified=True,
    ),
    Strategy(
        id="direct",
        name="Direct boot (experimental)",
        detail=(
            "Skip the frontend states - language select, press start, the autosave "
            "notice - by binding the profile directly, then drive the same menu macro. "
            "Ported from the SK8-Engine fork. Worth about 2.5 s; unproven here and "
            "unproven there (they ship it off by default)."
        ),
        cvars={"skate3_direct_boot": "true"},
    ),
    Strategy(
        id="engine_nav",
        name="Engine navigation (known broken)",
        detail=(
            "Let the engine walk the menu itself using cursor feedback instead of a "
            "macro. Kept only so the fix can be tested: the cursor it steers by never "
            "moves, so it stops on a stock district and loads Port Carverton. "
            "Measured 2026-08-23 at distance 404-443 from the target map."
        ),
        cvars={"skate3_loader_boot_index": "0"},
    ),
]

#: Diagnostics. None of these change where you land; they change what is logged.
DIAGNOSTICS: list[Strategy] = [
    Strategy(
        id="dlc_trace",
        name="Trace DLC content loading",
        detail=(
            "Log every call through the guest's content path: the content manager, the "
            "DLC driver's mount, each archive added by path, and each async file open. "
            "This is the tool for a pack that never mounts - it names the file the "
            "guest is waiting on. Loud; leave it off for normal play."
        ),
        cvars={"skate3_dlc_trace": "true"},
    ),
    Strategy(
        id="probe",
        name="Log frontend state transitions",
        detail=(
            "Record every frontend screen push and pop. What to turn on when the macro "
            "lands somewhere unexpected and you need to see which screen it confirmed."
        ),
        cvars={"skate3_demo_path_probe": "true"},
    ),
]

#: Menu timings. The defaults are measured; see loader/navigate.py for the
#: evidence behind each one. Raising them is the first thing to try if a slow
#: machine lands in the wrong place.
TIMINGS = [
    ("SKATE3LOADER_TAB_DELAY_MS", "Tab press wait", 300, "ms between the presses that reach the Locations tab"),
    ("SKATE3LOADER_SETTLE_MS", "Settle before input", 800, "ms to wait after gameplay starts before the first press"),
    ("SKATE3LOADER_CLAMP_DELAY_MS", "Row step wait", 260, "ms between the presses that walk down the list"),
    ("SKATE3LOADER_CONFIRM_MS", "Confirm wait", 600, "ms after each confirm press"),
]


def by_id(strategies: list[Strategy], wanted: str) -> Strategy | None:
    return next((s for s in strategies if s.id == wanted), None)


def cvars_for(boot_id: str, diagnostics: list[str]) -> dict[str, str]:
    """Every engine cvar implied by the current selection."""
    cvars: dict[str, str] = {}
    boot = by_id(BOOT_STRATEGIES, boot_id)
    if boot:
        cvars.update(boot.cvars)
    for entry in DIAGNOSTICS:
        if entry.id in diagnostics:
            cvars.update(entry.cvars)
    return cvars
