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

wLib consists of a Python 3 backend and a Vue 3 frontend. 

### Prerequisites

- Python 3.10+
- Node.js 18+ and npm
- Linux environment (tested heavily on Arch/CachyOS)
- Playwright dependencies (installed automatically on first run, but may need system packages)

### Setup

#### 1. Python Backend
Set up a virtual environment and install the dependencies:
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
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

**Terminal 2 (Backend):**
Ensure your virtual environment is activated, then run:
```bash
DEV_MODE=1 python main.py
```
*Setting `DEV_MODE=1` instructs the Python backend to load the frontend from the Vite development server (`http://localhost:5173`) instead of the compiled static files, enabling Hot Module Replacement (HMR).*

## 🧪 Testing and Linting

We maintain a suite of automated tests and use strict formatting rules. **Please run these before submitting a PR.**

### Python Code
We use `pytest` for testing and `ruff` / `black` for formatting.
```bash
# Run all tests
pytest

# Run a specific test module
pytest tests/test_database.py -v

# Format and lint code
black core/ main.py tests/
ruff check core/ main.py tests/
```

### Frontend Code
```bash
cd ui
npx prettier --write "src/**/*.{js,vue,css}"
npm run build # Ensure the production build succeeds
```

## 📚 Architecture & Documentation

Before making architectural changes, reviewing our internal documentation is highly recommended:
- [Architecture Overview](docs/architecture.md)
- [Backend Systems](docs/backend.md)
- [Frontend Details](docs/frontend.md)
- [Database & Schema](docs/database.md)
- [Browser Extension API](docs/extension_api.md)

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
- [ ] Added documentation for any new features or schema changes.

## 🐛 Reporting Issues

When reporting a bug, please include:
- Steps to reproduce the issue.
- Expected vs. actual behavior.
- Linux distribution, version, and Desktop Environment (Wayland/X11).
- Terminal output logs (run the app from the terminal to capture errors).

## 💡 Where to Help?
Check our issues page! We are always looking for help with:
- **Game Engine Support:** Better auto-detection for older RPGM engines.
- **UI Tweaks:** Smoother animations and accessibility polish in Vue.
- **Cross-Distro Compatibility:** Testing on edge-case Linux distros and window managers.
