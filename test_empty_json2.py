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

export_path = os.path.expanduser("~/export.json")
api.export_library_backup(destination_path=export_path)
with open(export_path, "r") as f:
    data = json.load(f)

print(json.dumps(data, indent=2))
