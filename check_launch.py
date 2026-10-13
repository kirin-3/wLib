import sqlite3
import os
from core.database import init_db, get_connection

db_path = os.path.expanduser("~/.local/share/wLib/wlib.db")
if os.path.exists(db_path):
    os.remove(db_path)
init_db()
conn = get_connection()
conn.execute("INSERT INTO games (title, exe_path) VALUES ('A', '/a')")
conn.execute("INSERT INTO game_launch_targets (game_id, label, exe_path) VALUES (1, 'T', '/t')")
conn.commit()
for row in conn.execute("SELECT * FROM game_launch_targets").fetchall():
    print(row)
