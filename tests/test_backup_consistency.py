from core import database, library_backup
from core.library_sync import SyncManager


def _fresh_db(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "wlib.db"))
    database.init_db()


def test_restore_into_an_empty_library_can_be_undone(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    _ = database.add_game("Game", "/game.exe")
    name = library_backup.write_sync_snapshot()
    assert name is not None
    # A pull of an empty remote library cleared this one.
    for game in database.get_all_games():
        database.delete_game(int(str(game["id"])))
    database.update_setting("custom_play_statuses", '["Later"]')

    sync = SyncManager()
    result = sync.restore_snapshot(name)
    assert result["success"], result
    assert [game["title"] for game in database.get_all_games()] == ["Game"]
    assert database.get_setting("custom_play_statuses") != '["Later"]'

    # The empty library the restore replaced is a snapshot of its own.
    snapshots = sync.list_snapshots()["snapshots"]
    assert len(snapshots) == 2
    undo = snapshots[0]
    assert undo["name"] != name and undo["games"] == 0
    assert sync.restore_snapshot(undo["name"])["success"]
    assert database.get_all_games() == []
    assert database.get_setting("custom_play_statuses") == '["Later"]'


def test_sync_snapshot_still_skips_an_empty_library(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    assert library_backup.write_sync_snapshot() is None


def test_export_pairs_playtime_with_the_session_it_came_from(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    game_id = database.add_game("Game", "/game.exe")
    assert game_id is not None
    session_id = database.start_play_session(game_id, "1.0")

    read_games = library_backup.get_all_games

    def tick_then_read() -> list[dict[str, object]]:
        # A play tick commits between the export's reads.
        _ = database.record_play_tick(game_id, session_id, 60, False)
        return read_games()

    monkeypatch.setattr(library_backup, "get_all_games", tick_then_read)
    backup = library_backup.build_library_backup(
        {"sections": [library_backup.SECTION_USER_STATE]}
    )
    games = backup["games"]
    assert isinstance(games, list) and len(games) == 1
    state = games[0]["user_state"]
    sessions = state["play_sessions"]
    assert sum(int(session["duration_seconds"]) for session in sessions) == int(
        state["playtime_seconds"]
    )
