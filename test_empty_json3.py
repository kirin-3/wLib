import json
import sqlite3
import os
from core.database import init_db
from core.api import Api

db_path = os.path.expanduser("~/.local/share/wLib/wlib.db")
if os.path.exists(db_path):
    os.remove(db_path)
init_db()

api = Api()

# Add a game
game_id = api.add_game("Game", "/path")["id"]
api.add_launch_target = api.create_launch_target
target = api.add_launch_target(game_id, "test", "/path2")

api.update_game(game_id, {"play_status": "Not Started"}) # ensure empty defaults
api.update_game(game_id, {"rating": 5.0})

export_path = os.path.expanduser("~/export.json")
api.export_library_backup(destination_path=export_path)

# modify original game
api.update_game(game_id, {"title": "Game - modified", "rating": 2.0})

api.import_library_backup(export_path, options={"merge_mode": "overwrite"})

games_after = api.get_games()
print(games_after[0]["title"], games_after[0]["rating"])

# check fill_missing
api.update_game(game_id, {"title": "Game - modified", "rating": 2.0})
api.import_library_backup(export_path, options={"merge_mode": "fill_missing"})
games_after = api.get_games()
print(games_after[0]["title"], games_after[0]["rating"])
