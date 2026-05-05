# Backend Systems (Python)

The core strength of wLib relies on its native Python backend, divided primarily into `main.py` and modularized controllers in the `core/` package. The supported development and CI target for backend work is Python 3.12.

## Bootstrapping in `main.py`
`main.py` acts as the primary orchestrator. Upon launch, it executes these sequential steps:

1. **Environment Setup**: Parses command-line arguments and sets up `~/.local/share/wLib` directories.
2. **SSL Certificate Configuration**: Configures SSL certificates from bundled certifi or system paths for secure scraping (`configure_ssl_certificates()`)
3. **Qt Runtime Configuration**: Detects session type (X11/Wayland) and configures Qt platform plugins (`configure_qt_runtime_environment()`)
4. **Database Initialization**: Calls `core.database.init_db()` to create/migrate the SQLite schema.
5. **Extension Sync**: Copies the bundled browser extension assets into `~/.local/share/wLib/extension/` when the installed files are missing or the bundled manifest version changed.
6. **Playwright Preflight**: Silently fires a `playwright install chromium` subprocess if the required browsers aren't found in `~/.cache/ms-playwright`.
7. **Daemon Threads**: Starts the HTTP extension proxy server (`start_extension_server()`) in a daemonized background thread to prevent blocking the main GUI loop.
8. **WebView Launch**: Binds the `core.api.Api` instance to `pywebview` and enters the blocking UI loop.

`main.py` also exposes a small maintenance CLI path: `python main.py --install-playwright-if-needed` runs the Playwright browser preflight without starting the desktop UI.

### Renderer Diagnostics System

`main.py` includes a comprehensive renderer diagnostics system for troubleshooting GPU and Qt issues:

- **GPU Detection**: Probes GPU capabilities using `glxinfo`, `/sys/class/drm/`, and environment variables
- **Crash Guard**: Implements `~/.local/share/wLib/.gpu_crash_guard` to auto-fallback to software rendering after GPU crashes
- **Diagnostic Logging**: Logs Qt backend selection, GPU detection reason, renderer details, and WebGL probe results to `~/.local/share/wLib/renderer-diagnostics.log`
- **Browser Renderer Probe**: Executes WebGL detector scripts in the PyWebView to capture the browser's GPU renderer information
- **AppImage Mirroring**: AppImage launches mirror diagnostic context to `~/.local/share/wLib/appimage-launch.log`

## Core Modules

### `core/api.py` (The API Bridge)
The `Api` class acts as the single point of entry for the Vue frontend. All methods defined without a leading underscore (e.g. `get_games`, `launch_game`) are automatically serialized into Promises on the `window.pywebview.api` object.
- **Concurrency**: UI calls are technically asynchronous on the JS side but block the pywebview worker pool on the Python side. The `Api` class heavily uses background thread spawning (`threading.Thread`) for long tasks (like downloading updates or mass-scraping metadata) so the UI doesn't freeze.
- **Event Emitter**: Contains wrapper helpers to dispatch Global UI Events back to Vue using `webview.evaluate_js()`.
- **Extension Sync Metadata**: Tracks whether startup extension synchronization actually updated the installed browser files so the frontend can show a toast prompting the user to reload the addon.
- **Launch Mode Contract**: Carries each game's `launch_mode` through add, update, list, and launch calls. Missing or unsupported values normalize to `auto`; supported values are `auto`, `native`, `wine_proton`, and `rpgmaker_linux`.
- **Launch Target Contract**: Exposes CRUD and reordering methods for additional game launch targets. `get_games()` returns each game's extra `launch_targets`, while the canonical default executable remains `games.exe_path`.
- **Library Migration Contract**: Exposes `export_library_backup`, `inspect_library_backup`, and `import_library_backup` for one-file JSON migration. Import uses an inspect-before-write flow and backup-wins field merges for selected sections.

### `core/library_backup.py` (JSON Migration)
This module serializes and imports semantic library backups without copying the raw SQLite database.
- **Format**: Writes one JSON document with `format`, `format_version`, export timestamp, selected sections, game metadata, optional game sections, and selected settings groups.
- **Always-included metadata**: Every exported game includes title, developer, engine, tags, F95 URL, version fields, and cover reference so records remain identifiable after migration.
- **Import matching**: Matches games by normalized F95 thread identity first, then by normalized title/developer only when there is exactly one local match. Ambiguous fallback matches are skipped and reported.
- **Merge behavior**: Matched games receive backup values for always-included metadata and selected optional sections. Unselected sections preserve local values. Playtime is overwritten from the backup when user state is imported, never summed.
- **Safety boundaries**: The JSON export excludes scraper browser sessions, cookies, webview storage, downloaded runtimes, Playwright browser binaries, extension copies, caches, and diagnostics.

### `core/launcher.py` (Process Management)
This module handles the complexities of launching games on Linux.
- **Environment Overrides**: Depending on the settings enabled for a specific game (e.g. `run_wayland`, `run_japanese_locale`), the launcher injects OS-level environment variables (`LC_ALL=ja_JP.UTF-8`, `SDL_VIDEODRIVER=wayland`) directly into the `env` dictionary passed to `subprocess`.
- **Launch Modes**: `auto` preserves extension/executable detection, `native` runs supported Linux host targets without Wine/Proton settings, `wine_proton` forces the compatibility-runtime branch, and `rpgmaker_linux` invokes an externally installed `rpgmaker-linux` runner with `--gamepath` resolved from the selected executable directory.
- **Launch Targets**: Alternate targets reuse the same launcher entrypoint as the default executable; only the selected executable path changes. Playtime remains keyed to the parent `game_id`.
- **Wine & Proton**: Prepends the configured `proton_path` or `wine` binary when compatibility mode is selected or auto-detection falls through to a Windows-style target, ensuring the proper `WINEPREFIX` or Proton compatibility path is enforced.
- **Cheat Engine Integration**: Implements logic to auto-start `lunarengine-x86_64.exe` natively, passing a Lua injection script to map directly to the game's PID.
- **RPGMaker Tooling**: Implements enhanced Wine/NW.js fixes for RPGMaker MV/MZ and can optionally launch through the external `rpgmakermlinux-cicpoffs` runner when users install/configure it themselves. wLib links to the upstream project but does not bundle, install, update, export, bug-report, or run mutation-oriented upstream commands automatically.
- **Playtime Tracking**: Uses `Popen.wait()` in a dedicated watcher thread, capturing timestamps on start and exit, then executing a database UPDATE callback to record total seconds played.

### `core/scraper.py` (Playwright Engine)
Responsibile for fetching and parsing data from F95Zone.
- **Headless Operations**: Uses `playwright.sync_api` to spin up headless Chromium instances.
- **Persistent Browser Sessions**: Maintains a persistent browser profile under `~/.local/share/wLib/browser_session/` that preserves F95Zone login cookies, localStorage, and session state across restarts. The session directory is reused across scraper invocations to maintain authentication.
- **Cloudflare Bypass**: Implements resilient `page.wait_for_selector()` heuristics to intelligently wait out or detect Cloudflare turnstiles, and identifies login-wall blocks to bubble up authentication errors to the UI.
- **DOM Parsing**: Compiles metadata (title, version, image URLs, developer) by executing query selectors on the rendered HTML structure.
- **Environment Cleanup**: Strips AppImage-specific environment variables (`APPIMAGE`, `APPDIR`, `LD_LIBRARY_PATH`) before launching Playwright to prevent library conflicts with the bundled Chromium binaries.

## Backend Verification Workflow

- **Type checking**: `pyrightconfig.json` is configured for `basedpyright` and currently checks `main.py` plus modules under `core/`.
- **Strict rollout**: The backend entrypoints and core runtime modules (`main.py`, `core/api.py`, `core/database.py`, `core/launcher.py`, and `core/scraper.py`) are opted into `strict` checking while the rest of the project remains at `recommended`.
- **Initial rollout scope**: `tests/` are intentionally excluded from type checking for now so backend diagnostics stay focused on application code.
- **Baseline tracking**: `.basedpyright/baseline.json` is currently empty. It remains in the repo so future typing rollouts can use the same incremental workflow without changing tool paths.
- **Linting**: `ruff` is configured repo-wide for Python files so tests, utility scripts, and backend modules share the same baseline lint rules.
- **Runtime smoke check**: `scripts/smoke_backend.py` imports the backend, configures runtime helpers, imports `pywebview`, and exercises extension sync inside a temporary HOME directory so it does not touch a contributor's real app data.
- **Local wrappers**: `bash scripts/check-python.sh` runs `ruff`, `basedpyright`, the smoke check, and `pytest` in the active environment. `bash scripts/check-python-clean.sh` recreates that workflow from a fresh Python 3.12 virtual environment.

## SSL Certificate Configuration

`main.py` configures SSL certificates on startup to ensure reliable HTTPS connections for scraping and API requests:

1. **Bundled Certifi**: Includes `certifi` CA certificates within the PyInstaller bundle at `_internal/certifi/cacert.pem`
2. **System Certificate Fallback**: Checks standard system paths (`/etc/ssl/certs/ca-certificates.crt`, `/etc/ssl/cert.pem`) if certifi is unavailable
3. **Environment Variable Setup**: Sets `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE`, and `CURL_CA_BUNDLE` to the selected certificate bundle
4. **AppImage Configuration**: The AppRun script performs parallel certificate setup for the AppImage runtime context

This ensures scraping works reliably across different Linux distributions without depending on system certificate configurations.
