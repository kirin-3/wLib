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

api.create_collection("Existing")

with open(os.path.expanduser("~/export.json"), "w") as f:
    f.write(json.dumps({
        "format": "wlib.library_migration",
        "format_version": 1,
        "selected_sections": ["settings_general"],
        "games": [],
        "settings": {
            "general": {
                "collections": '["New"]'
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
                "custom_play_statuses": '["Status1"]'
            }
        }
    }))

api.save_settings({"custom_play_statuses": ["LocalStatus"]})

api.import_library_backup(os.path.expanduser("~/export.json"), options={"merge_mode": "fill_missing"})
print("Statuses:", api.get_settings()["custom_play_statuses"])
