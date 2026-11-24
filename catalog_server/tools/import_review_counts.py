# SPDX-License-Identifier: GPL-3.0-or-later
"""Fill missing F95 review counts from a tab-separated file.

Each line is `thread_id<TAB>count`, for example `223393<TAB>1200`. Empty
lines and lines starting with `#` are skipped, and so are counts of 0:

    python -m catalog_server.tools.import_review_counts counts.tsv

Only threads whose first post was read and that have no review count yet
change. The counts are published like F95's own; the next read of each
thread replaces them with the count on its page.
"""

import argparse
import os
import re
import sys
from collections.abc import Iterable, Sequence

from ..__main__ import _run_lock  # pyright: ignore[reportPrivateUsage]
from ..config import data_dir
from ..store import CATALOG_FILE, CatalogStore

_THREAD_RE = re.compile(r"[1-9]\d*")
_COUNT_RE = re.compile(r"\d+")


class Args(argparse.Namespace):
    counts: str = ""
    data_dir: str | None = None


def parse_counts(lines: Iterable[str]) -> tuple[dict[int, int], list[str]]:
    """Return thread ID -> positive count and one message per rejected line."""
    counts: dict[int, int] = {}
    errors: list[str] = []
    for number, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split("\t")]
        if (
            len(parts) != 2
            or not _THREAD_RE.fullmatch(parts[0])
            or not _COUNT_RE.fullmatch(parts[1])
        ):
            errors.append(f"line {number}: expected a thread ID and a count")
            continue
        if int(parts[1]) > 0:
            counts[int(parts[0])] = int(parts[1])
    return counts, errors


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0] if __doc__ else None
    )
    _ = parser.add_argument("counts", help="tab-separated count file")
    _ = parser.add_argument(
        "--data-dir",
        default=None,
        help="builder data folder (default: $WLIB_CATALOG_DATA_DIR or /var/lib/wlib-catalog)",
    )
    args = parser.parse_args(argv, namespace=Args())
    with open(args.counts, encoding="utf-8") as handle:
        counts, errors = parse_counts(handle)
    for message in errors:
        print(message, file=sys.stderr)
    folder = args.data_dir or data_dir()
    with _run_lock(folder) as acquired:
        if not acquired:
            print(
                "a catalog run is in progress; try again when it finishes",
                file=sys.stderr,
            )
            return 1
        catalog = CatalogStore(os.path.join(folder, CATALOG_FILE))
        try:
            filled = catalog.fill_f95_rating_counts(sorted(counts.items()))
        finally:
            catalog.close()
    print(f"filled {filled} of {len(counts)} counts, skipped {len(errors)} lines")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
