# wLib Code Review & Compatibility Report

This document details findings from a code review and testing session focused on ensuring **wLib** works across major Linux distributions (Debian/Ubuntu, Fedora, Arch, etc.).

## Executive Summary

The application is well-structured using Python (backend) and Vue.js (frontend). The core logic relies heavily on `pywebview` for the UI and `playwright` for scraping updates.

**Key Strengths:**
- Uses `~/.local/share/wLib` for storing prefixes and game data (XDG compliance).
- Attempts multiple file openers (`xdg-open`, `gio`, etc.) for broad desktop environment support.
- Threading is used appropriately for long-running tasks (updates, downloads).

**Critical Issues for Distro Compatibility:**
- **Hardcoded Relative Paths**: The database (`wlib.db`) and browser session (`browser_session/`) are stored relative to the source code. This will fail if the application is installed to a read-only system directory (e.g., `/usr/share/wlib` or `/opt/wlib`) via a package manager.
- **Dependency Assumptions**: The application assumes `wine`, `winetricks`, and `npm` are in the system `PATH`.
- **Launcher Script**: `wlib.sh` contains Debian/Ubuntu-specific instructions (`apt install ...`).

---

## 1. Distro Compatibility & Packaging

### 1.1. Data Storage Locations (High Priority)
Currently, `core/database.py` and `core/scraper.py` determine data paths relative to `__file__`:
- **Database**: `os.path.join(os.path.dirname(__file__), '..', 'wlib.db')`
- **Browser Session**: `os.path.join(os.path.dirname(__file__), '..', 'browser_session')`

**Issue:** If packaged (DEB, RPM, AUR), the source code will reside in a root-owned directory. The application will crash with `PermissionDenied` when trying to write to the database or create browser profiles.
**Recommendation:** Move these files to `~/.local/share/wLib/` or follow `XDG_DATA_HOME`.

### 1.2. External Dependencies
The application relies on external binaries being present in the user's `PATH`:
- `wine` (unless Proton is configured)
- `winetricks` (for `install_rpgmaker_dependencies`)
- `npm` (for building the UI)

**Issue:** On minimal installs (e.g., Arch Linux), these might be missing. The application will fail silently or with a generic error if `subprocess.Popen` cannot find the executable.
**Recommendation:**
- Add a startup check using `shutil.which()` to verify these tools exist.
- Display a user-friendly error dialog if dependencies are missing.

### 1.3. Launcher Script (`wlib.sh`)
The script includes:
```bash
echo "   Please install Python 3.11+ (e.g., sudo apt install python3 python3-venv)"
```
**Issue:** `apt` is specific to Debian/Ubuntu. Fedora uses `dnf`, Arch uses `pacman`.
**Recommendation:** Detect the distro (check `/etc/os-release`) or provide generic instructions ("Please install Python 3.11+ and the venv module using your system package manager").

---

## 2. Logic & Edge Cases

### 2.1. Network Reliability
In `core/scraper.py` and `core/api.py`:
- `page.goto(url, ...)` has a 60s timeout. This is good.
- `urllib.request.urlopen` in `install_rpgmaker_rtp` and `download_proton_ge` does **not** specify a timeout.
**Risk:** The application could hang indefinitely if the download server is unresponsive.
**Fix:** Add `timeout=30` to `urlopen` calls.

### 2.2. Database Concurrency
`core/database.py` uses a standard SQLite connection.
- **Issue:** SQLite allows one writer at a time. While `check_all_updates` runs in a thread, if the user interacts with the UI (triggering another DB write) simultaneously, a `sqlite3.OperationalError: database is locked` could occur.
**Fix:** Enable WAL (Write-Ahead Logging) mode: `cursor.execute("PRAGMA journal_mode=WAL;")` on initialization. Set a busy timeout: `conn = sqlite3.connect(..., timeout=10)`.

### 2.3. Scraper Resilience
The scraper uses regex to extract versions: `[v1.0]`, `[Version 1.0]`.
- **Edge Case:** Thread titles with unconventional formats (e.g., `(v1.0)`) might result in "Unknown".
- **Handling:** The current fallback to "Unknown" is safe, but could be improved with more robust parsing or fuzzy matching.

---

## 3. Findings from Testing

A test suite was created (`tests/`) covering:
- Database CRUD operations.
- Launcher command construction (Wine vs Proton).
- Scraper parsing logic.
- API integration.

**Results:**
- **Pass**: All 17 tests passed.
- **Observation**: The logic correctly prefers `proton_path` if set, falling back to system `wine`.
- **Observation**: The `install_rpgmaker_rtp` function correctly identifies URLs but relies on hardcoded mirrors (`dl.komodo.jp`) which might change or go offline.

---

## 4. Recommendations for Next Steps

1.  **Migrate Data Paths**: Change `DB_PATH` and `Scraper.user_data_dir` to use `os.path.expanduser("~/.local/share/wLib/")`.
2.  **Add Startup Checks**: In `main.py` or `Api.__init__`, verify `winetricks` and `wine` availability.
3.  **Update `wlib.sh`**: Make the failure message distro-agnostic.
4.  **Enhance Error Reporting**: Capture `subprocess.CalledProcessError` in `Launcher` and return the stderr output to the UI.
5.  **Secure Downloads**: Ensure all `urllib` calls use HTTPS context (already done) and have timeouts.
