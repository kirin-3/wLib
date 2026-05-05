# Database & Schema

wLib manages all relational states locally via a single SQLite database file.

**Location**: `~/.local/share/wLib/wlib.db`

## Engine Configuration
Upon startup in `core/database.py`, the engine executes `PRAGMA journal_mode=WAL` (Write-Ahead Logging). This is crucial because `pywebview`, the `HTTPServer` extension daemon, and the Playwright scraper all operate on independent threads. `WAL` mode prevents `sqlite3.OperationalError: database is locked` exceptions by allowing concurrent readers alongside a single active writer.

### WAL Mode Advantages

- **Concurrent Access**: Multiple threads can read from the database simultaneously while one thread writes
- **Crash Recovery**: WAL mode provides better crash recovery guarantees with atomic commit semantics
- **Performance**: Write operations don't block read operations, improving UI responsiveness during background tasks
- **Thread Safety**: Essential for wLib's multi-threaded architecture where the UI, extension server, and scraper all access the database concurrently

## Schema Versioning
wLib avoids heavyweight migration libraries like Alembic in favor of a lightweight auto-patching approach. 
The `init_db()` function programmatically inspects `PRAGMA table_info(games)` during boot. If the application updates require new columns, `init_db` automatically executes `ALTER TABLE` statements conditionally to bring the schema up to date. It also performs additive data normalization for evolved fields such as `play_status`, allowing older stored values to be mapped into the current canonical status set during startup.

### Play Status Normalization

The `play_status` field uses a canonical set of values: `Not Started`, `Plan to Play`, `Playing`, `Waiting For Update`, `On Hold`, `Completed`, `Abandoned`. During startup, `init_db()` normalizes legacy values:

- Empty or NULL values → `Not Started`
- Legacy `status` field values are migrated to `play_status`
- New games default to `Not Started` automatically

This ensures consistent status values across the UI and API responses.

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
| `f95_url` | `TEXT` | F95Zone thread URL. Unique index protects against exact duplicates; the app also rejects equivalent F95 thread URL variants by thread identity. |
| `version` | `TEXT` | The local downloaded version string. |
| `latest_version` | `TEXT` | The remote version identified by the scraper. |
| `exe_path` | `TEXT` | Absolute path to the executable binary. |
| `cover_image_path` | `TEXT` | Path to local cover image. |
| `progress` | `TEXT` | User notes or completion status (legacy). |
| `rating` | `TEXT` | Overall user rating. |
| `rating_graphics` | `REAL` | Graphics rating (0-5). |
| `rating_story` | `REAL` | Story rating (0-5). |
| `rating_fappability` | `REAL` | Fappability rating (0-5). |
| `rating_gameplay` | `REAL` | Gameplay rating (0-5). |
| `command_line_args` | `TEXT` | Custom launch arguments. |
| `run_japanese_locale` | `BOOLEAN` | Overrides `LC_ALL` to Japanese. |
| `run_wayland` | `BOOLEAN` | Forces Wayland compatibility mode. |
| `auto_inject_ce` | `BOOLEAN` | Enables Cheat Engine injection on launch. |
| `custom_prefix` | `TEXT` | Per-game Wine prefix path override. |
| `proton_version` | `TEXT` | Per-game Proton path override. |
| `launch_mode` | `TEXT` | Per-game runtime selector: `auto`, `native`, or `wine_proton`. Defaults and invalid values normalize to `auto`. |
| `playtime_seconds` | `INTEGER` | Total accumulated seconds played. |
| `last_played` | `TIMESTAMP` | ISO timestamp of last launch. |
| `date_added` | `TIMESTAMP` | ISO timestamp when added to library. |
| `status` | `TEXT` | Legacy status field (migrated to `play_status`). |
| `play_status` | `TEXT` | User-defined status (`Not Started`, `Plan to Play`, `Playing`, `Waiting For Update`, `On Hold`, `Completed`, `Abandoned`). New games default to `Not Started`; older legacy values are normalized during startup. |
| `is_favorite` | `BOOLEAN` | Favorite flag for library filtering. |
| `thread_main_post_last_edit_at` | `TIMESTAMP` | Last edit timestamp from F95Zone thread main post. |
| `thread_main_post_checked_at` | `TIMESTAMP` | When the thread was last checked for updates. |

### Table: `game_launch_targets`
Stores additional executable targets for games that ship with multiple playable parts. The canonical default executable remains `games.exe_path`; this table only contains extra named targets.

| Column | Type | Description |
|--------|------|-------------|
| `id` | `INTEGER` | Primary Key. |
| `game_id` | `INTEGER` | Parent `games.id`. Deleted automatically with the parent game. |
| `label` | `TEXT` | User-facing target name, such as `Part 2` or `Bonus`. |
| `exe_path` | `TEXT` | Absolute path to the additional executable. |
| `sort_order` | `INTEGER` | Display order after the canonical default target. |
| `created_at` | `TIMESTAMP` | ISO timestamp when the target was created. |
| `updated_at` | `TIMESTAMP` | ISO timestamp when the target was last changed. |

### Table: `settings`
A simple generic key-value store for application-wide persistence.

| Column | Type | Description |
|--------|------|-------------|
| `key` | `TEXT` | Primary Key string matching a setting name (e.g. `proton_path`). |
| `value` | `TEXT` | Setting string value. Rehydrated in Python/Vue depending on type. |
