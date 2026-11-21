# pyright: reportMissingImports=false
# SPDX-License-Identifier: GPL-3.0-or-later
"""An LC or F95 thread ID beyond SQLite's 64-bit range never breaks a catalog read."""

import sqlite3

import pytest

from catalog_server.store import CATALOG_SCHEMA
from core import database, game_lookup, lewdcorner

HUGE = str(2**63)
MAX = str(2**63 - 1)


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "wlib.db"))
    database.init_db()
    conn = sqlite3.connect(tmp_path / "catalog.db")
    conn.executescript(CATALOG_SCHEMA)
    conn.execute(
        "INSERT INTO lc_threads(thread_id,title,tier,url,first_seen,last_seen,screens) "
        + "VALUES (21, 'Free game', 'Free', '', '', '', '[\"https://example.org/a.webp\"]')"
    )
    conn.execute(
        "INSERT INTO f95_threads(thread_id,title,url,first_seen,last_seen,cover_url) "
        + "VALUES (5, 'F95', '', '', '', 'https://example.org/c.webp')"
    )
    conn.commit()
    yield conn
    conn.close()


def test_lc_url_parser_rejects_ids_beyond_sqlite_range():
    assert lewdcorner.extract_thread_id(f"https://lewdcorner.com/threads/{HUGE}/") == ""
    assert (
        lewdcorner.extract_thread_id(f"https://lewdcorner.com/threads/x.{HUGE}/") == ""
    )
    assert (
        lewdcorner.normalize_thread_url(f"https://lewdcorner.com/threads/{HUGE}/") == ""
    )
    assert lewdcorner.extract_thread_id(f"https://lewdcorner.com/threads/{MAX}/") == MAX
    assert (
        lewdcorner.extract_thread_id(f"https://lewdcorner.com/threads/000{MAX}/") == MAX
    )


def test_catalog_reads_skip_ids_beyond_sqlite_range(catalog):
    assert game_lookup.lc_thread_tiers(["21", HUGE]) == {"21": "Free"}
    assert game_lookup.lc_thread_screenshots(HUGE) is None
    assert game_lookup.lc_threads_screenshots(["21", HUGE]) == {
        "21": ["https://example.org/a.webp"]
    }
    assert game_lookup.f95_thread_screenshots(HUGE) is None
    assert game_lookup.f95_thread_cover(HUGE) == ""
    assert game_lookup.f95_thread_cover("5") == "https://example.org/c.webp"


def test_f95_catalog_cover_accepts_decimal_digits_in_thread_id(catalog):
    assert game_lookup.f95_thread_cover("٥") == "https://example.org/c.webp"


def test_very_long_thread_ids_are_ignored(catalog):
    digits = "9" * 5000
    assert (
        lewdcorner.extract_thread_id(f"https://lewdcorner.com/threads/{digits}/") == ""
    )
    assert game_lookup.f95_thread_cover(digits) == ""
    assert game_lookup.lc_thread_tiers(["21", digits]) == {"21": "Free"}


def test_library_loads_with_a_stored_oversized_lc_url(catalog):
    from core.api import Api

    good = database.add_game(
        "Good", "", lc_url="https://lewdcorner.com/threads/21/", lc_mode="off"
    )
    bad = database.add_game("Bad", "")
    assert good is not None and bad is not None
    # An older build could store this URL; write it the way such a row looks.
    with closing_db() as conn:
        conn.execute(
            "UPDATE games SET lc_url = ?, f95_url = ? WHERE id = ?",
            (
                f"https://lewdcorner.com/threads/{HUGE}/",
                f"https://f95zone.to/threads/{HUGE}/",
                bad,
            ),
        )
        conn.commit()

    api = Api()
    games = {game["id"]: game for game in api.get_games()}
    assert games[good]["lc_catalog_tier"] == "Free"
    assert "lc_catalog_tier" not in games[bad]
    assert api.get_game_screenshots(bad)["success"] is True


def closing_db():
    from contextlib import closing

    return closing(sqlite3.connect(database.DB_PATH))
