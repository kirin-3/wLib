# Database & Schema

## Library sync change tracking

`init_db()` adds idempotent `sync_games_insert`, `sync_games_delete`, and `sync_games_update` SQLite triggers. Updates track only metadata/user-state columns, including ratings, favorites, play status, finished version, thread completion, notes, playtime, and dates. Executable paths, launch modes/configuration, launch targets, and path settings do not mark shared data as changed. Settings insert/update/delete triggers track only `GENERAL_SETTINGS_KEYS`. No-op updates do not advance the time; writes within one millisecond advance monotonically. Triggers write UTC ISO `sync_local_modified_at` and never react to their own state writes. Because `CREATE TRIGGER IF NOT EXISTS` would keep a pre-existing trigger's column list, `init_db()` drops and recreates `sync_games_update` on every start so databases upgraded after new columns (such as `thread_completed`) watch them too.

Local settings keys, all excluded from manual exports/imports and the sync file:

- `sync_location`, `sync_folder_path`: configured provider and folder.
- `sync_dropbox_refresh_token`, `sync_dropbox_account`: local credential and display name; access tokens stay in memory.
- `sync_gdrive_refresh_token`, `sync_gdrive_account`: the same for Google Drive; the account is the Google email.
- `sync_encryption_key`, `sync_encryption_salt`: base64 derived key and salt, never a passphrase.
- `sync_encryption_change`: pending encryption-format change requiring a write.
- `sync_local_modified_at`, `sync_last_synced_at`, `sync_last_revision`: change detection and successful file revision.
- `sync_last_error`, `sync_last_device`: display status.

`update_settings` commits several state keys atomically. Mirror imports use one write transaction, preserve local launch data, delete unmatched games with foreign-key cascade to their targets, and protect ambiguous candidates. Pulls set the winning timestamps in that transaction to cancel self-triggered change bumps. A local snapshot at `<data dir>/sync/last-local.json` contains all allowlisted backup sections for undoing a mistaken pull.

wLib manages all relational states locally via a single SQLite database file.

**Location**: `~/.local/share/wLib/wlib.db` on Linux or `%LOCALAPPDATA%\wLib\wlib.db` on Windows. `WLIB_DATA_DIR` overrides the root for isolated smoke and packaging tests.

Both platforms use the same schema and additive migrations. Windows MSI and portable ZIP builds share the same database location; MSI uninstall preserves user data. See [Platform Paths](architecture.md#platform-paths) for data-directory overrides and Linux XDG behavior.

## Engine Configuration
Upon startup, `init_db()` in `core/database.py` executes `PRAGMA journal_mode=WAL` (Write-Ahead Logging). The mode is stored in the database file, so later connections inherit it. This is crucial because `pywebview`, the extension server, and HTTP scraping workers all operate on independent threads. `WAL` mode allows concurrent readers alongside a single active writer.

### WAL Mode Advantages

- **Concurrent Access**: Multiple threads can read from the database simultaneously while one thread writes
- **Crash Recovery**: WAL mode provides better crash recovery guarantees with atomic commit semantics
- **Performance**: Write operations don't block read operations, improving UI responsiveness during background tasks
- **Thread Safety**: Essential for wLib's multi-threaded architecture where the UI, extension server, and scraper all access the database concurrently

## Schema Versioning
wLib avoids heavyweight migration libraries like Alembic in favor of a lightweight auto-patching approach.
The `init_db()` function creates missing tables and indexes, inserts missing default settings with `INSERT OR IGNORE`, and programmatically inspects `PRAGMA table_info(games)` during boot. If the application updates require new columns, `init_db` automatically executes `ALTER TABLE` statements conditionally to bring the schema up to date. It also performs additive data normalization for evolved fields such as `play_status` and `launch_mode`, allowing older stored values to be mapped into the current canonical sets during startup.

For example, the `0.3.3` to `0.3.4` upgrade adds `games.launch_mode`, the `game_launch_targets` table/index, and the `rpgmaker_linux_runner_path` setting without rewriting existing game rows or settings.

### Play Status Normalization

The `play_status` field supports canonical values `Not Started`, `Plan to Play`, `Playing`, `Waiting For Update`, `On Hold`, `Completed`, `Abandoned`, plus custom values. Unknown non-empty names are trimmed and capped at 40 characters before legacy fallback, preserving custom statuses during saves, startup migration, and backup import. During startup, `init_db()` normalizes legacy values:

- Empty or NULL values → `Not Started`
- Legacy `status` field values are migrated to `play_status`, then cleared so the migration runs once and later user choices are kept
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
| `command_line_args` | `TEXT` | Launch arguments, or the complete command for Linux `custom` mode. |
| `run_japanese_locale` | `BOOLEAN` | Overrides `LC_ALL` to Japanese on Linux; preserved but ignored on Windows. |
| `run_wayland` | `BOOLEAN` | Applies Linux Wayland compatibility settings; preserved but ignored on Windows. |
| `auto_inject_ce` | `BOOLEAN` | Enables Cheat Engine injection for Linux Wine/Proton launches. |
| `custom_prefix` | `TEXT` | Per-game Wine prefix override, used on Linux. |
| `proton_version` | `TEXT` | Per-game Proton path override, used on Linux. |
| `launch_mode` | `TEXT` | Per-game runtime selector: `auto`, `native`, `wine_proton`, `rpgmaker_linux`, or `custom`. Defaults and invalid values normalize to `auto`; Windows launches support only `auto`. |
| `playtime_seconds` | `INTEGER` | Total accumulated seconds played. |
| `last_played` | `TIMESTAMP` | ISO timestamp of last launch. |
| `date_added` | `TIMESTAMP` | ISO timestamp when added to library. |
| `status` | `TEXT` | Legacy status field (migrated to `play_status`, then cleared on startup). |
| `play_status` | `TEXT` | Canonical or custom status, at most 40 characters. New games default to `Not Started`; legacy values are normalized during startup. |
| `finished_version` | `TEXT` | Free-text version the user finished, independent of `version`. Empty means not finished. Values are trimmed on save. Added with `TEXT DEFAULT ''`; existing games are never backfilled — a `Completed` game may have been updated since it was finished, so copying the installed version would be a guess. |
| `is_favorite` | `BOOLEAN` | Favorite flag for library filtering. |
| `thread_main_post_last_edit_at` | `TIMESTAMP` | Last edit timestamp from F95Zone thread main post. |
| `thread_main_post_checked_at` | `TIMESTAMP` | When the thread was last checked for updates. |
| `thread_completed` | `BOOLEAN DEFAULT 0` | Whether the F95 thread currently has a `Completed` prefix. Added to existing databases during `init_db()`. |
| `collections` | `TEXT DEFAULT '[]'` | JSON array of collection names the game belongs to. Read back as a decoded `list[str]`, matched against the collection list case-insensitively; names that no longer exist are hidden. Part of `SYNC_GAME_COLUMNS`, so membership changes bump the sync time. |
| `screenshots` | `TEXT DEFAULT '[]'` | JSON list of remote screenshot URLs collected from the thread starter post. Refreshed from thread page reads alongside thread edit metadata; an empty page result keeps the stored list. Read back as a decoded `list[str]` of non-empty strings, empty when none are stored. Not user-editable (`update_game` ignores it) and not part of `SYNC_GAME_COLUMNS`, so a refresh alone does not bump the sync time. |

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

Notable launcher settings include `proton_path`, `wine_prefix_path`, `enable_logging`, and `rpgmaker_linux_runner_path`. The RPGMaker Linux runner path is optional; when empty, wLib detects common external install locations at runtime instead of storing a derived path.

`custom_play_statuses` stores a JSON list of user-defined names (default `[]`). Removing a name through Settings resets affected games to `Not Started` atomically with the setting update. `urm_rpa_path` stores the optional URM source archive path (default empty). Neither requires a schema change. Backups include the status list under general settings and URM under machine-specific paths; game statuses survive imports without settings.

`collections` stores the ordered JSON list of user-defined collection names (default `[]`). `get_collections()` validates it on read like custom play statuses: names of 1–40 characters, unique ignoring case, never `All` (reserved for the built-in tab). `create_collection`, `rename_collection`, `delete_collection`, `reorder_collections`, and `set_game_collections` each rewrite the setting and the affected games' `collections` column in one transaction. Renames rewrite matching memberships (case-insensitive) so members are kept; deletes strip only that membership. Both `collections` and `library_filter_presets` sit in `GENERAL_SETTINGS_KEYS`, so backups and sync carry them and the settings triggers watch them.

`library_filter_presets` stores a JSON list of `{name, filterCollection, filterStatuses, filterInstallState, filterEngines, filterTags, sortBy, sortDir}` objects (default `[]`). `get_library_filter_presets()` drops entries without a 1–40 character name and case-insensitive duplicate names; the frontend further normalises each preset through `normalizeFilterPreset` so unknown engines/tags are dropped on apply.

## JSON Migration Backups

wLib's import/export flow uses a semantic JSON file rather than copying `wlib.db` directly. This avoids WAL sidecar issues, schema drift, and local primary-key coupling when moving a library between machines.

- **Format metadata**: Exports include `format`, `format_version`, `exported_at`, app metadata, and the selected optional sections.
- **Always-included game metadata**: Every exported game includes title, developer, engine, tags, F95 URL, local/latest version fields, and cover reference.
- **Optional sections**: Users can include user state (play status, ratings, notes, favorites, playtime, timestamps), launch configuration, executable paths, additional launch targets, general settings, and machine-specific path settings.
- **Import matching**: Imports match by normalized F95 thread identity first. Games without F95 URLs fall back to normalized title/developer only when the match is unambiguous; records with no developer or executable match a unique same-title local game that has no F95 URL or developer. A record whose thread URL is not in the library merges into a unique URL-less local game with the same title/developer.
- **Merge semantics**: For matched games, backup values win for always-included metadata and selected optional sections; unselected optional sections preserve local values. Playtime is overwritten from the backup when user state is selected and is not summed.
- **Excluded data**: Cookies, webview storage, downloaded runtimes, extension copies, caches, logs, and embedded cover image files are not part of JSON backups.
- **Linux/Windows migration**: Per-game paths and runtime options can be imported without automatic path conversion. Update missing executable/launch-target paths on the destination and select Auto Detect for Windows launches. Foreign global path settings (for example, a Linux Wine prefix imported on Windows) are reported as `foreign_path` during inspection and skipped during import, preserving the destination setting.
