import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'wlib.db')

def get_connection():
    return sqlite3.connect(DB_PATH)

def init_db():
    conn = get_connection()
    cursor = conn.cursor()
    
    # Create the Games table
    # f95_url: the F95Zone thread URL to scrape
    # exe_path: the path to the main game executable
    # version: the last known version string from F95Zone
    # progress: optional user notes or completion status
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS games (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            f95_url TEXT,
            exe_path TEXT NOT NULL,
            version TEXT,
            progress TEXT DEFAULT '',
            developer TEXT DEFAULT '',
            last_played TIMESTAMP,
            cover_image_path TEXT
        )
    ''')
    
    # Create a table for Settings (like Proton Path, Default Prefix Path)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    ''')
    
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('proton_path', '')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('wine_prefix_path', '')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('enable_logging', 'false')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('auto_update_check', 'weekly')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('last_update_check', '')")
    
    # Safely migrate existing DBs by adding new columns
    new_columns = [
        ("tags", "TEXT DEFAULT ''"),
        ("rating", "TEXT DEFAULT ''"),
        ("command_line_args", "TEXT DEFAULT ''"),
        ("status", "TEXT DEFAULT ''"),
        ("rating_graphics", "REAL DEFAULT 0"),
        ("rating_story", "REAL DEFAULT 0"),
        ("rating_fappability", "REAL DEFAULT 0"),
        ("rating_gameplay", "REAL DEFAULT 0"),
        ("engine", "TEXT DEFAULT ''"),
        ("latest_version", "TEXT DEFAULT ''"),
    ]
    for col_name, col_type in new_columns:
        try:
            cursor.execute(f"ALTER TABLE games ADD COLUMN {col_name} {col_type}")
        except sqlite3.OperationalError:
            pass
        
    conn.commit()
    conn.close()

# CRUD Operations for Games
def add_game(title, exe_path, f95_url='', cover_image='', tags='', rating='', developer='', engine=''):
    conn = get_connection()
    cursor = conn.cursor()
    
    # tags might be a list, so convert to comma-separated string if needed
    if isinstance(tags, list):
        tags = ", ".join(tags)

    cursor.execute(
        "INSERT INTO games (title, exe_path, f95_url, cover_image_path, tags, rating, developer, engine) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (title, exe_path, f95_url, cover_image, tags, rating, developer, engine)
    )
    game_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return game_id

def get_all_games():
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM games ORDER BY title ASC")
    games = cursor.fetchall()
    conn.close()
    return [dict(g) for g in games]

def update_game_version(game_id, version):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE games SET version = ? WHERE id = ?", (version, game_id))
    conn.commit()
    conn.close()

def delete_game(game_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM games WHERE id = ?", (game_id,))
    conn.commit()
    conn.close()

def update_game(game_id, fields):
    """Update arbitrary fields on a game row. `fields` is a dict of column->value."""
    if not fields:
        return
    allowed = {
        'title', 'exe_path', 'f95_url', 'version', 'progress', 'developer',
        'cover_image_path', 'tags', 'rating', 'command_line_args', 'status',
        'rating_graphics', 'rating_story', 'rating_fappability', 'rating_gameplay',
        'engine', 'latest_version'
    }
    # Only allow known columns
    safe_fields = {k: v for k, v in fields.items() if k in allowed}
    if not safe_fields:
        return
    
    set_clause = ", ".join([f"{k} = ?" for k in safe_fields.keys()])
    values = list(safe_fields.values()) + [game_id]
    
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(f"UPDATE games SET {set_clause} WHERE id = ?", values)
    conn.commit()
    conn.close()

# Settings Operations
def get_setting(key):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else None

def update_setting(key, value):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = ?",
        (key, value, value)
    )
    conn.commit()
    conn.close()
