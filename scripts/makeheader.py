#!/usr/bin/env python3
"""Synthesize the `.header` a raw .big pack needs, for packs that ship without one.

Several downloaded packs are a bare `<name>_00000000.big` with no header, and
the loader cannot import those: freeskate's `[dlc_packages]` needs a header, and
the game's content scan silently drops a package whose header is missing or
wrong (it does not error - the game just quits with "Execution complete", which
reads as an unrelated crash).

The format is small and entirely mechanical - there is nothing signed in it:

    [0x000:0x004]  version, big-endian, must be 1
    [0x004:0x008]  content type, big-endian, must be 2 (marketplace content)
    [0x008:0x108]  display name, UTF-16-BE, null padded (256 bytes)
    [0x108:0x132]  content id, ASCII, null padded (42 bytes)
    [size-12:size-8] title id, 454108E6
    [size-8:size]  zeroes
    total 328 bytes

The one rule that matters: **the content id must equal the package directory
name**, or the content manager rejects the package without saying so. Since we
choose the directory, a synthesized header is legitimate here - the earlier
"do not synthesize one" note came from a case where the two did not match, not
from the format being unforgeable.

    python3 scripts/makeheader.py <pack.big> [--name "Display Name"] [--id CONTENTID]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

HEADER_SIZE = 328
CONTENT_ID_OFFSET = 0x108
CONTENT_ID_LENGTH = 42
TITLE_ID = bytes.fromhex("454108E6")


def content_id_for(big: Path) -> str:
    """A stable, unique-ish id from the .big filename.

    Uppercase alphanumerics only, which is what every shipped header uses, and
    the trailing `_00000000` these packs all carry is dropped.
    """
    stem = re.sub(r"_0+$", "", big.stem)
    ident = re.sub(r"[^A-Za-z0-9]", "", stem).upper()
    return (ident or "SKATE3PACK")[:CONTENT_ID_LENGTH]


def build(name: str, content_id: str) -> bytes:
    if len(content_id) > CONTENT_ID_LENGTH:
        raise ValueError(f"content id longer than {CONTENT_ID_LENGTH}: {content_id}")
    header = bytearray(HEADER_SIZE)
    header[0:4] = (1).to_bytes(4, "big")            # version
    header[4:8] = (2).to_bytes(4, "big")            # marketplace content
    encoded = name.encode("utf-16-be")[: 0x108 - 8 - 2]
    header[8 : 8 + len(encoded)] = encoded          # display name, null padded
    ascii_id = content_id.encode("ascii")
    header[CONTENT_ID_OFFSET : CONTENT_ID_OFFSET + len(ascii_id)] = ascii_id
    header[HEADER_SIZE - 12 : HEADER_SIZE - 8] = TITLE_ID
    return bytes(header)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("big", type=Path)
    parser.add_argument("--name", default=None, help="display name (default: from filename)")
    parser.add_argument("--id", dest="content_id", default=None)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    if not args.big.is_file():
        print(f"no such file: {args.big}", file=sys.stderr)
        return 1

    content_id = args.content_id or content_id_for(args.big)
    name = args.name or re.sub(r"_0+$", "", args.big.stem).replace("_", " ").title()
    out = args.out or args.big.with_suffix(".header")
    out.write_bytes(build(name, content_id))

    print(f":: {out}")
    print(f"   display name {name!r}")
    print(f"   content id   {content_id!r}")
    print(f"   package dir must be exactly: {content_id}")

    # Validate with the loader's own checker rather than trusting the writer.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from loader import catalog  # noqa: E402

    try:
        catalog.validate_header(out, content_id)
        print("   validate_header: PASS")
    except catalog.ImportError_ as exc:
        print(f"   validate_header: FAIL - {exc}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
