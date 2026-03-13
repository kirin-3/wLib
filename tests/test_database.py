# pyright: reportMissingImports=false
import pytest
import os
from core.database import (
    DEFAULT_PLAY_STATUS,
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
    assert "thread_main_post_last_edit_at" in columns
    assert "thread_main_post_checked_at" in columns

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
    assert games[0]["play_status"] == DEFAULT_PLAY_STATUS


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
    assert game_id is not None

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
    assert game_id is not None

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


def test_find_game_by_f95_url_returns_play_status_for_extension_matches():
    game_id = add_game(
        title="Status Match",
        exe_path="/tmp/status.exe",
        f95_url="https://f95zone.to/threads/status-slug.56789/",
    )
    assert game_id is not None

    update_game(game_id, {"play_status": "waiting_update"})

    match = find_game_by_f95_url(
        "https://f95zone.to/threads/renamed-status-slug.56789/page-3?latest=1#post-9"
    )

    assert match is not None
    assert match["id"] == game_id
    assert match["play_status"] == "Waiting For Update"


def test_init_db_recovers_legacy_statuses_and_preserves_existing_plan_to_play():
    waiting_game_id = add_game(title="Waiting", exe_path="/tmp/waiting.exe")
    abandoned_game_id = add_game(title="Abandoned", exe_path="/tmp/abandoned.exe")
    planned_game_id = add_game(title="Planned", exe_path="/tmp/planned.exe")
    missing_game_id = add_game(title="Missing", exe_path="/tmp/missing.exe")
    assert waiting_game_id is not None
    assert abandoned_game_id is not None
    assert planned_game_id is not None
    assert missing_game_id is not None

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE games SET play_status = ?, status = ? WHERE id = ?",
        ("On Hold", "waiting_update", waiting_game_id),
    )
    cursor.execute(
        "UPDATE games SET play_status = ?, status = ? WHERE id = ?",
        ("On Hold", "abandoned", abandoned_game_id),
    )
    cursor.execute(
        "UPDATE games SET play_status = ?, status = ? WHERE id = ?",
        ("Plan to Play", "", planned_game_id),
    )
    cursor.execute(
        "UPDATE games SET play_status = ?, status = ? WHERE id = ?",
        ("", "", missing_game_id),
    )
    conn.commit()
    conn.close()

    init_db()

    games_by_id = {game["id"]: game for game in get_all_games()}
    assert games_by_id[waiting_game_id]["play_status"] == "Waiting For Update"
    assert games_by_id[abandoned_game_id]["play_status"] == "Abandoned"
    assert games_by_id[planned_game_id]["play_status"] == "Plan to Play"
    assert games_by_id[missing_game_id]["play_status"] == DEFAULT_PLAY_STATUS


def test_update_game_normalizes_legacy_play_status_values():
    game_id = add_game(title="Legacy Update", exe_path="/tmp/legacy-update.exe")
    assert game_id is not None

    update_game(game_id, {"play_status": "waiting_update"})

    games = get_all_games()
    game = next(game for game in games if game["id"] == game_id)
    assert game["play_status"] == "Waiting For Update"
