# SPDX-License-Identifier: GPL-3.0-or-later
"""The dev tool that measures title matches against Atlas."""

import sqlite3

from catalog_server.store import CatalogStore
from catalog_server.tools.check_lc_matches import main

NOW = "2026-11-18T00:00:00Z"


def _catalog(path):
    catalog = CatalogStore(str(path))
    lc = [(1, "Alpha", "Dev"), (2, "Beta", "Dev"), (3, "Gamma", "Dev"), (4, "Delta", "Dev")]
    f95 = [(11, "Alpha", "Dev"), (12, "Beta", "Dev"), (13, "Gamma", "Dev"), (14, "Delta", "Other"),
           (15, "Beta Remastered", "Dev")]
    catalog.upsert("lc_threads", "thread_id", [
        {"thread_id": i, "title": t, "creator": c, "url": "u"} for i, t, c in lc
    ], NOW)
    catalog.upsert("f95_threads", "thread_id", [
        {"thread_id": i, "title": t, "creator": c, "url": "u"} for i, t, c in f95
    ], NOW)
    catalog.close()
    return str(path)


def _atlas(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE lewdcorner_data (lc_id INTEGER PRIMARY KEY, atlas_id INTEGER,
            downloads STRING);
        CREATE TABLE f95_zone_data (f95_id INTEGER PRIMARY KEY, atlas_id INTEGER);
        -- Alpha agrees, Beta disagrees (Atlas: the remaster, also Atlas-only), Gamma unknown to
        -- Atlas, Delta (different developers) is Atlas-only.
        INSERT INTO lewdcorner_data VALUES (1, 100, '[]'), (2, 200, '[]'), (4, 400, '[]');
        INSERT INTO f95_zone_data VALUES (11, 100), (15, 200), (14, 400);
        """
    )
    conn.commit()
    conn.close()
    return str(path)


def test_report_counts_and_examples(tmp_path, capsys):
    code = main([_catalog(tmp_path / "catalog.sqlite"), _atlas(tmp_path / "atlas.db")])
    out = capsys.readouterr().out
    assert code == 1
    assert "matched 3, agreed 1, disagreed 1, unknown 1, atlas only 2" in out
    assert "lc 2 'Beta' by 'Dev' -> f95 12" in out and "Atlas says f95 15" in out
