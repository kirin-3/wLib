import json
import sqlite3
import os
from core.database import init_db
from core.api import Api
from core.library_backup import apply_library_backup

db_path = os.path.expanduser("~/.local/share/wLib/wlib.db")
if os.path.exists(db_path):
    os.remove(db_path)
init_db()
api = Api()

api.create_collection("Existing")

with open(os.path.expanduser("~/export.json"), "w") as f:
    f.write(json.dumps({
        "format": "wlib.library_migration",
        "format_version": 1,
        "selected_sections": ["settings_general"],
        "games": [],
        "settings": {
            "general": {
                "collections": '["New"]',
                "enable_logging": "false"
            }
        }
    }))

api.import_library_backup(os.path.expanduser("~/export.json"), options={"merge_mode": "fill_missing"})
print("Cols:", api.get_collections())

with open(os.path.expanduser("~/export.json"), "w") as f:
    f.write(json.dumps({
        "format": "wlib.library_migration",
        "format_version": 1,
        "selected_sections": ["settings_general"],
        "games": [],
        "settings": {
            "general": {
                "collections": '["New", "Both"]'
            }
        }
    }))
api.import_library_backup(os.path.expanduser("~/export.json"), options={"merge_mode": "fill_missing"})
print("Cols:", api.get_collections())
