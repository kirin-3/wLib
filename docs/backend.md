# Backend Systems (Python)

The core strength of wLib relies on its native Python backend, divided primarily into `main.py` and modularized controllers in the `core/` package.

## Bootstrapping in `main.py`
`main.py` acts as the primary orchestrator. Upon launch, it executes these sequential steps:

1. **Environment Setup**: Parses command-line arguments and sets up `~/.local/share/wLib` directories.
2. **Database Initialization**: Calls `core.database.init_db()` to create/migrate the SQLite schema.
3. **Extension Sync**: Copies the bundled browser extension assets into `~/.local/share/wLib/extension/` when the installed files are missing or the bundled manifest version changed.
4. **Playwright Preflight**: Silently fires a `playwright install chromium` subprocess if the required browsers aren't found in `~/.cache/ms-playwright`.
5. **Daemon Threads**: Starts the HTTP extension proxy server (`start_extension_server()`) in a daemonized background thread to prevent blocking the main GUI loop.
6. **WebView Launch**: Binds the `core.api.Api` instance to `pywebview` and enters the blocking UI loop.

## Core Modules

### `core/api.py` (The API Bridge)
The `Api` class acts as the single point of entry for the Vue frontend. All methods defined without a leading underscore (e.g. `get_games`, `launch_game`) are automatically serialized into Promises on the `window.pywebview.api` object.
- **Concurrency**: UI calls are technically asynchronous on the JS side but block the pywebview worker pool on the Python side. The `Api` class heavily uses background thread spawning (`threading.Thread`) for long tasks (like downloading updates or mass-scraping metadata) so the UI doesn't freeze.
- **Event Emitter**: Contains wrapper helpers to dispatch Global UI Events back to Vue using `webview.evaluate_js()`.
- **Extension Sync Metadata**: Tracks whether startup extension synchronization actually updated the installed browser files so the frontend can show a toast prompting the user to reload the addon.

### `core/launcher.py` (Process Management)
This module handles the complexities of launching games on Linux.
- **Environment Overrides**: Depending on the settings enabled for a specific game (e.g. `run_wayland`, `run_japanese_locale`), the launcher injects OS-level environment variables (`LC_ALL=ja_JP.UTF-8`, `SDL_VIDEODRIVER=wayland`) directly into the `env` dictionary passed to `subprocess`.
- **Wine & Proton**: Auto-detects if the target is an `.exe` file. If so, it prepends the configured `proton_path` or `wine` binary to the launch arguments, ensuring the proper `WINEPREFIX` is enforced.
- **Cheat Engine Integration**: Implements logic to auto-start `lunarengine-x86_64.exe` natively, passing a Lua injection script to map directly to the game's PID.
- **Playtime Tracking**: Uses `Popen.wait()` in a dedicated watcher thread, capturing timestamps on start and exit, then executing a database UPDATE callback to record total seconds played.

### `core/scraper.py` (Playwright Engine)
Responsible for fetching and parsing data from F95Zone.
- **Headless Operations**: Uses `playwright.sync_api` to spin up headless Chromium instances.
- **State Persistence**: Maintains session cookies manually by saving/loading states to a JSON file in the app data directory. This ensures the user does not have to log in on every scraper execution.
- **Cloudflare Bypass**: Implements resilient `page.wait_for_selector()` heuristics to intelligently wait out or detect Cloudflare turnstiles, and identifies login-wall blocks to bubble up authentication errors to the UI.
- **DOM Parsing**: Compiles metadata (title, version, image URLs, developer) by executing query selectors on the rendered HTML structure.
