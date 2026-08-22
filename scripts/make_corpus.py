#!/usr/bin/env python3
"""Distil the run logs in work/spots into a corpus small enough to commit.

`classifier_test` is the only test that pins `transient_boot_failure` against
real data, and it could not run in CI: its inputs live under `work/`, which is
gitignored, 17 MB of them. A fixture-free version of that test would be a
different, weaker test - so the logs come along, minus everything the rule never
looks at.

The rule reads exactly three patterns (RE_TAKEOVER, RE_SEQUENCE_DONE, RE_FATAL),
and order between them is all that matters. So each log is reduced to just those
lines, and every distilled log is checked to produce the SAME verdict as the
original before it is written. A corpus that changed a verdict would be worse
than no corpus at all.

    python3 scripts/make_corpus.py [--out tests/corpus]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loader import config, logwatch  # noqa: E402

PATTERNS = (logwatch.RE_TAKEOVER, logwatch.RE_SEQUENCE_DONE, logwatch.RE_FATAL)


def distil(text: str) -> str:
    kept = [line for line in text.splitlines()
            if any(pattern.search(line) for pattern in PATTERNS)]
    return "\n".join(kept) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default=None, help="defaults to config.SPOTS_DIR")
    parser.add_argument("--out", default="tests/corpus")
    args = parser.parse_args()

    source = Path(args.source) if args.source else config.SPOTS_DIR
    out = Path(args.out)
    logs = sorted(source.glob("s[0-9]__*.log"))
    if not logs:
        print(f"no s<N>__*.log under {source}", file=sys.stderr)
        return 1

    out.mkdir(parents=True, exist_ok=True)
    for stale in out.glob("*.log"):
        stale.unlink()

    written, bytes_out = 0, 0
    for log in logs:
        original = log.read_text(errors="replace")
        reduced = distil(original)
        before = logwatch.transient_boot_failure(original)
        after = logwatch.transient_boot_failure(reduced)
        if before != after:
            print(f"REFUSING: {log.name} distils to a different verdict "
                  f"({before!r} -> {after!r})", file=sys.stderr)
            return 2
        target = out / log.name
        target.write_text(reduced)
        written += 1
        bytes_out += len(reduced)

    manifest = {
        "logs": written,
        "note": "distilled by scripts/make_corpus.py; verdict-equivalent to the "
                "full logs under work/spots",
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f":: {written} logs, {bytes_out / 1024:.0f} KiB -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
