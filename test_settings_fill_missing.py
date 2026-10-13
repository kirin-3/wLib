import json
import sqlite3
import os
from core.database import init_db, update_setting
from core.api import Api

db_path = os.path.expanduser("~/.local/share/wLib/wlib.db")
if os.path.exists(db_path):
    os.remove(db_path)
init_db()

update_setting("enable_logging", "true")

with open(os.path.expanduser("~/export.json"), "w") as f:
    f.write(json.dumps({
        "format": "wlib.library_migration",
        "format_version": 1,
        "selected_sections": ["settings_general"],
        "games": [],
        "settings": {
            "general": {
                "enable_logging": "false",
            }
        }
    }))

api = Api()
api.import_library_backup(os.path.expanduser("~/export.json"), options={"merge_mode": "fill_missing"})
print("enable_logging should be True (fill_missing shouldn't overwrite):", api.get_settings()["enable_logging"])
