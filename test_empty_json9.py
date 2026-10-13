import json
import sqlite3
import os
from core.database import init_db
from core.api import Api

db_path = os.path.expanduser("~/.local/share/wLib/wlib.db")
if os.path.exists(db_path):
    os.remove(db_path)
init_db()

export_path = os.path.expanduser("~/export.json")
with open(export_path, "w") as f:
    f.write(json.dumps({
        "format": "wlib.library_migration",
        "format_version": 1,
        "selected_sections": ["settings_general"],
        "games": [],
        "settings": {
            "general": {
                "collections": '["New"]',
            }
        }
    }))

api = Api()
api.import_library_backup(export_path, options={"merge_mode": "fill_missing"})
print("Collections (expected ['New']):", api.get_collections())

with open(export_path, "w") as f:
    f.write(json.dumps({
        "format": "wlib.library_migration",
        "format_version": 1,
        "selected_sections": ["settings_general"],
        "games": [],
        "settings": {
            "general": {
                "collections": '["New", "Both"]',
            }
        }
    }))

api.import_library_backup(export_path, options={"merge_mode": "fill_missing"})
print("Collections (expected ['New']):", api.get_collections())
