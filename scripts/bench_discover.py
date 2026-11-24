# SPDX-License-Identifier: GPL-3.0-or-later
"""Time the Discover index build and first-page queries on a real catalog.

Copies the catalog into a temporary folder (the original is never opened for
writing), builds the index there, and times each sort and a few searches and
filters. Exits 1 when a query takes longer than the target.

    python scripts/bench_discover.py [catalog.db] [--library wlib.db]
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
import tempfile
import time
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import catalog_discover  # noqa: E402

TARGET_MS = 500.0
DEFAULT_CATALOG = Path.home() / ".local/share/wLib/catalog.db"
DEFAULT_LIBRARY = Path.home() / ".local/share/wLib/wlib.db"

QUERIES: list[tuple[str, dict[str, object]]] = [
    *((f"sort {sort}", {"sort": sort}) for sort in catalog_discover.SORTS),
    ("search 'the'", {"search": "the"}),
    ("search 'sister'", {"search": "sister"}),
    ("search two words", {"search": "my sister"}),
    ("search Japanese", {"search": "妹"}),
    ("search no match", {"search": "zzqxjv"}),
    ("tags include 2", {"include_tags": ["big tits", "sandbox"], "sort": "rating"}),
    ("tags exclude", {"exclude_tags": ["netorare"]}),
    ("include F95", {"include_sites": ["f95"], "sort": "title"}),
    ("exclude VNDB", {"exclude_sites": ["vndb"]}),
    (
        "completed Ren'Py 4.0+",
        {"statuses": ["completed"], "engines": ["Ren'Py"], "min_rating": 4.0},
    ),
    ("hide my games", {"hide_library": True}),
    ("page 200", {"offset": 120 * 200}),
]


class Args(argparse.Namespace):
    catalog: str = str(DEFAULT_CATALOG)
    library: str = str(DEFAULT_LIBRARY)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0] if __doc__ else None
    )
    _ = parser.add_argument("catalog", nargs="?", default=str(DEFAULT_CATALOG))
    _ = parser.add_argument("--library", default=str(DEFAULT_LIBRARY))
    args = parser.parse_args(argv, namespace=Args())
    with tempfile.TemporaryDirectory(prefix="wlib-bench-discover-") as folder:
        catalog = Path(folder) / "catalog.db"
        _ = shutil.copyfile(args.catalog, catalog)
        index = Path(folder) / catalog_discover.INDEX_FILE
        result = catalog_discover.build_index(catalog, index, catalog_sha256="bench")
        print(
            f"build: {result.games} games from {result.entries} entries, "
            + f"{result.seconds:.2f} s, {result.size / 1024 / 1024:.1f} MiB"
        )
        identities: list[catalog_discover.LibraryIdentity] = []
        if Path(args.library).is_file():
            uri = Path(args.library).as_uri() + "?mode=ro"
            with closing(sqlite3.connect(uri, uri=True)) as library:
                identities = catalog_discover.library_identities(library)
        print(f"library identities: {len(identities)}")
        slow = 0
        for name, params in QUERIES:
            query = catalog_discover.parse_query(params)
            started = time.perf_counter()
            page = catalog_discover.search(index, query, identities)
            elapsed = (time.perf_counter() - started) * 1000
            slow += elapsed > TARGET_MS
            flag = "  SLOW" if elapsed > TARGET_MS else ""
            print(f"{elapsed:8.1f} ms  {name:26} total {page['total']}{flag}")
        started = time.perf_counter()
        _ = catalog_discover.filters(index)
        print(f"{(time.perf_counter() - started) * 1000:8.1f} ms  filters")
    return 1 if slow else 0


if __name__ == "__main__":
    sys.exit(main())
