# Backend Systems (Python)

The core strength of wLib relies on its native Python backend, divided primarily into `main.py` and modularized controllers in the `core/` package. The supported development and CI target for backend work is Python 3.12.

## Bootstrapping in `main.py`
`main.py` acts as the primary orchestrator. Upon launch, it executes these sequential steps:

1. **Environment Setup**: Parses command-line arguments and resolves platform paths (`~/.local/share/wLib` on Linux, `%LOCALAPPDATA%\wLib` on Windows).
2. **SSL Certificate Configuration**: Configures SSL certificates from bundled certifi or system paths for secure scraping (`configure_ssl_certificates()`)
3. **Qt Runtime Configuration**: Detects Linux session type (X11/Wayland) and configures Qt platform plugins; on Windows, leaves native platform selection to Qt unless explicitly overridden (`configure_qt_runtime_environment()`). Both platforms start PyWebView with `gui="qt"`.
4. **Database Initialization**: Calls `core.database.init_db()` to create/migrate the SQLite schema.
5. **Extension Sync**: Copies bundled extension assets into the platform user-data directory when files are missing or the bundled manifest version changed.
6. **Playwright Preflight**: Runs the packaged Playwright driver in a background thread when Chromium is absent (`~/.cache/ms-playwright` on Linux, `%LOCALAPPDATA%\wLib\playwright` on Windows), keeping UI startup responsive.
7. **Daemon Threads**: Starts the HTTP extension proxy server (`start_extension_server()`) in a daemonized background thread to prevent blocking the main GUI loop.
8. **WebView Launch**: Binds the `core.api.Api` instance to `pywebview` and enters the blocking UI loop.

`main.py` also exposes maintenance CLI paths: `--install-playwright-if-needed` runs browser preflight, while `--smoke-test` validates imports, database initialization, bundled assets, and driver resolution without opening the UI or downloading a browser.

### Renderer Diagnostics System

`main.py` logs Qt and WebGL renderer details under the resolved data directory on both platforms: `~/.local/share/wLib/renderer-diagnostics.log` on Linux or `%LOCALAPPDATA%\wLib\renderer-diagnostics.log` on Windows. The Linux release launcher supplies the GPU probe and crash-guard context described below; Windows uses Qt's native selection.

- **GPU Detection**: Probes GPU capabilities using `glxinfo`, `/sys/class/drm/`, and environment variables
- **Crash Guard**: Implements `~/.local/share/wLib/.gpu_crash_guard` to auto-fallback to software rendering after GPU crashes
- **Diagnostic Logging**: Logs Qt backend selection, GPU detection reason, renderer details, and WebGL probe results to `~/.local/share/wLib/renderer-diagnostics.log`
- **Browser Renderer Probe**: Executes WebGL detector scripts in the PyWebView to capture the browser's GPU renderer information
- **AppImage Mirroring**: AppImage launches mirror diagnostic context to `~/.local/share/wLib/appimage-launch.log`

## Core Modules

### `core/host_platform.py` (Host Paths and Capabilities)

Centralizes Linux/Windows detection, data/cache/browser/extension paths, and platform capabilities. `Api.get_platform_capabilities()` exposes these through `getPlatformCapabilities()` in the frontend bridge. Linux-only runtime installers return `success: false` with `code: "unsupported_platform"` before starting downloads or subprocesses on Windows. Windows file/folder and HTML opening uses system associations. See [Platform Paths](architecture.md#platform-paths) for defaults and environment overrides.

### `core/api.py` (The API Bridge)
The `Api` class acts as the single point of entry for the Vue frontend. All methods defined without a leading underscore (e.g. `get_games`, `launch_game`) are automatically serialized into Promises on the `window.pywebview.api` object.
- **Concurrency**: UI calls are technically asynchronous on the JS side but block the pywebview worker pool on the Python side. The `Api` class heavily uses background thread spawning (`threading.Thread`) for long tasks (like downloading updates or mass-scraping metadata) so the UI doesn't freeze.
- **Event Emitter**: Contains wrapper helpers to dispatch Global UI Events back to Vue using `webview.evaluate_js()`.
- **Extension Sync Metadata**: Tracks whether startup extension synchronization actually updated the installed browser files so the frontend can show a toast prompting the user to reload the addon.
- **Launch Mode Contract**: Carries each game's `launch_mode` through add, update, list, and launch calls. Missing or invalid values normalize to `auto`; supported values are `auto`, `native`, `wine_proton`, `rpgmaker_linux`, and Linux-only `custom`. Add calls also accept `command_line_args`.
- **Launch Target Contract**: Exposes CRUD and reordering methods for additional game launch targets. `get_games()` returns each game's extra `launch_targets`, while the canonical default executable remains `games.exe_path`.
- **Library Migration Contract**: Exposes `export_library_backup`, `inspect_library_backup`, and `import_library_backup` for one-file JSON migration. Import uses an inspect-before-write flow and backup-wins field merges for selected sections.
- **Process Control**: `get_running_games()` returns a list of game IDs; `stop_game(game_id)` requests termination and returns a success/error payload. Duplicate tracked launches return `already_running`; stopping an untracked game returns `not_running`, and stopping one whose process is still starting (e.g. behind a UAC prompt) returns `starting`.
- **Custom Statuses**: `get_settings()` returns `custom_play_statuses` as a list. `save_settings()` validates trimmed names (1–40 characters, no case-insensitive duplicates, canonical clashes, or legacy aliases such as `Replaying`); removing a name resets games using it to `Not Started` in the same transaction as the list update.
- **URM**: `urm_rpa_path` stores a user-supplied archive path. `browse_urm_file()` selects `.rpa` files. `get_urm_status(game_id)` returns `success`, `renpy`, `installed`, and `source_configured`. `set_urm_installed(game_id, installed)` detects `renpy/` plus `game/` beside the stored executable and installs/removes `game/0x52_URM.rpa`. Installs replace the destination only after a successful copy; filesystem errors return `success: false`. No download is performed.
- **Runner Discovery**: `get_available_runners()` lists system Wine and wLib Proton first, then Steam and Lutris, sorted within each source and deduplicated by real path. Steam roots: `~/.steam/root`, `~/.steam/steam`, `~/.local/share/Steam`, `~/.var/app/com.valvesoftware.Steam/data/Steam`, and `~/.var/app/com.valvesoftware.Steam/.local/share/Steam`; scans `compatibilitytools.d/*/proton` and `steamapps/common/Proton*/proton`. Lutris scans `*/bin/wine` under `~/.local/share/lutris/runners/wine` and `~/.var/app/net.lutris.Lutris/data/lutris/runners/wine`. Windows returns an empty list.

### `core/library_backup.py` (JSON Migration)
This module serializes and imports semantic library backups without copying the raw SQLite database.
- **Format**: Writes one JSON document with `format`, `format_version`, export timestamp, selected sections, game metadata, optional game sections, and selected settings groups.
- **Always-included metadata**: Every exported game includes title, developer, engine, tags, F95 URL, version fields, and cover reference so records remain identifiable after migration.
- **Import matching**: Matches games by normalized F95 thread identity first, then by normalized title/developer only when there is exactly one local match. Ambiguous fallback matches are skipped and reported.
- **Merge behavior**: Matched games receive backup values for always-included metadata and selected optional sections. Unselected sections preserve local values. Playtime is overwritten from the backup when user state is imported, never summed.
- **Safety boundaries**: The JSON export excludes scraper browser sessions, cookies, webview storage, downloaded runtimes, Playwright browser binaries, extension copies, caches, and diagnostics.
- **Cross-platform paths**: Inspection reports missing executable paths and foreign global path settings. Import skips global settings whose path syntax belongs to the other OS; per-game paths and launch options remain available to edit after migration.

### `core/launcher.py` (Process Management)
This module handles native host launching and Linux compatibility runtimes.
- **Environment Overrides (Linux)**: Per-game Japanese locale and Wayland settings modify the `env` dictionary passed to `subprocess`. Windows ignores these flags and removes Wine/Proton environment variables from native launch environments.
- **Launch Modes**: Windows exposes `auto`: `.exe` runs directly, `.bat`/`.cmd` uses `cmd.exe`, `.jar` uses `java -jar` with Java on `PATH`, and `.html`/`.htm` opens in the default browser. Linux additionally supports `native`, `wine_proton`, `rpgmaker_linux`, and `custom`. Custom Command uses `command_line_args` as the full command, substitutes `%command%` tokens with the absolute target, and runs in the game folder with a clean host environment. Leading `NAME=value` assignments are applied without a shell. Empty commands are rejected. Windows command-line arguments use Windows quoting rules.
- **Launch Targets**: Alternate targets reuse the same launcher entrypoint as the default executable; only the selected executable path changes. Playtime remains keyed to the parent `game_id`.
- **Wine & Proton**: Prepends the configured `proton_path` or `wine` binary when compatibility mode is selected or auto-detection falls through to a Windows-style target, ensuring the proper `WINEPREFIX` or Proton compatibility path is enforced.
- **Capability Boundary**: Windows rejects Linux-only launch modes and runtime installation/injection APIs before runtime actions. Imported per-game options remain stored; Windows launches ignore Linux-only flags, prefixes, and Proton paths. An imported Linux launch mode must be changed to `auto` before launching.
- **Cheat Engine Integration (Linux)**: Starts `lunarengine-x86_64.exe` through Wine/Proton, passing a Lua injection script to map directly to the game's PID.
- **RPGMaker Tooling**: Implements enhanced Wine/NW.js fixes for RPGMaker MV/MZ and can optionally launch through the external `rpgmakermlinux-cicpoffs` runner when users install/configure it themselves. wLib links to the upstream project but does not bundle, install, update, export, bug-report, or run mutation-oriented upstream commands automatically.
- **Playtime Tracking**: Uses `Popen.wait()` in a dedicated watcher thread, capturing timestamps on start and exit, then executing a database UPDATE callback to record total seconds played. HTML/browser targets do not support process-based playtime tracking on either platform.
- **Running Registry**: `launch(..., game_id=...)` registers process handles under a lock and removes them on exit by process identity before the final playtime tick. Linux launches use a new session; Stop sends SIGTERM to the process group and SIGKILL after five seconds if the group remains. Wine/Proton also uses the matching `wineserver -k` with the resolved prefix when available (Proton `files/bin` or `dist/bin`, Wine sibling or system server). This ends all processes in that Wine prefix. Windows uses `taskkill /T /F /PID`, including the PID of elevated handles; failure advises closing the game itself. Launcher processes that exit before their detached games are not tracked further.

### `core/scraper.py` (Playwright Engine)
Responsibile for fetching and parsing data from F95Zone.
- **Headless Operations**: Uses `playwright.sync_api` to spin up headless Chromium instances.
- **Persistent Browser Sessions**: Maintains a persistent browser profile below the resolved platform data directory (`browser_session/`) so login cookies, localStorage, and session state survive restarts.
- **Cloudflare Bypass**: Implements resilient `page.wait_for_selector()` heuristics to intelligently wait out or detect Cloudflare turnstiles, and identifies login-wall blocks to bubble up authentication errors to the UI.
- **DOM Parsing**: Compiles metadata (title, version, image URLs, developer) by executing query selectors on the rendered HTML structure.
- **Version Fallback**: Numeric, chapter/episode, and bare-v patterns retain precedence. With two or more brackets, an otherwise unmatched title uses the trimmed first bracket (`Final`, `b12`); single developer brackets still allow the first-post fallback. Update checks compare actionable versions as strings.
- **Environment Cleanup**: Strips AppImage-specific environment variables (`APPIMAGE`, `APPDIR`, `LD_LIBRARY_PATH`) before launching Playwright to prevent library conflicts with the bundled Chromium binaries.

## Backend Verification Workflow

- **Type checking**: `pyrightconfig.json` is configured for `basedpyright` and currently checks `main.py` plus modules under `core/`.
- **Strict rollout**: The backend entrypoints and core runtime modules (`main.py`, `core/api.py`, `core/database.py`, `core/launcher.py`, and `core/scraper.py`) are opted into `strict` checking while the rest of the project remains at `recommended`.
- **Initial rollout scope**: `tests/` are intentionally excluded from type checking for now so backend diagnostics stay focused on application code.
- **Baseline tracking**: `.basedpyright/baseline.json` is currently empty. It remains in the repo so future typing rollouts can use the same incremental workflow without changing tool paths.
- **Linting**: `ruff` is configured repo-wide for Python files so tests, utility scripts, and backend modules share the same baseline lint rules.
- **Runtime smoke check**: `scripts/smoke_backend.py` imports the backend, configures runtime helpers, imports `pywebview`, and exercises extension sync with a temporary HOME/browser path. Windows also needs an explicit temporary `WLIB_DATA_DIR` because `HOME` does not override `%LOCALAPPDATA%`; see the [PowerShell smoke example](../CONTRIBUTING.md#smoke-backend-test).
- **Local checks**: Linux wrappers `bash scripts/check-python.sh` and `bash scripts/check-python-clean.sh` run `ruff`, `basedpyright`, the smoke check, and `pytest` in an active or fresh Python 3.12 environment. On Windows, install `requirements-dev.txt` plus `requirements-windows.txt` and run the same tools directly in PowerShell.

## SSL Certificate Configuration

`main.py` configures SSL certificates on startup to ensure reliable HTTPS connections for scraping and API requests:

1. **Bundled Certifi**: Includes `certifi` CA certificates within the PyInstaller bundle at `_internal/certifi/cacert.pem`
2. **System Certificate Fallback**: Checks standard system paths (`/etc/ssl/certs/ca-certificates.crt`, `/etc/ssl/cert.pem`) if certifi is unavailable
3. **Environment Variable Setup**: Sets `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE`, and `CURL_CA_BUNDLE` to the selected certificate bundle
4. **AppImage Configuration**: The AppRun script performs parallel certificate setup for the AppImage runtime context

Windows releases bundle certifi and use the same certificate-selection flow. The `/etc/ssl/...` fallbacks and AppImage setup apply to Linux; HTTPS verification remains enabled on both platforms.
