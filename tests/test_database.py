# pyright: reportMissingImports=false
import pytest
import os
from core.database import (
    init_db,
    get_connection,
    add_game,
    find_game_by_f95_url,
    update_game,
    get_all_games,
)


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path, monkeypatch):
    """Fixture to provide a clean database for each test."""
    db_file = tmp_path / "test_wlib.db"
    monkeypatch.setattr("core.database.DB_PATH", str(db_file))
    init_db()
    yield
    if os.path.exists(db_file):
        os.remove(db_file)


def test_database_initialization():
    """Test if tables are created and pragma is applied."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [row[0] for row in cursor.fetchall()]
    assert "games" in tables
    assert "settings" in tables

    # Check if the migration columns were added
    cursor.execute("PRAGMA table_info(games)")
    columns = [row[1] for row in cursor.fetchall()]
    assert "tags" in columns
    assert "run_wayland" in columns

    cursor.execute("PRAGMA foreign_keys")
    assert cursor.fetchone()[0] == 1

    conn.close()


def test_add_and_get_game():
    """Test inserting and retrieving a game."""
    game_id = add_game(
        title="Test Game", exe_path="/tmp/game.exe", tags="visual novel, rpg"
    )
    assert game_id is not None

    games = get_all_games()
    assert len(games) == 1
    assert games[0]["title"] == "Test Game"
    assert games[0]["exe_path"] == "/tmp/game.exe"
    assert games[0]["tags"] == "visual novel, rpg"


def test_add_game_with_version():
    """Test that version is stored when provided at creation time."""
    game_id = add_game(
        title="Versioned Game",
        exe_path="/tmp/versioned.exe",
        f95_url="https://f95zone.to/threads/test.12345/",
        version="1.07",
        developer="Test Dev",
    )
    assert game_id is not None

    games = get_all_games()
    game = next(g for g in games if g["id"] == game_id)
    assert game["version"] == "1.07"
    assert game["developer"] == "Test Dev"


def test_update_game_fields():
    """Test updating arbitrary fields of a game."""
    game_id = add_game(title="Test Game 2", exe_path="/tmp/game2.exe")

    update_game(
        game_id, {"title": "Updated Game 2", "rating": "9/10", "run_wayland": True}
    )

    games = get_all_games()
    game = games[0]
    assert game["title"] == "Updated Game 2"
    assert game["rating"] == "9/10"
    assert game["run_wayland"] == 1


def test_update_invalid_field():
    """Test updating a non-existent field is ignored safely."""
    game_id = add_game(title="Test Game 3", exe_path="/tmp/game3.exe")

    # "invalid_column" is not in allowed fields
    update_game(game_id, {"title": "Changed Title", "invalid_column": "should ignore"})

    games = get_all_games()
    assert games[0]["title"] == "Changed Title"


def test_find_game_by_f95_url_matches_equivalent_thread_variants():
    game_id = add_game(
        title="Variant Match",
        exe_path="/tmp/variant.exe",
        f95_url="https://f95zone.to/threads/original-slug.12345/",
    )

    lookup_urls = [
        "https://f95zone.to/threads/renamed-slug.12345/",
        "https://f95zone.to/threads/renamed-slug.12345/page-2",
        "https://f95zone.to/threads/renamed-slug.12345/?latest=1",
        "https://f95zone.to/threads/renamed-slug.12345/#post-999",
    ]

    for lookup_url in lookup_urls:
        match = find_game_by_f95_url(lookup_url)
        assert match is not None
        assert match["id"] == game_id
