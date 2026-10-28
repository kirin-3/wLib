# Contributing to wLib

Thank you for your interest in contributing to wLib! We welcome all contributions, from bug reports and documentation updates to new features and core improvements.

This guide provides instructions and workflows to help you set up your development environment and standardize your contributions.

## 🚀 Getting Started

1. **Fork** the repository on GitHub.
2. **Clone** your fork locally:
   ```bash
   git clone https://github.com/YOUR_USERNAME/wLib.git
   cd wLib
   ```
3. **Create a branch** for your change:
   ```bash
   git checkout -b feat/my-awesome-feature
   ```

## 🛠 Development Environment

wLib consists of a Python 3 backend and a Vue 3 + TypeScript frontend.

### Prerequisites

- Python 3.12 (recommended for backend development and type checking)
- Node.js 22.12+ and npm (supports the current Vite build and frontend unit tests)
- Linux (tested heavily on Arch/CachyOS) or Windows x64; see [Windows release qualification](docs/build.md#windows-release-qualification) for tested OS coverage

### GPU & Rendering Notes

wLib uses PyWebView with Qt WebEngine on Linux and Windows. Linux release launchers include automatic GPU detection and a crash guard system:

- **GPU Detection**: The AppImage probes your GPU on startup using `glxinfo` and `/sys/class/drm/`
- **Crash Guard**: If the app crashes during accelerated startup, the next launch automatically uses software rendering via `QT_QUICK_BACKEND=software`
- **Renderer Diagnostics**: GPU detection results are logged to `<data dir>/renderer-diagnostics.log`
- **Manual Override**: Set `WLIB_QPA_PLATFORM=xcb` or `QT_QUICK_BACKEND=software` to override automatic detection

On Windows, Qt selects its native platform and renderer. Diagnostics are written to `%LOCALAPPDATA%\wLib\renderer-diagnostics.log`; Linux `xcb`/Wayland overrides and the shell launcher's crash guard do not apply.

### Setup

#### 1. Python Backend
Set up a `.venv` virtual environment with Python 3.12 and install the development dependencies:

**Linux:**

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
```

**Windows (PowerShell):**

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python --version  # should report Python 3.12.x
python -m pip install -r requirements-dev.txt -r requirements-windows.txt
```

If PowerShell blocks `Activate.ps1`, call `.\.venv\Scripts\python.exe` and the other tools in `.venv\Scripts\` directly instead of activating the environment.

#### 2. Vue 3 Frontend
Install the Node dependencies for the UI:
```bash
cd ui
npm ci
```

### Running the App Locally

For desktop hot reload, activate your virtual environment and run from the repository root:

```bash
DEV_MODE=1 python main.py
```

On Windows PowerShell:

```powershell
$env:DEV_MODE = "1"
python main.py
```

*`DEV_MODE=1` starts the Vite dev server (`npm run dev` in `ui/`, or `npm.cmd` on Windows) and loads the frontend from `http://localhost:5173` instead of the compiled files in `ui/dist/`, so frontend edits apply through Hot Module Replacement (HMR).*

Without `DEV_MODE`, `python main.py` loads `ui/dist/`; rebuild it with `npm run build` in `ui/` after frontend edits.

For frontend-only work, run `npm run dev` in `ui/` and open it in a normal browser. `ui/src/services/api.ts` returns mock responses when PyWebView isn't present.

**Backend Initialization:** On startup, `main.py`:
1. Configures SSL certificates for secure scraping
2. Binds the extension HTTP server on `127.0.0.1:8183`; this doubles as the single-instance check
3. Installs the rotating app log at `<data dir>/logs/wlib.log`
4. Configures the Qt runtime environment and refreshes the packaged WebView cache when needed
5. Creates the `Api` bridge, which initializes the SQLite database with WAL mode and starts the optional game-catalog check
6. Syncs browser extension files to the platform data directory: `~/.local/share/wLib/extension/` on Linux or `%LOCALAPPDATA%\wLib\extension` on Windows
7. Opens the PyWebView window, cleans up app-owned legacy browser data in the background, and starts the extension server and sync watcher

## 🧪 Testing and Linting

We maintain a suite of automated tests and use strict formatting rules. **Please run these before submitting a PR.**

### Python Code
We use `pytest` for testing, `basedpyright` for backend type checking, and `ruff` for formatting and linting. `black` is available for local formatting but is not enforced in CI. The backend currently keeps project-wide checking at `recommended` and opts the main runtime files into `strict`, so new backend changes should keep both the strict subset and the empty baseline green. `ruff` is configured repo-wide for tracked Python files, including helper scripts and tests.
```bash
# Run all tests
pytest

# Run a specific test module
pytest tests/test_database.py -v

# Run backend type checking (main.py, core/ and catalog_server/)
basedpyright

# Run repo-wide Python linting
ruff check .

# Format and lint code
black .
ruff check .
```

These tool commands work in Bash and PowerShell with an active `.venv`. For the smoke check, use the platform instructions below. Linux contributors can run the whole suite with `bash scripts/check-python.sh`, or recreate a clean Python 3.12 environment with `bash scripts/check-python-clean.sh`. Those wrappers use Linux virtual-environment paths; on Windows, run `ruff check .`, `basedpyright`, `pytest`, and the isolated smoke check directly.

### Smoke Backend Test

The smoke test (`scripts/smoke_backend.py`) verifies backend initialization without opening the UI:

- Sets temporary HOME and `WLIB_DATA_DIR` paths automatically on both platforms
- Tests Qt platform configuration
- Verifies HTTP scraping initialization without a browser runtime
- Exercises extension file synchronization
- Does not require a display or GUI session

Use it for quick CI checks or to verify backend changes before running the full app.

**Linux:**

```bash
python scripts/smoke_backend.py
```

**Windows (PowerShell, active `.venv`):**

```powershell
python scripts/smoke_backend.py
```

For a source or packaged asset check, build the frontend first and run `python main.py --smoke-test` (or `wLib.exe --smoke-test`) with an isolated `WLIB_DATA_DIR`. This checks imports, database initialization, and bundled assets without opening the UI or downloading external content.

### Frontend Code
```bash
cd ui
npm run test:unit # Run frontend unit tests (API bridge, launch modes, saves, tools, library state)
npm run typecheck # Run vue-tsc type checks
npm run build # Ensure the production build succeeds
```

There is no enforced frontend formatter; match the surrounding style and let `vue-tsc` catch contract mistakes.

## 📚 Architecture & Documentation

Before making architectural changes, reviewing our internal documentation is highly recommended:
- [Architecture Overview](docs/architecture.md)
- [Backend Systems](docs/backend.md)
- [Frontend Details](docs/frontend.md)
- [Database & Schema](docs/database.md)
- [Game Catalog](docs/catalog.md)
- [Browser Extension API](docs/extension_api.md)
- [Build & Packaging](docs/build.md)

## 📝 Commit Messages

We use [Conventional Commits](https://www.conventionalcommits.org/). Prefix your commit messages appropriately:

| Prefix      | Use for                                    |
|-------------|--------------------------------------------|
| `feat:`     | New features                               |
| `fix:`      | Bug fixes                                  |
| `docs:`     | Documentation changes                      |
| `style:`    | Code formatting (no logic change)          |
| `refactor:` | Code restructuring without behavior change |
| `test:`     | Adding or fixing tests                     |
| `chore:`    | Build scripts, CI, dependencies            |

**Examples:**
```
feat: add bulk game import from CSV
fix: wine prefix not applied for Proton launches
docs: update installation instructions for Fedora
```

## 🔀 Submitting a Pull Request

1. **Commit and Push** your changes to your fork.
2. Open a **Pull Request** against the `master` branch.
3. Describe what you changed and how you tested it.
4. Link any related issues (e.g., "Closes #42").

### PR Checklist

- [ ] Code follows existing style conventions.
- [ ] Tests pass locally (`pytest`).
- [ ] Batch version checks and guest thread metadata requests succeed over paced HTTP.
- [ ] UI changes work elegantly in both Dark and Light themes.
- [ ] Added documentation and migration coverage for any new features, schema changes, settings, or frontend/backend API contracts.

## 🐛 Reporting Issues

When reporting a bug, please include:
- Steps to reproduce the issue.
- Expected vs. actual behavior.
- Windows version/build, or Linux distribution, version, and desktop environment (Wayland/X11).
- Installation format (Windows MSI/portable ZIP, Linux package/AppImage/Flatpak/Nix/tar.gz, or source).
- Terminal output for source runs and renderer diagnostics for packaged runs.

### Debug Logs & Diagnostics

wLib stores its data under `~/.local/share/wLib` on Linux or `%LOCALAPPDATA%\wLib` on Windows by default. See [platform paths](docs/architecture.md#platform-paths) for overrides. Files relative to that directory include:

- **App Log**: `logs/wlib.log` - console output, Python logging, and uncaught exceptions; rotates at 2 MiB with three backups
- **Renderer Diagnostics**: `renderer-diagnostics.log` - Qt backend selection and WebGL renderer details on both platforms, plus Linux GPU probe results
- **Linux Launcher Logs**: `wlib-launch.log` or `appimage-launch.log` - Linux release-launch context
- **Extension Files**: `extension/` - Installed browser extension copies
- **Save Backups**: `save-backups/<game id>/` - Local save backups, never exported or synced
- **PDF Attachments**: `attachments/<game id>/` - Local PDF attachments, never exported or synced
- **Local Covers**: `covers/` - Device-local cover images, never exported or synced
- **Sandbox Storage**: `sandbox/<game id>/` - Per-game private home and persistent sandbox data
- **Game Catalog**: `catalog.db` - Read-only cached catalog, download-only and deleted when disabled

Use the **Diagnostics** link in the Settings footer to copy a redacted report or open the log folder. **Enable Debug Logging** in Settings is separate: it saves Wine/Proton or native launch output next to the game executable.

## 💡 Where to Help?
Check our issues page! We are always looking for help with:
- **Game Engine Support:** Better auto-detection for older RPGM engines.
- **UI Tweaks:** Smoother animations and accessibility polish in Vue.
- **Platform Compatibility:** Testing Windows MSI/portable builds and edge-case Linux distros/window managers.
