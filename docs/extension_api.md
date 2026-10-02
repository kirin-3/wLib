# Browser Extension API

wLib bundles an optional companion web extension that integrates deeply with F95Zone in standard desktop browsers (Firefox, Chrome). To facilitate instantaneous data transfer without requiring cloud synchronization, wLib runs a background REST server.

## The Local Daemon
Inside `main.py`, a daemon thread launches `start_extension_server()`, binding `http.server.ThreadingHTTPServer` to `127.0.0.1:8183`. Requests run in separate threads so waiting for the UI to process an open/add event does not block library checks. On startup, the app synchronizes bundled extension files into `~/.local/share/wLib/extension/` on Linux or `%LOCALAPPDATA%\wLib\extension` on Windows so the installed unpacked/XPI copy tracks the app version.

## CORS Restrictions (Security Model)
Because the daemon binds to `localhost`, any website visited by the user *could* theoretically perform background requests against it.
Before dispatching any request, the daemon requires exactly one `Host` header matching `localhost:8183` or `127.0.0.1:8183` (case-insensitive). Missing, duplicate, and other Host values receive HTTP 403, preventing DNS rebinding from exposing the library through an external hostname.

Requests without an `Origin` header remain supported for local service checks and extension clients. When an `Origin` is present, `ExtensionRequestHandler._get_allowed_origin` accepts only:
- `chrome-extension://*`
- `moz-extension://*`
- `http://localhost:5173` (Development only)

*Any standard domain (e.g., `https://google.com`) executing `fetch('http://localhost:8183/')` will immediately encounter a CORS rejection.*

Pages can still fire a header-less `GET` without an `Origin` (for example `<img src="http://127.0.0.1:8183/api/open?...">`). Because `GET /api/open` has a side effect, it additionally requires an allowed extension `Origin` or the `X-wLib-Extension` header, which the extension background worker always sends. Pages cannot add that header without a CORS preflight, and preflights from non-extension origins are rejected.

Because of that restriction, the extension does not call `GET /api/check` directly from the F95Zone page context. The content script sends a message to the extension background worker, and the worker performs the request from the extension origin.

## REST API Endpoints

### 1. Check if Game Exists 
**`GET /api/check?url={f95_url}`**
Allows the extension to decorate an F95Zone page based on ownership.
- **Request:** `http://localhost:8183/api/check?url=https://f95zone.to/threads/example.123/`
- **Matching Behavior:** wLib compares F95Zone threads by thread identity, not only by raw URL text. Equivalent URL variants such as slug changes, `page-*` paths, query strings, and fragments still resolve to the same game when the thread ID matches.
- **Response:**
  ```json
  {
    "exists": true,
    "playStatus": "Playing"
  }
  ```
- **Contract Notes:**
  - `exists` remains the stable boolean consumed by both the thread widget and latest-alpha page badges.
  - `playStatus` is optional enrichment for matching games only. It may contain a canonical desktop status or a custom name (at most 40 characters).

### 2. Focus the App & Open Game
**`GET /api/open?url={f95_url}`**
Requests that the OS brings the wLib window to the foreground and opens the modal to the specified game.
- **Requires:** an extension `Origin` or the `X-wLib-Extension: 1` header; otherwise HTTP 403.
- **Action:** Triggers pywebview window activation. Emits JavaScript custom event `wlib-extension-open` using the stored library URL when an equivalent thread match is found.
- **Response:**
  ```json
  {
    "success": true
  }
  ```

### 3. Queue a New Game
**`POST /api/add`**
Sends scraped metadata directly to wLib to preemptively fill the "Add Game" modal.
- **Request Body (JSON):**
  ```json
  {
    "url": "https://f95zone.to/threads/example.123/",
    "title": "Example Visual Novel",
    "engine": "Ren'Py",
    "tags": "Romance, Sci-Fi"
  }
  ```
- **Action:** Emits `wlib-extension-add` payload to the Vue frontend, which catches the event and displays the UI form pre-populated with the data above.
- **Errors:** `{"success": false, "error": "..."}` with HTTP 400 for an invalid body or `Content-Length` (limit 1 MB), or HTTP 503 while the app window is not ready yet.
- **Response:**
  ```json
  {
    "success": true
  }
  ```

## Installed Extension Files

Extension version `1.0.7` adds non-numeric title versions: after numeric/chapter/bare-v parsing fails, titles with at least two brackets use the trimmed first bracket as version. `Game [Final] [Dev]` sends version `Final` and developer `Dev`; a single developer bracket does not supply a version. The backend uses the same fallback and string comparison for updates. Release packaging requires a newly signed Firefox XPI.

The scraper's `_extract_version_from_title` mirrors these title rules exactly (both parsers are tested against `tests/version_title_cases.json`), so a version stored by the extension is not later reported as an update. Hyphenated releases such as `[v1.0.0-beta2]` are kept whole. 1.0.7 also sends `X-wLib-Extension` on open and add requests and reports wLib's `{"success": false}` replies as failures instead of success.

The packaged extension files used by browsers live under `~/.local/share/wLib/extension/` on Linux or `%LOCALAPPDATA%\wLib\extension` on Windows by default. **Open Extension Folder** resolves the actual location, including any `WLIB_DATA_DIR` override:

- `chrome/`: unpacked extension directory for Chromium-based browsers
- `firefox/wLib.xpi`: signed Firefox archive in release builds, or an unsigned generated fallback in local development builds

wLib updates that directory automatically on startup when the bundled manifest version changes, the bundled signed Firefox archive changes, or the installed files are missing. Release builds copy the signed Firefox XPI bundled with the app; local development builds generate an unsigned fallback XPI when no signed artifact is bundled. The app also exposes the same sync path through **Open Extension Folder**, and the frontend shows a startup toast when extension files were refreshed so the user knows to reload the browser addon.

Firefox users install release XPIs through **Add-ons and themes** using **Install Add-on From File**. The `about:debugging` temporary add-on flow is only relevant to local development and is not the normal release installation path.

On Windows, Chromium-based browsers load `%LOCALAPPDATA%\wLib\extension\chrome` as an unpacked extension, and Firefox installs `%LOCALAPPDATA%\wLib\extension\firefox\wLib.xpi`. MSI and portable ZIP builds synchronize to the same user-data folder. Keep wLib running for the extension to reach `127.0.0.1:8183`; the endpoint and security checks are identical on Linux and Windows.

## Thread Widget Behavior

On F95 thread pages, the content script injects a floating widget near the upper-left viewport edge using rem-based offsets instead of anchoring it into the page rails.

- The expanded widget shows the wLib logo, a collapse control, a primary `Add to wLib` or `Open in wLib` action, and transient feedback text for open/add requests.
- When the thread already matches a library entry, the widget also renders a non-interactive `Status: <value>` line beneath the action using lightweight status-aware color treatment.
- Collapse state is ephemeral to the current page runtime. Collapsing compresses the card into a logo-only affordance that rests near the lower-left viewport edge; expanding reverses that transition.
- The latest-alpha page continues to reuse `GET /api/check` for its `In wLib` tile badges, but it only reads the `exists` field and ignores `playStatus`.
- Reduced-motion preferences are respected by shortening the collapse/expand transition to an effectively instant state change.
