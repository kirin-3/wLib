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
game1 = api.add_game("Game 1", "/game1")["id"]

# Backup has 1 game with "Col1" and "Col2"
api.create_collection("Col1")
api.create_collection("Col2")
api.set_game_collections(game1, ["Col1"])
export_path = os.path.expanduser("~/export.json")
api.export_library_backup(destination_path=export_path)

api.delete_collection("Col1")
api.delete_collection("Col2")
api.create_collection("LocalCol")
api.set_game_collections(game1, ["LocalCol"])

api.import_library_backup(export_path, options={"merge_mode": "fill_missing"})
print("Merge Fill Missing:", api.get_collections())
