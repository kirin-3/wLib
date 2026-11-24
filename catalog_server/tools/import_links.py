# SPDX-License-Identifier: GPL-3.0-or-later
"""Import link pairs between catalog entries from a tab-separated file.

Each line names two entries: `src_a<TAB>id_a<TAB>src_b<TAB>id_b`, for example
`f95<TAB>223393<TAB>dlsite<TAB>RJ01234567`. Sites are the catalog sources
(f95, lc, dlsite, ryuugames, vndb). Empty lines and lines starting with `#`
are skipped. An import replaces the previous one; an empty file clears it:

    python -m catalog_server.tools.import_links pairs.tsv

The pairs stay in state.sqlite (never published). Each run turns the pairs
whose two entries are current into links with `origin = 'import'`, after
stated and title links and never against them.
"""

import argparse
import os
import re
import sys
from collections.abc import Iterable, Sequence

from ..__main__ import _run_lock  # pyright: ignore[reportPrivateUsage]
from ..config import data_dir
from ..store import STATE_FILE, StateStore

# The ID form of each site, as the sources store it.
ID_PATTERNS: dict[str, re.Pattern[str]] = {
    "f95": re.compile(r"[1-9]\d*"),
    "lc": re.compile(r"[1-9]\d*"),
    "dlsite": re.compile(r"(?:RJ|VJ|BJ)\d{6,8}"),
    "ryuugames": re.compile(r"[1-9]\d*"),
    "vndb": re.compile(r"v[1-9]\d*"),
}

Pair = tuple[str, str, str, str]


class Args(argparse.Namespace):
    pairs: str = ""
    data_dir: str | None = None


def parse_pairs(lines: Iterable[str]) -> tuple[list[Pair], list[str]]:
    """Return the valid pairs and one message per rejected line."""
    pairs: list[Pair] = []
    errors: list[str] = []
    for number, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split("\t")]
        if len(parts) != 4:
            errors.append(f"line {number}: expected 4 tab-separated fields")
            continue
        src_a, id_a, src_b, id_b = parts
        src_a, src_b = src_a.lower(), src_b.lower()
        if src_a == src_b:
            errors.append(f"line {number}: both entries are on {src_a}")
            continue
        bad = [
            f"{site} ID {identity!r}"
            for site, identity in ((src_a, id_a), (src_b, id_b))
            if site not in ID_PATTERNS or not ID_PATTERNS[site].fullmatch(identity)
        ]
        if bad:
            errors.append(f"line {number}: unknown site or bad {', '.join(bad)}")
            continue
        pairs.append((src_a, id_a, src_b, id_b))
    return pairs, errors


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0] if __doc__ else None
    )
    _ = parser.add_argument("pairs", help="tab-separated pair file")
    _ = parser.add_argument(
        "--data-dir",
        default=None,
        help="builder data folder (default: $WLIB_CATALOG_DATA_DIR or /var/lib/wlib-catalog)",
    )
    args = parser.parse_args(argv, namespace=Args())
    with open(args.pairs, encoding="utf-8") as handle:
        pairs, errors = parse_pairs(handle)
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
        state = StateStore(os.path.join(folder, STATE_FILE))
        try:
            stored = state.replace_imported_links(pairs)
        finally:
            state.close()
    print(f"imported {stored} pairs, skipped {len(errors)} lines")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
