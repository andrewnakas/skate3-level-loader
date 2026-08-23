#!/usr/bin/env python3
"""Dropping a pack on the window installs it - and restores what we knew about it.

Drag and drop is the primary way maps get in now, so the handler is worth
pinning offline. Three properties:

  a dropped .big is imported
  a dropped FOLDER goes through the directory scan, not a single-file import
  a pack the shipped catalog already knows about comes back with its curation -
  the tested boots/stalls verdict especially, which no scan can re-derive

The third is the one that would rot silently: import_pack would happily write a
fresh record with status "" and the library would stop warning about maps that
are known not to load.

    python3 scripts/drop_test.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("GDK_BACKEND", "x11")

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))


class Uris:
    """The bit of Gtk.SelectionData the handler actually uses."""

    def __init__(self, paths):
        self._uris = [Path(p).as_uri() for p in paths]

    def get_uris(self):
        return self._uris


def main() -> int:
    from loader import catalog, config, ui_library

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        catalog_dir = tmp / "catalog"
        catalog_dir.mkdir()
        config.CATALOG_DIR = catalog_dir

        # A shipped record: curation, no file. This is what a release seeds.
        known = catalog.Pack(
            id="dropped-pack", name="Dropped Pack", kind="big-dlc", big="", header="",
            package="DROPPED",
            maps=[catalog.MapEntry(world_id="DroppedWorld", name="Dropped World",
                                   sub_index=0, status="stalls")],
        )
        (catalog_dir / "dropped-pack.json").write_text(known.to_json())
        check("a shipped record is not shown as playable",
              not catalog.Pack.from_json((catalog_dir / "dropped-pack.json").read_text()).located)

        window = ui_library.LibraryWindow.__new__(ui_library.LibraryWindow)
        calls = {"reload": 0, "status": [], "errors": []}
        window.reload = lambda: calls.__setitem__("reload", calls["reload"] + 1)
        window.set_status = lambda text: calls["status"].append(text)
        window.show_error = lambda title, detail: calls["errors"].append((title, detail))

        imported = []
        original_import = catalog.import_pack
        original_dir_import = None

        def fake_import(source, catalog_dir_arg, **kwargs):
            imported.append(("file", Path(source)))
            pack = catalog.Pack(id="dropped-pack", name="Dropped Pack", kind="big-dlc",
                                big=str(source), header=str(source) + ".header",
                                package="DROPPED",
                                maps=[catalog.MapEntry(world_id="DroppedWorld",
                                                       name="Dropped World", sub_index=0)])
            catalog._carry_over(pack, catalog_dir_arg)
            (catalog_dir_arg / f"{pack.id}.json").write_text(pack.to_json())
            return pack

        catalog.import_pack = fake_import
        ui_library.catalog = catalog

        big = tmp / "dropped_00000000.big"
        big.write_bytes(b"not really a pack")

        ui_library.LibraryWindow._on_drop(
            window, None, None, 0, 0, Uris([big]), 0, 0)

        check("a dropped .big is imported", imported == [("file", big)], str(imported))
        check("the library refreshes", calls["reload"] == 1, str(calls["reload"]))
        check("no error was shown", not calls["errors"], str(calls["errors"]))

        record = json.loads((catalog_dir / "dropped-pack.json").read_text())
        check("the file is remembered", record["big"] == str(big), record["big"])
        check("the tested verdict survives the drop",
              record["maps"][0]["status"] == "stalls",
              f"status is {record['maps'][0]['status']!r} - a scan cannot re-derive this")

        # A folder must go through the directory scan instead.
        catalog.import_pack = original_import
        from loader import discover

        scanned = []
        discover.import_directory = lambda d, **k: scanned.append(Path(d)) or []
        ui_library.discover = discover
        folder = tmp / "downloads"
        folder.mkdir()
        ui_library.LibraryWindow._on_drop(
            window, None, None, 0, 0, Uris([folder]), 0, 0)
        check("a dropped folder is scanned, not imported as a file",
              scanned == [folder], str(scanned))

    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n{passed}/{len(results)} checks passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
