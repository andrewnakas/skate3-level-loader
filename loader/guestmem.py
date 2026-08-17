"""Reading the running game's guest memory from outside the process.

`Memory::Initialize` (rexglue-sdk/src/system/xmemory.cpp:157-165) backs the whole
4 GiB guest address space with a shared-memory file named `xenia_memory_<ticks>`,
and maps it from offset 0. On Linux that file is visible as
/dev/shm/xenia_memory_*, so a guest virtual address IS a byte offset into it:

    guest 0x830CFE14  ->  /dev/shm/xenia_memory_123456 offset 0x830CFE14

That gives the launcher a live, zero-cost view of frontend state -- which screen
is open, which entry the cursor is on -- without patching, rebuilding, or
attaching a debugger. Guest memory is big-endian (PowerPC), so all reads here
byteswap.

This module is READ-ONLY on purpose. The file is writable, and poking guest
memory from another process would race the game's own threads in exactly the way
that produced the use-after-free crash this project already spent days chasing.
Observation is enough for what the loader needs.
"""

from __future__ import annotations

import os
import re
import struct
from dataclasses import dataclass
from pathlib import Path

SHM_DIR = Path("/dev/shm")
SHM_GLOB = "xenia_memory_*"

# Guest pointer to the FrontEndManager singleton (TU3).
FRONTEND_MANAGER_PTR = 0x830CFE14
# The manager's push-state stack: an eastl::vector of 20-byte records.
STACK_BEGIN = 0x210
STACK_END = 0x214
STACK_RECORD = 20
MAX_STACK_DEPTH = 16

# Screen ids observed in this title.
SCREEN_NAMES = {
    0: "frontend root",
    15: "edit skater",
    17: "challenge map",
    24: "intro movie",
    47: "language select",
    56: "pause root",
    59: "skate reel",
    63: "team",
}

# Guest address space is 4 GiB; anything outside is not a valid guest pointer.
GUEST_LIMIT = 0x100000000


class NotRunning(Exception):
    pass


def find_segment() -> Path:
    """The newest xenia_memory_* segment, i.e. the running game's."""
    candidates = sorted(
        SHM_DIR.glob(SHM_GLOB), key=lambda p: p.stat().st_mtime, reverse=True
    )
    if not candidates:
        raise NotRunning(
            "no /dev/shm/xenia_memory_* segment; the game does not appear to be running"
        )
    return candidates[0]


def looks_like_guest_pointer(value: int) -> bool:
    # The XEX and heaps live above 0x10000; 0 and small values are not pointers.
    return 0x10000 < value < GUEST_LIMIT


@dataclass
class StackEntry:
    screen_id: int
    words: tuple[int, int, int, int]

    @property
    def name(self) -> str:
        return SCREEN_NAMES.get(self.screen_id, f"screen {self.screen_id}")


class GuestMemory:
    """Random-access reads into the running game's address space."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else find_segment()
        self._fd = os.open(self.path, os.O_RDONLY)
        self._size = os.fstat(self._fd).st_size

    def close(self) -> None:
        if self._fd >= 0:
            os.close(self._fd)
            self._fd = -1

    def __enter__(self) -> "GuestMemory":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # -- primitives --------------------------------------------------------

    def read(self, address: int, length: int) -> bytes:
        if address < 0 or address + length > self._size:
            return b""
        try:
            return os.pread(self._fd, length, address)
        except OSError:
            return b""

    def u32(self, address: int) -> int | None:
        raw = self.read(address, 4)
        if len(raw) != 4:
            return None
        return struct.unpack(">I", raw)[0]

    def u16(self, address: int) -> int | None:
        raw = self.read(address, 2)
        if len(raw) != 2:
            return None
        return struct.unpack(">H", raw)[0]

    def words(self, address: int, count: int) -> list[int]:
        raw = self.read(address, count * 4)
        if len(raw) < count * 4:
            return []
        return list(struct.unpack(f">{count}I", raw))

    def cstring(self, address: int, limit: int = 128) -> str | None:
        raw = self.read(address, limit)
        if not raw:
            return None
        end = raw.find(b"\x00")
        if end == 0:
            return ""
        text = raw[: end if end > 0 else limit]
        try:
            decoded = text.decode("ascii")
        except UnicodeDecodeError:
            return None
        return decoded if decoded.isprintable() else None

    def utf16(self, address: int, limit: int = 128) -> str | None:
        raw = self.read(address, limit * 2)
        if not raw:
            return None
        out = []
        for i in range(0, len(raw) - 1, 2):
            code = struct.unpack_from(">H", raw, i)[0]
            if code == 0:
                break
            out.append(chr(code))
        text = "".join(out)
        return text if text and text.isprintable() else None

    # -- frontend ----------------------------------------------------------

    def frontend_manager(self) -> int | None:
        value = self.u32(FRONTEND_MANAGER_PTR)
        return value if value and looks_like_guest_pointer(value) else None

    def screen_stack(self) -> list[StackEntry]:
        """The frontend push-state stack, innermost screen last.

        Mirrors the sanity gate the in-engine fe-debug uses: a plausible
        begin/end pair no deeper than 16 screens.
        """
        manager = self.frontend_manager()
        if manager is None:
            return []
        begin = self.u32(manager + STACK_BEGIN)
        end = self.u32(manager + STACK_END)
        if begin is None or end is None:
            return []
        if not (looks_like_guest_pointer(begin) and end > begin):
            return []
        span = end - begin
        if span % STACK_RECORD or span > STACK_RECORD * MAX_STACK_DEPTH:
            return []

        entries = []
        for index in range(span // STACK_RECORD):
            record = self.words(begin + index * STACK_RECORD, 5)
            if not record:
                break
            entries.append(StackEntry(screen_id=record[0], words=tuple(record[1:5])))
        return entries

    def current_screen(self) -> StackEntry | None:
        stack = self.screen_stack()
        return stack[-1] if stack else None


def snapshot_region(memory: GuestMemory, address: int, word_count: int) -> list[int]:
    return memory.words(address, word_count)


def diff_words(before: list[int], after: list[int]) -> list[tuple[int, int, int]]:
    """(word index, old, new) for every word that changed."""
    return [
        (i, old, new)
        for i, (old, new) in enumerate(zip(before, after))
        if old != new
    ]


def find_ascii_strings(data: bytes, minimum: int = 4) -> list[tuple[int, str]]:
    """Offsets and text of printable ASCII runs inside a blob."""
    out = []
    for match in re.finditer(rb"[\x20-\x7e]{%d,}" % minimum, data):
        out.append((match.start(), match.group().decode("ascii")))
    return out
