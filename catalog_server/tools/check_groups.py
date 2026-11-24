# SPDX-License-Identifier: GPL-3.0-or-later
"""Measure a built catalog's cross-site links and game groups against a
reference pair file.

A dev tool: nothing is written anywhere. The reference file has the format of
`tools.import_links` (`src_a<TAB>id_a<TAB>src_b<TAB>id_b`).

    python -m catalog_server.tools.check_groups catalog.sqlite reference.tsv

Links, per `origin`, for each reference pair whose first entry has a link to
the second entry's site: agreed (the same entry) or disagreed (another entry,
listed with examples). Reference pairs without such a link are unknown.

Groups: the number of groups, the size distribution, the largest group, the
site combinations, and conflicts (a group with two entries on one site, which
must stay 0). The exit code is 1 when a link disagrees or a group conflicts.
"""

import argparse
import sqlite3
import sys
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import cast

from .import_links import parse_pairs

EXAMPLES = 20

Pair = tuple[str, str, str, str]


@dataclass
class Report:
    agreed: Counter[str] = field(default_factory=Counter)
    disagreed: dict[str, list[tuple[Pair, str]]] = field(
        default_factory=lambda: defaultdict(list)
    )
    unknown: int = 0
    groups: int = 0
    sizes: Counter[int] = field(default_factory=Counter)
    combinations: Counter[tuple[str, ...]] = field(default_factory=Counter)
    conflicts: list[str] = field(default_factory=list)


class Args(argparse.Namespace):
    catalog: str = ""
    reference: str = ""


def compare(catalog: sqlite3.Connection, reference: Sequence[Pair]) -> Report:
    report = Report()
    linked: dict[tuple[str, str, str], list[tuple[str, str]]] = defaultdict(list)
    for src_a, id_a, src_b, id_b, origin in cast(
        list[tuple[str, str, str, str, str]],
        catalog.execute(
            "SELECT src_a, id_a, src_b, id_b, origin FROM links"
        ).fetchall(),
    ):
        linked[(src_a, str(id_a), src_b)].append((str(id_b), origin))
        linked[(src_b, str(id_b), src_a)].append((str(id_a), origin))
    for pair in reference:
        src_a, id_a, src_b, id_b = pair
        found = linked.get((src_a, id_a, src_b))
        if not found:
            report.unknown += 1
            continue
        for other, origin in found:
            if other == id_b:
                report.agreed[origin] += 1
            else:
                report.disagreed[origin].append((pair, other))
    members: dict[str, list[str]] = defaultdict(list)
    for src, key in cast(
        list[tuple[str, str]],
        catalog.execute("SELECT src, group_key FROM game_groups").fetchall(),
    ):
        members[key].append(src)
    report.groups = len(members)
    for key, sites in members.items():
        report.sizes[len(sites)] += 1
        if len(sites) > 1:
            report.combinations[tuple(sorted(sites))] += 1
        if len(set(sites)) != len(sites):
            report.conflicts.append(key)
    return report


def _read_only(path: str) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0] if __doc__ else None
    )
    _ = parser.add_argument("catalog", help="built catalog (catalog.sqlite)")
    _ = parser.add_argument("reference", help="reference pair file (TSV)")
    args = parser.parse_args(argv, namespace=Args())
    with open(args.reference, encoding="utf-8") as handle:
        reference, errors = parse_pairs(handle)
    catalog = _read_only(args.catalog)
    try:
        report = compare(catalog, reference)
    finally:
        catalog.close()
    print(f"reference pairs {len(reference)} (skipped lines {len(errors)})")
    for origin in sorted(set(report.agreed) | set(report.disagreed)):
        print(
            f"links {origin}: agreed {report.agreed[origin]}, "
            + f"disagreed {len(report.disagreed[origin])}"
        )
        for (src_a, id_a, src_b, id_b), other in report.disagreed[origin][:EXAMPLES]:
            print(
                f"  {src_a} {id_a}: reference {src_b} {id_b}, catalog {src_b} {other}"
            )
    print(f"unknown {report.unknown}")
    largest = max(report.sizes, default=0)
    print(
        f"groups {report.groups}, multi-site {sum(report.combinations.values())}, "
        + f"largest {largest}, conflicts {len(report.conflicts)}"
    )
    print(
        "sizes "
        + ", ".join(f"{size}: {count}" for size, count in sorted(report.sizes.items()))
    )
    for combination, count in report.combinations.most_common(12):
        print(f"  {'+'.join(combination)}: {count}")
    for key in report.conflicts[:EXAMPLES]:
        print(f"  conflict in group {key}")
    bad = any(report.disagreed.values()) or bool(report.conflicts)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
