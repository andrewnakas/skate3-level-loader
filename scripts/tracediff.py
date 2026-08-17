#!/usr/bin/env python3
"""Read guest call traces written by --skate3_trace and compare them.

The engine writes `<log_file>.trace`: one line per recorded guest function
entry, with the arguments it was called with. Two traces of the same event with
one thing changed (Barcelona vs Spillway, boot vs menu teleport) answer two
questions that a single trace cannot:

    which functions ran in one and not the other      -> the code path
    which shared function was called with a different -> the argument that
    argument                                             carries the identity

    python3 scripts/tracediff.py summary a.trace
    python3 scripts/tracediff.py diff a.trace b.trace
    python3 scripts/tracediff.py args a.trace b.trace --thread main
    python3 scripts/tracediff.py tail a.trace -n 60
"""

import argparse
import sys
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Entry:
    seq: int
    dcycles: int
    thread: str
    fn: str
    r3: int
    r4: int
    r5: int
    # Any argument that pointed at a printable string, resolved by the engine at
    # record time. This is where a world name shows up.
    text: str = ""

    @property
    def args(self) -> tuple[int, int, int]:
        return (self.r3, self.r4, self.r5)

    def __str__(self) -> str:
        out = (
            f"{self.seq:>8} {self.dcycles:>14} {self.thread:<18} {self.fn} "
            f"r3={self.r3:08X} r4={self.r4:08X} r5={self.r5:08X}"
        )
        return f"{out}  {self.text}" if self.text else out


def load(path: Path, thread: str | None = None) -> list[Entry]:
    entries: list[Entry] = []
    header = ""
    for line in path.read_text(errors="replace").splitlines():
        if line.startswith("#"):
            if not header:
                header = line
            continue
        parts = line.split("\t")
        # 7 columns is the original format, 8 adds the resolved-string column.
        if len(parts) not in (7, 8):
            continue
        if thread and thread not in parts[2]:
            continue
        entries.append(
            Entry(
                seq=int(parts[0]),
                dcycles=int(parts[1]),
                thread=parts[2],
                fn=parts[3],
                r3=int(parts[4], 16),
                r4=int(parts[5], 16),
                r5=int(parts[6], 16),
                text=parts[7] if len(parts) == 8 else "",
            )
        )
    if header:
        print(f"{path.name}: {header.lstrip('# ')}", file=sys.stderr)
    return entries


def first_by_fn(entries: list[Entry]) -> "OrderedDict[str, Entry]":
    """First occurrence of each function, in arrival order."""
    out: OrderedDict[str, Entry] = OrderedDict()
    for e in entries:
        out.setdefault(e.fn, e)
    return out


def cmd_summary(args) -> int:
    entries = load(Path(args.trace), args.thread)
    if not entries:
        print("no entries")
        return 1
    threads: dict[str, int] = {}
    for e in entries:
        threads[e.thread] = threads.get(e.thread, 0) + 1
    span = entries[-1].dcycles - entries[0].dcycles
    print(f"{len(entries)} entries, {len(first_by_fn(entries))} distinct functions")
    print(f"cycle span {span} (~{span / 3.5e9:.2f}s at 3.5 GHz)")
    print("threads:")
    for name, count in sorted(threads.items(), key=lambda kv: -kv[1]):
        print(f"  {count:>8}  {name}")
    return 0


def cmd_diff(args) -> int:
    a = first_by_fn(load(Path(args.a), args.thread))
    b = first_by_fn(load(Path(args.b), args.thread))
    only_a = [fn for fn in a if fn not in b]
    only_b = [fn for fn in b if fn not in a]
    print(f"== only in {Path(args.a).name} ({len(only_a)}) ==")
    for fn in only_a[: args.limit]:
        print(f"  {a[fn]}")
    print(f"== only in {Path(args.b).name} ({len(only_b)}) ==")
    for fn in only_b[: args.limit]:
        print(f"  {b[fn]}")
    print(f"== in both: {len(set(a) & set(b))} ==")
    return 0


def cmd_args(args) -> int:
    """Functions both traces called, with different arguments.

    This is the one that names the world: the code path is the same either way,
    so whatever tells the two runs apart is in a register.
    """
    a = first_by_fn(load(Path(args.a), args.thread))
    b = first_by_fn(load(Path(args.b), args.thread))
    shown = 0
    for fn, ea in a.items():
        eb = b.get(fn)
        if eb is None or ea.args == eb.args:
            continue
        which = [
            name
            for name, x, y in (("r3", ea.r3, eb.r3), ("r4", ea.r4, eb.r4), ("r5", ea.r5, eb.r5))
            if x != y
        ]
        print(
            f"{fn}  differs in {','.join(which)}\n"
            f"    A seq={ea.seq:<8} r3={ea.r3:08X} r4={ea.r4:08X} r5={ea.r5:08X}\n"
            f"    B seq={eb.seq:<8} r3={eb.r3:08X} r4={eb.r4:08X} r5={eb.r5:08X}"
        )
        shown += 1
        if shown >= args.limit:
            print(f"... (limit {args.limit})")
            break
    if shown == 0:
        print("no shared function was called with different arguments")
    return 0


def cmd_tail(args) -> int:
    entries = load(Path(args.trace), args.thread)
    for e in entries[-args.n :]:
        print(e)
    return 0


def cmd_strings(args) -> int:
    """Every entry whose arguments pointed at text.

    The shortcut to the world loader: search this for the world id and the
    function that received it is named on the same line.
    """
    entries = load(Path(args.trace), args.thread)
    needle = args.match.lower() if args.match else None
    for e in entries:
        if not e.text:
            continue
        if needle and needle not in e.text.lower():
            continue
        print(e)
    return 0


def cmd_grep(args) -> int:
    entries = load(Path(args.trace), args.thread)
    needle = args.pattern.lower()
    for i, e in enumerate(entries):
        if needle in e.fn.lower() or needle in e.text.lower() or needle in f"{e.r3:08x}{e.r4:08x}{e.r5:08x}":
            lo = max(0, i - args.context)
            hi = min(len(entries), i + args.context + 1)
            for j in range(lo, hi):
                print(("->" if j == i else "  ") + str(entries[j]))
            print()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--thread", help="only entries whose thread label contains this")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("summary")
    p.add_argument("trace")
    p.set_defaults(func=cmd_summary)

    p = sub.add_parser("diff")
    p.add_argument("a")
    p.add_argument("b")
    p.add_argument("--limit", type=int, default=80)
    p.set_defaults(func=cmd_diff)

    p = sub.add_parser("args")
    p.add_argument("a")
    p.add_argument("b")
    p.add_argument("--limit", type=int, default=60)
    p.set_defaults(func=cmd_args)

    p = sub.add_parser("tail")
    p.add_argument("trace")
    p.add_argument("-n", type=int, default=40)
    p.set_defaults(func=cmd_tail)

    p = sub.add_parser("strings")
    p.add_argument("trace")
    p.add_argument("match", nargs="?", help="only lines whose text contains this")
    p.set_defaults(func=cmd_strings)

    p = sub.add_parser("grep")
    p.add_argument("trace")
    p.add_argument("pattern", help="function substring, resolved text, or a hex argument value")
    p.add_argument("--context", type=int, default=3)
    p.set_defaults(func=cmd_grep)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
