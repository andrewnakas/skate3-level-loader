#!/usr/bin/env python3
"""Search the running game's guest memory for bytes, and classify what it finds.

Used to answer "where does the boot world name come from": if the world the game
loads at startup is data-driven, the name or an id for it must be sitting in a
readable global before the load happens.

    python3 scripts/memfind.py "DIST_University"
    python3 scripts/memfind.py --hex 44495354 --range 82000000-84000000
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loader import guestmem  # noqa: E402

# Guest address space regions worth naming in output.
REGIONS = [
    (0x82000000, 0x83000000, "xex code"),
    (0x83000000, 0x84000000, "xex data/globals"),
    (0x40000000, 0x80000000, "heap"),
    (0x00000000, 0x40000000, "low heap"),
]

CHUNK = 8 << 20


def region_of(address: int) -> str:
    for lo, hi, name in REGIONS:
        if lo <= address < hi:
            return name
    return "?"


def search(memory: guestmem.GuestMemory, needle: bytes, lo: int, hi: int, limit: int):
    hits = []
    offset = lo
    overlap = len(needle)
    while offset < hi and len(hits) < limit:
        length = min(CHUNK, hi - offset)
        block = memory.read(offset, length)
        if not block:
            offset += length
            continue
        start = 0
        while True:
            found = block.find(needle, start)
            if found < 0:
                break
            hits.append(offset + found)
            start = found + 1
            if len(hits) >= limit:
                break
        offset += max(1, length - overlap)
    return hits


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("needle", nargs="?", help="ASCII string to find")
    parser.add_argument("--hex", dest="hexbytes", help="hex byte string instead")
    parser.add_argument("--utf16", action="store_true", help="search UTF-16BE too")
    parser.add_argument("--range", dest="rng", default="82000000-84000000")
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument("--context", type=int, default=0, help="bytes of context to dump")
    args = parser.parse_args()

    if args.hexbytes:
        needle = bytes.fromhex(args.hexbytes)
    elif args.needle:
        needle = args.needle.encode("ascii")
    else:
        parser.error("give a string or --hex")

    lo_s, _, hi_s = args.rng.partition("-")
    lo, hi = int(lo_s, 16), int(hi_s, 16)

    try:
        memory = guestmem.GuestMemory()
    except guestmem.NotRunning as exc:
        print(exc, file=sys.stderr)
        return 1

    with memory:
        print(f"segment {memory.path}")
        print(f"searching {lo:08X}-{hi:08X} for {needle!r}")
        hits = search(memory, needle, lo, hi, args.limit)
        for address in hits:
            line = f"  {address:08X}  [{region_of(address)}]"
            if args.context:
                raw = memory.read(address - 16, args.context + 16)
                printable = "".join(
                    chr(b) if 0x20 <= b < 0x7F else "." for b in raw
                )
                line += f"  {printable}"
            print(line)
        print(f"{len(hits)} hit(s)")

        if args.utf16 and args.needle:
            wide = args.needle.encode("utf-16-be")
            wide_hits = search(memory, wide, lo, hi, args.limit)
            print(f"utf-16be: {len(wide_hits)} hit(s)")
            for address in wide_hits:
                print(f"  {address:08X}  [{region_of(address)}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
