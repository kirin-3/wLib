# Database & Schema

## Tag colors

`tag_colors` is a general setting containing a JSON object with trimmed, lowercase,
case-insensitive tag names as keys and swatch names as values: `red`, `orange`,
`yellow`, `green`, `teal`, `blue`, `purple`, `pink`. `get_tag_colors()` returns `{}`
when absent and drops invalid entries on read. General-settings backup, import,
sync, and local change tracking include this setting; omitting general settings
from an import leaves local colors intact.

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
| `engine` | `TEXT` | Detected engine label (for example Ren'Py, RPGM, Unity, Unreal Engine, Godot, Wolf RPG, HTML). |
| `tags` | `TEXT` | Comma-separated list of tags. |
| `f95_url` | `TEXT` | F95Zone thread URL. Unique index protects against exact duplicates; the app also rejects equivalent F95 thread URL variants by thread identity. |
| `version` | `TEXT` | The local downloaded version string. |
| `latest_version` | `TEXT` | The remote version identified by the scraper. |
| `last_played_version` | `TEXT DEFAULT ''` | Launch-time version whose normalized session durations reached 300 seconds. Empty for existing games until played. Written by play ticks and user-state import. |
| `skipped_version` | `TEXT DEFAULT ''` | Latest release the user chose to skip. `update_game` accepts it and clears it when the installed version changes after normalization. |
| `exe_path` | `TEXT` | Absolute path to the executable binary. |
| `cover_image_path` | `TEXT` | Remote cover image URL. |
| `cover_local` | `TEXT` | Device-only random filename under `<data dir>/covers/`; NULL when unset. |
| `progress` | `TEXT` | Notes markdown shown in the game detail Notes tab (empty by default, saved with Save Changes, carried by backups and sync). |
| `rating` | `TEXT` | Overall user rating. |
| `rating_graphics` | `REAL` | Graphics rating (0-5). |
| `rating_story` | `REAL` | Story rating (0-5). |
| `rating_fappability` | `REAL` | Fappability rating (0-5). |
| `rating_gameplay` | `REAL` | Gameplay rating (0-5). |
| `command_line_args` | `TEXT` | Launch arguments, or the complete command for Linux `custom` mode. |
| `run_japanese_locale` | `BOOLEAN` | Overrides `LC_ALL` to Japanese on Linux; preserved but ignored on Windows. |
| `run_wayland` | `BOOLEAN` | Applies Linux Wayland compatibility settings; preserved but ignored on Windows. |
| `auto_inject_ce` | `BOOLEAN` | Enables Cheat Engine injection for Linux Wine/Proton launches. |
| `sandbox_enabled` | `BOOLEAN DEFAULT 0` | Per-game sandbox preference, shared by all launch targets. Added additively with sandboxing off for existing games. New games follow `sandbox_new_games` unless explicitly set. Machine-specific launch configuration: exported when selected, never synced. |
| `custom_prefix` | `TEXT` | Per-game Wine prefix override, used on Linux. |
| `proton_version` | `TEXT` | Per-game Proton path override, used on Linux. |
| `save_path_override` | `TEXT DEFAULT ''` | Absolute saves folder chosen for the game; empty means use detection. Machine-specific: exported with launch configuration, never synced. Added additively; `update_game` accepts it, the UI sets it through `set_game_save_folder`. |
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

### Table: `game_links`

Store identities are added by `init_db()` without changing `games.f95_url`.
Existing games start with no links.

| Column | Type | Description |
|--------|------|-------------|
| `id` | `INTEGER PRIMARY KEY` | Local link identity. |
| `game_id` | `INTEGER NOT NULL` | References `games.id`, with `ON DELETE CASCADE`. |
| `source` | `TEXT NOT NULL` | `dlsite`, `steam`, `itch`, `fanza`, `patreon`, `subscribestar`, `gumroad`, `vndb`, `ryuugames`, or `other`. |
| `external_id` | `TEXT NOT NULL` | Stable site identity derived by `core/store_links.py`. |
| `url` | `TEXT NOT NULL` | HTTP(S) URL, without fragment or tracking query. Product identity queries (`cid` and `p`) are retained. |
| `origin` | `TEXT NOT NULL` | `f95_post`, `page`, `user`, or `backup`. |
| `created_at` | `TIMESTAMP` | ISO timestamp when the row was inserted. |

`UNIQUE(game_id, source, external_id)` prevents duplicate identities within a
game. The non-unique `idx_game_links_identity(source, external_id)` index allows
lookups to report multiple matching games without choosing one to merge.
`find_games_by_links` matches exact work IDs only; `patreon`, `subscribestar`,
`gumroad`, and `other` never match games. F95 URLs are not stored here.

`get_game_links(game_id)` returns `{source, id, url}` records in insertion order.
`replace_game_links(game_id, origin, links)` normalizes incoming URLs and replaces
only rows from that origin. An identity already held by another origin stays
with that origin. Identical rows are not rewritten; changed URLs are deleted
and inserted. `sync_game_links_insert` and `sync_game_links_delete` advance
`sync_local_modified_at` using the same monotonic UTC rule as game changes.
No-op replacements leave the sync timestamp unchanged.

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

### Tables: `companion_tools` and `game_companion_tools`

`init_db()` creates both tables additively. Existing libraries start with no tools or overrides. Tool names have a unique index using `COLLATE NOCASE`; definitions are listed by `sort_order, id`.

| `companion_tools` column | Type / default | Meaning |
|---|---|---|
| `id` | INTEGER primary key, autoincrement | Local tool identity. |
| `name` | TEXT NOT NULL | Unique display name, ignoring case. |
| `kind` | TEXT NOT NULL | `wine`, `native`, or `command`. |
| `path` | TEXT NOT NULL DEFAULT '' | Executable path; empty for Command tools. |
| `arguments` | TEXT NOT NULL DEFAULT '' | Arguments, or the full Command line. |
| `show_on_all_games` | INTEGER NOT NULL DEFAULT 1 | Inherited visibility. |
| `sort_order` | INTEGER NOT NULL DEFAULT 0 | Display order. |
| `created_at` | TIMESTAMP | Creation timestamp. |
| `updated_at` | TIMESTAMP | Last change timestamp. |

| `game_companion_tools` column | Type | Meaning |
|---|---|---|
| `game_id` | INTEGER NOT NULL | References `games(id) ON DELETE CASCADE`. |
| `tool_id` | INTEGER NOT NULL | References `companion_tools(id) ON DELETE CASCADE`. |
| `visible` | INTEGER NOT NULL | Explicit visibility override. |

`(game_id, tool_id)` is the composite primary key. No row means follow the tool's default; resetting removes the row. Deleting either the game or tool removes its overrides. These machine-specific tables do not mark shared sync data changed.

Semantic backups export definitions without IDs under `settings.paths.companion_tools`, and explicit overrides by tool name under each game's `launch_config.companion_tools`. Imports merge names ignoring case, retain local IDs, skip foreign-host definitions, and resolve overrides after importing tools. Backups without these fields preserve local data.

### Table: `play_sessions`

Each tracked launch adds one row. `(game_id, started_at)` is indexed; deleting a game cascades to its sessions.

| Column | Type | Description |
|--------|------|-------------|
| `id` | `INTEGER` | Local primary key; excluded from JSON backups. |
| `game_id` | `INTEGER NOT NULL` | Foreign key to `games(id) ON DELETE CASCADE`. |
| `version` | `TEXT NOT NULL DEFAULT ''` | Raw installed version captured before launch. |
| `started_at` | `TEXT NOT NULL` | ISO start timestamp. |
| `ended_at` | `TEXT` | ISO exit timestamp, null until the final tick. A crash leaves it null. |
| `duration_seconds` | `INTEGER NOT NULL DEFAULT 0` | Time counted toward the game's playtime. |

INSERT, UPDATE, and DELETE triggers on this table bump `sync_local_modified_at` using the same monotonic timestamp statement as game changes. Both new game version columns belong to `SYNC_GAME_COLUMNS`. Settings triggers are recreated on startup so upgrades also track the new `update_notifications` general setting (`on` by default, `off` to disable).

### Table: `settings`
A simple generic key-value store for application-wide persistence.

| Column | Type | Description |
|--------|------|-------------|
| `key` | `TEXT` | Primary Key string matching a setting name (e.g. `proton_path`). |
| `value` | `TEXT` | Setting string value. Rehydrated in Python/Vue depending on type. |

Notable launcher settings include `proton_path`, `wine_prefix_path`, `enable_logging` (per-game launch output, default `false`), and `rpgmaker_linux_runner_path`. The RPGMaker Linux runner path is optional; when empty, wLib detects common external install locations at runtime instead of storing a derived path.

`sandbox_new_games` defaults to `false` and belongs to `GENERAL_SETTINGS_KEYS`, so
general-settings backups and sync carry it and changes advance the local sync
timestamp. Manual add, folder import, extension add, and external-library import
apply it only when creating a game without an explicit sandbox value. Changing
the default leaves existing games unchanged. `sandboxie_custom_path` defaults to
an empty string and belongs to the machine-specific `PATH_SETTINGS_KEYS`; path
backups include it when selected, while foreign-host paths are reported and
skipped on import.

`set_sandbox_for_all_games(enabled)` updates the preference in one `UPDATE` and
returns the number of games whose value changed. Like other launch configuration,
this preference does not advance the shared metadata sync timestamp. Sandbox
storage under `<data dir>/sandbox/<game id>/` is excluded from JSON backups and
sync. Missing sandbox fields in older backups preserve a matched game's local
value.

Other persisted keys: `auto_update_check` (`weekly` by default), `last_update_check`, `update_notifications` (`on` by default), `library_folder`, `library_folder_pattern` (`{creator}/{title}/{version}`), `downloads_folder` (XDG Downloads with a `~/Downloads` fallback), `download_detection_enabled` (`true`), `dismissed_download_archives` (`[]`, local-only), and `urm_rpa_path` (empty by default).

`custom_play_statuses` stores a JSON list of user-defined names (default `[]`). Removing a name through Settings resets affected games to `Not Started` atomically with the setting update. `urm_rpa_path` stores the optional URM source archive path (default empty). Neither requires a schema change. Backups include the status list under general settings and URM under machine-specific paths; game statuses survive imports without settings.

`collections` stores the ordered JSON list of user-defined collection names (default `[]`). `get_collections()` validates it on read like custom play statuses: names of 1–40 characters, unique ignoring case, never `All` (reserved for the built-in tab). `create_collection`, `rename_collection`, `delete_collection`, `reorder_collections`, and `set_game_collections` each rewrite the setting and the affected games' `collections` column in one transaction. Renames rewrite matching memberships (case-insensitive) so members are kept; deletes strip only that membership. `collections`, `library_filter_presets`, `tag_colors`, and `save_backup_keep` sit in `GENERAL_SETTINGS_KEYS`, so backups and sync carry them and the settings triggers watch them.

`library_filter_presets` stores a JSON list of `{name, filterCollection, filterStatuses, filterInstallState, filterEngines, filterTags, sortBy, sortDir}` objects (default `[]`). `get_library_filter_presets()` drops entries without a 1–40 character name and case-insensitive duplicate names; the frontend further normalises each preset through `normalizeFilterPreset` so unknown engines/tags are dropped on apply.

`save_backup_keep` stores how many save backups to keep per game: `all` or `1`–`99` (default missing, meaning keep all). `get_save_backup_keep()` returns `None` for `all`, missing or invalid values and an int otherwise. It is in `GENERAL_SETTINGS_KEYS`, so backups and sync carry it and the settings triggers watch it.

## JSON Migration Backups

wLib's import/export flow uses a semantic JSON file rather than copying `wlib.db` directly. This avoids WAL sidecar issues, schema drift, and local primary-key coupling when moving a library between machines.

- **Format metadata**: Exports include `format`, `format_version`, `exported_at`, app metadata, and the selected optional sections.
- **Always-included game metadata**: Every exported game includes title, developer, engine, tags, F95 URL, local/latest version fields, thread completion flag, screenshot URLs, and a cover reference.
- **Optional sections**: Users can include user state (play status, ratings, notes, favorites, playtime, timestamps), launch configuration, executable paths, additional launch targets, general settings, and machine-specific path settings.
- **Import matching**: Imports match by normalized F95 thread identity first. Games without F95 URLs fall back to normalized title/developer only when the match is unambiguous; records with no developer or executable match a unique same-title local game that has no F95 URL or developer. A record whose thread URL is not in the library merges into a unique URL-less local game with the same title/developer.
- **Merge semantics**: For matched games, backup values win for always-included metadata and selected optional sections; unselected optional sections preserve local values. Playtime is overwritten from the backup when user state is selected and is not summed.
- **Excluded data**: Cookies, webview storage, downloaded runtimes, extension copies, caches, logs, save backups, attached PDFs, and embedded cover image files are not part of JSON backups.
- **Journal user state**: `last_played_version`, `skipped_version`, and `play_sessions` travel with user state. Sessions include only version, start/end time, and duration. Imports replace sessions only when that key is present, and fill-missing imports keep a game's existing sessions; missing scalar fields also preserve local values. The additive fields keep format version 1. Sync uses the same replacement rule; it does not merge sessions from two machines.
- **Game attachments**: attached PDFs live under `<data dir>/attachments/<game id>/` and are local-only; exports and sync never include PDF data or attachment names. Deleting a game removes its folder; mirror imports and sync deletions prune orphan numeric folders after the commit, and startup prunes folders for IDs not in the library. A reset or replaced database therefore removes orphan PDFs (IDs are `AUTOINCREMENT` and never reused).
- **Linux/Windows migration**: Per-game paths and runtime options can be imported without automatic path conversion. Update missing executable/launch-target paths on the destination and select Auto Detect for Windows launches. Foreign global path settings (for example, a Linux Wine prefix imported on Windows) are reported as `foreign_path` during inspection and skipped during import, preserving the destination setting.

### Device-local covers

`init_db()` adds `cover_local` without rebuilding existing tables. Only
`set_game_local_cover` and `clear_game_local_cover` write it; the generic
`update_game` allowlist, `SYNC_GAME_COLUMNS`, and backup/import field lists omit
it. Changes to this column do not advance `sync_local_modified_at`. The
`covers/` folder is created on first use. Files and references are excluded
from exports and sync; applying backups or sync preserves existing covers.
