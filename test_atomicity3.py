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
game_id = api.add_game("Game 1", "/game1")["id"]
api.add_launch_target = api.create_launch_target

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
            },
            {
                "metadata": {
                    "title": "Bad Target Game",
                    "f95_url": "https://f95zone.to/threads/bad.2/"
                },
                "launch_targets": [
                    {
                        "label": "Bad",
                        "exe_path": None,
                    }
                ]
            }
        ]
    }))

try:
    print(api.import_library_backup(export_path, options={"sections": ["launch_targets", "metadata"]}))
except Exception as e:
    print(e)
print(len(api.get_games()))
