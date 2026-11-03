import json
import sqlite3

from core import database
from core.library_backup import apply_library_backup, build_library_backup, build_sync_backup


def test_migration_and_device_local_backup(tmp_path, monkeypatch):
    path = tmp_path / 'old.db'
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE games (id INTEGER PRIMARY KEY, title TEXT NOT NULL, exe_path TEXT NOT NULL, f95_url TEXT, version TEXT, progress TEXT, developer TEXT, last_played TIMESTAMP, cover_image_path TEXT)')
    monkeypatch.setattr(database, 'DB_PATH', str(path))
    database.init_db()
    game = database.add_game('Example', '/example')
    assert game is not None
    database.update_game(game, {'steam_shortcut': True})
    database.update_setting('steam_games_mode', 'marked')
    database.update_setting('steam_account_id', '123')
    with database.get_connection() as conn:
        conn.execute("INSERT INTO steam_shortcuts(account_id, game_id, app_id, state) VALUES ('123', ?, 100, 'linked')", (game,))
    backup = build_library_backup()
    snapshot = build_sync_backup('2026-01-01T00:00:00Z', 'other')
    for payload in (backup, snapshot):
        text = json.dumps(payload)
        assert 'steam_shortcut' not in text
        assert 'steam_games_mode' not in text
        assert 'steam_account_id' not in text
    apply_library_backup(snapshot, sync=True)
    assert database.get_game(game)['steam_shortcut'] == 1
    assert database.get_setting('steam_games_mode') == 'marked'
    with database.get_connection() as conn:
        assert conn.execute('SELECT count(*) FROM steam_shortcuts').fetchone()[0] == 1
    database.delete_game(game)
    with database.get_connection() as conn:
        assert conn.execute('SELECT count(*) FROM steam_shortcuts').fetchone()[0] == 0
