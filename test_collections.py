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

# We must create the collection first
api.create_collection("MyCollection")
api.set_game_collections(game_id, ["MyCollection"])

export_path = os.path.expanduser("~/export.json")
api.export_library_backup(destination_path=export_path)

# Verify export
with open(export_path, "r") as f:
    data = json.load(f)
    print("Exported general settings:", data["settings"]["general"])
    print("Exported game collections:", data["games"][0]["user_state"]["collections"])

api.delete_game(game_id)
api.delete_collection("MyCollection")

api.import_library_backup(export_path)

print("Games", [(g["id"], g["title"], g["collections"]) for g in api.get_games()])
print("Collections:", api.get_collections())
