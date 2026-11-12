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

## Game catalog client

`core/catalog.py` keeps a read-only copy of the published catalog (see
`docs/catalog.md`) as `<data dir>/catalog.db`, with a `catalog.json` sidecar
holding the installed manifest, `last_check` and `last_error`. `Api.__init__`
creates `self.catalog` and starts a daemon-thread check unless the
`catalog_enabled` setting is `"0"` (default on). A check skips when the last one
was under 12 hours ago unless forced, fetches the manifest
(`https://wlib-db.thekirin.net/v1/manifest.json`, overridable with
`WLIB_CATALOG_MANIFEST_URL`), and downloads the snapshot only when its SHA-256
differs from the installed one and its `schema_version` is in
`core.catalog_format.SUPPORTED_SCHEMA_VERSIONS`. The download streams into a temp
file in the data folder, stopping past the manifest size (hard cap 200 MB); then
the hash is checked, `lzma` decompresses into a temp `.db`, which is opened with
`mode=ro` for `PRAGMA quick_check` and `meta.schema_version`, and `os.replace`
installs it. Failures keep the old catalog and are only recorded in the
sidecar. Requests are plain GETs with a `wLib/<version>` user agent and no query,
cookies or library data. The app keeps no connection to `catalog.db` open and
never writes the library database here; library export and sync do not include
the catalog.

- `get_catalog_status()` returns `{success, enabled, installed, built_at,
  sources: {name: {count, updated_at}}, last_check, last_error, checking}`.
- `check_catalog_now()` starts a forced background check and returns the status
  plus `started`; it fails with an error when the catalog is turned off.
- `get_settings()` includes `catalog_enabled`; `save_settings({catalog_enabled})`
  stores `"1"`/`"0"`. Turning it off deletes `catalog.db` and `catalog.json`;
  turning it on starts a check. The setting is local and not part of sync or
  JSON export.

## Library sync and encrypted backups

`core/library_sync.py` contains `SyncManager`, `FolderLocation`, `DropboxLocation`, and `GoogleDriveLocation`. All locations expose revision/read/write methods. Sync shares metadata, user state, and general settings using `wlib.library_migration` v1 plus a UTC `sync.modified_at` and hostname `sync.device`. Machine paths, launch settings, and local cover files are excluded. When only one side changed since the last sync, that side replaces the other for the whole library. When both changed, or when the sync file would remove most of the local games (an empty file from a device whose folder sync had not delivered the real one yet), `_run_once` stops in `needs_choice` with `choice_reason` `conflict` or `removal` and waits for Use sync file, Use this machine or Merge once; push-only runs and the exit push never resolve it. A first sync on an empty library with no sync file pushes nothing. A new revision whose `sync.modified_at` equals `sync_last_synced_at` (a sync tool rewrote the file) counts as unchanged. Simultaneous use from several machines is unsupported.

First connection pauses at `needs_choice` if both libraries contain games. `use-remote` mirrors the file including deletions, `use-local` replaces the file, and `merge-once` imports without deletions then uploads. Sync uses conservative F95 thread, exact store-link, then title/developer matching, with unique-title matching for manual games without a developer; ambiguous candidates are preserved and reported. Before a pull or a sync merge changes the library, every backup section is saved to `<data dir>/sync/snapshots/<UTC time>.json` (the last five are kept; a pre-existing `sync/last-local.json` joins the list). `list_sync_snapshots()` lists them and `restore_sync_snapshot(name)` mirrors one back, including its executable paths and launch options, saving the current library first; the next sync uploads the result. The Sync panel offers this as "Restore an earlier library". Folder sync also removes `wlib-library.json.tmp.*` files older than an hour that a killed writer left in the sync folder. Local edits during downloads cause re-evaluation; uploads retain any edits made after the payload was captured.

Bridge methods: `get_sync_status`, `set_sync_folder(path)`, `connect_dropbox`, `connect_gdrive`, `disconnect_sync`, `sync_now`, `resolve_sync_choice(choice)`, `set_sync_passphrase(passphrase | null)`, and `unlock_sync(passphrase)`. Failures include `error_code`. Runs execute in background threads and status is polled by the UI. Startup checks both sides; the 60-second watcher pushes after 30 seconds without local edits and runs a full sync (pull included) every 5 minutes. A failed run is retried after 1, 2, 4 … up to 30 minutes, or after the provider's `Retry-After`; errors only the user can fix (rejected sign-in, full storage, broken sync file) are not retried. Being offline or a 5xx is `location_unavailable`, a 429 is `rate_limited`, and a full Dropbox or Drive is `dropbox_full`/`gdrive_full`; only a refused grant is `*_auth_failed`. Signing in again to the connected account keeps the sync state and encryption key. Exit allows a final push up to 10 seconds. Push-only runs do not pull remote changes; pulls emit `wlib-refresh-library`. The main window polls sync status and marks the Sync nav item (and shows a toast once) while sync needs attention.

Folder writes check mtime/size revisions, preserve `.bak`, write `.tmp`, and replace atomically. A missing folder is unavailable and is never created. Dropbox uses urllib with the configured SSL certificates and app-relative `/wlib-library.json` (visible as `/Apps/wLib/wlib-library.json`). Uploads use update/add with strict revision conflicts; conflict responses trigger re-evaluation. Access tokens stay in memory and refresh on expiry or a 401.

Dropbox uses OAuth PKCE S256 with offline access and a ten-minute state expiry. `GET /dropbox/callback` on the existing server forwards code/state to `complete_dropbox_callback` and returns a static, uncached completion page. Extension origin restrictions remain unchanged. Connect is disabled when port 8183 is unavailable. Configure the exact redirect `http://127.0.0.1:8183/dropbox/callback` and scopes `files.content.read`, `files.content.write`, `files.metadata.read`, and `account_info.read` (display name) in the app console. No app secret is shipped. Disconnect attempts token revocation and clears local credentials even if offline.

Google Drive reuses the PKCE plumbing (`_pkce`, `_token_request`) and the pending sign-in tags its provider, so a Dropbox callback cannot complete a Google sign-in. It requests only the `https://www.googleapis.com/auth/drive.appdata` scope with `access_type=offline` and `prompt=consent`, so wLib sees only its own hidden app data folder, never other Drive files. `GET /gdrive/callback` (redirect `http://127.0.0.1:8183/gdrive/callback`, a Desktop OAuth client) is routed through the same handler as the Dropbox callback via `OAUTH_CALLBACKS` in `core/extension_server.py`; bridge methods are `connect_gdrive` and `complete_gdrive_callback`. Settings keys are `sync_gdrive_refresh_token` and `sync_gdrive_account` (the Google email). `GoogleDriveLocation` finds `wlib-library.json` by name in `appDataFolder` on every revision check (earliest `createdTime` wins when duplicates exist), uses the file's `md5Checksum` as the revision (Drive's `version` keeps rising after an upload, so it can't be compared with the value from the upload response), and keeps the file ID in memory only. Drive has no conditional upload: `write` compares the checksum, then creates (multipart) or updates (media `PATCH`), so a small race window remains if two machines upload at the same moment; use one machine at a time. The overwritten version stays in Drive's revision history, which is not reachable from the Drive web UI for app data files. Reconnecting does not revoke the previous token, because Google revokes the whole grant and that would invalidate the new token too; Disconnect revokes it and leaves the sync file in Drive.

The Google client ID is a public constant (`GDRIVE_CLIENT_ID`). Desktop clients must still send a client secret, which Google does not treat as confidential for installed apps but which stays out of the repository. `_gdrive_client_secret()` reads `WLIB_GDRIVE_CLIENT_SECRET` first, then the gitignored `core/_gdrive_secret.py` written by the build scripts (see `docs/build.md`). Without it `gdrive_available` is false and `gdrive_unavailable_reason` says why, as it does when port 8183 is unavailable. Folder and Dropbox sync are unaffected.

`core/library_backup.py` encrypts the complete JSON document with scrypt (N=131072, r=8, p=1) and AES-256-GCM from `cryptography`. The v1 `wlib.encrypted` envelope authenticates its header and uses a fresh nonce. Manual exports use fresh salts; sync stores only a derived key and salt locally. Unknown keys/salts pause at `needs_passphrase`. Disabling encryption clears the key and marks the next write for plaintext. Backup load/inspect/import/write accept optional passphrases; errors include `passphrase_required`, `decrypt_failed`, and `unsupported_format_version`. Sync settings and credentials remain outside backup allowlists.

The core strength of wLib relies on its native Python backend, divided primarily into `main.py` and modularized controllers in the `core/` package. The supported development and CI target for backend work is Python 3.12.

## Bootstrapping in `main.py`
`main.py` acts as the primary orchestrator. Upon launch, it executes these sequential steps:

1. **SSL Certificate Configuration**: Configures SSL certificates from bundled certifi or system paths for secure scraping (`configure_ssl_certificates()`).
2. **Single-Instance Bind**: Calls `bind_extension_server()` to bind `127.0.0.1:8183`. If the port is taken and `focus_running_instance()` gets a wLib reply, the new process brings the running window forward and exits, so two copies never share the database. The port is bound without `SO_REUSEADDR` on Windows, where that option would let a second process bind it.
3. **Data folder check**: `main.py` checks that the data folder can be written. If not, it shows the reason through zenity, kdialog or notify-send (and stderr) and exits with code 1.
4. **App Log**: Calls `setup_app_log()` so `<data dir>/logs/wlib.log` captures console output, Python logging, and uncaught exceptions before the rest of startup runs.
5. **Qt Runtime and WebView Cache**: Detects the Linux session type (X11/Wayland) and configures Qt platform plugins; on Windows, leaves native platform selection to Qt unless explicitly overridden (`configure_qt_runtime_environment()`). Both platforms start PyWebView with `gui="qt"`. Packaged runs refresh the WebView cache when the app version or bundled UI changes.
6. **WebView Load and Startup Diagnostics**: Imports `pywebview`, then records the Qt and renderer startup snapshot.
7. **API and Database Initialization**: Constructs `core.api.Api`, which calls `core.database.init_db()` to create/migrate the SQLite schema, prunes orphan attachment folders (PDF, TXT, MD), and creates the sync, scraper, and launcher services.
8. **Extension Sync**: Copies bundled extension assets into the platform user-data directory when files are missing or the bundled manifest version changed.
9. **Window and Background Services**: Creates the PyWebView window, starts legacy browser-data cleanup and the extension server daemon thread, and starts the library sync watcher. Once the window exists, SIGTERM, SIGHUP and SIGINT close it (a pipe set with `signal.set_wakeup_fd` wakes a watcher thread, since Python signal handlers do not run inside the Qt loop), so playtime is saved and the last sync runs; a repeat after five seconds exits at once. `wlib.sh` and the packaged launcher run the app in the background and forward SIGTERM/SIGHUP to it.
10. **UI Loop**: Enters the blocking UI loop, then allows a final sync push on exit.

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

Flatpak host lookup uses `sh -lc` to load the login PATH, then appends `~/.local/bin`, `~/.nix-profile/bin`, `/nix/var/nix/profiles/default/bin`, `/home/linuxbrew/.linuxbrew/bin` and `/run/current-system/sw/bin`. A lookup that exceeds ten seconds counts as not found. The bundled archive extractor is the exception to host execution: `/app/bin/7zz` runs directly inside the sandbox.

### `core/api.py` and `core/api_mixins/` (The API Bridge)
The `Api` class acts as the single point of entry for the Vue frontend. PyWebView lists each public method (no leading underscore, e.g. `get_games`, `launch_game`) as a Promise on the `window.pywebview.api` object, except the names that `core/bridge_exposure.py` hides (see below).
- **Layout**: `core/api.py` holds only the `APP_VERSION` and `normalize_version` re-exports and `class Api`, which combines the feature mixins in `core/api_mixins/`: `library`, `sync`, `extension`, `launch`, `updates`, `game_tools`, `runtimes`, `rpgm_runtime`, `downloads`, `saves` and `settings`. Each mixin subclasses `ApiBase` (`core/api_mixins/base.py`), which owns `__init__`, all shared instance state, and each method that two or more mixins use. Shared TypedDicts and Protocols live in `core/api_mixins/types.py`. Put a new method in the mixin for its feature; if a second mixin needs it, move it to `ApiBase`. A mixin never calls a method of another mixin. A name may be defined in only one class of `Api.__mro__` (`tests/test_js_api_surface.py` checks this). The `APP_VERSION = "..."` line lives in `core/app_version.py` (release and build scripts read it there); mixins import it from that module, never from `core.api`, so there is no import cycle.
- **Test patches**: a test that replaces a host or path function (`is_linux`, `host_which`, ...) for `Api` uses `patch_api_name(monkeypatch, name, value)` from `tests/api_patching.py`, which replaces the name in every `core.api_mixins` module that imports it.
- **Bridge listing**: PyWebView also walks into public helper objects and lists their methods as nested names. `core/bridge_exposure.py` keeps names off the listing: `hide_from_js()` flags the helper objects (`scraper`, `launcher`, `library_sync`, `cover_cache`, `cover_cards`, `catalog`, `steam_sync` and `window`), and `@python_only` flags methods that only Python calls (for example `set_window`, `reserve_runner_launch` and the OAuth `complete_*_callback` methods). Both set PyWebView's `_serializable = False` flag. `tests/test_js_api_surface.py` pins the listing in `tests/fixtures/js_api_surface.json` and checks that it equals the names `ui/src/services/api.ts` calls; rewrite the fixture on purpose with `WLIB_UPDATE_SNAPSHOT=1 pytest tests/test_js_api_surface.py`. Hidden means not listed, not blocked: PyWebView resolves a called name with `getattr` and has no allow list, so page JavaScript can still call a hidden name.
- **Concurrency**: UI calls are technically asynchronous on the JS side but block the pywebview worker pool on the Python side. The `Api` class heavily uses background thread spawning (`threading.Thread`) for long tasks (like downloading updates or mass-scraping metadata) so the UI doesn't freeze.
- **Event Emitter**: Contains wrapper helpers to dispatch Global UI Events back to Vue using `webview.evaluate_js()`.
- **Extension Sync Metadata**: Tracks whether startup extension synchronization actually updated the installed browser files so the frontend can show a toast prompting the user to reload the addon.
- **Launch Mode Contract**: Carries each game's `launch_mode` through add, update, list, and launch calls. Missing or invalid values normalize to `auto`; supported values are `auto`, `native`, `wine_proton`, `rpgmaker_linux`, and Linux-only `custom`. Add and update calls also accept `command_line_args` and `block_network` (the per-game No internet preference).
- **Launch Target Contract**: Exposes CRUD and reordering methods for additional game launch targets. `get_games()` returns each game's extra `launch_targets`, while the canonical default executable remains `games.exe_path`.
- **Store Links and Local Covers**: `add_game` accepts `links: [{source, id, url}]`; `get_games()` returns normalized `links`, and the extension can replace F95-post links through `POST /api/thread-links`. `choose_game_cover`, `set_game_cover_file`, and `clear_game_cover` manage device-local cover files; game payloads include `cover_local_url` while the extension server is bound. `get_cover_cache_status()` and `purge_cover_cache()` report and flush the remote cover cache.
- **Finished Version Contract**: `update_game` accepts a free-text `finished_version` next to `version`; the value is trimmed and empty means not finished. When a call moves `play_status` to `Completed` from another status without sending `finished_version`, the backend fills it with the call's `version` — or the stored `version` when none was sent — in the same write. Re-saving `Completed` and leaving `Completed` both keep the stored value, and an explicit `finished_version` in the same call wins. The success response includes `finished_version` only when the backend filled it; unrelated edits return just `{"success": True}`. Version-only updates ("Mark updated"), archive installs, and metadata refreshes never touch the field.
- **Library Migration Contract**: Exposes `export_library_backup`, `inspect_library_backup`, and `import_library_backup` for one-file JSON migration. Import uses an inspect-before-write flow and backup-wins field merges for selected sections.
- **External Library Import Contract**: Exposes `detect_external_library(source)`, `inspect_external_library(source, path)`, `import_external_library(source, path, options)`, and `browse_external_library_file(source, start_path)` for bringing a library in from F95Checker, Atlas, XLibrary, f69, or Playnite (`source` is `"f95checker"`, `"atlas"`, `"xlibrary"`, `"f69"`, or `"playnite"`). Detection reports the found path plus the default locations checked; the file picker falls back to per-source filters when nothing is found. Inspect previews new/matched/ambiguous counts plus skipped games without an F95 thread, a qualifying store link (DLsite, Fanza, VNDB or Ryuugames), or a usable executable, dropped cross-OS executable paths, optional local-cover copies, and the source schema version. Import accepts `sections` (`user_state`, `executable_paths`, `launch_targets`, `covers`; game identity and metadata always apply to matches) and `merge_mode` (`"overwrite"` or `"fill_missing"`, defaulting to overwrite; fill-missing only sets fields that are empty locally and keeps existing launch targets). Results include `created_ids`/`updated_ids`, and imports that touch games with F95 URLs start `check_all_updates(game_ids=...)`, returning `metadata_check` as `"started"`, `"busy"`, or `"none"`. Failures return `{"success": False, "error": ...}`. The readers live in `core/external_import.py` (`read_external_library`, `locate_external_library`); SQLite sources are read from a temporary copy opened read-only so the source app can keep running.
- **Folder Import**: `inspect_game_path(exe_path)` guesses a title, version, and engine for the Add Game form; `scan_games_folder(folder)` walks subfolders, picks a launch file, and marks entries already in the library with `in_library`. The Import Folder dialog adds selected rows through one `add_game` call per game and refreshes the library once. Guesses come from folder names and files, so the frontend lets the user review and edit them after import.
- **Runtime Setup (Linux)**: `install_rpgmaker_dependencies(prefix_path, proton_path)` runs Winetricks verbs for common DirectX/VC++/.NET/font dependencies; `install_rpgmaker_rtp(prefix_path, proton_path)` downloads the official RTP archives and runs their setup silently. Proton targets use umu and the selected build's bundled Winetricks; plain Wine targets use host tools. Both start background threads, refuse concurrent installs, and report through `get_install_status()`. `get_system_deps_command()` detects the distro package manager and returns the command for 32-bit GStreamer/multimedia libraries. `download_proton_ge()` and `get_proton_download_status()` cover the checksum-verified GE-Proton download, and `is_cheat_engine_installed()`/`download_cheat_engine()` cover the pinned Lunar Engine archive. The Windows host rejects these Linux-only helpers before doing any work.
- **umu management (Linux)**: `get_umu_status()` reports the host or wLib copy and host Python availability. `download_umu()` starts the verified upstream download; `get_umu_download_status()` reports its progress and errors. `prepare_umu_runtime()` starts preparation for the global runner and default prefix; `get_umu_runtime_status()` reports the selected runtime, readiness, progress and failure class, and `cancel_umu_runtime_prepare()` requests cancellation. These methods reject non-Linux hosts.
- **Process Control**: `get_running_games()` returns a list of game IDs; `stop_game(game_id)` requests termination and returns a success/error payload. Duplicate tracked launches return `already_running`; stopping an untracked game returns `not_running`, and stopping one whose process is still starting (e.g. behind a UAC prompt) returns `starting`.
- **Custom Statuses**: `get_settings()` returns `custom_play_statuses` as a list. `save_settings()` validates trimmed names (1–40 characters, no case-insensitive duplicates, canonical clashes, or legacy aliases such as `Replaying`); removing a name resets games using it to `Not Started` in the same transaction as the list update.
- **Collections**: `get_collections()` returns the ordered collection list; `create_collection(name, game_id=None)`, `rename_collection(old, new)`, `delete_collection(name)`, `reorder_collections(names)`, and `set_game_collections(game_id, names)` each rewrite the setting and affected game memberships in one transaction and return `{"success": True, "collections": [...]}`, or `{"success": False, "error": ...}` on refusal (invalid or duplicate names, unknown collection or game). `get_games()` returns each game's `collections` as a `list[str]` filtered against the current collection list. Names are 1–40 characters, unique ignoring case, and `All` is reserved.
- **Filter Presets**: `get_settings()` returns `library_filter_presets` (a list of `{name, filterCollection, filterStatuses, filterInstallState, filterSources, filterEngines, filterTags, sortBy, sortDir}` objects); `save_library_filter_presets(presets)` validates the whole list (1–40 character names, unique ignoring case) and writes it as one JSON setting. The getter drops unreadable entries and case-insensitive duplicates so a malformed sync payload can't break the settings payload.
- **Path Checks**: `check_path(path, kind)` reports `{ok, reason}` for a typed path without blocking anything: `file` wants an existing file, `dir` an existing folder, `parent` an existing folder for a file wLib will create, and `runner` an existing file when the value has path separators or a command found on PATH otherwise. `~` is expanded like the launch paths; an unknown kind returns `{"success": False, "error": ...}`.
- **File Timestamps**: `get_executable_modified_time(exe_path)` returns `{success, modified_at}` from the executable's filesystem mtime, or `modified_at: None` with an error for a missing path. Game Detail uses it for the Overview footer's **Exe modified** line; the adjacent **Thread updated** line uses `thread_main_post_last_edit_at`/`thread_main_post_checked_at`.
- **URM**: `urm_rpa_path` stores a user-supplied archive path. `browse_urm_file()` selects `.rpa` files. `get_urm_status(game_id)` returns `success`, `renpy`, `installed`, and `source_configured`. `set_urm_installed(game_id, installed)` (controls live in the Tools tab) detects `renpy/` plus `game/` beside the stored executable and installs/removes `game/0x52_URM.rpa`. Installs replace the destination only after a successful copy; filesystem errors return `success: false`. No download is performed.
- **Runner Discovery**: `get_available_runners()` lists system Wine and wLib Proton first, then Steam and Lutris, sorted within each source and deduplicated by real path. Steam roots: `~/.steam/root`, `~/.steam/steam`, `~/.local/share/Steam`, `~/.var/app/com.valvesoftware.Steam/data/Steam`, and `~/.var/app/com.valvesoftware.Steam/.local/share/Steam`; scans `compatibilitytools.d/*/proton` and `steamapps/common/Proton*/proton`. Lutris scans `*/bin/wine` under `~/.local/share/lutris/runners/wine` and `~/.var/app/net.lutris.Lutris/data/lutris/runners/wine`. Windows returns an empty list.
- **Archive Install Contract**: `browse_archive_file()` picks `.zip`/`.7z`/`.rar` files. `inspect_archive(path, game_id=None, target_id=None, library_folder=None)` is read-only: guessed title/version, the stored engine for updates, destination preview, `engine_pending`, tool availability, and the old-folder guard. `preview_install_destination({title, version, creator, engine, game_id?, library_folder?})` recomputes `{destination, engine_pending}` without filesystem writes; the flag means the pattern contains an unresolved `{engine}`. `lookup_thread_details(url)` uses the paced scraper and extension fallback, then `core.f95zone.split_thread_title()` to return `{success, title, version, creator, engine, cover_image, tags}`; invalid URLs and lookup failures return `{success: False, error}`. `install_archive(options)` blocks while pywebview runs it on its own thread; the dialog polls `get_archive_install_status()` (guarded by `_status_lock`). Updates for a running game are refused (`game_running`); a thread URL that belongs to another game returns `duplicate_url` with that `game_id`. `install_archive` accepts `engine`, `cover_image`, `tags`, and `rating`; cover/tags/rating apply to new games only. New installs with a URL get the same background metadata fetch as `add_game`, backfilling missing fields. Success fires `wlib-refresh-library`. Failures return `{"success": False, "error": ...}`.
- **Download Offers**: `find_download_offers()` lists the Downloads folder without watching it; `dismiss_download_offer(key)` persists `path|size|mtime` keys (capped at 200). A missing folder reads as an empty list, never an error.
- **Game Notes and Attachments**: notes live in `games.progress` as markdown; `note_links` stores ordered `{url, label}` objects. URLs must be absolute HTTP(S), at most 2048 characters, with exact duplicates removed; labels are trimmed and capped at 200 characters. Both save through `update_game` and travel in backup and sync; the bridge exposes `list_game_attachments(game_id)`, `add_game_attachment(game_id, source_path)`, `open_game_attachment(game_id, name)`, `remove_game_attachment(game_id, name)`, and `browse_attachment_file(start_path)`. Failures return `{"success": False, "error": ...}` with `error_code` `game_not_found`, `attachment_missing`, or `unsupported_attachment`.

### `core/archive_install.py` (Install From Archive)
Pure filesystem logic behind the install flow; `core/api_mixins/downloads.py` stays a thin wrapper.
- **Formats**: `.zip` extracts with the stdlib (`zipfile`) including per-entry path validation and Unix modes from `external_attr`. `.7z`/`.rar` use the first available system tool (`7zz`/`7z`/`7za`, `bsdtar`, `unrar` for rar; plus `%ProgramFiles%\7-Zip` on Windows) with a listing-based traversal pre-check and a post-extraction symlink escape check. Without a tool the error names 7-Zip. No new Python dependencies.
- **Flatpak extractor**: `.7z`/`.rar` prefer the bundled `/app/bin/7zz`, including listings and size checks, so no host extractor is needed. If the bundled binary or archive is missing from the sandbox, or the library folder is not writable there, detection falls back to the host tool order above. Host tools use `host_command()` and the cleaned host environment; the bundled tool inherits the sandbox environment. Other package formats keep system-tool detection.
- **Layout**: `build_destination()` substitutes `{creator}`/`{title}`/`{version}`/`{engine}` in `library_folder_pattern` (default `{creator}/{title}/{version}`), sanitizes each segment (including Windows reserved names), drops empties, and falls back to `{title}`. Staging lives in `<library>/.wlib-staging-*/` so the final move is a rename; stale staging folders are cleaned on the next run; free space is checked when the uncompressed size is known.
- **Zip extraction**: `extract_zip()` inflates and writes members with one worker per usable CPU core (`zip_extract_workers()`, capped at `ZIP_MAX_WORKERS`; at least `NETWORK_IO_WORKERS` on network shares), each worker with its own `ZipFile` handle.
- **Network libraries**: when `/proc/self/mountinfo` puts the library on a network filesystem (`NETWORK_FS_TYPES`: NFS, SMB/CIFS, sshfs, gvfs and similar), 7z/rar extract to local `install-scratch/` and are copied over in parallel (falling back to direct extraction when local disk is too small), and a replaced old version is renamed to `<library>/.wlib-trash-*` and deleted in the background; `sweep_trash()` resumes interrupted deletes on the next install. Every zip install sets file modes and execute bits while writing, so there is no separate permissions pass.
- **Ordering**: extract → safety checks → execute bits (Linux `.sh`/ELF via `is_elf`) → flatten a single wrapper folder → detect the engine when `{engine}` is unresolved and recompute the destination and old-folder guard → carry in-folder saves (overwrite) → rename into place → update `games`/`game_launch_targets` → remove the old folder only if asked and allowed → delete the archive last. Engine detection uses the same `game_scan.detect_engine()` rules as folder import; an unknown engine drops the token. A collision at the detected destination removes staging and leaves the library unchanged. Steps before the move roll back (staging removed, `.wlib-old-<ts>` swap restored); an in-place update that fails after the move and before the library write restores the swap and keeps the extracted tree as `<destination>.wlib-new-<ts>`; a removal failure is reported while the update stands.
- **Progress and cancel**: while extracting, the install reports bytes written to staging about once a second against the archive's unpacked size (`get_archive_install_status()` `done`/`total`), and the dialog shows a percentage. `cancel_archive_install()` works until the new version starts moving into place (`cancellable` in the status): zip extraction stops between files, a 7-Zip/unrar/bsdtar process is killed, and staging is removed, so nothing changes (`error_code: install_cancelled`).
- **Multi-part archives**: `volume_set()` recognises `.partN.rar`, `.7z.001`/`.zip.001`/`.rar.001`, `.rar` + `.r00`, and spanned `.zip` + `.z01`. Choosing any part installs from the first; a set with gaps is refused naming the missing parts; deleting the archive removes every part. Split and spanned sets need 7-Zip (or unrar for rar sets), never bsdtar. The downloads-folder detector offers a set once, through its first part, after every part is present, none changed in the last 15 seconds, no part is still downloading under a browser's temporary name, and the parts are not all the same size (only the last part of a split is shorter, so equal sizes mean more are coming).
- **Deletion guard**: `can_remove_old_folder()` refuses roots (including a mounted drive's root), home, the library/Downloads folders, parents of the new destination, folders holding other games' executables or targets, and stale paths, returning `{removable, reason}` for the dialog.
- **Saves**: the shared in-folder helper (`game/saves`, `www/save`, `save(s)`, `SaveData`, loose `*.sav`/`*.rpgsave`/`*.rmmzsave`/`*.save`; `.dat` excluded) backs both `find_save_files` and save carry-over.
- **Download matching**: `find_download_offers()` keeps top-level archives, skips `.partN.rar` (N>1) and files newer than 15 s, and matches squashed titles only against games with a pending update whose installed version differs from the guess.
- **Settings keys**: `library_folder` (default), `library_folders` (JSON list), `library_folder_pattern`, `downloads_folder`, `download_detection_enabled` (machine-specific paths group in backups), plus local-only `dismissed_download_archives`.

### Torrent downloads (`core/downloads/`)
RPDL torrents download through a separate helper process, so a libtorrent crash never takes wLib down. The main process never imports `libtorrent`.
- **RPDL client** (`hosts/rpdl.py`): sign-in (`POST /api/user/login`), public search (`GET /api/torrents`), details (`GET /api/torrent/<id>`) and `.torrent` fetch (`GET /api/torrent/download/<id>`, Bearer token). Only `https://dl.rpdl.net` is contacted, one request at a time and at least 1 s apart. A `.torrent` must be a complete bencoded dictionary of at most 14 MiB. Failures are classified (`RpdlError.kind`: `sign_in`, `not_found`, `rate_limit`, `unavailable`, `invalid_response`, `bad_login`). A 401 renews the token once with the stored password. Search results are cached for 24 hours in `<cache root>/wLib/rpdl-search.json`.
- **Credentials**: `rpdl_username`, `rpdl_password` and `rpdl_token` are device-local settings (`credentials.py`), absent from every backup and sync allowlist. `accounts.status()["rpdl"]` reports only `connected` and `username`.
- **Matching** (`rpdl_match.py`): pure functions. `title_score()` gives 0-100, with penalties for a different sequel number and for marker words (OST, DLC, demo, ...) the game lacks. `rank()` hides results below 40, shows at most 8, and preselects one only when it scores 70 or more, has seeders, carries the update's version (`listing_has_version()`) and no other eligible result is within 8 points. `plan_queries()` tries the full title, then the version-free title, then title plus developer.
- **Helper** (`torrent_helper.py`, started as `wlib --torrent-helper`, dispatched in `main.py` before the API import): JSON lines on stdin (commands) and stdout (events); stderr goes to the app log. The helper writes events to the original `sys.__stdout__`, not to the app log's stdout copy, and points `sys.stdout` at stderr so stray prints cannot corrupt the protocol.
  - Commands: `hello` (options `download_kib`, `upload_kib`, `port_mapping`), `add` (`id`, `dir`, `paused`), `pause`, `resume`, `remove`, `stop_seeding`, `set_limits`, `shutdown`.
  - Events: `ready` (with the listen `port`), `status` (all torrents, at most once a second), `finished`, `error` (fixed message plus `code`), `saved`, `removed`, `stopped`, `bye`.
  - Session: DHT, local discovery and peer exchange off; UPnP and NAT-PMP only when `port_mapping` is true; the OS picks the port. Torrents whose raw paths are absolute or contain `..`, or that contain symbolic links (BEP 47 `attr` `l` or `symlink path`, in v1 file lists or v2 file trees), are refused before libtorrent sees them. Events never carry tracker paths; the `tracker` field is scheme and host only.
  - Resume data is written to `resume.dat` on pause, when a torrent finishes, every 5 minutes, on `shutdown` and when stdin closes (the helper then exits).
- **Supervisor** (`torrents.py`, `TorrentSupervisor`): owns job state and the helper. The helper starts lazily; `Api()` calls `load()` and `cleanup()` at startup and registers `shutdown()` with `atexit`. Torrent jobs are merged into `DownloadManager.status()` with `kind: "torrent"`, `peers`, `upload_speed`, `ratio`, `ratio_target`, `seed_state`, `paused`, `hint`, `eta`, `archive_path` or `folder_path`. `find_payload()` reports an archive (the first part of a multi-part set) only when every file is part of that one archive or a small extra (readme, `.nfo`, image); anything else, such as a game folder that ships its own `.zip`, is a folder install.
  - Restart: a crashed helper restarts after 1, 5 and 15 s; more than three exits within 10 minutes marks the downloads failed ("Torrent engine keeps stopping") and keeps their data. A missing engine reports "Torrent downloads are unavailable in this build".
  - Disk: a start needs the torrent's size free; a running torrent pauses below 512 MiB free.
  - Hints: "Looking for peers" after 20 s without peers; "Connected, but no data is arriving" after 120 s.
- **Seeding** (`seeding.py`, `torrent_settings.py`): `decide_after_finish()` maps `torrent_seed_mode` (`ask`, `always`, `never`) to ask, seed or stop. `limit_reached()` compares uploaded bytes with `torrent_seed_ratio` times the size and seeding time with `torrent_seed_hours`; only time the helper reports as seeding counts. `torrent_seed_*` are in `GENERAL_SETTINGS_KEYS` (backups and sync); `torrent_download_limit_kib`, `torrent_upload_limit_kib` and `torrent_port_mapping` are device-local. Saving the limits calls `supervisor.apply_limits()`.
  - `seed_state` is `""`, `awaiting_answer`, `seeding` or `ended`. An unanswered question is stored as phase `awaiting_seed_answer` and asked again after a restart, with the torrent re-added paused.
  - An ended torrent's folder is deleted only after an install from it succeeded (`release(path, installed=True)`) and no install holds it open. Ended torrents never installed are removed after 30 days.
- **Layout**: `downloads/torrents/<info-hash>/` holds `meta.torrent`, `resume.dat`, `state.json` (job id, game id, RPDL id, title, version, phase, error, uploaded bytes, seeding seconds, `installed`, timestamps) and `data/`. These files carry the tracker passkey, so they stay in this folder, outside backups, sync and diagnostics. `DownloadManager.cleanup()` only visits regular files in `downloads/`, so it skips this folder. Startup cleanup deletes folders with no job, and paused, failed or ended ones untouched for 30 days.
- **Install from a torrent**: `install_archive(..., source_kind="folder")` accepts only folders inside `downloads/torrents/`. A seeding torrent's files are copied; otherwise the folder is moved when staging and library share a drive (restored if the install fails before it lands). An archive inside the torrent area is never deleted by "Delete archive after install".
- **API** (`core/api_mixins/downloads.py`, mirrored in `ui/src/services/api.ts`): `connect_rpdl_account`, `disconnect_rpdl_account`, `find_rpdl_torrents(game_id, version, refresh)`, `start_rpdl_download(game_id, torrent_id, version)`, `pause_torrent`, `resume_torrent`, `answer_seed_prompt(job_id, seed, remember)`, `stop_seeding`. Starting is refused while the game runs or when not signed in. `delete_game` calls `supervisor.forget_game()`.
- **Packaging**: `libtorrent==2.1.1` is pinned in `requirements.txt` and the locks; `scripts/build.sh` and `packaging/windows/wlib.spec` collect it.

### Library folders and part updates

`get_settings()` returns `library_folders: list[str]` and `library_folder: str`; `save_settings()` validates the pair together, strips empty entries and deduplicates by realpath. The default must be listed whenever the list is non-empty. Both keys may be cleared together. `list_library_folders()` returns `[{path, is_default, available, free_bytes}]`, using sandbox writability and the nearest existing parent's free space; unavailable folders have `free_bytes: null`.

`inspect_archive` returns the chosen `library_folder` and `own_targets_inside` (other target labels inside the replaced folder). Without an explicit folder, updates choose the deepest listed folder containing the replaced part, falling back to the default. `preview_install_destination` accepts `library_folder` in its options. `install_archive(options)` accepts `library_folder` and `target_id`; an explicit folder must still be listed by realpath. Staging, disk-space checks and the final rename use that folder. Folder choices do not change the saved default.

A null/omitted `target_id` updates the default executable. A numeric ID must belong to `game_id`; saves and old-folder handling follow that target. Only the chosen target is directly repointed; other targets and the default move when their relative executables exist in the new install. Game version/engine and an in-folder save override still update on the game row. Cleanup protects every listed library folder and keeps the old install when any own target remains inside it after relocation, naming the target in `kept_old_folder_reason`.

Path-setting backups export `library_folders` as a native JSON list beside `library_folder`. Import filters foreign paths per entry with `foreign_path` warnings, chooses a surviving default, supports older default-only backups, and preserves a local pair in fill-missing mode.

### `core/game_saves.py` (Game Saves Folder)
Pure functions over explicit paths (testable against `tmp_path`); `core/api_mixins/saves.py` resolves the folder from the stored game row and calls in, so the frontend never sends a path that Backup or Import writes to.
- **Detection**: `detect_save_locations` holds the former `find_save_files` body, which stays as a wrapper on `Api`. Entries carry `eligible`; the game's own executable folder is `eligible: false` (a lead only). `resolve_effective_folder` picks the override, else the single eligible detected folder, else none.
- **API**: `get_game_saves(game_id)` (override, detected, effective folder, backups, retention), `set_game_save_folder(game_id, path)` (empty clears; `~` expanded; absolute only; not inside `save-backups/`; not an existing file; may not exist yet), `backup_game_saves(game_id)`, `import_game_saves(game_id, source)`, `restore_game_saves_backup(game_id, backup_name)`, `open_game_save_backup(game_id, backup_name)`. Opening the saves folder itself uses `open_folder`. Prefix and runner feed detection only for non-`native` launch modes. `backup_name` must match the timestamp pattern and resolve inside the game's backup folder.
- **Layout**: `<data dir>/save-backups/<game id>/<YYYY-MM-DD_HH-MM-SS>[-N]/` holds plain folders in local time. Copies go to a `.<name>.partial` sibling and are renamed into place; any failure removes the partial folder. Symlinks are skipped and counted. Listing and pruning only see pattern-named folders, so a user's own folders there are untouched. Removing a game moves its backups to `save-backups/removed/<game id>_<timestamp>/`, so they stay on disk but a later game with the same ID does not inherit them. Removing a game that is running, being installed, restored or changed by a mod tool is refused (`game_running` / `game_busy`).
- **Retention**: the `save_backup_keep` general setting is `all` (default) or `1`–`99`. After each successful backup, including pre-import ones, the game's oldest backups beyond the limit are deleted, never the one just made. Restoring also protects the backup being restored.
- **Import**: merge copy (same-named files overwritten, others kept). The source may not be, sit inside, or contain the saves folder; a file/folder type conflict stops it before any write. A non-empty target is backed up first and the import stops if that fails. The merge itself is not atomic, so the error names the backup.
- **Concurrency**: backup, import and restore hold a per-game guard (`Api._saves_busy`); a second call returns "A save operation is already running for this game". Calls block only their own PyWebView thread.
- **Backups and sync**: `save_path_override` exports with the launch-configuration section (path warning when missing, still imported) and never reaches the sync file. Save backups are never exported.

### Game mod tools (`core/game_tools.py`, `core/renpy_tools.py`, `core/rpgm_tools.py`, `core/unity_tools.py`)
Pure functions over explicit folders (testable against `tmp_path`); `core/api_mixins/game_tools.py` resolves the folder from the stored game row, so the frontend never sends a game path. Only the output folder for separate-folder extraction comes from the user.
- **Detection**: `game_tool_folder(row)` is the executable's folder (`Api._get_urm_game_dir` uses it). `detect_engine` reads the files on disk and ignores the stored engine label: `renpy/` plus `game/` is Ren'Py; the first of `<folder>/www` or `<folder>` holding `data/System.json` is the RPG Maker MV/MZ data root. Unity is a `UnityPlayer.dll`/`UnityPlayer.so` or a `<exe>_Data` folder with `globalgamemanagers`, `data.unity3d` or `mainData`; `detect_engine` only checks that layout (`is_unity_folder`), while `detect_unity` also reads the platform, scripting backend, architecture (PE machine field) and Unity version for `get_game_tools`.
- **API**: `get_game_tool_support(game_id)` (`engine`: `"renpy"`, `"rpgm"`, `"unity"` or `""`; cheap, decides whether the Tools tab exists), `get_game_tools(game_id)` (full tab state: Ren'Py console state, archive list with size and format, URM status; RPG Maker encrypted counts, missing copies, key source, switch state, backup present), `set_renpy_console(game_id, enabled, developer)`, `extract_renpy_archives(game_id, options)` (`{"mode": "folder" | "in_place", "output": str}`), `decrypt_rpgm_assets(game_id)`, `set_rpgm_use_decrypted(game_id, enabled)`, `get_game_tool_status(game_id)` and `cancel_game_tool(game_id)`. Unknown ids return `success: false`; an action on a game of the wrong engine is rejected.
- **Unity**: Unity tools expose `list_bepinex_versions(game_id)`, `install_bepinex(game_id, options)`, `uninstall_bepinex(game_id, options)` and `restore_bepinex(game_id)`. Downloads use `bepinex-cache/` in the data dir (SHA-256 checked when GitHub publishes a digest); IL2CPP installs also place the Unity base libraries in `BepInEx/unity-libs/` so the first launch needs no internet. Uninstall removes folders it leaves empty. Installations preserve plugins, configuration, patchers and Unity libraries, and back up replaced files. Linux-native Unity installations are detected but not supported for BepInEx actions.
- **Runs**: every action goes through `Api._tool_run`: one run per game (`A tool is already running for this game`) and none while `get_running_games()` lists the game. Status (`running`, `action`, `done`, `total`, `current`, `cancel`) lives in `_tool_status` under a lock and is dropped when the run ends, even after an exception. Extraction and decryption block their own PyWebView thread while the Tools tab polls `get_game_tool_status` every 500 ms; `cancel_game_tool` sets the flag the engine callback reads, checked between files and between chunks. Files written before a cancel are kept, and no `.wlib-partial` file is left behind.
- **Console** (`renpy_tools`): `game/zzz_wlib_console.rpy` sets `config.console = True` (and `config.developer = True` with developer mode) in an `init 999 python:` block. State is `off` (neither file), `on` (the `.rpy` without the developer line, or only the `.rpyc`) or `developer`. Changing the mode rewrites the script and deletes the stale `.rpyc`; turning it off deletes both.
- **RPA safety rules**: archive contents are untrusted. The index is read with a `pickle.Unpickler` subclass that allows only the two callables pickle protocol 2 uses for bytes, each restricted (`_codecs.encode` with latin-1 only; `bytes()` with no arguments only, for the empty prefix rpatool writes), and raises for everything else (`pickle.loads` is never used); the archive is then reported as unsupported and nothing runs. The index is inflated with a 64 MiB cap. RPA-2.0, 3.0 and 3.2 headers are read (3.0: XOR of the hex fields after the offset; 3.2: after one ignored field; anything else, including RPA-1.0, is unsupported). Entry data is the prefix followed by `length` bytes from `offset`; as in Ren'Py's own loader, `length` does not count the prefix. Entries whose data lies past the end of the archive are skipped. Entry paths are turned into `/`, then absolute, drive, empty or `..` paths are skipped, and the `realpath` of the destination's parent must stay inside the target root (this also stops a symlink planted in the target). Files are copied in 1 MiB chunks to `<dest>.wlib-partial` and renamed into place. `0x52_URM.rpa` is never listed or extracted.
- **Extraction modes**: `folder` writes `<output>/<archive name without .rpa>/` (overwriting same-named files there); `output` must be an existing absolute folder outside the game folder. `in_place` writes next to each archive, skips `.rpy`, `.rpyc`, `.rpym` and `.rpymc` (a loose copy of an archived script could define its labels twice) and never replaces an existing file. Free space on the target drive is checked before the first write. The result carries `files`, `bytes`, `skipped` (with `skipped_scripts`, `skipped_existing`, `skipped_unsafe`, `skipped_invalid`), `failed`, `unsupported` and `cancelled`; a `PermissionError` stops the run with its message.
- **RPG Maker decryption** (`rpgm_tools`): `.rpgmvp`/`.png_` become `.png`, `.rpgmvo`/`.ogg_` become `.ogg`, `.rpgmvm`/`.m4a_` become `.m4a`, next to the originals (kept). A file is decrypted only when it starts with `RPGMV` and is at least 32 bytes; an existing copy is kept and counted. The key is `encryptionKey` from `System.json` (read with `utf-8-sig`, 32 hex digits); when it is missing or malformed it is recovered from the first encrypted image's PNG header and confirmed on a second image when one exists (audio alone cannot recover a key). Decryption streams: the 16-byte header is dropped, the next 16 bytes are XORed, the rest is copied through `.wlib-partial`.
- **"Use decrypted files in-game"**: turning it on is refused while any really-encrypted asset lacks its copy (the error gives the count). It copies `System.json` to `System.json.wlib-backup` once (never overwritten), sets `hasEncryptedImages` and `hasEncryptedAudio` to `false`, and writes the file back minified with key order kept. Turning it off restores both flags to their values in the backup (`true` when there is no readable backup), so a game that encrypted only its images keeps `hasEncryptedAudio` false, and keeps the backup. The switch reads as on exactly when both flags are `false`.

### RPG Maker save editor (`core/rpgm_saves.py`, `core/lzstring.py`)
Reads, edits and rewrites RPG Maker MV (`fileN.rpgsave`) and MZ (`fileN.rmmzsave`) slot saves.
- **Codecs:** MV is LZString Base64 text (`core/lzstring.py`, an in-house port tested against reference JS output); MZ is zlib-deflated JSON stored as a UTF-8 "binary string". Read files in binary mode. Data that does not decode to a JSON object raises `UnsupportedSaveFormat`.
- **Names and view model:** `load_names()` reads `Items/Weapons/Armors/Actors/Classes/System.json` from the data root (blank names skipped, escape codes stripped, missing files fall back to empty). `build_view()` resolves gold, inventory, actors, switches and variables through MV's `{"@a": [...]}` array wrappers; `@` keys are never listed.
- **Patches:** edits arrive as `set`, `set_count`, `set_switch`, `set_variable` and `set_actor` ops, validated and applied in order to a freshly decoded tree. `@` keys are rejected, scalar types are kept, a level edit rewrites `_exp[classId]` from `exp_for_level()`.
- **Slots:** `list_slots()` lists `<data root>/save` plus the effective saves folder (deduplicated by real path); `resolve_slot()` addresses files by name only.
- **Safe write (`write_save`):** size/mtime check, decode and apply, backup of the file's folder, encode, verify by decoding, then temp file, `fsync` and `os.replace`. Any failure leaves the file untouched.

API methods in `core/api_mixins/saves.py` (mirrored in `ui/src/services/api.ts`): `list_rpgm_saves(game_id)`, `load_rpgm_save(game_id, file)` and `write_rpgm_save(game_id, file, patches, size, mtime_ns)`. `mtime_ns` is a string because nanosecond timestamps exceed JavaScript's safe integer range. Writes run inside `_save_operation(game_id, writes=True)`. Failures return `error_code`: `game_not_found`, `unsupported_engine`, `not_found`, `unsupported_format`, `invalid_patch`, `changed`, `backup_failed`, `game_running`, `busy`, `read_failed`, `write_failed`.

### `core/game_attachments.py` (Attachments)
Pure filesystem storage behind the attachment bridge; `core/database.py` stays free of file I/O.
- **Location**: `<data dir>/attachments/<game id>/` holds copied PDF, TXT and MD files; the filesystem is the only source of truth, so files deleted by hand disappear from the list. Listing returns regular non-dot PDF, TXT and MD files as `{name, size}` sorted case-insensitively by name.
- **Adding**: `add_attachment` accepts `.pdf`, `.txt`, and `.md` case-insensitively; PDFs require a `%PDF-` signature and text files must have no NUL in their first 8 KiB. It picks a free `name (N).ext` on collisions, copies through a `.<name>.part` temp file with `os.replace`, and removes the temp file on failure. The original file is left untouched. Attaching takes effect immediately without Save Changes.
- **Names**: `resolve_attachment` rejects empty names, `/`, `\`, NUL, `.`/`..`, and leading dots, requires the realpath parent to equal the game folder, and raises a distinct missing-file error (`attachment_missing`) when the file is gone. Open and remove act only inside the game's folder.
- **Cleanup**: `Api.delete_game` calls `remove_game_attachments` after a successful DB delete (logged, never fails the delete). `apply_library_backup` calls `prune_orphan_attachments` with the remaining game IDs after the commit when `deleted > 0`, covering sync and mirrored imports. `Api.__init__` prunes once after `init_db()` inside try/except with logging. `prune_orphan_attachments` and `remove_game_attachments` delete only all-digit subfolders for IDs not in the library; anything else under `attachments/` is left alone.
- **Backups**: JSON exports and cloud sync carry notes and links but never attachment data or attachment names; importing or syncing keeps attachments of games that remain.

### `core/library_backup.py` (JSON Migration)
This module serializes and imports semantic library backups without copying the raw SQLite database.
- **Format**: Writes one JSON document with `format`, `format_version`, export timestamp, selected sections, game metadata, optional game sections, and selected settings groups.
- **Always-included metadata**: Every exported game includes title, developer, engine, tags, F95 URL, version fields, thread completion flag, store links, screenshot URLs, and cover reference so records remain identifiable after migration.
- **Import matching**: Matches games by normalized F95 thread identity first, then by a shared exact store identity, then by normalized title/developer only when there is exactly one local match. Records carrying only a title match a unique same-title local game without an F95 URL or developer, and a record whose thread URL is unknown locally merges into a unique URL-less local game with the same title/developer. Ambiguous fallback matches are skipped and reported. Backups without a `selected_sections` manifest offer every game section the records actually carry.
- **Merge behavior**: Matched games receive backup values for always-included metadata and selected optional sections. Unselected sections preserve local values. Playtime is overwritten from the backup when user state is imported, never summed.
- **Safety boundaries**: The JSON export excludes cookies, webview storage, downloaded runtimes, extension copies, caches, local cover files, cover cache files, save backups, game attachments (PDF, TXT, MD), and diagnostics.
- **Cross-platform paths**: Inspection reports missing executable paths and foreign global path settings. Import skips global settings whose path syntax belongs to the other OS; per-game paths and launch options remain available to edit after migration.

### `core/launcher.py` (Process Management)
This module handles native host launching and Linux compatibility runtimes.
- **Environment Overrides (Linux)**: Per-game Japanese locale and Wayland settings modify the `env` dictionary passed to `subprocess`. Windows ignores these flags and removes Wine/Proton environment variables from native launch environments.
- **Launch Modes**: Windows exposes `auto`: `.exe` runs directly, `.bat`/`.cmd` uses `cmd.exe`, `.jar` uses `java -jar` with Java on `PATH`, and `.html`/`.htm` opens in the default browser. Linux additionally supports `native`, `wine_proton`, `rpgmaker_linux`, and `custom`. Custom Command uses `command_line_args` as the full command, substitutes `%command%` tokens with the absolute target, and runs in the game folder with a clean host environment. Leading `NAME=value` assignments are applied without a shell. Empty commands are rejected. Windows command-line arguments use Windows quoting rules.
- **Built-in Web Player**: `resolve_web_player()` in `core/web_launch.py` (re-exported by `core/game_launch.py`) decides whether an `.html`/`.htm`/`.swf` target opens in the built-in player or the system browser (`.swf` is always built-in; HTML follows the per-game `html_player` value, then the `html_builtin_player` setting, which reads as on when absent; Wine/Proton, Custom Command and the RPGMaker runner on Linux are not web launches). The built-in player is the wLib binary in a `--play-web --root --entry --profile --title [--flash] [--offline] [--links open|ignore]` mode (`core/web_player.py`, dispatched from `main.py` before `core.api` loads) and runs through `execute_process`, so the sandbox, No internet, GameMode/MangoHud, playtime, running state and Stop apply. It serves the game at `wlib-game://game/` (one stable origin per game, with a per-game QtWebEngine profile in `<data dir>/web-player/<id>/`, outside the sandbox home) and bundled Ruffle at `wlib-game://ruffle/` (`ui/dist/ruffle/`). Sandboxed launches bind the profile folder read-write and wLib's own files read-only, and set `QTWEBENGINE_DISABLE_SANDBOX=1`; `--offline` also blocks `http(s)`/`ws(s)` requests inside the player, and sandboxed or offline players drop external links (otherwise they open in the system browser). In Flatpak the player runs inside the app (not through `flatpak-spawn --host`); sandboxed launches use `flatpak-spawn --sandbox` with only the game and profile folders exposed. The first built-in launch of an HTML game that follows the default returns `notice: "html_player_first_use"` once (device-local `html_player_notice_shown`). Removed games' player storage moves to `web-player/removed/` and is pruned after 30 days with the sandboxes. System-browser launches stay untracked (no sandbox, No internet or Stop).
- **Launch Targets**: Alternate targets reuse the same launcher entrypoint as the default executable; only the selected executable path changes. Playtime remains keyed to the parent `game_id`.
- **Wine & Proton**: Plain Wine launches prepend the configured `wine` binary with the target's `WINEPREFIX`. Proton launches execute through `umu-run` (with the shared Steam Linux Runtime container) via `core/umu.py` and `core/umu_launch_wrapper.py`. Direct invocation of `proton run` is deprecated because modern Proton builds require the runtime container. If umu-launcher or the required runtime is missing, launches are refused with `umu_missing` or `umu_runtime_missing`. Existing Proton compatibility directories (`proton_compat`) are preserved, and symlinked `pfx` targets that umu would overwrite are rejected.
- **Network Isolation**: When `block_network` is enabled, Linux launches execute inside a private network namespace with only a loopback interface (`--unshare-net` inside bubblewrap sandbox, or a thin root-loopback `bwrap` wrapper when unsandboxed). A missing or unusable bubblewrap refuses launch with `network_block_unavailable`; on Windows, network isolation requires Sandboxie (`network_block_needs_sandbox`), and the box sets `NotifyInternetAccessDenied=n` so a blocked game does not open Sandboxie's SBIE1307 message window. For offline launches, Proton runner codenames are resolved to an installed build before launch, refusing with `proton_missing_offline` if none exists. The umu launch wrapper skips rewriting the runtime install marker so read-only mounts in the sandbox do not fail.
- **Cheat Engine Integration (Linux)**: Starts `lunarengine-x86_64.exe` through Wine/Proton, passing a Lua injection script to map directly to the game's PID. Sandboxed Proton launches skip Cheat Engine injection, while unsandboxed Proton launches join the running game's container.
- **Download Verification**: GE-Proton tarballs are checked against the release's `.sha512sum` asset (releases without one are refused). The Lunar Engine zip must match the pinned `LUNAR_ENGINE_SHA256` in `core/api_mixins/runtimes.py`; update it together with the download URL.
- **Update Checks**: `check_app_updates()` caches a successful GitHub release lookup for the session (GitHub allows 60 anonymous API calls an hour). Bulk game checks save `last_update_check` only after a run finishes without being cancelled, so a run cut short is retried at the next startup.
- **RPGMaker Tooling**: Implements enhanced Wine/NW.js fixes for RPGMaker MV/MZ and can optionally launch through the external `rpgmakermlinux-cicpoffs` runner when users install/configure it themselves. wLib links to the upstream project but does not bundle, install, update, export, bug-report, or run mutation-oriented upstream commands automatically.
- **Playtime Tracking**: The launcher's watcher reports approximately 60-second ticks and a final exit/shutdown tick. `Api.launch_game` captures the installed version before launching, creates one session on success, and buffers any ticks that arrive before the launch returns. `record_play_tick(game_id, session_id, delta, is_final)` updates total playtime, session duration, and final `ended_at` in one transaction. Normalized session durations for the launch-time version accumulate toward 300 seconds, then set `last_played_version`; empty versions never set it. Changing the installed version mid-run keeps the session on its original version. System-browser HTML targets return `tracks_playtime: false` and create no journal session; built-in player launches are tracked like any process.
- **Journal bridge**: `get_game_play_sessions(game_id)` returns `{success: true, sessions: [...]}` or `{success: false, error: ...}`. `wlib-playtime-tick` keeps its name and reports `{gameId, delta, isFinal, lastPlayedVersion, sessionId}`. The library updates playtime and the Unplayed badge; the open Journal refreshes its sessions. Interrupted sessions remain without an end time. If the open session disappears mid-play (sync pull or import) or cannot be created, ticks still add to `playtime_seconds`.
- **Skipped releases**: `_has_update(current, remote, skipped="")` normalizes all versions and hides the matching skipped release. Single and bulk checks still scrape and store latest metadata and report `skipped`. A different latest release becomes available again. `update_game` clears a skip when the normalized installed version changes.
- **Cover refresh option**: `check_all_updates(game_ids=None, refresh_covers=False)` accepts `refresh_covers` (bridge positional arg, sent by `checkAllUpdates(ids, { refreshCovers: true })`). A refresh pass only queues games with an F95 URL (catalog-only Ryuu games are skipped) and, for each game, reads the thread once through the shared scraper throttle (reusing a read the version check already made). `_refresh_cover_for_url` then overwrites `cover_image_path` only when the thread cover is non-empty and different, and wakes `cover_cache`; the automatic backfill still only fills empty covers. `get_update_status` reports `refresh_covers`, and each result entry of a refresh pass adds `cover_changed` and `previous_cover` so the UI can offer one Undo. A failed thread read keeps the existing cover.
- **Desktop notifications**: `maybe_auto_check` calls `check_all_updates(automatic=True)`; the keyword is internal to Python. An uncancelled run sends at most one Linux notification for changed latest versions that now have updates, plus newly completed wishlist games. Automatic runs qualify; manual runs qualify when the window is unfocused at completion. `set_window_focused(focused)` defaults to focused, and App reports focus, blur, and visibility changes. Updates > Automatic Checking has the default-on Desktop notifications toggle, saved as general setting `update_notifications` (`on`/`off`). The sender uses a daemon thread, `notify-send` then `gdbus`, five-second subprocess timeouts, and swallowed/logged failures. Flatpak permits the notification service. No new Python dependencies or Windows notifications.
- **Running Registry**: `launch(..., game_id=...)` registers process handles under a lock and removes them on exit by process identity before the final playtime tick. Linux launches use a new session; Stop sends SIGTERM to the process group and SIGKILL after five seconds if the group remains. Wine/Proton also uses the matching `wineserver -k` with the resolved prefix when available (Proton `files/bin` or `dist/bin`, Wine sibling or system server). This ends all processes in that Wine prefix, so it is skipped while another tracked game uses the same prefix (that game keeps running; only the stopped game's process group is signalled). Windows starts each unsandboxed game suspended inside a Job Object (no kill-on-close, so games outlive wLib), so a launcher that starts the real game and exits stays tracked while game processes remain in the job, and Stop ends those processes. Shared programs a game starts (a browser for a link, the Steam client, `conhost.exe`, `WerFault.exe`) neither keep the game running nor get closed by Stop. Elevated games (UAC) and a refused job fall back to `taskkill /T /F /PID`; failure advises closing the game itself. 7-Zip on Windows runs with `-sccUTF-8` (dropped for builds that reject it) so non-ASCII names list correctly. Helper tools (7-Zip, SbieIni, taskkill, powershell, winget) run with `CREATE_NO_WINDOW` because the windowed Windows build would otherwise open a terminal for each.

### `core/scraper.py` (HTTP Scraping)

- **Batch Versions**: `check_versions(urls, callback)` requests `https://f95zone.to/sam/checker.php?threads=<ids>` with at most 100 unique thread IDs per request. Versions use the title parser shared with the extension before comparison.
- **Page Fallback**: Missing, empty, and non-actionable versions, or endpoint failures other than challenges and HTTP 429 rate limits, fall back to guest thread pages. Regex extraction reads the title and starter post.
- **Guest-hidden attachments and links**: When a plain-HTTP page's starter post hides links, or hides attachments and the catalog has no cover, `_get_page()` re-fetches that thread once through a connected extension to get the signed-in HTML, because guest requests see a login prompt instead of screenshots posted as attachments or store links. The guest page is kept when no extension is available, the extension response is not HTTP 200, or that response is challenged or login-gated. This applies only to single-thread page reads; batch endpoint reads do not load the thread page.
- **Metadata**: Add-game fetches and single-game checks read engine, tags, cover, screenshots, store links, the thread's Completed flag, and starter-post edit timestamps. Thread page reads refresh screenshots alongside thread edit metadata: `_update_thread_edit_metadata_for_url` replaces the stored list when the page carries a non-empty screenshot list and keeps the stored list on an empty result. Bulk checks from the batch endpoint do not read the thread page and leave screenshots unchanged; bulk wishlist/fallback paths that read the thread page store them. A screenshots-only refresh does not bump `sync_local_modified_at`. Bulk checks preserve stored edit metadata.
- **Extension fallback order**: `_fetch_with_fallback(url)` is used by `_get_page` and `_check_chunk`. It tries plain HTTP first, then on `blocked` or `login_required` retries the same URL once through the connected extension (`core/extension_bridge.py`). The extension body is re-classified with the same rules; error results carry `browser_fallback: "unavailable"` (no eligible extension), `"logged_out"` (the extension returned F95's login page, so the browser sent no F95 login with its request), or `"failed"` (extension also challenged or its fetch failed). Each extension fetch reads XenForo's `data-logged-in` flag and passes it to `extension_bridge.record_fetch_outcome`; `extension_bridge.status_snapshot()` (connection state, disconnect count, last fetch) appears under the extension in Diagnostics, and bridge connects, disconnects and login-state changes go to the app log. No fallback runs for `rate_limited`, `not_found`, timeouts, or network errors. A challenged batch retries the same batch URL once through the extension, never as 100 thread pages.
- **Extension-first window**: a plain-HTTP `blocked` result sets `_prefer_extension_until = now + 15 minutes` (in memory only, never persisted). While the window is active and `extension_bridge.is_available()` is true, requests go straight to `_f95_get_via_extension` without a plain-HTTP attempt; its classified result is final. With no eligible extension connected, the window is ignored and plain HTTP is used. `login_required` never opens the window.
- **Pacing and TLS**: Every request passes through `_run_paced_f95_request`, which holds the global `_F95_REQUEST_LOCK` through the request and waits after the previous request finishes, for both `_f95_get` (urllib) and `_f95_get_via_extension` (bridge). `WLIB_UPDATE_CHECK_DELAY_SECONDS` defaults to 2 seconds and is bounded to 1–30. urllib uses the default SSL context and the configured CA certificates. Extension fetches count as F95 requests for serialization and pacing: no plain HTTP request runs while an extension request is pending, and consecutive requests stay spaced by at least the minimum interval.
- **Bounds and guarantees**: wLib waits at most 30 seconds for an extension response and never waits for an extension to connect. The fallback never opens a browser window or tab and never starts a browser; it works only when the user's browser is already running with the extension.
- **Failures**: Deleted threads report `not_found`, challenges report `blocked`, and login walls report `login_required`. Network timeouts report `navigation_timeout`; other network or parse failures report `scraper_error`.
- **Callbacks**: Results are delivered in input order, shared thread IDs reuse results, returning `False` stops processing, and callback exceptions propagate to the update worker.

### Supporting modules

- `core/game_scan.py`: `inspect_game_path` and `scan_games_folder` guess titles, versions, engines, and launch files from install folders for Add Game and Import Folder.
- `core/store_links.py`: normalizes store URLs, validates source/ID pairs, and supplies the exact store identities used by link storage, extension checks, and backup matching.
- `core/game_lookup.py`: resolves a page's supplied IDs through the read-only catalog, expands one hop of catalog links, and returns matching local game IDs.
- `core/game_covers.py`: recognizes JPEG, PNG, WebP, GIF and AVIF content, enforces the 20 MiB limit, and atomically stores device-local cover files.
- `core/cover_cards.py` and `core/webp_anim.py`: `CoverCardService` makes a still copy and a card-size animated copy (at most 640 px wide, never upscaled, at most 300 frames and 16 MiB) of each animated GIF or WebP local or cached cover, using Qt's image reader and WebP writer plus a small pure-Python animated-WebP muxer. Copies live in the device-local `covers/cards/` folder as `<source-stem>.still.webp` and `<source-stem>.anim.webp`, are served by the `/covers/cards/` route (strict name pattern, no listing), and are never exported, synced or counted as library changes. The still copy is written last. Orphans are swept at startup and after a cover is replaced, cleared, deleted or purged. `get_games` adds `cover_card_still_url` and `cover_card_anim_url` for the shown source only. If Qt has no WebP writer the service disables itself and covers behave as before.
- `core/litedb.py`: dependency-free LiteDB 4 reader used by Playnite import.
- `core/catalog_format.py`: shared catalog schema version, manifest field names, and `meta` keys (see [Game Catalog](catalog.md)).
- `core/f95zone.py`: host-limited thread-ID extraction, thread URL normalization, and equivalent-thread matching used by the database, scraper, and backup import.
- `core/versions.py`: normalized version comparison mirrored by `normalizeVersion()` in `ui/src/utils/libraryGames.ts`.
- `core/backup_format.py`: leaf module with shared backup format identifiers, the SQLite integer bound, and `BackupValidationError`; `core.library_backup` re-exports these names.
- `core/extension_ws.py`: minimal RFC 6455 handshake, frame encoding, strict masked-frame reading, and the 8 MiB message limit for `GET /api/bridge`.
- `core/umu.py`: discovery, release downloading, runtime preparation, and invocation construction for Proton launches via `umu-launcher`.
- `core/umu_launch_wrapper.py`: host-Python wrapper skipping umu's runtime update and install-marker rewrite when the Steam Linux Runtime is already prepared.

## Backend Verification Workflow

- **Type checking**: `pyrightconfig.json` is configured for `basedpyright` and currently checks `main.py`, modules under `core/`, and `catalog_server/`.
- **Strict rollout**: The backend entrypoints and core runtime modules (`main.py`, `core/api.py`, `core/api_mixins/`, `core/app_version.py`, `core/bridge_exposure.py`, `core/database.py`, `core/extension_server.py`, `core/launcher.py`, and `core/scraper.py`) are opted into `strict` checking while the rest of the project remains at `recommended`.
- **Initial rollout scope**: `tests/` are intentionally excluded from type checking for now so backend diagnostics stay focused on application code.
- **Linting**: `ruff` is configured repo-wide for Python files so tests, utility scripts, and backend modules share the same lint rules. `pyproject.toml` selects the whole `E4`, `E7`, `E9`, `F`, `B` (bugbear) and `UP` (pyupgrade) groups. Ruff is pinned in `requirements-dev.lock`; a Ruff upgrade that adds rules to these groups must fix the new findings in the same change. `B009` and `B010` are ignored repo-wide because their fix (plain attribute access instead of `getattr`/`setattr` with a constant name) breaks `basedpyright`. A repo-wide ignore needs a comment with its reason. To suppress one line, name the rule and give a short reason on that line, for example `# noqa: B023 - runs in this iteration`; never use a bare `# noqa`. Type aliases use the Python 3.12 `type X = ...` statement and are not used as runtime objects.
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
configuration, process-list parsing and tool installation. Linux detection runs
host `bwrap --version`, then a namespace probe with the launch options; Flatpak
runs both through `host_command`. `ready`, `missing` and `unusable` include a
path, version and reason. Only successful probes are cached, so a missing or
unusable tool is checked again next time; Recheck also clears the cache. Platform
capabilities expose `sandbox` (`bwrap`, `sandboxie`, or null).

The launcher wraps the final mode-specific argv before `host_command`. bwrap
uses private user/PID/IPC/UTS namespaces, a private `/tmp` and `/run`, read-only
system and runtime mounts, selected display/audio sockets, a writable game
folder and Wine prefix, and `<data dir>/sandbox/<id>/home` at the real HOME
path. A sandboxed Wine/Proton game without a custom prefix (or a `WINEPREFIX=`
launch argument) runs in its own prefix, `<data dir>/sandbox/<id>/prefix`
(`game_sandbox.sandbox_wine_prefix`), never the global one: the prefix is
writable inside the sandbox, so sharing it would let the game plant DLLs or
registry entries that later run unsandboxed in other games. Save detection and
the game's Winecfg, Winetricks and RTP actions resolve the same prefix. Removing a game moves its `sandbox/<id>/` folder to `sandbox/removed/<id>_<timestamp>/`, so a later game with the same ID starts clean; startup deletes removed sandboxes older than 30 days. On non-NixOS hosts, `/bin`, `/sbin` and `/lib*` entries that are symlinks
on the host are recreated as symlinks, so 32-bit loaders resolve, and
`/dev/ntsync` is passed through when present. It does not expose the session
D-Bus. The game gets a filtered environment (`game_sandbox.sandbox_environment`):
display, audio, locale, graphics and Wine/Proton variables from the host plus
everything wLib or the launch options set, but none of wLib's other variables
(tokens, SSH/GPG agents, credentials). bwrap runs without `--die-with-parent`,
so a sandboxed game keeps running when wLib quits, like other games. An in-namespace shell supervises launcher stubs until their detached
descendants exit. Stop terminates the outer bwrap process group and skips the
host wineserver. Automatic Cheat Engine injection, when enabled, runs inside
that same sandbox after its usual delay.

No internet adds `--unshare-net` to the sandbox. With sandboxing off it uses a
thin `bwrap --unshare-user --unshare-net --dev-bind / /` wrapper with the
original working directory, so files, Stop, playtime and the host wineserver
behave as unsandboxed; only a private loopback remains. A missing or unusable
bubblewrap refuses the launch with `network_block_unavailable`; Windows requires
the sandbox (`network_block_needs_sandbox`). A network-blocked game cannot share a Wine prefix with a running game that has
network (or the other way round): processes on a shared wineserver outlive one
game's block. Proton codenames resolve to
installed builds before offline launches, otherwise `proton_missing_offline`
refuses the launch. Offline, umu rewrites its runtime install marker; the launch
wrapper skips that when the marker already exists, because the sandbox mounts
the shared runtime read-only.

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


## Proton and umu-launcher

Upstream Proton builds require the Steam Linux Runtime container (`sniper` or
`steamrt4`). `core/umu.py` manages `umu-launcher` (`umu-run`) on Linux so Proton
games, Wine companion tools, Cheat Engine, and prefix/RTP/Winetricks installers
execute inside the proper container environment.

- **Discovery**: `locate_umu()` checks for system `umu-run` on `PATH` via
  `host_which("umu-run")`, falling back to wLib's downloaded copy under
  `<data dir>/umu/umu-run`. `get_umu_status()` reports path, source (`system` or
  `wlib`), version string, and whether `python3` is available on the host.
- **Zipapp download**: `download_release()` and the `UmuDownload` worker fetch
  the latest verified zipapp from the GitHub release
  (`Open-Wine-Components/umu-launcher`). The download verifies SHA-256 and SHA-512
  checksums, ensures member paths stay safe, and extracts only the single
  executable member `umu/umu-run` into `<data dir>/umu/umu-run` (capped at 8 MiB)
  with mode 0755.
- **Steam Linux Runtime**: Runtimes reside in `~/.local/share/umu/<variant>`
  (`XDG_DATA_HOME/umu`), where the variant comes from the build's
  `toolmanifest.vdf` `require_tool_appid` (`steamrt3` for sniper, `steamrt4`).
  Like umu 1.4.1+, `GE-Proton`/`UMU-Proton` codenames (and an empty runner, which
  umu treats as UMU-Proton) resolve to the newest installed build before reading
  its manifest. `runtime_status(runner)` checks for `.installed.ok` or
  `_v2-entry-point` plus platform directories. `UmuRuntimePrepare` runs
  `umu-run createprefix` in the background with `GAMEID=umu-default` and the
  selected runner's `PROTONPATH`. The UI polls status and progress (installed folder
  size in MiB), and supports cancellation via SIGTERM/SIGKILL to the process group.
  Failures classify into `disk`, `network`, `python`, or `other`, alongside raw
  stderr log tails.
- **Invocation construction**: `build_umu_invocation()` produces
  `["python3", "<data dir>/umu/umu-run-wlib", executable]` with environment:
  `WINEPREFIX` pointing to `proton_compat`, `GAMEID` set to `umu-wlib<id>` for
  tracked games or `umu-default` for installers, `PROTONPATH` set for custom
  runners and omitted for UMU-Proton, `STEAM_COMPAT_INSTALL_PATH` set to the game
  directory, and `UMU_RUNTIME_UPDATE="0"`. When not sandboxed,
  `UMU_CONTAINER_NSENTER="1"` allows companion tools and Cheat Engine to join the
  running game's container. Launches are refused if the `pfx` symlink inside
  `proton_compat` points elsewhere and would be replaced by umu.
- **Launch wrapper (`core/umu_launch_wrapper.py`)**: A small script deployed to
  `<data dir>/umu/umu-run-wlib`. When `UMU_RUNTIME_UPDATE=0`, it patches umu's
  `setup_umu` and `write_install_marker` at runtime. Skipping the update request
  and preventing rewrite of an existing install marker allows umu to launch
  reliably when the runtime is mounted read-only (in bubblewrap sandboxes or
  offline launches).
- **Offline codename resolution**: Offline Proton launches resolve runner codenames
  (`GE-Proton`, `UMU-Proton`) to an installed build before launching; if no
  matching build is installed, launch is refused with `proton_missing_offline`.
- **Sandbox integration**: Sandboxed Proton launches mount the shared Steam Linux
  Runtime read-only, with a per-game writable working directory
  (`<data dir>/sandbox/<game id>/umu-var`) for umu's state. Cheat Engine injection
  is disabled for sandboxed Proton launches. Proton games sharing a prefix cannot
  run concurrently.


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

Backup inspection summarizes tool definitions and warns about malformed entries or foreign-host paths. On Linux, import skips definitions whose executable is not on this host; Windows keeps imported Linux definitions unchanged because tools only run on Linux. Import counts `companion_tools_updated` and `companion_tools_skipped`, imports definitions before games in the same transaction, and merges by case-insensitive name. Overwrite preserves local IDs and replaces game overrides only when their field is present. Fill-missing retains matching definitions and existing overrides. Missing files generate migration warnings; definitions with host-compatible paths can still be imported and edited on the destination.

## Playnite imports and local covers

`core/external_import.py` accepts `playnite`, using the dependency-free LiteDB 4
reader in `core/litedb.py`. Pick `games.db` with its sibling database files,
`database.json` and `files/` directory. On Windows, detection reads Playnite's
`config.json` DatabasePath, then the default AppData library. Linux requires a
picked or copied library. Database format 4 is supported. Close Playnite first:
journal files and sharing failures return `source_locked`. Only the seven
library collections are snapshotted, and add-on settings are never read.
Missing optional database files or expected collections in valid metadata
databases are empty lookups (statuses, companies, categories, tags, features,
genres). Unresolved references omit those fields and preserve local values in
both merge modes. The Game collection remains required, even if empty;
corrupt, encrypted, locked, and unsupported databases still fail validation.

Playnite links, F95 plugin versions and prefixes, completion status, notes,
collections, seconds of playtime, dates and user scores are translated into the
existing import pipeline. File play actions support install variables and extra
launch targets; foreign-OS paths are dropped. An F95 thread, adult store/catalog
link (DLsite, Fanza, VNDB, Ryuugames), or host executable qualifies a game for
import. Steam or itch.io alone does not.

`core/game_covers.py` recognizes JPEG, PNG, WebP, GIF and AVIF by content and
enforces 20 MiB before and during copying. AVIF detection inspects the complete
initial `ftyp` box (up to 64 KiB), including normal/extended lengths and `avif`
or `avis` major/compatible brands. Filename extensions are ignored, so AVIF
named `.png` is stored as `.avif`. This is type recognition rather than full
image decoding. Atomic, byte-preserving copies use UUID filenames under
`<data dir>/covers/`. `choose_game_cover(game_id)` opens the image picker;
`set_game_cover_file(game_id, path)` copies a chosen image, and
`clear_game_cover(game_id)` removes it. Successful set/clear responses include
`game`; cancelling the picker returns `cancelled: true`. Game payloads include
`cover_local_url` only when the file exists and the extension server is bound.
Replacement, removal and game deletion clean up the old file.
The read-only cover route serves AVIF as `image/avif` with `nosniff` and the
existing private immutable caching policy. The embedded browser decodes it
directly; no conversion dependency is needed. Undecodable images use the
existing remote-cover fallback.

External previews expose `covers` only when source games carry `cover_file`,
and report `covers_to_copy`. Invalid images become per-game warnings. Covers
copy inside the database import transaction, roll back on failure and remove
replaced files only after commit. Fill-missing keeps existing local covers.
Remote cover URLs remain metadata; local covers and their references never
export or sync, and F95 metadata refreshes leave them alone.


### Remote cover cache

`Api` owns `CoverCacheService` in `core/game_covers.py`. After the window's loaded event it sweeps orphan files and fills `<data dir>/covers/cache/` in one daemon thread. Remote images share local-cover content validation (JPEG, PNG, WebP, GIF, AVIF; 20 MiB maximum) and atomic random-name storage. `f95_get_bytes` uses bounded plain HTTP reads without extension fallback, sharing the global F95 request lock and interval with page requests. Foreground waiters take priority over further background acquisitions, and a background download starts only after one interval with no foreground request, so bulk checks and imports keep their turn. Failed URLs are skipped for the rest of the session.

The device-only `games.cover_cache` filename and `games.cover_cache_url` source URL are written only through dedicated database helpers. Guarded writes discard downloads if the source URL changed or the game was removed. Adds, edits, metadata checks, backup imports and sync pulls wake the service. API reads expose a localhost `cover_cache_url` only for a current source URL and an existing file while port 8183 is available. `/covers/cache/<random name>` serves immutable images without directory listing.

`get_cover_cache_status()` returns `{success, count, bytes, running}`. `purge_cover_cache()` disables downloads for this session, clears the columns and deletes cache files under the same lock used to store them, then dispatches `wlib-refresh-library`. In-flight reads are discarded. Local user covers survive. Shutdown stops the service. Cached files and references are excluded from exports, backups and sync and never modify the sync timestamp.

### Performance and prefix tools

On Linux, launch options resolve GameMode and MangoHud per game against their global defaults. Enabled, available wrappers surround the complete game command before Cheat Engine, sandbox, network isolation, and Flatpak host wrapping. GameMode is skipped in the sandbox; MangoHud remains available. Browser and Windows launches skip both. Skip reasons are printed and written to the per-game debug log when enabled. Companion tools and Wine cleanup commands are not wrapped.

Winecfg and Winetricks use the same prefix and runner resolution as the runtime installers. Proton tools run through umu; Wine tools use host binaries. Winetricks requires zenity or kdialog. Only one prefix tool can run at a time; it cannot run alongside an installer or a game using the same prefix, and installers and games on that prefix refuse while it remains open.



### Update downloads

`core/download_links.py` parses fresh first-post mirrors fetched through the signed-in extension session. `core/update_downloads.py` holds link addresses only in memory, behind opaque IDs, for at most five minutes or until the picker closes; they are never written to the database, exports, sync, or logs. `core/downloads/` contains the job manager, transfer loop, and Pixeldrain, MEGA and Buzzheavier adapters. Transfers run in the background into `<data dir>/downloads/`, use temporary `.part` files and verify archive signatures before publishing the result. Pixeldrain and Buzzheavier transfers can resume: their partial file is named after the host's file ID and size (`resume-<hash>.part`), a dropped connection reconnects with an HTTP `Range` request up to three times, and a failed download keeps its partial file so the next download of the same file continues it (a host that answers without `206` starts over). Cancelling, an invalid file or a full disk removes it. MEGA decrypts while downloading and always starts over. Staging is device-local and excluded from backups and sync; startup removes partial files that cannot resume, resumable ones older than seven days, and old unused archives. Optional file-host accounts (`core/downloads/accounts.py`) make downloads use the user's quota: a Pixeldrain API key is sent as HTTP Basic auth, and MEGA sign-in stores only the session id (`sid`), never the password. The `pixeldrain_api_key`, `pixeldrain_username`, `mega_session` and `mega_email` settings are device-local and absent from the backup/sync allowlists.

`get_game_screenshots(game_id)` returns `success`, `screenshots` as `{thumb, full}` URL pairs, `source` (`catalog` or `library`), and `in_catalog`. An unknown game returns `success: false` and `error`. The catalog is read without requesting the thread or changing the library.

### Steam shortcuts and headless launches

On Linux, `core/steam_env.py` discovers native Steam, local accounts, Steam Deck and Gaming Mode. `core/steam_shortcuts.py` reads the binary shortcut file while preserving other entries. It writes through a temporary file and keeps a `.wlib-bak` copy; additions, updates and removals wait while Steam is running and until the file has been stable for five seconds after exit. A serialized GUI worker debounces library changes and polls pending work every 30 seconds. The mode, account, game marks and shortcut links are device-local and are excluded from library exports and sync.

At GUI startup `core/steam_script.py` refreshes `<data>/steam/wlib-steam` for the current AppImage, native launcher, Flatpak or checkout. A game shortcut passes its game ID to `--launch`, which starts the shared tracked launcher without a window or extension server. The runner remains alive through the final playtime tick and forwards stop signals. Per-game run records in `<data>/runs/` let an open window show and stop a runner; process locks prevent concurrent launches. Stale records are discarded. Failures are shown in the open window, or a GUI child opens to show them. Before it starts the game the runner asks an open window (`POST /api/launch-check`) whether the game is busy (install, restore, mod tool), whether a prefix tool or runtime install holds its prefix, and whether a running game blocks the prefix; the window holds that prefix until the runner reports `ended` or `error`. The runner endpoints (`/api/running`, `/api/launch-check`, `/api/runner-event`) require the `X-wLib-Runner` token from `<data>/runner-token` (mode 0600), which a sandboxed game cannot read.

Shortcut link states include `linked`, `pending_add`, `pending_update`, `pending_removal` and `user_removed`. A shortcut removed in Steam stays removed until the mark is toggled or the user selects Sync now. Artwork is generated from the local or cached cover without network access: a 600×900 portrait, 920×430 capsule and 1920×620 hero. The source filename and mtime avoid unnecessary rewrites. Covers Qt cannot decode (AVIF) are queued; the window converts them to PNG (`wlib-steam-covers` event, `get_steam_cover_conversions`, `save_steam_cover_png`) into the device-local `covers/cache/steam-png/`, and the next sync uses that copy. A game waiting for conversion gets its shortcut without artwork until then. Flatpak host launches forward Steam's overlay and app ID environment; overlay and focus under Flatpak remain experimental.
