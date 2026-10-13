import os
import json
from core.database import init_db, get_connection
from core.api import Api

# setup clean DB
db_path = os.path.expanduser("~/.local/share/wLib/wlib.db")
if os.path.exists(db_path):
    os.remove(db_path)

api = Api()
init_db()

# Create a game manually
game_id = api.add_game("Game 1", "/game1.exe", "https://f95zone.to/threads/game.1/")["id"]

# export
export_path = os.path.expanduser("~/export.json")
api.export_library_backup(destination_path=export_path)

# modify original game
api.update_game(game_id, {"title": "Game 1 - modified"})

# Create another game in DB so that id generation would give id=2
api.add_game("Game 2", "/game2.exe", "https://f95zone.to/threads/game.2/")

# Now delete the first game
api.delete_game(game_id)

# Now import the first game
api.import_library_backup(export_path)

games_after = api.get_games()
print("Games after import:")
for g in games_after:
    print(g["id"], g["title"], g["f95_url"])

print("Targets for imported game:")
print(api.get_launch_targets(3)) # newly created one?

# Now test duplicate detection logic
if os.path.exists(db_path):
    os.remove(db_path)
init_db()
g1 = api.add_game("Same", "/test1.exe", "https://f95zone.to/threads/game.1/")["id"]
api.add_launch_target = api.create_launch_target
api.add_launch_target(g1, "target1", "/target1.exe")
api.export_library_backup(destination_path=export_path)

# change url
api.update_game(g1, {"f95_url": "https://f95zone.to/threads/game.2/"})

# re-import. Should fall back to title match?
api.import_library_backup(export_path)

games_after2 = api.get_games()
for g in games_after2:
    print(g["id"], g["title"], g["f95_url"])
    print(api.get_launch_targets(g["id"]))
