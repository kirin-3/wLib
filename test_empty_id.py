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
# try to import json with a duplicate f95 URL
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
                    "title": "Duplicate URL",
                    "f95_url": "https://f95zone.to/threads/bad.1/"
                }
            }
        ]
    }))

try:
    print(api.import_library_backup(export_path))
except Exception as e:
    print(e)

print(len(api.get_games()))
