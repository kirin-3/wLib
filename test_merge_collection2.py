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
api.create_collection("MyCollection")
api.set_game_collections(game_id, ["MyCollection"])
export_path = os.path.expanduser("~/export.json")
api.export_library_backup(destination_path=export_path)

api.create_collection("AnotherCollection")
api.import_library_backup(export_path, options={"merge_mode": "fill_missing"})
print(api.get_collections())
