# AGENTS.md
Guide for coding agents working in `wLib`.

## 1) Project At A Glance
- `wLib` is a Linux-first desktop app for managing F95Zone games.
- Stack: Python 3.12+, PyWebView, SQLite, Playwright, Vue 3, Vite, TailwindCSS.
- Runtime split:
  - `main.py` boots the app, configures runtime env, and runs the extension HTTP server.
  - `core/api.py` is the Python bridge exposed to the UI.
  - `core/database.py` owns SQLite setup, migrations, and CRUD helpers.
  - `core/launcher.py` handles native, Wine, and Proton launch flows.
  - `core/scraper.py` handles F95Zone scraping and persistent browser sessions.
  - `ui/src/services/api.js` is the only frontend-to-backend bridge.
  - `extension/` contains the browser extension source.
- Persistent app data lives under `~/.local/share/wLib`.

## 2) Useful Docs
- `docs/architecture.md`: overall system boundaries and event flow.
- `docs/backend.md`: backend responsibilities and threading patterns.
- `docs/frontend.md`: Vue runtime details and mock API fallback.
- `docs/database.md`: DB path, WAL mode, and migration style.
- `docs/extension_api.md`: localhost extension API and CORS rules.
- `docs/build.md`: packaging, PyInstaller, and AppImage behavior.

## 3) Common Commands
Run from repo root unless noted.

```bash
# quick local run
./wlib.sh

# backend setup
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

# frontend dev
cd ui && npm install && npm run dev

# desktop app in dev mode
DEV_MODE=1 python main.py

# tests
ruff check .
basedpyright
python scripts/smoke_backend.py
bash scripts/check-python.sh
bash scripts/check-python-clean.sh
pytest
pytest tests/test_database.py -v
pytest tests/test_launcher.py -v
pytest tests/test_scraper.py -v
pytest tests/test_main.py -v
pytest tests/test_api_updates.py -v

# frontend/build
cd ui && npm run build
bash scripts/build.sh
```

## 4) Repo-Specific Rules
### Backend
- Keep frontend-callable methods in `core/api.py`; if you add one, mirror it in `ui/src/services/api.js`.
- Keep API payloads JSON-serializable and prefer `{"success": False, "error": "..."}` on failures.
- Long-running backend work should not block the UI; follow the existing thread + status/event pattern.
- Reuse `core/database.py` helpers instead of adding ad hoc SQL in unrelated files.
- Keep schema changes additive in `init_db()`; preserve WAL mode and the allowlist pattern in `update_game`.
- Preserve launcher behavior for native files, Wine, Proton, locale overrides, and playtime tracking.
- Preserve scraper session persistence under `~/.local/share/wLib/browser_session`.

### Frontend
- Use Vue 3 Composition API patterns already present in `ui/src/`.
- Route backend access through `ui/src/services/api.js`, not direct `window.pywebview` calls in views.
- `ui/src/services/api.js` supports mock responses in a normal browser; do not assume PyWebView is always present.
- Keep UI changes compatible with both light and dark themes.

### Extension And Desktop Bridge
- The local extension server listens on `127.0.0.1:8183`.
- Do not loosen CORS beyond extension origins and the Vite dev server in dev mode.
- Keep custom event names stable unless the frontend and backend are updated together:
  - `wlib-extension-open`
  - `wlib-extension-add`
  - `wlib-playtime-tick`

## 5) Testing Expectations
- Start with the smallest relevant test for the code you touched.
- Run `ruff check .` before finishing Python changes.
- Run `basedpyright` before finishing backend changes.
- Run full `pytest` before finishing backend changes.
- If you change backend tooling, environment bootstrap, or CI, run `bash scripts/check-python-clean.sh`.
- Run `cd ui && npm run build` before finishing frontend changes.
- If you change extension, launcher, scraper, or startup behavior, prefer targeted coverage first.

## 6) Delivery Checklist
1. Keep changes scoped to the request.
2. Avoid unrelated refactors unless needed to complete the task safely.
3. Preserve PyWebView, dev-mode, and AppImage workflows.
4. Report what changed, what you verified, and any remaining risks.
