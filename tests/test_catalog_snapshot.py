# SPDX-License-Identifier: GPL-3.0-or-later
import hashlib
import lzma
import sqlite3

from catalog_server.groups import rebuild_groups
from catalog_server.snapshot import build_snapshot, snapshot_name
from catalog_server.store import CatalogStore


def test_snapshot_decompresses_to_a_readable_catalog(tmp_path):
    catalog = CatalogStore(str(tmp_path / "catalog.sqlite"))
    catalog.upsert(
        "f95_threads",
        "thread_id",
        [{"thread_id": 1, "title": "A", "url": "u"}],
        "2026-10-26T00:00:00Z",
    )
    catalog.upsert_f95_details(
        1, 10, "2026-10-26T00:00:00Z", {"status": "ok", "rating_count": 12}
    )
    rebuild_groups(catalog)
    snapshot = build_snapshot(catalog, str(tmp_path / "out"), "2026-10-27T05:12:09Z")
    assert snapshot.name == "catalog-20261027T051209Z.sqlite.xz"
    data = open(snapshot.path, "rb").read()
    assert len(data) == snapshot.size
    assert hashlib.sha256(data).hexdigest() == snapshot.sha256
    raw = lzma.decompress(data)
    assert len(raw) == snapshot.uncompressed_size
    db = tmp_path / "check.db"
    db.write_bytes(raw)
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    assert conn.execute("PRAGMA quick_check").fetchone() == ("ok",)
    assert dict(conn.execute("SELECT key, value FROM meta")) == {
        "schema_version": "1",
        "built_at": "2026-10-27T05:12:09Z",
    }
    assert conn.execute("SELECT title FROM f95_threads").fetchall() == [("A",)]
    assert conn.execute(
        "SELECT rating_count FROM f95_thread_details WHERE thread_id = 1"
    ).fetchone() == (12,)
    # Groups ride along without a schema change.
    assert conn.execute("SELECT src, id, group_key FROM game_groups").fetchall() == [
        ("f95", "1", "f95:1")
    ]
    conn.close()
    # Only the compressed file is left behind.
    assert sorted(path.name for path in (tmp_path / "out").iterdir()) == [snapshot.name]


def test_snapshot_names_sort_by_time():
    names = [
        snapshot_name(stamp)
        for stamp in (
            "2026-10-27T05:00:00Z",
            "2026-11-01T17:00:00Z",
            "2026-10-28T05:00:00Z",
        )
    ]
    assert sorted(names) == [names[0], names[2], names[1]]
