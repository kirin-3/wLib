# SPDX-License-Identifier: GPL-3.0-or-later
"""Measure the strict LC↔F95 title match against an Atlas database.

A dev tool: it runs `match_lc_f95` on a built catalog and compares the pairs
with Atlas's own links (`lewdcorner_data.atlas_id` = `f95_zone_data.atlas_id`).
Atlas data is only used to measure; nothing is written anywhere.

    python -m catalog_server.tools.check_lc_matches catalog.sqlite atlas-latest.db

Counts:
- matched: pairs the title match finds;
- agreed: matched pairs that Atlas links the same way;
- disagreed: matched pairs whose LC thread Atlas links to a different F95 thread;
- unknown: matched pairs whose LC thread Atlas does not link to F95;
- atlas only: Atlas pairs between current catalog threads that the match misses.

Title links are fit to publish only when "disagreed" is 0, or each remaining
case was checked by hand and found to be Atlas's error. The exit code is 1
while any pair disagrees.
"""

import argparse
import sqlite3
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import cast

from ..title_match import match_lc_f95

_ATLAS_PAIRS = """
SELECT l.lc_id, f.f95_id FROM lewdcorner_data l
JOIN f95_zone_data f ON f.atlas_id = l.atlas_id
WHERE l.atlas_id IS NOT NULL
"""
EXAMPLES = 20


@dataclass
class MatchReport:
    matched: int = 0
    agreed: int = 0
    disagreed: list[tuple[int, int, int]] = field(
        default_factory=list[tuple[int, int, int]]
    )
    unknown: int = 0
    atlas_only: int = 0


def _read_only(path: str) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def atlas_pairs(atlas: sqlite3.Connection) -> dict[int, set[int]]:
    pairs: dict[int, set[int]] = {}
    for lc_id, f95_id in cast(
        list[tuple[object, object]], atlas.execute(_ATLAS_PAIRS).fetchall()
    ):
        lc, f95 = _int(lc_id), _int(f95_id)
        if lc is not None and f95 is not None:
            pairs.setdefault(lc, set()).add(f95)
    return pairs


def _int(value: object) -> int | None:
    if isinstance(value, int):
        return value
    text = str(value).strip() if value is not None else ""
    return int(text) if text.isdigit() else None


def _current(catalog: sqlite3.Connection, table: str) -> set[int]:
    rows = cast(
        list[tuple[int]],
        catalog.execute(
            f"SELECT thread_id FROM {table} WHERE removed_at IS NULL"
        ).fetchall(),
    )
    return {row[0] for row in rows}


def compare(catalog: sqlite3.Connection, atlas: sqlite3.Connection) -> MatchReport:
    ours = match_lc_f95(catalog)
    theirs = atlas_pairs(atlas)
    report = MatchReport(matched=len(ours))
    for lc_id, f95_id in ours:
        linked = theirs.get(lc_id)
        if linked is None:
            report.unknown += 1
        elif f95_id in linked:
            report.agreed += 1
        else:
            report.disagreed.append((lc_id, f95_id, min(linked)))
    found = set(ours)
    lc_current = _current(catalog, "lc_threads")
    f95_current = _current(catalog, "f95_threads")
    report.atlas_only = sum(
        1
        for lc_id, linked in theirs.items()
        if lc_id in lc_current
        for f95_id in linked
        if f95_id in f95_current and (lc_id, f95_id) not in found
    )
    return report


def _title(catalog: sqlite3.Connection, table: str, thread_id: int) -> str:
    row = cast(
        tuple[str, str | None] | None,
        catalog.execute(
            f"SELECT title, creator FROM {table} WHERE thread_id = ?", (thread_id,)
        ).fetchone(),
    )
    return f"{row[0]!r} by {row[1]!r}" if row else "(not in catalog)"


class Args(argparse.Namespace):
    catalog: str = ""
    atlas: str = ""


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0] if __doc__ else None
    )
    _ = parser.add_argument("catalog", help="built catalog (catalog.sqlite)")
    _ = parser.add_argument("atlas", help="Atlas database (atlas-latest.db)")
    args = parser.parse_args(argv, namespace=Args())
    catalog = _read_only(args.catalog)
    atlas = _read_only(args.atlas)
    try:
        report = compare(catalog, atlas)
        print(
            f"matched {report.matched}, agreed {report.agreed}, "
            + f"disagreed {len(report.disagreed)}, unknown {report.unknown}, "
            + f"atlas only {report.atlas_only}"
        )
        for lc_id, f95_id, atlas_f95 in report.disagreed[:EXAMPLES]:
            print(
                f"  lc {lc_id} {_title(catalog, 'lc_threads', lc_id)} -> f95 {f95_id} "
                + f"{_title(catalog, 'f95_threads', f95_id)}; Atlas says f95 {atlas_f95} "
                + _title(catalog, "f95_threads", atlas_f95)
            )
    finally:
        catalog.close()
        atlas.close()
    return 1 if report.disagreed else 0


if __name__ == "__main__":
    sys.exit(main())
