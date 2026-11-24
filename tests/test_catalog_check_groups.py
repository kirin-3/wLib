# SPDX-License-Identifier: GPL-3.0-or-later
"""The links and groups check against a reference pair file."""

from catalog_server.groups import rebuild_groups
from catalog_server.links import rebuild_links
from catalog_server.store import CatalogStore
from catalog_server.tools import check_groups

NOW = "2026-11-24T00:00:00Z"


def test_counts_agree_disagree_unknown_and_groups(tmp_path, capsys):
    path = tmp_path / "catalog.sqlite"
    catalog = CatalogStore(str(path))
    catalog.upsert(
        "dlsite_works",
        "product_id",
        [
            {"product_id": code, "title": code, "url": "d", "site": "maniax"}
            for code in ("RJ01000001", "RJ01000002", "RJ01000003")
        ],
        NOW,
    )
    catalog.upsert(
        "f95_threads",
        "thread_id",
        [{"thread_id": n, "title": f"T{n}", "url": "u", "ts": 1} for n in (1, 2, 3)],
        NOW,
    )
    for thread_id, code in ((1, "RJ01000001"), (2, "RJ01000002")):
        catalog.upsert_f95_details(
            thread_id,
            1,
            NOW,
            {"status": "ok", "external_ids": {"dlsite": [{"id": code, "url": "x"}]}},
        )
    rebuild_links(catalog)
    rebuild_groups(catalog)
    catalog.close()
    reference = tmp_path / "ref.tsv"
    reference.write_text(
        "f95\t1\tdlsite\tRJ01000001\n"  # agrees
        "f95\t2\tdlsite\tRJ01000003\n"  # disagrees
        "f95\t3\tdlsite\tRJ01000003\n",  # unknown
        encoding="utf-8",
    )
    assert check_groups.main([str(path), str(reference)]) == 1
    out = capsys.readouterr().out
    assert "links f95: agreed 1, disagreed 1" in out
    assert "f95 2: reference dlsite RJ01000003, catalog dlsite RJ01000002" in out
    assert "unknown 1" in out
    assert "groups 4, multi-site 2, largest 2, conflicts 0" in out
    assert "dlsite+f95: 2" in out
