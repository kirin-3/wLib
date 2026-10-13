# pyright: reportMissingImports=false
# SPDX-License-Identifier: GPL-3.0-or-later
import json

import pytest
import os
from typing import cast
from core.database import (
    DEFAULT_PLAY_STATUS,
    DEFAULT_LAUNCH_MODE,
    RPGMAKER_LINUX_RUNNER_SETTING,
    SYNC_GAME_COLUMNS,
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
    get_collections,
    get_custom_play_statuses,
    get_library_filter_presets,
    get_setting,
    update_setting,
    normalize_launch_mode,
    normalize_play_status,
    reorder_game_launch_targets,
    update_custom_play_statuses,
    update_playtime,
    create_collection,
    rename_collection,
    delete_collection,
    reorder_collections,
    set_game_collections,
)


def test_sync_change_tracking():
    update_setting("sync_local_modified_at", "2000-01-01T00:00:00Z")
    game_id = add_game("Tracking", "/game.exe")
    assert get_setting("sync_local_modified_at") != "2000-01-01T00:00:00Z"
    for fields in ({"rating": "9"}, {"play_status": "Playing"}, {"is_favorite": True}):
        update_setting("sync_local_modified_at", "2000-01-01T00:00:00Z")
        update_game(game_id, fields)
        assert get_setting("sync_local_modified_at") != "2000-01-01T00:00:00Z"
    update_setting("sync_local_modified_at", "2000-01-01T00:00:00Z")
    conn = get_connection()
    conn.execute("UPDATE games SET thread_completed = 1 WHERE id = ?", (game_id,))
    conn.commit()
    conn.close()
    assert get_setting("sync_local_modified_at") != "2000-01-01T00:00:00Z"
    update_setting("sync_local_modified_at", "2000-01-01T00:00:00Z")
    update_playtime(game_id, 3)
    assert get_setting("sync_local_modified_at") != "2000-01-01T00:00:00Z"
    update_setting("sync_local_modified_at", "2000-01-01T00:00:00Z")
    update_game(game_id, {"exe_path": "/new.exe", "launch_mode": "native"})
    update_setting("proton_path", "/proton")
    add_game_launch_target(game_id, "Extra", "/extra.exe")
    init_db()
    init_db()
    assert get_setting("sync_local_modified_at") == "2000-01-01T00:00:00Z"
    update_setting("enable_logging", "true")
    assert get_setting("sync_local_modified_at") != "2000-01-01T00:00:00Z"
    update_setting("sync_local_modified_at", "2000-01-01T00:00:00Z")
    delete_game(game_id)
    assert get_setting("sync_local_modified_at") != "2000-01-01T00:00:00Z"


def assert_raises_value_error(message: str, callback):
    try:
        callback()
    except ValueError as exc:
        assert message in str(exc)
    else:
        raise AssertionError("Expected ValueError")


def test_init_db_removes_legacy_browser_path_and_preserves_other_settings():
    update_setting("playwright_browsers_path", "/shared/browser")
    update_setting("proton_path", "/custom/proton")
    update_setting("custom_key", "preserve")
    init_db()
    assert get_setting("playwright_browsers_path") is None
    assert get_setting("proton_path") == "/custom/proton"
    assert get_setting("custom_key") == "preserve"


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path, monkeypatch):
    """Fixture to provide a clean database for each test."""
    db_file = tmp_path / "test_wlib.db"
    monkeypatch.setattr("core.database.DB_PATH", str(db_file))
    init_db()
    yield
    if os.path.exists(db_file):
        os.remove(db_file)


def test_existing_database_gains_thread_completed_with_false_default():
    game_id = add_game("Older game", "")
    assert game_id is not None
    conn = get_connection()
    try:
        conn.execute("DROP TRIGGER sync_games_update")
        conn.execute("ALTER TABLE games DROP COLUMN thread_completed")
        conn.commit()
    finally:
        conn.close()

    init_db()

    conn = get_connection()
    row = conn.execute(
        "SELECT thread_completed FROM games WHERE id = ?", (game_id,)
    ).fetchone()
    conn.close()
    assert row is not None and row[0] == 0


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
    assert "thread_completed" in columns

    cursor.execute("PRAGMA table_info(game_launch_targets)")
    target_columns = [row[1] for row in cursor.fetchall()]
    assert "game_id" in target_columns
    assert "label" in target_columns
    assert "exe_path" in target_columns
    assert "sort_order" in target_columns

    cursor.execute("PRAGMA foreign_keys")
    assert cursor.fetchone()[0] == 1

    cursor.execute(
        "SELECT value FROM settings WHERE key = ?",
        (RPGMAKER_LINUX_RUNNER_SETTING,),
    )
    runner_setting = cursor.fetchone()
    assert runner_setting is not None
    assert runner_setting[0] == ""

    conn.close()


def test_custom_mode_and_status_survive_update_and_startup():
    assert normalize_launch_mode("custom") == "custom"
    assert normalize_play_status(" ") == DEFAULT_PLAY_STATUS
    assert normalize_play_status("x" * 45) == "x" * 40
    game_id = add_game(
        "Custom", "/tmp/game.exe", launch_mode="custom", command_line_args="xsystem35"
    )
    assert game_id is not None
    update_game(game_id, {"play_status": "  Backlog  "})
    assert get_all_games()[0]["play_status"] == "Backlog"
    conn = get_connection()
    conn.execute("UPDATE games SET status = 'completed' WHERE id = ?", (game_id,))
    conn.commit()
    conn.close()
    init_db()
    game = get_all_games()[0]
    assert game["play_status"] == "Backlog"
    assert game["launch_mode"] == "custom"
    assert game["command_line_args"] == "xsystem35"


def test_custom_status_case_only_rename_keeps_games():
    game_id = add_game("Renamed", "/tmp/game.exe")
    assert game_id is not None
    update_custom_play_statuses(["backlog"])
    update_game(game_id, {"play_status": "backlog"})
    update_custom_play_statuses(["Backlog"])
    assert get_all_games()[0]["play_status"] == "backlog"
    update_custom_play_statuses([])
    assert get_all_games()[0]["play_status"] == DEFAULT_PLAY_STATUS


def test_init_db_normalizes_legacy_f95_urls_without_merging_duplicates():
    conn = get_connection()
    conn.executemany(
        "INSERT INTO games (title, exe_path, f95_url) VALUES (?, ?, ?)",
        [
            ("Legacy", "/tmp/a.exe", "https://www.f95zone.to/threads/legacy.1/post-9"),
            ("Kept", "/tmp/b.exe", "https://f95zone.to/threads/dupe.2/"),
            ("Dupe", "/tmp/c.exe", "https://www.f95zone.to/threads/dupe.2"),
        ],
    )
    conn.commit()
    conn.close()
    init_db()
    urls = {game["title"]: game["f95_url"] for game in get_all_games()}
    assert urls == {
        "Legacy": "https://f95zone.to/threads/legacy.1/",
        "Kept": "https://f95zone.to/threads/dupe.2/",
        "Dupe": "https://www.f95zone.to/threads/dupe.2",
    }


def test_imported_custom_statuses_never_shadow_built_in_ones():
    game_id = add_game("Playing", "/tmp/game.exe")
    assert game_id is not None
    update_game(game_id, {"play_status": "Playing"})
    # An imported backup or sync file can hold any list.
    update_setting(
        "custom_play_statuses", '["playing", "replaying", " Backlog ", "backlog", 3]'
    )
    assert get_custom_play_statuses() == ["Backlog"]
    update_custom_play_statuses([])
    assert get_all_games()[0]["play_status"] == "Playing"


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


def test_launch_targets_load_past_old_sqlite_parameter_limit(monkeypatch):
    import sqlite3
    import core.database as database

    game_id = add_game(title="Last", exe_path="/tmp/main.exe")
    assert game_id is not None
    _ = add_game_launch_target(game_id, "Part 2", "/tmp/part2.exe")

    def old_sqlite_connection():
        conn = get_connection()
        _ = conn.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 999)
        return conn

    monkeypatch.setattr(database, "get_connection", old_sqlite_connection)
    targets = database.list_launch_targets_for_games([*range(100000, 101199), game_id])
    assert len(targets) == 1200
    assert [target["label"] for target in targets[game_id]] == ["Part 2"]


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
        "include all targets",
        lambda: reorder_game_launch_targets(game_id, [first["id"]]),
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
    rpgmaker_id = add_game(
        title="RPGMaker Linux Game",
        exe_path="/tmp/rpgmaker/Game.exe",
        launch_mode="rpgmaker_linux",
    )
    default_id = add_game(title="Default Game", exe_path="/tmp/default.exe")
    assert native_id is not None
    assert rpgmaker_id is not None
    assert default_id is not None

    update_game(default_id, {"launch_mode": "wine_proton"})

    games_by_id = {game["id"]: game for game in get_all_games()}
    assert games_by_id[native_id]["launch_mode"] == "native"
    assert games_by_id[rpgmaker_id]["launch_mode"] == "rpgmaker_linux"
    assert games_by_id[default_id]["launch_mode"] == "wine_proton"

    update_game(native_id, {"launch_mode": "unsupported"})
    games_by_id = {game["id"]: game for game in get_all_games()}
    assert games_by_id[native_id]["launch_mode"] == DEFAULT_LAUNCH_MODE

    assert normalize_launch_mode(None) == DEFAULT_LAUNCH_MODE
    assert normalize_launch_mode("") == DEFAULT_LAUNCH_MODE
    assert normalize_launch_mode("rpgmaker_linux") == "rpgmaker_linux"
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
    assert games_by_id[waiting_game_id]["status"] == ""

    # Recovery is one-time: a status the user picks later survives restarts.
    update_game(waiting_game_id, {"play_status": "On Hold"})
    init_db()
    games_by_id = {game["id"]: game for game in get_all_games()}
    assert games_by_id[waiting_game_id]["play_status"] == "On Hold"


def test_update_game_normalizes_legacy_play_status_values():
    game_id = add_game(title="Legacy Update", exe_path="/tmp/legacy-update.exe")
    assert game_id is not None

    update_game(game_id, {"play_status": "waiting_update"})

    games = get_all_games()
    game = next(game for game in games if game["id"] == game_id)
    assert game["play_status"] == "Waiting For Update"


def test_thread_urls_ignore_non_f95_hosts():
    from core.f95zone import extract_thread_id, thread_urls_match

    f95 = "https://f95zone.to/threads/game.123/"
    assert extract_thread_id("https://www.f95zone.to/threads/game.123/") == "123"
    assert extract_thread_id("https://example.com/threads/game.123/") == ""
    assert extract_thread_id("https://f95zone.to.evil.test/threads/game.123/") == ""
    assert not thread_urls_match(f95, "https://example.com/threads/other.123/")
    assert thread_urls_match(f95, "https://www.f95zone.to/threads/renamed.123/page-2")


def test_finished_version_starts_empty_for_new_games():
    """New games get an empty finished version, and saves trim the value."""
    game_id = add_game(title="Fresh Finished", exe_path="/tmp/fresh.exe")
    assert game_id is not None

    game = next(game for game in get_all_games() if game["id"] == game_id)
    assert game["finished_version"] == ""

    update_game(game_id, {"finished_version": "  0.5  "})
    game = next(game for game in get_all_games() if game["id"] == game_id)
    assert game["finished_version"] == "0.5"


def test_init_db_migrates_finished_version_without_backfill():
    """Games from before the column existed stay empty, Completed or not."""
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
            cover_image_path TEXT,
            play_status TEXT DEFAULT 'Not Started'
        )
    """)
    cursor.executemany(
        "INSERT INTO games (title, exe_path, version, play_status) VALUES (?, ?, ?, ?)",
        [
            ("Completed Legacy", "/tmp/a.exe", "0.5", "Completed"),
            ("Playing Legacy", "/tmp/b.exe", "0.7", "Playing"),
        ],
    )
    conn.commit()
    conn.close()

    init_db()

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(games)")
    columns = [row[1] for row in cursor.fetchall()]
    conn.close()
    assert "finished_version" in columns

    finished = {
        game["title"]: game["finished_version"] for game in get_all_games()
    }
    assert finished == {"Completed Legacy": "", "Playing Legacy": ""}


def test_sync_trigger_recreation_watches_finished_version():
    """A database with the pre-column trigger gets it recreated with the new column."""
    game_id = add_game(title="Trigger Watch", exe_path="/tmp/trigger.exe")
    assert game_id is not None

    conn = get_connection()
    old_columns = [
        column for column in SYNC_GAME_COLUMNS if column != "finished_version"
    ]
    columns_sql = ", ".join(old_columns)
    changed_sql = " OR ".join(
        f"OLD.{column} IS NOT NEW.{column}" for column in old_columns
    )
    conn.execute("DROP TRIGGER sync_games_update")
    conn.execute(
        f"CREATE TRIGGER sync_games_update AFTER UPDATE OF {columns_sql} ON games "
        f"WHEN {changed_sql} BEGIN "
        "UPDATE settings SET value = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
        "WHERE key = 'sync_local_modified_at'; END"
    )
    conn.commit()
    conn.close()

    init_db()

    update_setting("sync_local_modified_at", "2000-01-01T00:00:00Z")
    update_game(game_id, {"finished_version": "0.5"})
    assert get_setting("sync_local_modified_at") != "2000-01-01T00:00:00Z"


def test_updating_version_keeps_finished_version():
    """Editing the installed version never touches the finished version."""
    game_id = add_game(title="Version Keeps", exe_path="/tmp/keeps.exe", version="0.5")
    assert game_id is not None
    update_game(game_id, {"finished_version": "0.5"})

    update_game(game_id, {"version": "0.7"})

    game = next(game for game in get_all_games() if game["id"] == game_id)
    assert game["version"] == "0.7"
    assert game["finished_version"] == "0.5"


def test_completed_status_change_auto_fills_finished_version():
    """Moving to Completed records the installed version; leaving it keeps the value."""
    game_id = add_game(title="Auto Fill", exe_path="/tmp/autofill.exe", version="0.5")
    assert game_id is not None
    update_game(game_id, {"play_status": "Playing"})

    # Playing -> Completed records the installed version and reports it applied.
    applied = update_game(game_id, {"play_status": "Completed"})
    assert applied["finished_version"] == "0.5"
    game = next(game for game in get_all_games() if game["id"] == game_id)
    assert game["finished_version"] == "0.5"

    # Completed -> Completed leaves the recorded value alone.
    applied = update_game(game_id, {"play_status": "Completed"})
    assert "finished_version" not in applied
    game = next(game for game in get_all_games() if game["id"] == game_id)
    assert game["finished_version"] == "0.5"

    # Leaving Completed keeps the value.
    update_game(game_id, {"play_status": "Waiting For Update"})
    game = next(game for game in get_all_games() if game["id"] == game_id)
    assert game["finished_version"] == "0.5"

    # An explicit finished_version in the same change wins.
    applied = update_game(
        game_id, {"play_status": "Completed", "finished_version": "0.4"}
    )
    game = next(game for game in get_all_games() if game["id"] == game_id)
    assert game["finished_version"] == "0.4"

    # Completing again after an update records the incoming new version.
    update_game(game_id, {"play_status": "Waiting For Update"})
    applied = update_game(game_id, {"play_status": "Completed", "version": "0.7"})
    assert applied["finished_version"] == "0.7"
    game = next(game for game in get_all_games() if game["id"] == game_id)
    assert game["finished_version"] == "0.7"


def test_completed_status_change_without_stored_version_falls_back_to_incoming():
    """A Completed change with no stored version uses the incoming version."""
    game_id = add_game(title="No Stored", exe_path="/tmp/nostored.exe", version="")
    assert game_id is not None

    applied = update_game(game_id, {"play_status": "Completed", "version": "0.9"})

    assert applied["finished_version"] == "0.9"
    game = next(game for game in get_all_games() if game["id"] == game_id)
    assert game["finished_version"] == "0.9"


def test_archive_install_settings_defaults_for_fresh_and_existing_db():
    from core.database import DEFAULT_LIBRARY_FOLDER_PATTERN

    assert get_setting("library_folder") == ""
    assert get_setting("library_folder_pattern") == DEFAULT_LIBRARY_FOLDER_PATTERN
    assert (
        (get_setting("downloads_folder") or "").replace("\\", "/").endswith("Downloads")
    )
    assert get_setting("download_detection_enabled") == "true"
    assert get_setting("dismissed_download_archives") == "[]"

    # Existing DB keeps custom values; missing keys still get defaults.
    update_setting("library_folder", "/games")
    conn = get_connection()
    conn.execute("DELETE FROM settings WHERE key = 'downloads_folder'")
    conn.commit()
    conn.close()
    init_db()
    assert get_setting("library_folder") == "/games"
    assert get_setting("library_folder_pattern") == DEFAULT_LIBRARY_FOLDER_PATTERN
    assert (
        (get_setting("downloads_folder") or "").replace("\\", "/").endswith("Downloads")
    )


def test_default_downloads_folder_ignores_xdg_home_fallback(monkeypatch):
    """xdg-user-dir prints $HOME when DOWNLOAD is unset; use ~/Downloads instead."""
    import shutil
    import subprocess

    from core.database import default_downloads_folder

    home = os.path.abspath(os.path.expanduser("~"))
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/xdg-user-dir")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_a, **_k: subprocess.CompletedProcess([], 0, stdout=home + "\n"),
    )

    assert default_downloads_folder() == os.path.join(home, "Downloads")


def test_existing_database_gains_collections_with_empty_default():
    """Databases from before collections existed gain the column as []."""
    game_id = add_game("Older Collection Game", "/tmp/old.exe")
    assert game_id is not None
    conn = get_connection()
    try:
        conn.execute("DROP TRIGGER sync_games_update")
        conn.execute("ALTER TABLE games DROP COLUMN collections")
        conn.commit()
    finally:
        conn.close()

    init_db()

    conn = get_connection()
    row = conn.execute(
        "SELECT collections FROM games WHERE id = ?", (game_id,)
    ).fetchone()
    conn.close()
    assert row is not None and row[0] == "[]"

    update_setting("sync_local_modified_at", "2000-01-01T00:00:00Z")
    _ = set_game_collections(game_id, [])
    conn = get_connection()
    conn.execute(
        "UPDATE games SET collections = ? WHERE id = ?",
        ('["Backlog"]', game_id),
    )
    conn.commit()
    conn.close()
    assert get_setting("sync_local_modified_at") != "2000-01-01T00:00:00Z"


def test_collections_crud_rename_keeps_members_and_delete_removes_membership():
    backlog_id = add_game("Backlogged", "/tmp/backlog.exe")
    best_id = add_game("Best Game", "/tmp/best.exe")
    assert backlog_id is not None
    assert best_id is not None

    create_collection("Backlog", game_id=backlog_id)
    create_collection("Best of", game_id=backlog_id)
    set_game_collections(best_id, ["Best of"])
    assert get_collections() == ["Backlog", "Best of"]

    games_by_title = {game["title"]: game for game in get_all_games()}
    assert games_by_title["Backlogged"]["collections"] == ["Backlog", "Best of"]
    assert games_by_title["Best Game"]["collections"] == ["Best of"]

    # Rename keeps members.
    rename_collection("Backlog", "To play")
    games_by_title = {game["title"]: game for game in get_all_games()}
    assert games_by_title["Backlogged"]["collections"] == ["To play", "Best of"]
    assert "Backlog" not in get_collections()

    # Delete keeps games and removes only that membership.
    delete_collection("Best of")
    games_by_title = {game["title"]: game for game in get_all_games()}
    assert games_by_title["Backlogged"]["collections"] == ["To play"]
    assert games_by_title["Best Game"]["collections"] == []
    assert get_collections() == ["To play"]

    # Reorder changes the stored order without touching membership.
    create_collection("Zed")
    reorder_collections(["Zed", "To play"])
    assert get_collections() == ["Zed", "To play"]
    assert {game["title"]: game for game in get_all_games()}["Backlogged"][
        "collections"
    ] == ["To play"]


def test_collection_names_are_validated_on_create_and_rename():
    create_collection("Backlog")
    for bad_name in ("", "   ", "All", "all", "x" * 41):
        with pytest.raises(ValueError, match="1–40|reserved"):
            create_collection(bad_name)
    with pytest.raises(ValueError, match="already exists"):
        create_collection("backlog")

    with pytest.raises(ValueError, match="already exists"):
        create_collection("backlog")

    # A case-only rename is allowed and keeps the single entry.
    rename_collection("Backlog", "BACKLOG")
    assert get_collections() == ["BACKLOG"]

    create_collection("Other")
    with pytest.raises(ValueError, match="already exists"):
        rename_collection("BACKLOG", "other")
    with pytest.raises(ValueError, match="does not exist"):
        rename_collection("Missing", "Whatever")
    with pytest.raises(ValueError, match="does not exist"):
        delete_collection("Missing")
    with pytest.raises(ValueError, match="1–40"):
        rename_collection("BACKLOG", "x" * 41)


def test_imported_collection_list_is_validated_and_stale_membership_filtered():
    game_id = add_game("Synced", "/tmp/synced.exe")
    assert game_id is not None
    # A sync or import can store any list, and any membership names.
    update_setting(
        "collections",
        json.dumps([" Backlog ", "backlog", "All", "x" * 41, 3, "Best"]),
    )
    conn = get_connection()
    conn.execute(
        "UPDATE games SET collections = ? WHERE id = ?",
        ('["backlog", "Ghost", "Best", 7]', game_id),
    )
    conn.commit()
    conn.close()

    assert get_collections() == ["Backlog", "Best"]
    # Membership is matched case-insensitively against the current list and
    # canonicalized; stale names are hidden.
    game = next(game for game in get_all_games() if game["id"] == game_id)
    assert game["collections"] == ["Backlog", "Best"]


def test_set_game_collections_validates_names_and_game():
    game_id = add_game("Member", "/tmp/member.exe")
    other_id = add_game("Other Member", "/tmp/other.exe")
    assert game_id is not None
    assert other_id is not None
    create_collection("Backlog")
    create_collection("Best of")

    assert set_game_collections(game_id, ["best of", "Backlog"]) == [
        "Best of",
        "Backlog",
    ]
    game = next(game for game in get_all_games() if game["id"] == game_id)
    assert game["collections"] == ["Best of", "Backlog"]

    with pytest.raises(ValueError, match="does not exist"):
        set_game_collections(game_id, ["Ghost"])
    with pytest.raises(ValueError, match="twice"):
        set_game_collections(game_id, ["Backlog", "BACKLOG"])
    with pytest.raises(ValueError, match="Game not found"):
        set_game_collections(999999, ["Backlog"])
    with pytest.raises(ValueError, match="Game not found"):
        create_collection("With game", game_id=999999)


def test_reorder_collections_requires_every_collection_once():
    create_collection("One")
    create_collection("Two")
    with pytest.raises(ValueError, match="every collection once"):
        reorder_collections(["One"])
    with pytest.raises(ValueError, match="every collection once"):
        reorder_collections(["One", "One", "Two"])
    with pytest.raises(ValueError, match="every collection once"):
        reorder_collections(["One", "Ghost"])
    assert reorder_collections(["two", "ONE"]) == ["Two", "One"]


def test_library_filter_presets_drop_invalid_entries_and_duplicates():
    update_setting(
        "library_filter_presets",
        json.dumps(
            [
                {
                    "name": "Ren'Py now",
                    "filterCollection": "Favorites",
                    "filterStatuses": ["Playing"],
                    "filterInstallState": "installed",
                    "filterEngines": ["Ren'Py"],
                    "filterTags": ["dating-sim"],
                    "sortBy": "rating",
                    "sortDir": "desc",
                },
                {"name": "   "},
                {"nope": True},
                "corrupt",
                {"name": "ren'py now", "sortBy": "title"},
                {
                    "name": "Broken enums",
                    "filterInstallState": "bogus",
                    "sortDir": "sideways",
                    "filterStatuses": "nope",
                    "filterEngines": [1, "Ren'Py"],
                },
            ]
        ),
    )
    presets = get_library_filter_presets()
    assert [preset["name"] for preset in presets] == ["Ren'Py now", "Broken enums"]
    assert presets[0]["filterStatuses"] == ["Playing"]
    assert presets[0]["sortBy"] == "rating"
    assert presets[1]["filterInstallState"] == ""
    assert presets[1]["sortDir"] == ""
    assert presets[1]["filterStatuses"] == []
    assert presets[1]["filterEngines"] == ["Ren'Py"]

    update_setting("library_filter_presets", "not json")
    assert get_library_filter_presets() == []
    update_setting("library_filter_presets", '"also not a list"')
    assert get_library_filter_presets() == []
