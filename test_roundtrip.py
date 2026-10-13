import os
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

# add a game
api.add_game("Test", "/path", f95_url="https://f95zone.to/threads/test.123/")

# fetch the game
games_before = api.get_games()
print("Before export:", games_before)

# export
export_path = os.path.expanduser("~/export.json")
res = api.export_library_backup(destination_path=export_path)
print("Export result:", res)

# nuke DB
os.remove(db_path)
init_db()

# import
res = api.import_library_backup(export_path)
print("Import result:", res)

games_after = api.get_games()
print("After import:", games_after)

# check differences
import json
print("Diff?")
print(json.dumps(games_before) == json.dumps(games_after))
