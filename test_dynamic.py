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

# Add a game
game_id = api.add_game("Game", "/path")["id"]
# Update game with custom properties?
try:
    api.update_game(game_id, {"title": "Updated", "rating": 5.0, "collections": ["col1"]})
except Exception as e:
    print("Error:", e)

print(api.get_games()[0])
