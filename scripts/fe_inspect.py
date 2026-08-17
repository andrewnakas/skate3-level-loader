#!/usr/bin/env python3
"""Read the Locations screen's selection state by name, live.

The cursor was located by `fe_probe hunt`: it sits at

    frontend manager -> +0x208 -> +0x218

with what look like a count at +0x210 and an entry array pointer at +0x214.
This script follows that path, tries to resolve the entries to readable names,
and streams the current selection while the macro moves it.
"""

import argparse
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loader import guestmem  # noqa: E402

CURSOR_OBJECT_OFFSET = 0x208
COUNT_OFFSET = 0x210
ARRAY_OFFSET = 0x214
CURSOR_OFFSET = 0x218


def resolve_cursor_object(memory: guestmem.GuestMemory) -> int | None:
    manager = memory.frontend_manager()
    if manager is None:
        return None
    obj = memory.u32(manager + CURSOR_OBJECT_OFFSET)
    if obj and guestmem.looks_like_guest_pointer(obj):
        return obj
    return None


def any_string(memory: guestmem.GuestMemory, address: int, limit: int = 96) -> str | None:
    """Read a string at a guest address, trying both encodings the title uses."""
    if not guestmem.looks_like_guest_pointer(address):
        return None
    raw = memory.read(address, limit)
    if not raw:
        return None
    # ASCII, NUL-terminated.
    end = raw.find(b"\x00")
    if end > 2:
        try:
            text = raw[:end].decode("ascii")
            if text.isprintable():
                return text
        except UnicodeDecodeError:
            pass
    # UTF-16BE, NUL-terminated.
    out = []
    for i in range(0, len(raw) - 1, 2):
        code = struct.unpack_from(">H", raw, i)[0]
        if code == 0:
            break
        if code < 0x20 or code > 0x7E:
            out = []
            break
        out.append(chr(code))
    if len(out) > 2:
        return "".join(out)
    return None


def scan_for_strings(memory: guestmem.GuestMemory, base: int, words: int = 512):
    """Every word in a region that points at readable text."""
    found = []
    for index, word in enumerate(memory.words(base, words)):
        text = any_string(memory, word)
        if text and len(text) >= 3:
            found.append((index * 4, word, text))
    return found


def cmd_dump(memory: guestmem.GuestMemory) -> int:
    obj = resolve_cursor_object(memory)
    if obj is None:
        print("cursor object not resolved (is the Locations screen open?)")
        return 1

    count = memory.u32(obj + COUNT_OFFSET)
    array = memory.u32(obj + ARRAY_OFFSET)
    cursor = memory.u32(obj + CURSOR_OFFSET)
    print(f"cursor object : {obj:08X}")
    print(f"  +0x210 count?: {count:08X} ({count})")
    print(f"  +0x214 array?: {array:08X}")
    print(f"  +0x218 cursor: {cursor}")
    print()

    if array and guestmem.looks_like_guest_pointer(array):
        print(f"strings reachable from the array at {array:08X}:")
        for offset, pointer, text in scan_for_strings(memory, array)[:40]:
            print(f"  +0x{offset:04X}  {pointer:08X}  {text!r}")
        print()

    print(f"strings reachable from the cursor object {obj:08X}:")
    for offset, pointer, text in scan_for_strings(memory, obj)[:40]:
        mark = "  <-- at cursor offset" if offset == CURSOR_OFFSET else ""
        print(f"  +0x{offset:04X}  {pointer:08X}  {text!r}{mark}")
    return 0


def cmd_follow(memory: guestmem.GuestMemory, seconds: float) -> int:
    """Stream the selection index (and any name we can pin to it) as it moves."""
    obj = resolve_cursor_object(memory)
    if obj is None:
        print("cursor object not resolved")
        return 1

    start = time.monotonic()
    last = None
    while time.monotonic() - start < seconds:
        cursor = memory.u32(obj + CURSOR_OFFSET)
        stack = memory.screen_stack()
        signature = (cursor, tuple(e.screen_id for e in stack))
        if signature != last:
            screens = " -> ".join(str(e.screen_id) for e in stack)
            print(f"[{time.monotonic() - start:6.2f}s] cursor={cursor}  stack=[{screens}]")
            last = signature
        time.sleep(0.1)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["dump", "follow"], nargs="?", default="dump")
    parser.add_argument("--seconds", type=float, default=60.0)
    args = parser.parse_args()

    try:
        memory = guestmem.GuestMemory()
    except guestmem.NotRunning as exc:
        print(exc, file=sys.stderr)
        return 1
    with memory:
        return cmd_dump(memory) if args.mode == "dump" else cmd_follow(memory, args.seconds)


if __name__ == "__main__":
    raise SystemExit(main())
