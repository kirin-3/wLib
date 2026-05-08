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
- Node.js 18+ and npm
- Linux environment (tested heavily on Arch/CachyOS)
- Playwright dependencies (installed automatically on first run, but may need system packages)

### GPU & Rendering Notes

wLib includes automatic GPU detection and a crash guard system:

- **GPU Detection**: The AppImage probes your GPU on startup using `glxinfo` and `/sys/class/drm/`
- **Crash Guard**: If the app crashes during accelerated startup, the next launch automatically uses software rendering via `QT_QUICK_BACKEND=software`
- **Renderer Diagnostics**: GPU detection results are logged to `~/.local/share/wLib/renderer-diagnostics.log`
- **Manual Override**: Set `WLIB_QPA_PLATFORM=xcb` or `QT_QUICK_BACKEND=software` to override automatic detection

### Setup

#### 1. Python Backend
Set up a `.venv` virtual environment with Python 3.12 and install the development dependencies:
```bash
python -m venv .venv
source .venv/bin/activate
python --version  # should report Python 3.12.x
pip install -r requirements-dev.txt
```

#### 2. Vue 3 Frontend
Install the Node dependencies for the UI:
```bash
cd ui
npm install
```

### Running the App Locally

To develop, you should run the frontend and backend simultaneously in two separate terminals.

**Terminal 1 (Frontend):**
```bash
cd ui
npm run dev
```
*This starts the Vite dev server on `http://localhost:5173` with hot module replacement enabled.*

**Terminal 2 (Backend):**
Ensure your virtual environment is activated, then run:
```bash
DEV_MODE=1 python main.py
```
*Setting `DEV_MODE=1` instructs the Python backend to load the frontend from the Vite development server instead of the compiled static files in `ui/dist/`, enabling Hot Module Replacement (HMR).*

**Backend Initialization:** On startup, `main.py`:
1. Configures the Qt runtime environment and GPU detection
2. Sets up SSL certificates for secure scraping
3. Initializes the SQLite database with WAL mode
4. Syncs browser extension files to `~/.local/share/wLib/extension/`
5. Installs Playwright Chromium browsers if missing
6. Starts the extension HTTP server on `127.0.0.1:8183`
7. Launches the PyWebView window

## 🧪 Testing and Linting

We maintain a suite of automated tests and use strict formatting rules. **Please run these before submitting a PR.**

### Python Code
We use `pytest` for testing, `basedpyright` for backend type checking, and `ruff` / `black` for formatting. The backend currently keeps project-wide checking at `recommended` and opts the main runtime files into `strict`, so new backend changes should keep both the strict subset and the empty baseline green. `ruff` is configured repo-wide for tracked Python files, including helper scripts and tests.
```bash
# Run the full backend check suite inside the active .venv
bash scripts/check-python.sh

# Recreate a clean Python 3.12 environment and rerun the backend checks
bash scripts/check-python-clean.sh

# Run all tests
pytest

# Run a specific test module
pytest tests/test_database.py -v

# Run backend type checking (main.py + core/)
basedpyright

# Run repo-wide Python linting
ruff check .

# Run the backend smoke check without opening the UI
python scripts/smoke_backend.py

# Format and lint code
black .
ruff check .
```

### Smoke Backend Test

The smoke test (`scripts/smoke_backend.py`) verifies backend initialization without opening the UI:

- Runs in an isolated temporary HOME directory
- Tests Qt platform configuration
- Verifies Playwright browser path setup
- Exercises extension file synchronization
- Does not require a display or GUI session

Use it for quick CI checks or to verify backend changes before running the full app.

### Frontend Code
```bash
cd ui
npx prettier --write "src/**/*.{ts,vue,css}"
npm run typecheck # Run vue-tsc type checks
npm run build # Ensure the production build succeeds
```

## 📚 Architecture & Documentation

Before making architectural changes, reviewing our internal documentation is highly recommended:
- [Architecture Overview](docs/architecture.md)
- [Backend Systems](docs/backend.md)
- [Frontend Details](docs/frontend.md)
- [Database & Schema](docs/database.md)
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
2. Open a **Pull Request** against the `main` branch.
3. Fill out the PR template, describing what you changed and how you tested it.
4. Link any related issues (e.g., "Closes #42").

### PR Checklist

- [ ] Code follows existing style conventions.
- [ ] Tests pass locally (`pytest`).
- [ ] The app launches headless Chromium (Playwright) successfully.
- [ ] UI changes work elegantly in both Dark and Light themes.
- [ ] Added documentation and migration coverage for any new features, schema changes, settings, or frontend/backend API contracts.

## 🐛 Reporting Issues

When reporting a bug, please include:
- Steps to reproduce the issue.
- Expected vs. actual behavior.
- Linux distribution, version, and Desktop Environment (Wayland/X11).
- Terminal output logs (run the app from the terminal to capture errors).

### Debug Logs & Diagnostics

wLib generates several diagnostic files:

- **Renderer Diagnostics**: `~/.local/share/wLib/renderer-diagnostics.log` - GPU detection and Qt backend selection
- **AppImage Launch Log**: `~/.local/share/wLib/appimage-launch.log` - AppImage-specific launch context
- **Browser Session**: `~/.local/share/wLib/browser_session/` - Persistent Playwright browser profile for F95Zone
- **Extension Files**: `~/.local/share/wLib/extension/` - Installed browser extension copies

Enable debug logging in **Settings → Debug Logging** for verbose application logs.

## 💡 Where to Help?
Check our issues page! We are always looking for help with:
- **Game Engine Support:** Better auto-detection for older RPGM engines.
- **UI Tweaks:** Smoother animations and accessibility polish in Vue.
- **Cross-Distro Compatibility:** Testing on edge-case Linux distros and window managers.
