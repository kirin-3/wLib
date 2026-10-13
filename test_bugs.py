import os
import json
import sqlite3
from datetime import datetime
from core.database import init_db, get_connection, add_game, get_all_games
from core.api import Api

# setup clean DB
db_path = os.path.expanduser("~/.local/share/wLib/wlib.db")
if os.path.exists(db_path):
    os.remove(db_path)

api = Api()
init_db()

# Create a game with null fields or specific fields
conn = get_connection()
cursor = conn.cursor()
cursor.execute("INSERT INTO games (title, exe_path, f95_url, date_added, last_played, playtime_seconds, rating) VALUES ('Test Game', '/a/b.exe', 'https://f95zone.to/threads/test.1/', NULL, NULL, 0, NULL)")
conn.commit()

# add a collection and target
game_id = cursor.lastrowid
conn.execute("INSERT INTO game_launch_targets (game_id, label, exe_path, sort_order) VALUES (?, 'target1', '/a/c.exe', 1)", (game_id,))
conn.commit()
conn.close()

api.set_game_collections(game_id, ["col1", "col2"])
api.save_settings({"custom_play_statuses": ["MyStatus"]})

games_before = api.get_games()

export_path = os.path.expanduser("~/export.json")
api.export_library_backup(destination_path=export_path)

os.remove(db_path)
init_db()
api.import_library_backup(export_path)

games_after = api.get_games()

def compare_games(g1, g2):
    for k in g1:
        if g1.get(k) != g2.get(k):
            print(f"Diff in {k}: {g1.get(k)} vs {g2.get(k)}")

compare_games(games_before[0], games_after[0])
