# Database & Schema

## Tag colors

`tag_colors` is a general setting containing a JSON object with trimmed, lowercase,
case-insensitive tag names as keys and swatch names as values: `red`, `orange`,
`yellow`, `green`, `teal`, `blue`, `purple`, `pink`. `get_tag_colors()` returns `{}`
when absent and drops invalid entries on read. General-settings backup, import,
sync, and local change tracking include this setting; omitting general settings
from an import leaves local colors intact.

## Library sync change tracking

`init_db()` adds idempotent `sync_games_insert`, `sync_games_delete`, and `sync_games_update` SQLite triggers. Updates track only metadata/user-state columns, including ratings, favorites, play status, finished version, thread completion, notes, links, block_network (No internet), playtime, and dates. Executable paths, launch modes/configuration, launch targets, and path settings do not mark shared data as changed. Settings insert/update/delete triggers track only `GENERAL_SETTINGS_KEYS`. No-op updates do not advance the time; writes within one millisecond advance monotonically. Triggers write UTC ISO `sync_local_modified_at` and never react to their own state writes. If the stored timestamp cannot be parsed by SQLite, the bump falls back to the current time instead of failing the original write. Because `CREATE TRIGGER IF NOT EXISTS` would keep a pre-existing trigger's SQL and column list, `init_db()` drops and recreates every sync trigger on each start so databases upgraded after new columns (such as `thread_completed`) or trigger changes watch them too.

Local settings keys, all excluded from manual exports/imports and the sync file:

- `sync_location`, `sync_folder_path`: configured provider and folder.
- `sync_dropbox_refresh_token`, `sync_dropbox_account`: local credential and display name; access tokens stay in memory.
- `sync_gdrive_refresh_token`, `sync_gdrive_account`: the same for Google Drive; the account is the Google email.
- `sync_encryption_key`, `sync_encryption_salt`: base64 derived key and salt, never a passphrase.
- `sync_encryption_change`: pending encryption-format change requiring a write.
- `sync_local_modified_at`, `sync_last_synced_at`, `sync_last_revision`: change detection and successful file revision.
- `sync_last_error`, `sync_last_device`: display status.

`update_settings` commits several state keys atomically. Mirror imports use one write transaction, preserve local launch data, delete unmatched games with foreign-key cascade to their targets, and protect ambiguous candidates. Pulls set the winning timestamps in that transaction to cancel self-triggered change bumps. Snapshots in `<data dir>/sync/snapshots/` (the last five, written before each pull or sync merge) contain all allowlisted backup sections for undoing a mistaken pull.

wLib manages its relational library state in one SQLite database file. The optional game catalog is a separate read-only cache (`catalog.db`) downloaded by `core/catalog.py`; the app never writes library data to it, and it is not part of backups or sync.

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
**Safety net**: every `init_db()` call first runs `PRAGMA quick_check(1)`. A damaged file raises `DatabaseDamagedError`; `main()` shows its message in the fatal-start dialog and exits, and the file is left as it is. Before migrations change a database that already has a `games` table, `init_db()` copies it with the SQLite backup API to `<data dir>/db-backups/wlib-<YYYYMMDD-HHMMSS>.db` and keeps the newest three (`DB_BACKUP_KEEP`). A failed copy is logged and does not stop the start. The copies are device-local and are never exported or synced. To restore one, close wLib, move `wlib.db`, `wlib.db-wal` and `wlib.db-shm` away, and put the copy in the data folder as `wlib.db`.

`init_db()` stores, in `PRAGMA user_version`, a hash of its migrations' fingerprint (the code of `_migrate_schema`, the module helpers it calls and the constants they read) combined with the table, index and trigger SQL they left, and skips the migrations when it still matches. An older wLib that ran its own migrations on the file changes that SQL, so the next start migrates again; so a normal start does no schema work; changing any of that code reruns them once, with no version number to bump. Tests rerun the migrations on every call unless marked `schema_fingerprint`. When they run, they create missing tables and indexes, inserts missing default settings with `INSERT OR IGNORE`, and programmatically inspects `PRAGMA table_info(games)` during boot. If the application updates require new columns, `init_db` automatically executes `ALTER TABLE` statements conditionally to bring the schema up to date. It also performs additive data normalization for evolved fields such as `play_status` and `launch_mode`, allowing older stored values to be mapped into the current canonical sets during startup.

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
| `tags_user_edited` | `BOOLEAN DEFAULT 0` | Set when the user changed the tags (Game Detail or bulk Add/Remove tag). F95 metadata backfill never writes tags to such a game, even when empty. Exported and synced with `tags`; an older backup without the field leaves the local value unchanged. |
| `f95_tags` | `TEXT` (nullable) | The last F95 tag list that wLib saw for the game (comma list, same format as `tags`). Written by F95 metadata backfill when it writes tags and by Refresh F95 info; user tag edits never change it. NULL means no baseline yet; an empty text is a real empty list. Refresh F95 info uses it to tell user-added and user-removed tags from F95 tags (`core/f95_tags.py`). Exported and synced with `tags`; an older backup without the field imports as NULL for a new game and keeps the local value for an existing one. |
| `f95_url` | `TEXT` | F95Zone thread URL. Unique index protects against exact duplicates; the app also rejects equivalent F95 thread URL variants by thread identity. |
| `version` | `TEXT` | The local downloaded version string. |
| `latest_version` | `TEXT` | The remote version identified by the scraper. |
| `last_played_version` | `TEXT DEFAULT ''` | Launch-time version whose normalized session durations reached 300 seconds. Empty for existing games until played. Written by play ticks and user-state import. |
| `skipped_version` | `TEXT DEFAULT ''` | Latest release the user chose to skip. `update_game` accepts it and clears it when the installed version changes after normalization. |
| `exe_path` | `TEXT` | Absolute path to the executable binary. |
| `cover_image_path` | `TEXT` | Remote cover image URL. |
| `cover_cache` | `TEXT` | Device-only random filename in `<data dir>/covers/cache/`; nullable. |
| `cover_cache_url` | `TEXT` | Remote URL used to create the cached file; nullable. |
| `cover_local` | `TEXT` | Device-only random filename under `<data dir>/covers/`; NULL when unset. |
| `note_links` | `TEXT DEFAULT '[]'` | Ordered JSON array of `{url, label}` objects, decoded on read and carried by backups and sync. URLs must be absolute HTTP(S), at most 2048 characters and unique by exact value; labels are trimmed and capped at 200 characters. |
| `progress` | `TEXT` | Notes markdown shown in the game detail Notes tab (empty by default, saved with Save Changes, carried by backups and sync). |
| `rating` | `TEXT` | F95 community rating as text, `X.X/5 (count)` or `X.X/5`. Written by the extension add flow, by each thread-page read that finds a rating (update checks, Refresh F95 info), and by the catalog fill after a new catalog snapshot installs. The UI sorts by its first number. |
| `rating_updated_at` | `TEXT DEFAULT ''` | When `rating` was read: UTC ISO time of the thread-page read, or the catalog's `built_at` for a catalog fill. Empty means unknown. The catalog replaces a rating only when it was built after this time (or the time is empty) and the value at one decimal differs. Exported, imported and synced with `rating`. |
| `rating_graphics` | `REAL` | Graphics rating (0-5). |
| `rating_story` | `REAL` | Story rating (0-5). |
| `rating_fappability` | `REAL` | Fappability rating (0-5). |
| `rating_gameplay` | `REAL` | Gameplay rating (0-5). |
| `command_line_args` | `TEXT` | Launch arguments, or the complete command for Linux `custom` mode. |
| `run_japanese_locale` | `BOOLEAN` | Overrides `LC_ALL` to Japanese on Linux; preserved but ignored on Windows. |
| `run_wayland` | `BOOLEAN` | Applies Linux Wayland compatibility settings; preserved but ignored on Windows. |
| `auto_inject_ce` | `BOOLEAN` | Enables Cheat Engine injection for Linux Wine/Proton launches. |
| `block_network` | `BOOLEAN DEFAULT 0` | Per-game No internet preference, shared by all launch targets and carried by backup and sync. |
| `html_player` | `TEXT DEFAULT ''` | Where an `.html`/`.htm` target opens: `''` (follow the `html_builtin_player` setting), `builtin` or `browser`. Unknown values normalize to `''`. Included in launch configuration backups and synced. |
| `gamemode`, `mangohud` | `TEXT DEFAULT 'default'` | Per-game launch overrides: `default`, `on`, or `off`. Included in launch configuration backups, not synced. |
| `gamescope` | `TEXT DEFAULT 'default'` | Per-game gamescope override: `default`, `on`, or `off` (`normalize_tool_override`). Launch configuration backups, not synced. |
| `gamescope_resolution`, `gamescope_scaler`, `gamescope_filter`, `gamescope_args` | `TEXT DEFAULT ''` | Per-game gamescope overrides; empty uses the global value. `update_game` and backup import normalize them with `core/gamescope.py` (`WxH` with sides 1–16384; a known scaler or filter; extra arguments that `shlex.split` accepts with no standalone `--`); an invalid value becomes empty. Launch configuration backups, not synced. |
| `sandbox_enabled` | `BOOLEAN DEFAULT 0` | Per-game sandbox preference, shared by all launch targets. Added additively with sandboxing off for existing games. New games follow `sandbox_new_games` unless explicitly set. Machine-specific launch configuration: exported when selected, never synced. |
| `custom_prefix` | `TEXT` | Per-game Wine prefix override, used on Linux. |
| `proton_version` | `TEXT` | Per-game Proton path override, used on Linux. |
| `save_path_override` | `TEXT DEFAULT ''` | Absolute saves folder chosen for the game; empty means use detection. Machine-specific: exported with launch configuration, never synced. Added additively; `update_game` accepts it, the UI sets it through `set_game_save_folder`. |
| `default_target_label` | `TEXT DEFAULT ''` | Optional name of the canonical default launch target (`games.exe_path`); empty shows "Default". Spaces at both ends are removed on save. Exported with the launch-targets backup section as a game-level `default_target_label` key; an import applies it only when that section is selected and the key is present. Added additively; `update_game` accepts it. |
| `sync_uid` | `TEXT` | Random 32-hex per-game identity. `init_db()` backfills empty values and the `games_assign_sync_uid` insert trigger fills new rows. Exported as each game record's `uid`; imports and sync match on it before F95 URL, store link, or title, so a game renamed or re-linked on another device is updated instead of deleted and re-created. A metadata match adopts the incoming `uid` unless another row holds it. Not in `SYNC_GAME_COLUMNS`, so writing it never advances `sync_local_modified_at`. |
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

Semantic backups export definitions without IDs under `settings.paths.companion_tools`, and explicit overrides by tool name under each game's `launch_config.companion_tools`. Imports merge names ignoring case and retain local IDs. On Linux, definitions whose executable is not on the host are skipped; Windows keeps imported Linux definitions unchanged because tools only run on Linux. Overrides resolve after importing tools; backups without these fields preserve local data.

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

### Table: `download_jobs`

The device-local download queue that `core/downloads/queue.py` owns. One row per in-app download (file host or torrent). It is never exported, synced or included in diagnostics, has no sync triggers, and holds no download link, tracker address or passkey. `game_id` has no foreign key: the queue removes a deleted game's jobs together with their data (`DownloadQueue.forget_game`). Access goes through `list_download_jobs`, `insert_download_job` (appends at the end unless a `position` is given), `update_download_job` (refuses `id`, `kind` and `created_at`), `reorder_download_jobs` and `delete_download_job`.

| Column | Type | Description |
|--------|------|-------------|
| `id` | `TEXT PRIMARY KEY` | Job id; also the worker's job id. |
| `kind` | `TEXT NOT NULL` | `host` or `torrent`. |
| `game_id` | `INTEGER NOT NULL` | The game the download is for. |
| `title`, `version` | `TEXT NOT NULL DEFAULT ''` | Shown on the row; `version` is the build's version. |
| `position` | `INTEGER NOT NULL DEFAULT 0` | Queue order, lowest first (indexed). |
| `state` | `TEXT NOT NULL` | `preparing`, `queued`, `retry_wait`, `held`, `running`, `paused`, `attention` or `ready`. |
| `reason` | `TEXT NOT NULL DEFAULT ''` | Extra state detail, such as `game` or `battery` for `held`, or `seed_prompt`. |
| `error_code`, `error_text` | `TEXT NOT NULL DEFAULT ''` | Failure code from `core/downloads/failures.py` and the original message. |
| `source` | `TEXT NOT NULL DEFAULT '{}'` | JSON. Host: the build identity (`label`, `section`, `platform`, `host`, `part`). Torrent: `rpdl_id` and `info_hash`. |
| `bytes_done`, `bytes_total` | `INTEGER` | Last known progress; `bytes_total` is null when unknown. |
| `retries`, `retry_at` | `INTEGER`, `REAL` | Automatic network retries used, and the epoch time of the next one. |
| `created_at`, `updated_at`, `finished_at` | `REAL` | Epoch seconds. |

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

Other persisted keys: `auto_update_check` (`weekly` by default), `last_update_check`, `update_notifications` (`on` by default), `library_folder` (default), `library_folders` (JSON array of paths, seeded from the existing default), `library_folder_pattern` (`{creator}/{title}/{version}`), `downloads_folder` (XDG Downloads with a `~/Downloads` fallback), `download_detection_enabled` (`true`), `dismissed_download_archives` (`[]`, local-only), `catalog_enabled` (`"1"`/`"0"`, missing means on, local-only), and `urm_rpa_path` (empty by default).

Download queue settings (`core/downloads/queue_settings.py`) are device-local and not in `GENERAL_SETTINGS_KEYS`: `download_limit_kib` (shared download speed limit for file hosts and torrents in KiB/s, `0` = unlimited), `download_max_active` (downloads at once, `1`–`5`, default `2`), `download_pause_while_playing` (`true` by default) and `download_pause_on_battery` (`false` by default). A missing or invalid value reads as the default. When `download_limit_kib` was never saved, startup copies `torrent_download_limit_kib` into it once; the old key stays in place for older versions and is no longer read.

`get_library_folders()` returns `(folders, default)` and repairs drift: a missing default is prepended, or the first listed folder becomes the default when none is set. A non-empty list always contains exactly one default. Settings strip paths and deduplicate by realpath; both keys can be cleared together.

`custom_play_statuses` stores a JSON list of user-defined names (default `[]`). Removing a name through Settings resets affected games to `Not Started` atomically with the setting update. `urm_rpa_path` stores the optional URM source archive path (default empty). Neither requires a schema change. Backups include the status list under general settings and URM under machine-specific paths; game statuses survive imports without settings.

`collections` stores the ordered JSON list of user-defined collection names (default `[]`). `get_collections()` validates it on read like custom play statuses: names of 1–40 characters, unique ignoring case, never `All` (reserved for the built-in tab). `create_collection`, `rename_collection`, `delete_collection`, and `set_game_collections` rewrite the setting and the affected games' `collections` column in one transaction; `reorder_collections` rewrites only the setting. Renames rewrite matching memberships (case-insensitive) so members are kept; deletes strip only that membership. `collections`, `library_filter_presets`, `tag_colors`, and `save_backup_keep` sit in `GENERAL_SETTINGS_KEYS`, so backups and sync carry them and the settings triggers watch them.

`library_filter_presets` stores a JSON list of `{name, filterCollection, filterStatuses, filterInstallState, filterSources, filterEngines, filterTags, sortBy, sortDir}` objects (default `[]`). `get_library_filter_presets()` drops entries without a 1–40 character name and case-insensitive duplicate names; the frontend further normalises each preset through `normalizeFilterPreset` so unknown engines/tags are dropped on apply.

`save_backup_keep` stores how many save backups to keep per game: `all` or `1`–`99` (default missing, meaning keep all). `get_save_backup_keep()` returns `None` for `all`, missing or invalid values and an int otherwise. It is in `GENERAL_SETTINGS_KEYS`, so backups and sync carry it and the settings triggers watch it.

## JSON Migration Backups

wLib's import/export flow uses a semantic JSON file rather than copying `wlib.db` directly. This avoids WAL sidecar issues, schema drift, and local primary-key coupling when moving a library between machines.

- **Format metadata**: Exports include `format`, `format_version`, `exported_at`, app metadata, and the selected optional sections.
- **Always-included game metadata**: Every exported game includes title, developer, engine, tags, F95 URL, local/latest version fields, thread completion flag, store links, screenshot URLs, and a cover reference.
- **Optional sections**: Users can include user state (play status, ratings, notes, favorites, playtime, timestamps), launch configuration, executable paths, additional launch targets, general settings, and machine-specific path settings. External-library imports also offer a `covers` section that copies the source app's local cover images.
- **Import matching**: Imports match by normalized F95 thread identity first, then by an exact shared store identity. Games without F95 URLs fall back to normalized title/developer only when the match is unambiguous; records with no developer or executable match a unique same-title local game that has no F95 URL or developer. A record whose thread URL is not in the library merges into a unique URL-less local game with the same title/developer.
- **Merge semantics**: For matched games, backup values win for always-included metadata and selected optional sections; unselected optional sections preserve local values. Playtime is overwritten from the backup when user state is selected and is not summed.
- **Excluded data**: Cookies, webview storage, downloaded runtimes, extension copies, caches, logs, local cover files, cover cache files, save backups, game attachments (PDF, TXT, MD), and embedded cover image files are not part of JSON backups.
- **Journal user state**: `last_played_version`, `skipped_version`, and `play_sessions` travel with user state. Sessions include only version, start/end time, and duration. Imports replace sessions only when that key is present, and fill-missing imports keep a game's existing sessions; missing scalar fields also preserve local values. The additive fields keep format version 1. Sync uses the same replacement rule; it does not merge sessions from two machines. An invalid session is skipped with an `invalid_play_session` preview warning instead of failing the import, and a list with no valid session keeps the local ones.
- **Fill-missing defaults**: a launch mode of `auto` and a GameMode/MangoHud/gamescope override of `default` count as empty, so fill-missing imports set them; any other value stays.
- **Duplicate weak matches**: when several imported games claim one local game only by store link or title, the one titled exactly like the local game keeps the match (the first in file order on a tie) and the others are imported as new games, so none overwrites another.
- **Game attachments**: attached PDF, TXT and MD files live under `<data dir>/attachments/<game id>/` and are local-only; exports and sync never include attachment data or attachment names. Deleting a game removes its folder; mirror imports and sync deletions prune orphan numeric folders after the commit, and startup prunes folders for IDs not in the library. A reset or replaced database therefore removes orphan attachment folders (IDs are `AUTOINCREMENT` and never reused).
- **Linux/Windows migration**: Per-game paths and runtime options can be imported without automatic path conversion. Update missing executable/launch-target paths on the destination and select Auto Detect for Windows launches. Foreign global path settings (for example, a Linux Wine prefix imported on Windows) are reported as `foreign_path` during inspection and skipped during import, preserving the destination setting.

### Device-local covers

`init_db()` adds `cover_local` without rebuilding existing tables. Only
`set_game_local_cover` and `clear_game_local_cover` write it; the generic
`update_game` allowlist, `SYNC_GAME_COLUMNS`, and backup/import field lists omit
it. Changes to this column do not advance `sync_local_modified_at`. The
`covers/` folder is created on first use. Files and references are excluded
from exports and sync; applying backups or sync preserves existing covers.


### Device-local remote cover cache

`init_db()` adds nullable `cover_cache` and `cover_cache_url` columns. Dedicated helpers read the work list, list referenced names, clear one/all rows, and set both fields guarded by the current `cover_image_path`. Neither column is in `SYNC_GAME_COLUMNS` or the generic `update_game` allowlist. Cache writes and purge never change the sync timestamp. JSON exports, semantic backups and sync omit both columns and all files in `covers/cache/`; imports preserve existing cache columns. The background downloader shares F95 pacing, and purge removes only this cache while disabling downloads until the next launch.

`gamemode_enabled` and `mangohud_enabled` are general boolean settings, both defaulting to `false`. The gamescope settings are general settings (exported and synced) with `INSERT OR IGNORE` defaults: `gamescope_enabled` (`false`), `gamescope_window_mode` (`fullscreen|borderless|windowed`, default `fullscreen`), `gamescope_output_resolution` and `gamescope_game_resolution` (`WxH` or empty for Auto), `gamescope_scaler` (`auto|integer|fit|fill|stretch`, default `auto`), `gamescope_filter` (`linear|nearest|fsr|nis|pixel`, default `linear`), `gamescope_fps_limit` (1–1000 or empty for Off) and `gamescope_args` (empty). `save_settings` refuses an invalid value with `{"success": false, "field": <key>, "error": ...}` and saves nothing; backup import turns an invalid value into the default. `html_builtin_player` ("Play HTML games in the built-in player") is a general boolean setting that is exported and synced; an absent row reads as `true`. `web_player_devtools` ("Web player DevTools (F12)") works the same way: general, boolean, exported and synced, and an absent row reads as `true`. `html_player_notice_shown` is a device-local flag (not exported or synced) set after the one-time built-in player note.
