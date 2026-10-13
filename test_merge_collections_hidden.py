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
# Setup DB 1 (Backup)
game1 = api.add_game("Game 1", "/game1")["id"]
api.create_collection("BackupCol")
api.set_game_collections(game1, ["BackupCol"])

export_path = os.path.expanduser("~/export.json")
api.export_library_backup(destination_path=export_path)

# Setup DB 2 (Local)
os.remove(db_path)
init_db()
api = Api()
game2 = api.add_game("Game 2", "/game2")["id"]
api.create_collection("LocalCol")
api.set_game_collections(game2, ["LocalCol"])

# Import Backup into Local
api.import_library_backup(export_path)

print("Settings Collections:", api.get_collections())
for g in api.get_games():
    print(g["title"], g["collections"])
