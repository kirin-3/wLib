# pyright: reportMissingImports=false
import pytest
import os
from typing import cast
from core.database import (
    DEFAULT_PLAY_STATUS,
    DEFAULT_LAUNCH_MODE,
    init_db,
    get_connection,
    add_game,
    add_game_launch_target,
    delete_game,
    delete_game_launch_target,
    find_game_by_f95_url,
    list_game_launch_targets,
    update_game,
    update_game_launch_target,
    get_all_games,
    normalize_launch_mode,
    reorder_game_launch_targets,
)


def assert_raises_value_error(message: str, callback):
    try:
        callback()
    except ValueError as exc:
        assert message in str(exc)
    else:
        raise AssertionError("Expected ValueError")


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
    assert "game_launch_targets" in tables

    # Check if the migration columns were added
    cursor.execute("PRAGMA table_info(games)")
    columns = [row[1] for row in cursor.fetchall()]
    assert "tags" in columns
    assert "run_wayland" in columns
    assert "thread_main_post_last_edit_at" in columns
    assert "thread_main_post_checked_at" in columns
    assert "launch_mode" in columns

    cursor.execute("PRAGMA table_info(game_launch_targets)")
    target_columns = [row[1] for row in cursor.fetchall()]
    assert "game_id" in target_columns
    assert "label" in target_columns
    assert "exe_path" in target_columns
    assert "sort_order" in target_columns

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
    assert games[0]["launch_mode"] == DEFAULT_LAUNCH_MODE
    assert games[0]["launch_targets"] == []


def test_launch_targets_crud_ordering_and_game_payload():
    """Test additional launch targets stay ordered under their parent game."""
    game_id = add_game(title="Multi Part", exe_path="/tmp/main.exe")
    assert game_id is not None

    part_one = add_game_launch_target(game_id, "Part 1", "/tmp/part1.exe")
    part_two = add_game_launch_target(game_id, "Part 2", "/tmp/part2.exe")
    bonus = add_game_launch_target(game_id, "Bonus", "/tmp/bonus.exe")

    assert [target["label"] for target in list_game_launch_targets(game_id)] == [
        "Part 1",
        "Part 2",
        "Bonus",
    ]

    updated = update_game_launch_target(
        part_two["id"], {"label": "Season 2", "exe_path": "/tmp/s2.exe"}
    )
    assert updated is not None
    assert updated["label"] == "Season 2"
    assert updated["exe_path"] == "/tmp/s2.exe"

    reordered = reorder_game_launch_targets(
        game_id, [bonus["id"], part_one["id"], part_two["id"]]
    )
    assert [target["label"] for target in reordered] == [
        "Bonus",
        "Part 1",
        "Season 2",
    ]

    game = get_all_games()[0]
    assert game["exe_path"] == "/tmp/main.exe"
    launch_targets = cast(list[dict[str, object]], game["launch_targets"])
    assert [target["label"] for target in launch_targets] == [
        "Bonus",
        "Part 1",
        "Season 2",
    ]

    assert delete_game_launch_target(part_one["id"]) is True
    assert [target["label"] for target in list_game_launch_targets(game_id)] == [
        "Bonus",
        "Season 2",
    ]


def test_launch_targets_validate_required_fields():
    """Test launch target labels and paths must be non-empty."""
    game_id = add_game(title="Invalid Target", exe_path="/tmp/main.exe")
    assert game_id is not None

    assert_raises_value_error(
        "label", lambda: add_game_launch_target(game_id, "   ", "/tmp/part.exe")
    )
    assert_raises_value_error(
        "path", lambda: add_game_launch_target(game_id, "Part", "   ")
    )

    target = add_game_launch_target(game_id, "Part", "/tmp/part.exe")

    assert_raises_value_error(
        "label", lambda: update_game_launch_target(target["id"], {"label": ""})
    )
    assert_raises_value_error(
        "path", lambda: update_game_launch_target(target["id"], {"exe_path": ""})
    )


def test_launch_targets_are_deleted_with_parent_game():
    """Test deleting a parent game cascades to its additional targets."""
    game_id = add_game(title="Delete Parent", exe_path="/tmp/main.exe")
    assert game_id is not None
    add_game_launch_target(game_id, "Part 1", "/tmp/part1.exe")
    add_game_launch_target(game_id, "Part 2", "/tmp/part2.exe")

    delete_game(game_id)

    assert list_game_launch_targets(game_id) == []


def test_reorder_launch_targets_rejects_missing_or_foreign_targets():
    """Test reordering requires exactly the parent game's additional targets."""
    game_id = add_game(title="Source", exe_path="/tmp/source.exe")
    other_id = add_game(title="Other", exe_path="/tmp/other.exe")
    assert game_id is not None
    assert other_id is not None
    first = add_game_launch_target(game_id, "First", "/tmp/first.exe")
    second = add_game_launch_target(game_id, "Second", "/tmp/second.exe")
    other = add_game_launch_target(other_id, "Other", "/tmp/other-part.exe")

    assert_raises_value_error(
        "include all targets", lambda: reorder_game_launch_targets(game_id, [first["id"]])
    )
    assert_raises_value_error(
        "include all targets",
        lambda: reorder_game_launch_targets(game_id, [first["id"], other["id"]]),
    )
    assert_raises_value_error(
        "duplicate",
        lambda: reorder_game_launch_targets(
            game_id, [first["id"], first["id"], second["id"]]
        ),
    )


def test_launch_mode_is_persisted_and_normalized():
    """Test launch mode defaults, persistence, and invalid-value normalization."""
    native_id = add_game(
        title="Native Game", exe_path="/tmp/native.sh", launch_mode="native"
    )
    default_id = add_game(title="Default Game", exe_path="/tmp/default.exe")
    assert native_id is not None
    assert default_id is not None

    update_game(default_id, {"launch_mode": "wine_proton"})

    games_by_id = {game["id"]: game for game in get_all_games()}
    assert games_by_id[native_id]["launch_mode"] == "native"
    assert games_by_id[default_id]["launch_mode"] == "wine_proton"

    update_game(native_id, {"launch_mode": "unsupported"})
    games_by_id = {game["id"]: game for game in get_all_games()}
    assert games_by_id[native_id]["launch_mode"] == DEFAULT_LAUNCH_MODE

    assert normalize_launch_mode(None) == DEFAULT_LAUNCH_MODE
    assert normalize_launch_mode("") == DEFAULT_LAUNCH_MODE
    assert normalize_launch_mode("unsupported") == DEFAULT_LAUNCH_MODE


def test_init_db_migrates_legacy_games_without_launch_mode():
    """Test existing databases gain launch_mode with the safe default."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DROP TABLE games")
    cursor.execute("""
        CREATE TABLE games (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            f95_url TEXT,
            exe_path TEXT NOT NULL,
            version TEXT,
            progress TEXT DEFAULT '',
            developer TEXT DEFAULT '',
            last_played TIMESTAMP,
            cover_image_path TEXT
        )
    """)
    cursor.execute(
        "INSERT INTO games (title, exe_path) VALUES (?, ?)",
        ("Legacy Native", "/tmp/legacy.sh"),
    )
    conn.commit()
    conn.close()

    init_db()

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(games)")
    columns = [row[1] for row in cursor.fetchall()]
    cursor.execute("SELECT launch_mode FROM games WHERE title = ?", ("Legacy Native",))
    row = cursor.fetchone()
    conn.close()

    assert "launch_mode" in columns
    assert row is not None
    assert row[0] == DEFAULT_LAUNCH_MODE


def test_init_db_normalizes_invalid_stored_launch_modes():
    """Test migration startup cleans invalid launch_mode values in-place."""
    game_id = add_game(title="Invalid Mode", exe_path="/tmp/invalid.exe")
    assert game_id is not None

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE games SET launch_mode = ? WHERE id = ?",
        ("broken_mode", game_id),
    )
    conn.commit()
    conn.close()

    init_db()

    game = next(game for game in get_all_games() if game["id"] == game_id)
    assert game["launch_mode"] == DEFAULT_LAUNCH_MODE


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
