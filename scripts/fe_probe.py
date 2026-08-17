#!/usr/bin/env python3
"""Live frontend probe: read the running game's menu state out of its guest memory.

Modes
  attach   print the screen stack once, to prove the mapping is right
  watch    stream screen-stack changes as they happen
  hunt     find the Locations selection cursor by diffing memory while the
           macro walks the list

`hunt` is the interesting one. The in-engine fe-debug can only diff the first
2 KiB of the frontend manager, and the Locations cursor is known not to live
there. From outside we have no such limit: we walk pointers out of the manager,
snapshot every region they reach, and look for a word that steps by exactly +/-1
each time the cursor moves -- which is what a selection index looks like.
"""

import argparse
import sys
import time
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loader import guestmem  # noqa: E402

# How much of each discovered object to snapshot, in 32-bit words.
REGION_WORDS = 256
# Pointer-following depth out of the frontend manager.
MAX_DEPTH = 3
MAX_REGIONS = 400


def describe_stack(stack) -> str:
    if not stack:
        return "(no stack)"
    return " -> ".join(f"{e.screen_id}:{e.name}" for e in stack)


def cmd_attach(memory: guestmem.GuestMemory) -> int:
    manager = memory.frontend_manager()
    print(f"segment : {memory.path}")
    print(f"manager : {manager:08X}" if manager else "manager : <not resolved>")
    if manager is None:
        print("The frontend manager pointer is null - the game may still be booting.")
        return 1
    stack = memory.screen_stack()
    print(f"stack   : {describe_stack(stack)}")
    for entry in stack:
        words = " ".join(f"{w:08X}" for w in entry.words)
        print(f"          {entry.screen_id:>4} {entry.name:<16} {{{words}}}")
    return 0


def cmd_watch(memory: guestmem.GuestMemory, seconds: float) -> int:
    start = time.monotonic()
    last = None
    while time.monotonic() - start < seconds:
        stack = memory.screen_stack()
        signature = tuple(e.screen_id for e in stack)
        if signature != last:
            elapsed = time.monotonic() - start
            print(f"[{elapsed:7.2f}s] {describe_stack(stack)}")
            last = signature
        time.sleep(0.1)
    return 0


def collect_regions(memory: guestmem.GuestMemory, root: int) -> list[int]:
    """Breadth-first walk of guest pointers reachable from the manager."""
    seen = {root}
    order = [root]
    queue = deque([(root, 0)])
    while queue and len(order) < MAX_REGIONS:
        address, depth = queue.popleft()
        if depth >= MAX_DEPTH:
            continue
        for word in memory.words(address, REGION_WORDS):
            if not guestmem.looks_like_guest_pointer(word):
                continue
            # Align: object pointers are 4-byte aligned in practice.
            if word % 4:
                continue
            if word in seen:
                continue
            if memory.read(word, 4) == b"":
                continue
            seen.add(word)
            order.append(word)
            queue.append((word, depth + 1))
            if len(order) >= MAX_REGIONS:
                break
    return order


def cmd_hunt(memory: guestmem.GuestMemory, seconds: float, quiet: float) -> int:
    manager = memory.frontend_manager()
    if manager is None:
        print("frontend manager not resolved; is the game past the boot screens?")
        return 1

    print(f"segment : {memory.path}")
    print(f"manager : {manager:08X}")
    print(f"stack   : {describe_stack(memory.screen_stack())}")
    print("walking pointers out of the manager...")
    regions = collect_regions(memory, manager)
    print(f"tracking {len(regions)} regions x {REGION_WORDS} words")
    print()
    print("Now let the macro step the cursor. Watching for words that move by +/-1.")
    print()

    baseline = {addr: memory.words(addr, REGION_WORDS) for addr in regions}
    # address -> list of (elapsed, old, new)
    steps: dict[tuple[int, int], list[tuple[float, int, int]]] = {}
    # Words that change constantly are counters, not cursors.
    churn: dict[tuple[int, int], int] = {}

    start = time.monotonic()
    while time.monotonic() - start < seconds:
        time.sleep(quiet)
        elapsed = time.monotonic() - start
        for addr in regions:
            current = memory.words(addr, REGION_WORDS)
            if not current:
                continue
            previous = baseline.get(addr)
            if not previous:
                baseline[addr] = current
                continue
            for index, old, new in guestmem.diff_words(previous, current):
                key = (addr, index)
                churn[key] = churn.get(key, 0) + 1
                if abs(new - old) == 1 and new < 256 and old < 256:
                    steps.setdefault(key, []).append((elapsed, old, new))
            baseline[addr] = current

    print("candidates (stepped by exactly 1, low value, low churn):")
    ranked = []
    for key, hits in steps.items():
        noise = churn.get(key, 0)
        # A cursor changes ONLY when it moves, so hits should be most of its churn.
        if noise > len(hits) * 3:
            continue
        ranked.append((len(hits), key, hits, noise))
    ranked.sort(reverse=True)

    if not ranked:
        print("  none found - try a longer run, or more cursor movement")
        return 2

    for count, (addr, index), hits, noise in ranked[:12]:
        trail = " ".join(f"{old}->{new}" for _, old, new in hits[:10])
        offset = addr - manager
        where = f"mgr+0x{offset:X}" if 0 <= offset < 0x10000 else f"{addr:08X}"
        print(f"  {where}+0x{index * 4:03X}  steps={count:<3} churn={noise:<3} {trail}")

    # Absolute heap addresses differ every run, so the useful artefact is the
    # pointer PATH from the manager - that is what stays valid next boot.
    print()
    best_addr, best_index = ranked[0][1]
    path = find_pointer_path(memory, manager, best_addr)
    if path:
        chain = " -> ".join(f"+0x{off:X}" for off in path)
        print(f"pointer path to the cursor object: mgr {chain} +0x{best_index * 4:X}")
    else:
        print("could not re-derive a pointer path to the cursor object")

    print()
    print(f"context around the cursor object {best_addr:08X}:")
    dump_context(memory, best_addr, best_index)
    return 0


def find_pointer_path(
    memory: guestmem.GuestMemory, root: int, target: int, max_depth: int = MAX_DEPTH
) -> list[int] | None:
    """Shortest chain of pointer offsets leading from the manager to an object."""
    queue = deque([(root, [])])
    seen = {root}
    while queue:
        address, path = queue.popleft()
        if len(path) >= max_depth:
            continue
        for index, word in enumerate(memory.words(address, REGION_WORDS)):
            if word == target:
                return path + [index * 4]
            if not guestmem.looks_like_guest_pointer(word) or word % 4 or word in seen:
                continue
            seen.add(word)
            queue.append((word, path + [index * 4]))
    return None


def dump_context(memory: guestmem.GuestMemory, address: int, cursor_index: int) -> None:
    """Show words near the cursor, resolving anything that points at a string.

    A selection index usually sits beside the thing it indexes: a count, and a
    pointer to an array of entries. Naming those is what lets the loader say
    "the cursor is on Barcelona" instead of "the cursor is on index 3".
    """
    base = address
    words = memory.words(base, REGION_WORDS)
    if not words:
        print("  (unreadable)")
        return
    for index, word in enumerate(words):
        if index < cursor_index - 8 or index > cursor_index + 12:
            continue
        marker = "  <- cursor" if index == cursor_index else ""
        text = ""
        if guestmem.looks_like_guest_pointer(word):
            ascii_text = memory.cstring(word, 64)
            wide_text = memory.utf16(word, 64)
            found = ascii_text or wide_text
            if found and len(found) > 2:
                text = f'  "{found}"'
        print(f"  +0x{index * 4:03X}  {word:08X}{text}{marker}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["attach", "watch", "hunt"], default="attach", nargs="?")
    parser.add_argument("--seconds", type=float, default=60.0)
    parser.add_argument("--interval", type=float, default=0.25)
    args = parser.parse_args()

    try:
        memory = guestmem.GuestMemory()
    except guestmem.NotRunning as exc:
        print(exc, file=sys.stderr)
        return 1

    with memory:
        if args.mode == "attach":
            return cmd_attach(memory)
        if args.mode == "watch":
            return cmd_watch(memory, args.seconds)
        return cmd_hunt(memory, args.seconds, args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
