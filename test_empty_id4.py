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
# try to import json with a duplicate target game id mapping
export_path = os.path.expanduser("~/export.json")
with open(export_path, "w") as f:
    f.write(json.dumps({
        "format": "wlib.library_migration",
        "format_version": 1,
        "games": [
            {
                "metadata": {
                    "title": "Good Game",
                    "f95_url": "https://f95zone.to/threads/bad.1/"
                }
            }
        ]
    }))

api.import_library_backup(export_path)

game_id = api.get_games()[0]["id"]
api.add_launch_target = api.create_launch_target
target = api.add_launch_target(game_id, "test", "/path2")

api.export_library_backup(destination_path=export_path)

os.remove(db_path)
init_db()
api.import_library_backup(export_path)

print(api.get_games()[0]["launch_targets"])
