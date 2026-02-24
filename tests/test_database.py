import os
import sqlite3
import pytest
from core import database

# Use a temporary database for testing
@pytest.fixture
def temp_db(tmp_path):
    db_file = tmp_path / "test_wlib.db"
    # Patch the DB_PATH in database module
    original_db_path = database.DB_PATH
    database.DB_PATH = str(db_file)

    database.init_db()

    yield db_file

    # Teardown
    database.DB_PATH = original_db_path
    if os.path.exists(str(db_file)):
        os.remove(str(db_file))

def test_init_db(temp_db):
    conn = sqlite3.connect(str(temp_db))
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = [row[0] for row in cursor.fetchall()]
    assert "games" in tables
    assert "settings" in tables
    conn.close()

def test_add_get_game(temp_db):
    game_id = database.add_game("Test Game", "/path/to/exe", "http://f95.com/123", tags="RPG, Adult")
    games = database.get_all_games()
    assert len(games) == 1
    assert games[0]["title"] == "Test Game"
    assert games[0]["tags"] == "RPG, Adult"

def test_update_game(temp_db):
    game_id = database.add_game("Old Title", "/path/to/exe")
    database.update_game(game_id, {"title": "New Title", "rating": "5/5"})
    games = database.get_all_games()
    assert games[0]["title"] == "New Title"
    assert games[0]["rating"] == "5/5"

def test_delete_game(temp_db):
    game_id = database.add_game("To Delete", "/path")
    database.delete_game(game_id)
    games = database.get_all_games()
    assert len(games) == 0

def test_settings(temp_db):
    database.update_setting("theme", "dark")
    val = database.get_setting("theme")
    assert val == "dark"

    database.update_setting("theme", "light")
    val = database.get_setting("theme")
    assert val == "light"

def test_concurrency(temp_db):
    import threading

    def add_games():
        for i in range(10):
            database.add_game(f"Game {i}", f"/path/{i}")

    threads = [threading.Thread(target=add_games) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    games = database.get_all_games()
    assert len(games) == 50
