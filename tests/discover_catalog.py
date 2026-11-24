# SPDX-License-Identifier: GPL-3.0-or-later
"""A small catalog for the Discover tests."""

from pathlib import Path

from catalog_server.store import CatalogStore

SEEN = "'2026-01-01', '2026-01-01'"


def make_catalog(tmp_path: Path, *, groups: bool = True) -> Path:
    path = tmp_path / "catalog.db"
    store = CatalogStore(str(path))
    conn = store.conn
    with conn:
        _ = conn.executescript(
            f"""
            INSERT INTO f95_prefix_names (id, name, category) VALUES
              (1, 'Ren''Py', 'Engine'), (2, 'Completed', 'Status'), (3, 'Unity', 'Engine');
            INSERT INTO f95_tag_names (id, name) VALUES (10, 'big tits'), (11, 'sandbox');
            INSERT INTO lc_prefix_names (id, name) VALUES (9, 'RPGM'), (24, 'Abandoned');
            INSERT INTO f95_threads (thread_id, title, creator, version, ts, rating,
              prefixes, tags, cover_url, url, first_seen, last_seen) VALUES
              (100, 'Echo Park', 'EchoDev', 'v0.5', 1700000000, 4.6, '[1, 2]', '[10, 11]',
               'https://attachments.f95zone.to/2023/01/a.jpg', 'https://f95zone.to/threads/100/', {SEEN}),
              (200, 'Solo F95', 'Solo', '1.0', 1600000000, NULL, '[3]', '[]',
               NULL, 'https://f95zone.to/threads/200/', {SEEN});
            INSERT INTO f95_thread_details (thread_id, fetched_at, status, original_title,
              aliases, rating_count, external_ids) VALUES
              (100, '2026-01-01', 'ok', 'エコーパーク', '["Echo Town"]', 1200,
               '{{"steam": [{{"id": "943700"}}], "dlsite": [{{"id": "RJ01234567"}}]}}');
            INSERT INTO lc_threads (thread_id, title, creator, version, ts, tier, prefixes,
              tags, cover_url, url, first_seen, last_seen) VALUES
              (300, 'Echo Park LC', 'EchoDev', 'v0.6', 1710000000, 'Premium', '[9]',
               '["big-tits", "lc-only-tag"]', 'https://lewdcorner.com/attachments/a.1/',
               'https://lewdcorner.com/threads/300/', {SEEN}),
              (400, 'Only On LC', 'LcDev', '2.0', 1650000000, 'Free', '[9, 24]', '["big-tits"]',
               'https://lewdcorner.com/attachments/b.2/', 'https://lewdcorner.com/threads/400/', {SEEN});
            INSERT INTO lc_thread_details (thread_id, fetched_at, status, developer, released_at)
              VALUES (400, '2026-01-01', 'ok', 'LC Studio', '2022-03-04');
            INSERT INTO dlsite_works (product_id, site, title, title_en, circle, circle_en,
              released_at, cover_url, url, first_seen, last_seen) VALUES
              ('RJ01234567', 'maniax', '日本語タイトル', 'English Title', 'サークル', 'Circle EN',
               '2021-05-06 16:00:00', '//img.dlsite.jp/a.jpg', 'https://www.dlsite.com/', {SEEN}),
              ('RJ07654321', 'appx', 'スマホ版', NULL, 'サークル', NULL,
               '2021-05-06', NULL, 'https://www.dlsite.com/', {SEEN}),
              ('RJ09999999', 'maniax', 'Removed work', NULL, NULL, NULL,
               '2021-05-06', NULL, 'https://www.dlsite.com/', {SEEN});
            UPDATE dlsite_works SET removed_at = '2026-01-02' WHERE product_id = 'RJ09999999';
            INSERT INTO vndb_vns (vn_id, title, alttitle, developers, released, cover_url, url,
              first_seen, last_seen) VALUES
              ('v77', 'VN Title', '原題', '["VN Dev"]', '2020-01-02',
               'https://t.vndb.org/cv/1.jpg', 'https://vndb.org/v77', {SEEN}),
              ('v88', 'Planned VN', NULL, '[]', '2999-01-01', NULL, 'https://vndb.org/v88', {SEEN});
            INSERT INTO ryuu_posts (post_id, title, raw_title, developer, version, published_at,
              updated_at, cover_url, url, first_seen, last_seen) VALUES
              (5, 'Ryuu Game', '[ENG] Ryuu Game', 'Ryuu Dev', 'RJ01234567', '2019-02-03T00:00:00Z',
               '2026-06-01T00:00:00Z', 'https://www.ryuugames.com/c.jpg', 'https://www.ryuugames.com/?p=5', {SEEN});
            INSERT INTO links (src_a, id_a, src_b, id_b, origin) VALUES
              ('ryuugames', '5', 'steam', '111', 'ryuugames');
            """
        )
        if groups:
            _ = conn.executemany(
                "INSERT INTO game_groups (src, id, group_key) VALUES (?, ?, ?)",
                [
                    ("f95", "100", "f95:100"),
                    ("lc", "300", "f95:100"),
                    ("dlsite", "RJ01234567", "dlsite:RJ01234567"),
                    ("vndb", "v77", "dlsite:RJ01234567"),
                    ("f95", "200", "f95:200"),
                    ("lc", "400", "lc:400"),
                    ("vndb", "v88", "vndb:v88"),
                    ("ryuugames", "5", "ryuugames:5"),
                ],
            )
        else:
            _ = conn.execute("DROP TABLE game_groups")
    conn.close()
    return path
