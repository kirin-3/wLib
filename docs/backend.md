# Backend Systems (Python)

## Tag colors

`Api.set_tag_color(tag, swatch)` updates one trimmed, lowercase, case-insensitive
tag key and returns `{success, tag_colors}`. Swatches are `red`, `orange`, `yellow`,
`green`, `teal`, `blue`, `purple`, `pink`; unknown swatches return an error. `None`
or an empty string clears a color. `get_settings()` includes the validated map.

## App log and diagnostics

`core/app_log.py` installs the app log in `main()` after the single-instance check.
`<data dir>/logs/wlib.log` captures stdout, stderr, Python logging, and uncaught
main and worker exceptions while preserving terminal output. The file rotates at
2 MiB (`MAX_BYTES`) with three backups (`BACKUP_COUNT`), `wlib.log.1` through
`wlib.log.3`. Logging is always on and separate from `enable_logging`, which
controls per-game launch output. Logs are local and never exported or synced.
Setup failure prints one warning and leaves the original streams and hooks intact.
Backend and packaged smoke tests do not install logging.

`core/diagnostics.py` keeps copies of renderer stages under a lock and collects
environment facts, paths, database size including WAL, and a game count. Tool
discovery reuses Flatpak-aware host lookups and runner discovery; Windows omits
Wine and Proton. The API passes its existing version and tool helpers into the
collectors, so collecting diagnostics does not construct another API or load games.

- `get_diagnostics()` returns `{success, basic, report_basic}`.
- `get_diagnostics_tools()` returns `{success, tools}`.
- `get_diagnostics_report()` returns `{success, report}` with the full plain-text
  report, replacing the home prefix with `~` and login-name path segments with
  `<user>` (segment replacement skips names shorter than three characters).
- `open_log_folder()` creates the log folder and opens it in the system file
  manager. Errors include the folder path.

Reports contain no game titles, tags, URLs, or notes. The page requests basic
details and slower tool checks in parallel.

## Library sync and encrypted backups

`core/library_sync.py` contains `SyncManager`, `FolderLocation`, `DropboxLocation`, and `GoogleDriveLocation`. All locations expose revision/read/write methods. Sync shares metadata, user state, and general settings using `wlib.library_migration` v1 plus a UTC `sync.modified_at` and hostname `sync.device`. Machine paths, launch settings, and local cover files are excluded. Newest wins for the whole library; simultaneous use from several machines is unsupported.

First connection pauses at `needs_choice` if both libraries contain games. `use-remote` mirrors the file including deletions, `use-local` replaces the file, and `merge-once` imports without deletions then uploads. Sync uses conservative thread/title/developer matching, with unique-title matching for manual games without a developer; ambiguous candidates are preserved and reported. Mirror apply saves every backup section to `<data dir>/sync/last-local.json` before applying in one database transaction. Local edits during downloads cause re-evaluation; uploads retain any edits made after the payload was captured.

Bridge methods: `get_sync_status`, `set_sync_folder(path)`, `connect_dropbox`, `connect_gdrive`, `disconnect_sync`, `sync_now`, `resolve_sync_choice(choice)`, `set_sync_passphrase(passphrase | null)`, and `unlock_sync(passphrase)`. Failures include `error_code`. Runs execute in background threads and status is polled by the UI. Startup checks both sides; the 60-second watcher pushes after 30 seconds without local edits. Exit allows a final push up to 10 seconds. Background and exit runs do not pull remote changes; pulls emit `wlib-refresh-library`.

Folder writes check mtime/size revisions, preserve `.bak`, write `.tmp`, and replace atomically. A missing folder is unavailable and is never created. Dropbox uses urllib with the configured SSL certificates and app-relative `/wlib-library.json` (visible as `/Apps/wLib/wlib-library.json`). Uploads use update/add with strict revision conflicts; conflict responses trigger re-evaluation. Access tokens stay in memory and refresh on expiry or a 401.

Dropbox uses OAuth PKCE S256 with offline access and a ten-minute state expiry. `GET /dropbox/callback` on the existing server forwards code/state to `complete_dropbox_callback` and returns a static, uncached completion page. Extension origin restrictions remain unchanged. Connect is disabled when port 8183 is unavailable. Configure the exact redirect `http://127.0.0.1:8183/dropbox/callback` and scopes `files.content.read`, `files.content.write`, `files.metadata.read`, and `account_info.read` (display name) in the app console. No app secret is shipped. Disconnect attempts token revocation and clears local credentials even if offline.

Google Drive reuses the PKCE plumbing (`_pkce`, `_token_request`) and the pending sign-in tags its provider, so a Dropbox callback cannot complete a Google sign-in. It requests only the `https://www.googleapis.com/auth/drive.appdata` scope with `access_type=offline` and `prompt=consent`, so wLib sees only its own hidden app data folder, never other Drive files. `GET /gdrive/callback` (redirect `http://127.0.0.1:8183/gdrive/callback`, a Desktop OAuth client) is routed through the same handler as the Dropbox callback via `OAUTH_CALLBACKS` in `main.py`; bridge methods are `connect_gdrive` and `complete_gdrive_callback`. Settings keys are `sync_gdrive_refresh_token` and `sync_gdrive_account` (the Google email). `GoogleDriveLocation` finds `wlib-library.json` by name in `appDataFolder` on every revision check (earliest `createdTime` wins when duplicates exist), uses the file's `md5Checksum` as the revision (Drive's `version` keeps rising after an upload, so it can't be compared with the value from the upload response), and keeps the file ID in memory only. Drive has no conditional upload: `write` compares the checksum, then creates (multipart) or updates (media `PATCH`), so a small race window remains if two machines upload at the same moment; use one machine at a time. The overwritten version stays in Drive's revision history, which is not reachable from the Drive web UI for app data files. Reconnecting does not revoke the previous token, because Google revokes the whole grant and that would invalidate the new token too; Disconnect revokes it and leaves the sync file in Drive.

The Google client ID is a public constant (`GDRIVE_CLIENT_ID`). Desktop clients must still send a client secret, which Google does not treat as confidential for installed apps but which stays out of the repository. `_gdrive_client_secret()` reads `WLIB_GDRIVE_CLIENT_SECRET` first, then the gitignored `core/_gdrive_secret.py` written by the build scripts (see `docs/build.md`). Without it `gdrive_available` is false and `gdrive_unavailable_reason` says why, as it does when port 8183 is unavailable. Folder and Dropbox sync are unaffected.

`core/library_backup.py` encrypts the complete JSON document with scrypt (N=131072, r=8, p=1) and AES-256-GCM from `cryptography`. The v1 `wlib.encrypted` envelope authenticates its header and uses a fresh nonce. Manual exports use fresh salts; sync stores only a derived key and salt locally. Unknown keys/salts pause at `needs_passphrase`. Disabling encryption clears the key and marks the next write for plaintext. Backup load/inspect/import/write accept optional passphrases; errors include `passphrase_required`, `decrypt_failed`, and `unsupported_format_version`. Sync settings and credentials remain outside backup allowlists.

The core strength of wLib relies on its native Python backend, divided primarily into `main.py` and modularized controllers in the `core/` package. The supported development and CI target for backend work is Python 3.12.

## Bootstrapping in `main.py`
`main.py` acts as the primary orchestrator. Upon launch, it executes these sequential steps:

1. **SSL Certificate Configuration**: Configures SSL certificates from bundled certifi or system paths for secure scraping (`configure_ssl_certificates()`).
2. **Single-Instance Bind**: Calls `bind_extension_server()` to bind `127.0.0.1:8183`. If the port is taken and `focus_running_instance()` gets a wLib reply, the new process brings the running window forward and exits, so two copies never share the database. The port is bound without `SO_REUSEADDR` on Windows, where that option would let a second process bind it.
3. **App Log**: Calls `setup_app_log()` so `<data dir>/logs/wlib.log` captures console output, Python logging, and uncaught exceptions before the rest of startup runs.
4. **Qt Runtime and WebView Cache**: Detects the Linux session type (X11/Wayland) and configures Qt platform plugins; on Windows, leaves native platform selection to Qt unless explicitly overridden (`configure_qt_runtime_environment()`). Both platforms start PyWebView with `gui="qt"`. Packaged runs refresh the WebView cache when the app version or bundled UI changes.
5. **WebView Load and Startup Diagnostics**: Imports `pywebview`, then records the Qt and renderer startup snapshot.
6. **API and Database Initialization**: Constructs `core.api.Api`, which calls `core.database.init_db()` to create/migrate the SQLite schema, prunes orphan PDF attachment folders, and creates the sync, scraper, and launcher services.
7. **Extension Sync**: Copies bundled extension assets into the platform user-data directory when files are missing or the bundled manifest version changed.
8. **Window and Background Services**: Creates the PyWebView window, starts legacy browser-data cleanup and the extension server daemon thread, and starts the library sync watcher.
9. **UI Loop**: Enters the blocking UI loop, then allows a final sync push on exit.

`main.py --smoke-test` validates imports, database initialization, and bundled assets without opening the UI or downloading external content.

### Renderer Diagnostics System

`main.py` logs Qt and WebGL renderer details under the resolved data directory on both platforms: `~/.local/share/wLib/renderer-diagnostics.log` on Linux or `%LOCALAPPDATA%\wLib\renderer-diagnostics.log` on Windows. The Linux release launcher supplies the GPU probe and crash-guard context described below; Windows uses Qt's native selection.

- **GPU Detection**: Probes GPU capabilities using `glxinfo`, `/sys/class/drm/`, and environment variables
- **Crash Guard**: Implements `<data dir>/.gpu_crash_guard` to auto-fallback to software rendering after GPU crashes
- **Diagnostic Logging**: Logs Qt backend selection, GPU detection reason, renderer details, and WebGL probe results to `<data dir>/renderer-diagnostics.log`
- **Browser Renderer Probe**: Executes WebGL detector scripts in the PyWebView to capture the browser's GPU renderer information
- **AppImage Mirroring**: AppImage launches mirror diagnostic context to `<data dir>/appimage-launch.log`

## Core Modules

### `core/host_platform.py` (Host Paths and Capabilities)

Centralizes Linux/Windows detection, data/cache/extension paths, and platform capabilities. `Api.get_platform_capabilities()` exposes these through `getPlatformCapabilities()` in the frontend bridge. Linux-only runtime installers return `success: false` with `code: "unsupported_platform"` before starting downloads or subprocesses on Windows. Windows file/folder and HTML opening uses system associations. See [Platform Paths](architecture.md#platform-paths) for defaults and environment overrides.

`Api.get_install_status(prefix_path, proton_path)` reports `success`, `deps`/`rtps` progress payloads (`running`, `done`, `total`, `current`, `error`), and the derived `dlls_installed`/`rtps_installed` flags for the resolved prefix. On hosts without runtime installers it keeps that same shape and reports `success: false` with `code: "unsupported_platform"`, so the polling dialog can read either response the same way.

`is_flatpak()`, `host_which()`, and `host_command()` keep game processes and external tools on the host. Inside a Flatpak sandbox, commands are wrapped in `flatpak-spawn --host` (with `--watch-bus`, optional working directory and environment overrides, and process-group supervision for tracked launches); sandbox-only variables such as `LD_LIBRARY_PATH`, `QT_*`, and `FLATPAK_*` are not forwarded. Runner discovery, archive tools, runtime installers, notifications, and game launches reuse these helpers.

### `core/api.py` (The API Bridge)
The `Api` class acts as the single point of entry for the Vue frontend. All methods defined without a leading underscore (e.g. `get_games`, `launch_game`) are automatically serialized into Promises on the `window.pywebview.api` object.
- **Concurrency**: UI calls are technically asynchronous on the JS side but block the pywebview worker pool on the Python side. The `Api` class heavily uses background thread spawning (`threading.Thread`) for long tasks (like downloading updates or mass-scraping metadata) so the UI doesn't freeze.
- **Event Emitter**: Contains wrapper helpers to dispatch Global UI Events back to Vue using `webview.evaluate_js()`.
- **Extension Sync Metadata**: Tracks whether startup extension synchronization actually updated the installed browser files so the frontend can show a toast prompting the user to reload the addon.
- **Launch Mode Contract**: Carries each game's `launch_mode` through add, update, list, and launch calls. Missing or invalid values normalize to `auto`; supported values are `auto`, `native`, `wine_proton`, `rpgmaker_linux`, and Linux-only `custom`. Add calls also accept `command_line_args`.
- **Launch Target Contract**: Exposes CRUD and reordering methods for additional game launch targets. `get_games()` returns each game's extra `launch_targets`, while the canonical default executable remains `games.exe_path`.
- **Finished Version Contract**: `update_game` accepts a free-text `finished_version` next to `version`; the value is trimmed and empty means not finished. When a call moves `play_status` to `Completed` from another status without sending `finished_version`, the backend fills it with the call's `version` — or the stored `version` when none was sent — in the same write. Re-saving `Completed` and leaving `Completed` both keep the stored value, and an explicit `finished_version` in the same call wins. The success response includes `finished_version` only when the backend filled it; unrelated edits return just `{"success": True}`. Version-only updates ("Mark updated"), archive installs, and metadata refreshes never touch the field.
- **Library Migration Contract**: Exposes `export_library_backup`, `inspect_library_backup`, and `import_library_backup` for one-file JSON migration. Import uses an inspect-before-write flow and backup-wins field merges for selected sections.
- **External Library Import Contract**: Exposes `detect_external_library(source)`, `inspect_external_library(source, path)`, `import_external_library(source, path, options)`, and `browse_external_library_file(source, start_path)` for bringing a library in from F95Checker, Atlas, XLibrary, or f69 (`source` is `"f95checker"`, `"atlas"`, `"xlibrary"`, or `"f69"`). Detection reports the found path plus the default locations checked; the file picker falls back to per-source filters when nothing is found. Inspect previews new/matched/ambiguous counts plus skipped games without an F95 thread or usable executable, dropped cross-OS executable paths, and the source schema version. Import accepts `sections` (`user_state`, `executable_paths`, `launch_targets`; game identity and metadata always apply to matches) and `merge_mode` (`"overwrite"` or `"fill_missing"`, defaulting to overwrite; fill-missing only sets fields that are empty locally and keeps existing launch targets). Results include `created_ids`/`updated_ids`, and imports that touch games with F95 URLs start `check_all_updates(game_ids=...)`, returning `metadata_check` as `"started"`, `"busy"`, or `"none"`. Failures return `{"success": False, "error": ...}`. The readers live in `core/external_import.py` (`read_external_library`, `locate_external_library`); SQLite sources are read from a temporary copy opened read-only so the source app can keep running.
- **Folder Import**: `inspect_game_path(exe_path)` guesses a title, version, and engine for the Add Game form; `scan_games_folder(folder)` walks subfolders, picks a launch file, and marks entries already in the library with `in_library`. The Import Folder dialog adds selected rows through one `add_game` call per game and refreshes the library once. Guesses come from folder names and files, so the frontend lets the user review and edit them after import.
- **Runtime Setup (Linux)**: `install_rpgmaker_dependencies(prefix_path, proton_path)` runs Winetricks verbs for common DirectX/VC++/.NET/font dependencies; `install_rpgmaker_rtp(prefix_path, proton_path)` downloads the official RTP archives and runs their setup silently. Both start background threads, refuse concurrent installs, and report through `get_install_status()`. `get_system_deps_command()` detects the distro package manager and returns the command for 32-bit GStreamer/multimedia libraries. `download_proton_ge()` and `get_proton_download_status()` cover the checksum-verified GE-Proton download, and `is_cheat_engine_installed()`/`download_cheat_engine()` cover the pinned Lunar Engine archive. The Windows host rejects these Linux-only helpers before doing any work.
- **Process Control**: `get_running_games()` returns a list of game IDs; `stop_game(game_id)` requests termination and returns a success/error payload. Duplicate tracked launches return `already_running`; stopping an untracked game returns `not_running`, and stopping one whose process is still starting (e.g. behind a UAC prompt) returns `starting`.
- **Custom Statuses**: `get_settings()` returns `custom_play_statuses` as a list. `save_settings()` validates trimmed names (1–40 characters, no case-insensitive duplicates, canonical clashes, or legacy aliases such as `Replaying`); removing a name resets games using it to `Not Started` in the same transaction as the list update.
- **Collections**: `get_collections()` returns the ordered collection list; `create_collection(name, game_id=None)`, `rename_collection(old, new)`, `delete_collection(name)`, `reorder_collections(names)`, and `set_game_collections(game_id, names)` each rewrite the setting and affected game memberships in one transaction and return `{"success": True, "collections": [...]}`, or `{"success": False, "error": ...}` on refusal (invalid or duplicate names, unknown collection or game). `get_games()` returns each game's `collections` as a `list[str]` filtered against the current collection list. Names are 1–40 characters, unique ignoring case, and `All` is reserved.
- **Filter Presets**: `get_settings()` returns `library_filter_presets` (a list of `{name, filterCollection, filterStatuses, filterInstallState, filterEngines, filterTags, sortBy, sortDir}` objects); `save_library_filter_presets(presets)` validates the whole list (1–40 character names, unique ignoring case) and writes it as one JSON setting. The getter drops unreadable entries and case-insensitive duplicates so a malformed sync payload can't break the settings payload.
- **Path Checks**: `check_path(path, kind)` reports `{ok, reason}` for a typed path without blocking anything: `file` wants an existing file, `dir` an existing folder, `parent` an existing folder for a file wLib will create, and `runner` an existing file when the value has path separators or a command found on PATH otherwise. `~` is expanded like the launch paths; an unknown kind returns `{"success": False, "error": ...}`.
- **File Timestamps**: `get_executable_modified_time(exe_path)` returns `{success, modified_at}` from the executable's filesystem mtime, or `modified_at: None` with an error for a missing path. Game Detail uses it for the Overview footer's **Exe modified** line; the adjacent **Thread updated** line uses `thread_main_post_last_edit_at`/`thread_main_post_checked_at`.
- **URM**: `urm_rpa_path` stores a user-supplied archive path. `browse_urm_file()` selects `.rpa` files. `get_urm_status(game_id)` returns `success`, `renpy`, `installed`, and `source_configured`. `set_urm_installed(game_id, installed)` (controls live in the Tools tab) detects `renpy/` plus `game/` beside the stored executable and installs/removes `game/0x52_URM.rpa`. Installs replace the destination only after a successful copy; filesystem errors return `success: false`. No download is performed.
- **Runner Discovery**: `get_available_runners()` lists system Wine and wLib Proton first, then Steam and Lutris, sorted within each source and deduplicated by real path. Steam roots: `~/.steam/root`, `~/.steam/steam`, `~/.local/share/Steam`, `~/.var/app/com.valvesoftware.Steam/data/Steam`, and `~/.var/app/com.valvesoftware.Steam/.local/share/Steam`; scans `compatibilitytools.d/*/proton` and `steamapps/common/Proton*/proton`. Lutris scans `*/bin/wine` under `~/.local/share/lutris/runners/wine` and `~/.var/app/net.lutris.Lutris/data/lutris/runners/wine`. Windows returns an empty list.
- **Archive Install Contract**: `browse_archive_file()` picks `.zip`/`.7z`/`.rar` files. `inspect_archive(path, game_id)` is read-only: guessed title/version, destination preview, tool availability, and the old-folder guard. `install_archive(options)` blocks while pywebview runs it on its own thread; the dialog polls `get_archive_install_status()` (guarded by `_status_lock`). Updates for a running game are refused (`game_running`); a thread URL that belongs to another game returns `duplicate_url` with that `game_id`. New installs with a URL get the same background metadata fetch as `add_game`. Success fires `wlib-refresh-library`. Failures return `{"success": False, "error": ...}`.
- **Download Offers**: `find_download_offers()` lists the Downloads folder without watching it; `dismiss_download_offer(key)` persists `path|size|mtime` keys (capped at 200). A missing folder reads as an empty list, never an error.
- **Game Notes and PDF Attachments**: notes live in `games.progress` as markdown and save through `update_game`; the bridge exposes `list_game_attachments(game_id)`, `add_game_attachment(game_id, source_path)`, `open_game_attachment(game_id, name)`, `remove_game_attachment(game_id, name)`, and `browse_pdf_file(start_path)`. Failures return `{"success": False, "error": ...}` with `error_code` `game_not_found`, `attachment_missing`, or `not_a_pdf`.

### `core/archive_install.py` (Install From Archive)
Pure filesystem logic behind the install flow; `core/api.py` stays a thin wrapper.
- **Formats**: `.zip` extracts with the stdlib (`zipfile`) including per-entry path validation and Unix modes from `external_attr`. `.7z`/`.rar` use the first available system tool (`7zz`/`7z`/`7za`, `bsdtar`, `unrar` for rar; plus `%ProgramFiles%\7-Zip` on Windows) with a listing-based traversal pre-check and a post-extraction symlink escape check. Without a tool the error names 7-Zip. No new Python dependencies.
- **Layout**: `build_destination()` substitutes `{creator}`/`{title}`/`{version}`/`{engine}` in `library_folder_pattern` (default `{creator}/{title}/{version}`), sanitizes each segment (including Windows reserved names), drops empties, and falls back to `{title}`. Staging lives in `<library>/.wlib-staging-*/` so the final move is a rename; stale staging folders are cleaned on the next run; free space is checked when the uncompressed size is known.
- **Ordering**: extract → safety checks → execute bits (Linux `.sh`/ELF via `is_elf`) → flatten a single wrapper folder → carry in-folder saves (overwrite) → rename into place → update `games`/`game_launch_targets` → remove the old folder only if asked and allowed → delete the archive last. Steps before the move roll back (staging removed, `.wlib-old-<ts>` swap restored); a removal failure is reported while the update stands.
- **Deletion guard**: `can_remove_old_folder()` refuses roots, home, the library/Downloads folders, parents of the new destination, folders holding other games' executables or targets, and stale paths, returning `{removable, reason}` for the dialog.
- **Saves**: the shared in-folder helper (`game/saves`, `www/save`, `save(s)`, `SaveData`, loose `*.sav`/`*.rpgsave`/`*.rmmzsave`/`*.save`; `.dat` excluded) backs both `find_save_files` and save carry-over.
- **Download matching**: `find_download_offers()` keeps top-level archives, skips `.partN.rar` (N>1) and files newer than 15 s, and matches squashed titles only against games with a pending update whose installed version differs from the guess.
- **Settings keys**: `library_folder`, `library_folder_pattern`, `downloads_folder`, `download_detection_enabled` (machine-specific paths group in backups), plus local-only `dismissed_download_archives`.

### `core/game_saves.py` (Game Saves Folder)
Pure functions over explicit paths (testable against `tmp_path`); `core/api.py` resolves the folder from the stored game row and calls in, so the frontend never sends a path that Backup or Import writes to.
- **Detection**: `detect_save_locations` holds the former `find_save_files` body, which stays as a wrapper on `Api`. Entries carry `eligible`; the game's own executable folder is `eligible: false` (a lead only). `resolve_effective_folder` picks the override, else the single eligible detected folder, else none.
- **API**: `get_game_saves(game_id)` (override, detected, effective folder, backups, retention), `set_game_save_folder(game_id, path)` (empty clears; `~` expanded; absolute only; not inside `save-backups/`; not an existing file; may not exist yet), `backup_game_saves(game_id)`, `import_game_saves(game_id, source)`, `restore_game_saves_backup(game_id, backup_name)`, `open_game_save_backup(game_id, backup_name)`. Opening the saves folder itself uses `open_folder`. Prefix and runner feed detection only for non-`native` launch modes. `backup_name` must match the timestamp pattern and resolve inside the game's backup folder.
- **Layout**: `<data dir>/save-backups/<game id>/<YYYY-MM-DD_HH-MM-SS>[-N]/` holds plain folders in local time. Copies go to a `.<name>.partial` sibling and are renamed into place; any failure removes the partial folder. Symlinks are skipped and counted. Listing and pruning only see pattern-named folders, so a user's own folders there are untouched. Removing a game leaves its backups on disk.
- **Retention**: the `save_backup_keep` general setting is `all` (default) or `1`–`99`. After each successful backup, including pre-import ones, the game's oldest backups beyond the limit are deleted, never the one just made. Restoring also protects the backup being restored.
- **Import**: merge copy (same-named files overwritten, others kept). The source may not be, sit inside, or contain the saves folder; a file/folder type conflict stops it before any write. A non-empty target is backed up first and the import stops if that fails. The merge itself is not atomic, so the error names the backup.
- **Concurrency**: backup, import and restore hold a per-game guard (`Api._saves_busy`); a second call returns "A save operation is already running for this game". Calls block only their own PyWebView thread.
- **Backups and sync**: `save_path_override` exports with the launch-configuration section (path warning when missing, still imported) and never reaches the sync file. Save backups are never exported.

### Game mod tools (`core/game_tools.py`, `core/renpy_tools.py`, `core/rpgm_tools.py`)
Pure functions over explicit folders (testable against `tmp_path`); `core/api.py` resolves the folder from the stored game row, so the frontend never sends a game path. Only the output folder for separate-folder extraction comes from the user.
- **Detection**: `game_tool_folder(row)` is the executable's folder (`Api._get_urm_game_dir` uses it). `detect_engine` reads the files on disk and ignores the stored engine label: `renpy/` plus `game/` is Ren'Py; the first of `<folder>/www` or `<folder>` holding `data/System.json` is the RPG Maker MV/MZ data root.
- **API**: `get_game_tool_support(game_id)` (`engine`: `"renpy"`, `"rpgm"` or `""`; cheap, decides whether the Tools tab exists), `get_game_tools(game_id)` (full tab state: Ren'Py console state, archive list with size and format, URM status; RPG Maker encrypted counts, missing copies, key source, switch state, backup present), `set_renpy_console(game_id, enabled, developer)`, `extract_renpy_archives(game_id, options)` (`{"mode": "folder" | "in_place", "output": str}`), `decrypt_rpgm_assets(game_id)`, `set_rpgm_use_decrypted(game_id, enabled)`, `get_game_tool_status(game_id)` and `cancel_game_tool(game_id)`. Unknown ids return `success: false`; an action on a game of the wrong engine is rejected.
- **Runs**: every action goes through `Api._tool_run`: one run per game (`A tool is already running for this game`) and none while `get_running_games()` lists the game. Status (`running`, `action`, `done`, `total`, `current`, `cancel`) lives in `_tool_status` under a lock and is dropped when the run ends, even after an exception. Extraction and decryption block their own PyWebView thread while the Tools tab polls `get_game_tool_status` every 500 ms; `cancel_game_tool` sets the flag the engine callback reads, checked between files and between chunks. Files written before a cancel are kept, and no `.wlib-partial` file is left behind.
- **Console** (`renpy_tools`): `game/zzz_wlib_console.rpy` sets `config.console = True` (and `config.developer = True` with developer mode) in an `init 999 python:` block. State is `off` (neither file), `on` (the `.rpy` without the developer line, or only the `.rpyc`) or `developer`. Changing the mode rewrites the script and deletes the stale `.rpyc`; turning it off deletes both.
- **RPA safety rules**: archive contents are untrusted. The index is read with a `pickle.Unpickler` subclass that allows only the two callables pickle protocol 2 uses for bytes, each restricted (`_codecs.encode` with latin-1 only; `bytes()` with no arguments only, for the empty prefix rpatool writes), and raises for everything else (`pickle.loads` is never used); the archive is then reported as unsupported and nothing runs. The index is inflated with a 64 MiB cap. RPA-2.0, 3.0 and 3.2 headers are read (3.0: XOR of the hex fields after the offset; 3.2: after one ignored field; anything else, including RPA-1.0, is unsupported). Entry data is the prefix followed by `length` bytes from `offset`; as in Ren'Py's own loader, `length` does not count the prefix. Entries whose data lies past the end of the archive are skipped. Entry paths are turned into `/`, then absolute, drive, empty or `..` paths are skipped, and the `realpath` of the destination's parent must stay inside the target root (this also stops a symlink planted in the target). Files are copied in 1 MiB chunks to `<dest>.wlib-partial` and renamed into place. `0x52_URM.rpa` is never listed or extracted.
- **Extraction modes**: `folder` writes `<output>/<archive name without .rpa>/` (overwriting same-named files there); `output` must be an existing absolute folder outside the game folder. `in_place` writes next to each archive, skips `.rpy`, `.rpyc`, `.rpym` and `.rpymc` (a loose copy of an archived script could define its labels twice) and never replaces an existing file. Free space on the target drive is checked before the first write. The result carries `files`, `bytes`, `skipped` (with `skipped_scripts`, `skipped_existing`, `skipped_unsafe`, `skipped_invalid`), `failed`, `unsupported` and `cancelled`; a `PermissionError` stops the run with its message.
- **RPG Maker decryption** (`rpgm_tools`): `.rpgmvp`/`.png_` become `.png`, `.rpgmvo`/`.ogg_` become `.ogg`, `.rpgmvm`/`.m4a_` become `.m4a`, next to the originals (kept). A file is decrypted only when it starts with `RPGMV` and is at least 32 bytes; an existing copy is kept and counted. The key is `encryptionKey` from `System.json` (read with `utf-8-sig`, 32 hex digits); when it is missing or malformed it is recovered from the first encrypted image's PNG header and confirmed on a second image when one exists (audio alone cannot recover a key). Decryption streams: the 16-byte header is dropped, the next 16 bytes are XORed, the rest is copied through `.wlib-partial`.
- **"Use decrypted files in-game"**: turning it on is refused while any really-encrypted asset lacks its copy (the error gives the count). It copies `System.json` to `System.json.wlib-backup` once (never overwritten), sets `hasEncryptedImages` and `hasEncryptedAudio` to `false`, and writes the file back minified with key order kept. Turning it off restores both flags to their values in the backup (`true` when there is no readable backup), so a game that encrypted only its images keeps `hasEncryptedAudio` false, and keeps the backup. The switch reads as on exactly when both flags are `false`.

### `core/game_attachments.py` (PDF Attachments)
Pure filesystem storage behind the attachment bridge; `core/database.py` stays free of file I/O.
- **Location**: `<data dir>/attachments/<game id>/` holds copied PDFs; the filesystem is the only source of truth, so files deleted by hand disappear from the list. Listing returns regular non-dot `*.pdf` files as `{name, size}` sorted case-insensitively by name.
- **Adding**: `add_attachment` requires a `.pdf` extension and a `%PDF-` signature, picks a free `name (N).pdf` on collisions, copies through a `.<name>.part` temp file with `os.replace`, and removes the temp file on failure. The original file is left untouched. Attaching takes effect immediately without Save Changes.
- **Names**: `resolve_attachment` rejects empty names, `/`, `\`, NUL, `.`/`..`, and leading dots, requires the realpath parent to equal the game folder, and raises a distinct missing-file error (`attachment_missing`) when the file is gone. Open and remove act only inside the game's folder.
- **Cleanup**: `Api.delete_game` calls `remove_game_attachments` after a successful DB delete (logged, never fails the delete). `apply_library_backup` calls `prune_orphan_attachments` with the remaining game IDs after the commit when `deleted > 0`, covering sync and mirrored imports. `Api.__init__` prunes once after `init_db()` inside try/except with logging. `prune_orphan_attachments` and `remove_game_attachments` delete only all-digit subfolders for IDs not in the library; anything else under `attachments/` is left alone.
- **Backups**: JSON exports and cloud sync carry notes text but never PDF data or attachment names; importing or syncing keeps attachments of games that remain.

### `core/library_backup.py` (JSON Migration)
This module serializes and imports semantic library backups without copying the raw SQLite database.
- **Format**: Writes one JSON document with `format`, `format_version`, export timestamp, selected sections, game metadata, optional game sections, and selected settings groups.
- **Always-included metadata**: Every exported game includes title, developer, engine, tags, F95 URL, version fields, thread completion flag, screenshot URLs, and cover reference so records remain identifiable after migration.
- **Import matching**: Matches games by normalized F95 thread identity first, then by normalized title/developer only when there is exactly one local match. Records carrying only a title match a unique same-title local game without an F95 URL or developer, and a record whose thread URL is unknown locally merges into a unique URL-less local game with the same title/developer. Ambiguous fallback matches are skipped and reported. Backups without a `selected_sections` manifest offer every game section the records actually carry.
- **Merge behavior**: Matched games receive backup values for always-included metadata and selected optional sections. Unselected sections preserve local values. Playtime is overwritten from the backup when user state is imported, never summed.
- **Safety boundaries**: The JSON export excludes cookies, webview storage, downloaded runtimes, extension copies, caches, save backups, PDF attachments, and diagnostics.
- **Cross-platform paths**: Inspection reports missing executable paths and foreign global path settings. Import skips global settings whose path syntax belongs to the other OS; per-game paths and launch options remain available to edit after migration.

### `core/launcher.py` (Process Management)
This module handles native host launching and Linux compatibility runtimes.
- **Environment Overrides (Linux)**: Per-game Japanese locale and Wayland settings modify the `env` dictionary passed to `subprocess`. Windows ignores these flags and removes Wine/Proton environment variables from native launch environments.
- **Launch Modes**: Windows exposes `auto`: `.exe` runs directly, `.bat`/`.cmd` uses `cmd.exe`, `.jar` uses `java -jar` with Java on `PATH`, and `.html`/`.htm` opens in the default browser. Linux additionally supports `native`, `wine_proton`, `rpgmaker_linux`, and `custom`. Custom Command uses `command_line_args` as the full command, substitutes `%command%` tokens with the absolute target, and runs in the game folder with a clean host environment. Leading `NAME=value` assignments are applied without a shell. Empty commands are rejected. Windows command-line arguments use Windows quoting rules.
- **Launch Targets**: Alternate targets reuse the same launcher entrypoint as the default executable; only the selected executable path changes. Playtime remains keyed to the parent `game_id`.
- **Wine & Proton**: Prepends the configured `proton_path` or `wine` binary when compatibility mode is selected or auto-detection falls through to a Windows-style target, ensuring the proper `WINEPREFIX` or Proton compatibility path is enforced.
- **Capability Boundary**: Windows rejects Linux-only launch modes and runtime installation/injection APIs before runtime actions. Imported per-game options remain stored; Windows launches ignore Linux-only flags, prefixes, and Proton paths. An imported Linux launch mode must be changed to `auto` before launching.
- **Cheat Engine Integration (Linux)**: Starts `lunarengine-x86_64.exe` through Wine/Proton, passing a Lua injection script to map directly to the game's PID.
- **Download Verification**: GE-Proton tarballs are checked against the release's `.sha512sum` asset (releases without one are refused). The Lunar Engine zip must match the pinned `LUNAR_ENGINE_SHA256` in `core/api.py`; update it together with the download URL.
- **Update Checks**: `check_app_updates()` caches a successful GitHub release lookup for the session (GitHub allows 60 anonymous API calls an hour). Bulk game checks save `last_update_check` only after a run finishes without being cancelled, so a run cut short is retried at the next startup.
- **RPGMaker Tooling**: Implements enhanced Wine/NW.js fixes for RPGMaker MV/MZ and can optionally launch through the external `rpgmakermlinux-cicpoffs` runner when users install/configure it themselves. wLib links to the upstream project but does not bundle, install, update, export, bug-report, or run mutation-oriented upstream commands automatically.
- **Playtime Tracking**: The launcher's watcher reports approximately 60-second ticks and a final exit/shutdown tick. `Api.launch_game` captures the installed version before launching, creates one session on success, and buffers any ticks that arrive before the launch returns. `record_play_tick(game_id, session_id, delta, is_final)` updates total playtime, session duration, and final `ended_at` in one transaction. Normalized session durations for the launch-time version accumulate toward 300 seconds, then set `last_played_version`; empty versions never set it. Changing the installed version mid-run keeps the session on its original version. HTML/browser targets return `tracks_playtime: false` and create no journal session.
- **Journal bridge**: `get_game_play_sessions(game_id)` returns `{success: true, sessions: [...]}` or `{success: false, error: ...}`. `wlib-playtime-tick` keeps its name and reports `{gameId, delta, isFinal, lastPlayedVersion, sessionId}`. The library updates playtime and the Unplayed badge; the open Journal refreshes its sessions. Interrupted sessions remain without an end time. If the open session disappears mid-play (sync pull or import) or cannot be created, ticks still add to `playtime_seconds`.
- **Skipped releases**: `_has_update(current, remote, skipped="")` normalizes all versions and hides the matching skipped release. Single and bulk checks still scrape and store latest metadata and report `skipped`. A different latest release becomes available again. `update_game` clears a skip when the normalized installed version changes.
- **Desktop notifications**: `maybe_auto_check` calls `check_all_updates(automatic=True)`; the keyword is internal to Python. An uncancelled run sends at most one Linux notification for changed latest versions that now have updates, plus newly completed wishlist games. Automatic runs qualify; manual runs qualify when the window is unfocused at completion. `set_window_focused(focused)` defaults to focused, and App reports focus, blur, and visibility changes. Updates > Automatic Checking has the default-on Desktop notifications toggle, saved as general setting `update_notifications` (`on`/`off`). The sender uses a daemon thread, `notify-send` then `gdbus`, five-second subprocess timeouts, and swallowed/logged failures. Flatpak permits the notification service. No new Python dependencies or Windows notifications.
- **Running Registry**: `launch(..., game_id=...)` registers process handles under a lock and removes them on exit by process identity before the final playtime tick. Linux launches use a new session; Stop sends SIGTERM to the process group and SIGKILL after five seconds if the group remains. Wine/Proton also uses the matching `wineserver -k` with the resolved prefix when available (Proton `files/bin` or `dist/bin`, Wine sibling or system server). This ends all processes in that Wine prefix, so it is skipped while another tracked game uses the same prefix (that game keeps running; only the stopped game's process group is signalled). Windows uses `taskkill /T /F /PID`, including the PID of elevated handles; failure advises closing the game itself. Launcher processes that exit before their detached games are not tracked further.

### `core/scraper.py` (HTTP Scraping)

- **Batch Versions**: `check_versions(urls, callback)` requests `https://f95zone.to/sam/checker.php?threads=<ids>` with at most 100 unique thread IDs per request. Versions use the title parser shared with the extension before comparison.
- **Page Fallback**: Missing, empty, and non-actionable versions, or endpoint failures other than challenges and HTTP 429 rate limits, fall back to guest thread pages. Regex extraction reads the title and starter post.
- **Guest-hidden attachments**: When a plain-HTTP page's starter post contains `messageHide--attach`, `_get_page()` re-fetches that thread once through a connected extension to get the signed-in HTML, because guest requests see a login prompt instead of screenshots posted as attachments. The guest page is kept when no extension is available, the extension response is not HTTP 200, or that response is challenged or login-gated. This applies only to single-thread page reads; batch endpoint reads do not load the thread page.
- **Metadata**: Add-game fetches and single-game checks read engine, tags, cover, screenshots, and starter-post edit timestamps. Thread page reads refresh screenshots alongside thread edit metadata: `_update_thread_edit_metadata_for_url` replaces the stored list when the page carries a non-empty screenshot list and keeps the stored list on an empty result. Bulk checks from the batch endpoint do not read the thread page and leave screenshots unchanged; bulk wishlist/fallback paths that read the thread page store them. A screenshots-only refresh does not bump `sync_local_modified_at`. Bulk checks preserve stored edit metadata.
- **Extension fallback order**: `_fetch_with_fallback(url)` is used by `_get_page` and `_check_chunk`. It tries plain HTTP first, then on `blocked` or `login_required` retries the same URL once through the connected extension (`core/extension_bridge.py`). The extension body is re-classified with the same rules; error results carry `browser_fallback: "unavailable"` (no eligible extension) or `"failed"` (extension also challenged/login or its fetch failed). No fallback runs for `rate_limited`, `not_found`, timeouts, or network errors. A challenged batch retries the same batch URL once through the extension, never as 100 thread pages.
- **Extension-first window**: a plain-HTTP `blocked` result sets `_prefer_extension_until = now + 15 minutes` (in memory only, never persisted). While the window is active and `extension_bridge.is_available()` is true, requests go straight to `_f95_get_via_extension` without a plain-HTTP attempt; its classified result is final. With no eligible extension connected, the window is ignored and plain HTTP is used. `login_required` never opens the window.
- **Pacing and TLS**: Every request passes through `_run_paced_f95_request`, which holds the global `_F95_REQUEST_LOCK` through the request and waits after the previous request finishes, for both `_f95_get` (urllib) and `_f95_get_via_extension` (bridge). `WLIB_UPDATE_CHECK_DELAY_SECONDS` defaults to 2 seconds and is bounded to 1–30. urllib uses the default SSL context and the configured CA certificates. Extension fetches count as F95 requests for serialization and pacing: no plain HTTP request runs while an extension request is pending, and consecutive requests stay spaced by at least the minimum interval.
- **Bounds and guarantees**: wLib waits at most 30 seconds for an extension response and never waits for an extension to connect. The fallback never opens a browser window or tab and never starts a browser; it works only when the user's browser is already running with the extension.
- **Failures**: Deleted threads report `not_found`, challenges report `blocked`, and login walls report `login_required`. Network timeouts report `navigation_timeout`; other network or parse failures report `scraper_error`.
- **Callbacks**: Results are delivered in input order, shared thread IDs reuse results, returning `False` stops processing, and callback exceptions propagate to the update worker.

### Supporting modules

- `core/game_scan.py`: `inspect_game_path` and `scan_games_folder` guess titles, versions, engines, and launch files from install folders for Add Game and Import Folder.
- `core/f95zone.py`: host-limited thread-ID extraction, thread URL normalization, and equivalent-thread matching used by the database, scraper, and backup import.
- `core/versions.py`: normalized version comparison mirrored by `normalizeVersion()` in `ui/src/utils/libraryGames.ts`.
- `core/backup_format.py`: leaf module with shared backup format identifiers, the SQLite integer bound, and `BackupValidationError`; `core.library_backup` re-exports these names.
- `core/extension_ws.py`: minimal RFC 6455 handshake, frame encoding, strict masked-frame reading, and the 8 MiB message limit for `GET /api/bridge`.

## Backend Verification Workflow

- **Type checking**: `pyrightconfig.json` is configured for `basedpyright` and currently checks `main.py` plus modules under `core/`.
- **Strict rollout**: The backend entrypoints and core runtime modules (`main.py`, `core/api.py`, `core/database.py`, `core/launcher.py`, and `core/scraper.py`) are opted into `strict` checking while the rest of the project remains at `recommended`.
- **Initial rollout scope**: `tests/` are intentionally excluded from type checking for now so backend diagnostics stay focused on application code.
- **Linting**: `ruff` is configured repo-wide for Python files so tests, utility scripts, and backend modules share the same baseline lint rules.
- **Runtime smoke check**: `scripts/smoke_backend.py` imports the backend, configures runtime helpers, imports `pywebview`, and exercises extension sync with temporary HOME and `WLIB_DATA_DIR` paths on both platforms.
- **Local checks**: Linux wrappers `bash scripts/check-python.sh` and `bash scripts/check-python-clean.sh` run `ruff`, `basedpyright`, the smoke check, and `pytest` in an active or fresh Python 3.12 environment. On Windows, install `requirements-dev.txt` plus `requirements-windows.txt` and run the same tools directly in PowerShell.

## SSL Certificate Configuration

`main.py` configures SSL certificates on startup to ensure reliable HTTPS connections for scraping and API requests:

1. **Bundled Certifi**: Includes `certifi` CA certificates within the PyInstaller bundle at `_internal/certifi/cacert.pem`
2. **System Certificate Fallback**: Checks standard system paths (`/etc/ssl/certs/ca-certificates.crt`, `/etc/ssl/cert.pem`) if certifi is unavailable
3. **Environment Variable Setup**: Sets `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE`, and `CURL_CA_BUNDLE` to the selected certificate bundle
4. **AppImage Configuration**: The AppRun script performs parallel certificate setup for the AppImage runtime context

Windows releases bundle certifi and use the same certificate-selection flow. The `/etc/ssl/...` fallbacks and AppImage setup apply to Linux; HTTPS verification remains enabled on both platforms.

## Game sandboxing

`core/game_sandbox.py` owns cached host detection, argv construction, Sandboxie
configuration, process-list parsing and tool installation. Linux detection uses
host `bwrap --version` and `unshare -Ur true`; Flatpak runs both through
`host_command`. `ready`, `missing` and `unusable` include a path, version and
reason. Recheck invalidates the cache. Platform capabilities expose `sandbox`
(`bwrap`, `sandboxie`, or null).

The launcher wraps the final mode-specific argv before `host_command`. bwrap
uses private user/PID/IPC/UTS namespaces, a private `/tmp` and `/run`, read-only
system and runtime mounts, selected display/audio sockets, a writable game
folder and Wine prefix, and `<data dir>/sandbox/<id>/home` at the real HOME
path. It does not expose the session D-Bus. An in-namespace shell supervises
launcher stubs until their detached descendants exit. Stop terminates the outer
bwrap process group and skips the host wineserver. Automatic Cheat Engine
injection, when enabled, runs inside that same sandbox after its usual delay.

Windows configures `wLib_<id>` with `SbieIni.exe set` and `/reload`, preserving
sandbox storage and replacing the game-folder pass-through path each launch.
Tracking parses the count followed by PIDs from `Start.exe /box:<box> /listpids`;
Stop calls `/terminate` in the game's Windows session. HTML games continue to
open in the browser without sandboxing or playtime tracking. A missing or
unusable tool refuses a sandboxed launch; it never falls back to a plain launch.
A writable mount (game folder, Wine prefix, or Sandboxie pass-through path) that
is `/`, HOME, or an ancestor of HOME is refused, because it would expose the whole
home folder. A runtime under `~/.local/bin` mounts only that `bin` folder.

Each running Wine/Proton game records its resolved prefix and sandbox mode.
Under the registry lock, a launch is refused with `prefix_in_use` when the same
prefix is already in use and either launch is sandboxed. Prefix comparisons
resolve symlinks and normalize Proton's `pfx` subdirectory. The error names the
running game. The API reads sandbox preferences from the stored parent game,
including launches through extra targets.

Frontend-callable sandbox methods:

| Method | Result / behavior |
| --- | --- |
| `get_sandbox_status()` | Cached tool status, path, version and reason. |
| `recheck_sandbox_status()` | Re-runs host detection. |
| `get_sandbox_install_info()` | Host command, package manager, pkexec availability, install availability and official URL. |
| `install_sandbox_tool(tool)` | Starts a background install; mismatched host/tool returns `unsupported_platform`. |
| `get_sandbox_install_status()` | Thread-safe running/done/current/error progress. |
| `set_sandbox_for_all_games(enabled)` | Updates every game's next-launch preference and returns `changed`. |
| `open_game_sandbox_folder(game_id)` | Opens persistent per-game storage, creating the folder if necessary. |

Linux installs use the distro's bubblewrap package via host `pkexec`; no portable
bwrap is downloaded. Windows installs prefer winget. The direct-download
fallback selects the official architecture-specific GitHub release, checks
Authenticode trust and the expected publisher before elevation, and deletes the
staged installer on every exit. Signature failure never runs the installer.

Save detection searches both the real profile and the sandbox profile when the
stored flag is enabled: Linux `.renpy`, `.config` and `.local/share`, and Windows
box `user/current`/profile roots with AppData, Documents and Saved Games. Overrides
continue to work when sandboxing is switched off. Removing a game or switching
sandboxing off does not delete its sandbox storage. JSON backups carry the
preference and selected settings, while sandbox files remain local.


## Companion tools

The Linux-only `companion_tools` capability controls launching; CRUD remains available on other hosts so saved definitions survive. The bridge exposes:

- `list_companion_tools()`: ordered definitions in `{success, tools}`.
- `save_companion_tool(tool)`: create or update by optional ID; returns `{success, tool}`. Validates name, kind, executable file and parsed arguments/command.
- `delete_companion_tool(tool_id)`: removes the definition and cascading overrides.
- `get_game_companion_tools(game_id)`: definitions with effective `visible` and `overridden` flags.
- `set_game_companion_tool_override(game_id, tool_id, visible)`: boolean to override, null to reset.
- `list_launchable_companion_tools(game_id)`: visible tools supported by the current run; empty when not running.
- `launch_companion_tool(game_id, tool_id)`: rechecks host, existence, visibility and running state; errors name the tool, and duplicates return `tool_running`.

`RunningGame` captures the executable path and a copy of the final Wine/Proton command and environment. Only unsandboxed Wine/Proton runs have `wine_context`; changing settings during play does not change it. Arguments use `shlex.split` followed by per-token `%game_dir%` / `%game_exe%` expansion and leading `VAR=value` extraction. Wine and Native tools use their own folder; Command tools use the game folder. Host tools strip packaged-runtime and inherited Wine/Proton environment variables.

The launcher starts tools through `host_command` in separate sessions, records them in `_tools` under the running lock, and reaps them independently of game playtime. A tool never keeps a game marked running. On game exit or Stop, only that game's Wine tool groups receive SIGTERM followed by SIGKILL after two seconds; Native and Command tools keep running and are forgotten. Cleanup never uses wineserver to terminate tool groups, so another game sharing the prefix is spared. Tool signalling rejects non-integer IDs, IDs <= 1 and the launcher's own group. Shutdown leaves games and tools running; tests disable atexit registration for mocked launchers.

Backup inspection summarizes tool definitions and warns about malformed entries or foreign-host paths. Import counts `companion_tools_updated` and `companion_tools_skipped`, imports definitions before games in the same transaction, and merges by case-insensitive name. Overwrite preserves local IDs and replaces game overrides only when their field is present. Fill-missing retains matching definitions and existing overrides. Missing files generate migration warnings; definitions with host-compatible paths can still be imported and edited on the destination.
