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
# Create game in DB
game_id = api.add_game("Game 1", "/game1")["id"]
api.add_launch_target = api.create_launch_target
api.add_launch_target(game_id, "Target 1", "/game1.exe")

export_path = os.path.expanduser("~/export.json")
api.export_library_backup(destination_path=export_path)
api.delete_game(game_id)

api.add_game("Game 2", "/game2")

api.import_library_backup(export_path)
print("Games", [(g["id"], g["title"]) for g in api.get_games()])
print("Targets for Game 1", api.get_launch_targets(2))
