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
# Backup with one game with id=1, containing target with game_id=1
with open(os.path.expanduser("~/export.json"), "w") as f:
    f.write(json.dumps({
        "format": "wlib.library_migration",
        "format_version": 1,
        "selected_sections": ["launch_targets", "metadata"],
        "games": [
            {
                "metadata": {
                    "title": "Good Game",
                    "f95_url": "https://f95zone.to/threads/bad.1/"
                },
                "launch_targets": [
                    {
                        "label": "Bad",
                        "exe_path": "/bad.exe",
                    }
                ]
            }
        ]
    }))

# DB with game
api.add_game("Local", "/local.exe") # gets id=1

api.import_library_backup(os.path.expanduser("~/export.json"))
print(api.get_launch_targets(2))
