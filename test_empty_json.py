import json
import os
from core.api import Api
from core.database import init_db
from core.library_backup import build_library_backup

db_path = os.path.expanduser("~/.local/share/wLib/wlib.db")
if os.path.exists(db_path):
    os.remove(db_path)
init_db()

api = Api()
# try to import weird json
export_path = os.path.expanduser("~/weird.json")
with open(export_path, "w") as f:
    f.write(json.dumps({
        "format": "wlib.library_migration",
        "format_version": 1,
        "games": [
            {
                "metadata": {
                    "title": "Bad Target Game",
                    "f95_url": "https://f95zone.to/threads/bad.1/"
                },
                "launch_targets": [
                    {
                        "label": "Bad",
                        # no exe_path!
                    }
                ]
            }
        ]
    }))

print(api.import_library_backup(export_path))

# test missing metadata url but title exists
with open(export_path, "w") as f:
    f.write(json.dumps({
        "format": "wlib.library_migration",
        "format_version": 1,
        "games": [
            {
                "metadata": {
                    "title": "Bad Game No URL",
                }
            }
        ]
    }))

print(api.import_library_backup(export_path))
