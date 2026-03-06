# Database & Schema

wLib manages all relational states locally via a single SQLite database file.

**Location**: `~/.local/share/wLib/wlib.db`

## Engine Configuration
Upon startup in `core/database.py`, the engine executes `PRAGMA journal_mode=WAL` (Write-Ahead Logging). This is crucial because `pywebview`, the `HTTPServer` extension daemon, and the Playwright scraper all operate on independent threads. `WAL` mode prevents `sqlite3.OperationalError: database is locked` exceptions by allowing concurrent readers alongside a single active writer.

## Schema Versioning
wLib avoids heavyweight migration libraries like Alembic in favor of a lightweight auto-patching approach. 
The `init_db()` function programmatically inspects `PRAGMA table_info(games)` during boot. If the application updates require new columns, `init_db` automatically executes `ALTER TABLE` statements conditionally to bring the schema up to date.

## Tables & Structures

### Table: `games`
Stores the library records and their associated configuration flags.

| Column | Type | Description |
|--------|------|-------------|
| `id` | `INTEGER` | Primary Key. |
| `title` | `TEXT` | Game name. |
| `developer` | `TEXT` | Extracted developer name. |
| `engine` | `TEXT` | Game engine (Ren'Py, RPGM, Unity). |
| `tags` | `TEXT` | Comma-separated list of tags. |
| `f95_url` | `TEXT` | F95Zone thread URL (`UNIQUE` indexed to prevent duplication). |
| `version` | `TEXT` | The local downloaded version string. |
| `latest_version` | `TEXT` | The remote version identified by the scraper. |
| `exe_path` | `TEXT` | Absolute path to the executable binary. |
| `run_japanese_locale` | `INTEGER` | Boolean (`1` or `0`). Overrides `LC_ALL` to Japanese. |
| `run_wayland` | `INTEGER` | Boolean (`1` or `0`). Forces `SDL_VIDEODRIVER=wayland`. |
| `auto_inject_ce` | `INTEGER` | Boolean (`1` or `0`). Enables Cheat Engine injection on launch. |
| `playtime_seconds` | `INTEGER` | Total accumulated seconds the process was running. |
| `last_played` | `REAL` | Unix epoch timestamp of last launch. |
| `play_status` | `TEXT` | User-defined status (e.g. `Playing`, `Completed`, `Dropped`). |

### Table: `settings`
A simple generic key-value store for application-wide persistence.

| Column | Type | Description |
|--------|------|-------------|
| `key` | `TEXT` | Primary Key string matching a setting name (e.g. `proton_path`). |
| `value` | `TEXT` | Setting string value. Rehydrated in Python/Vue depending on type. |
