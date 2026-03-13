# Browser Extension API

wLib bundles an optional companion web extension that integrates deeply with F95Zone in standard desktop browsers (Firefox, Chrome). To facilitate instantaneous data transfer without requiring cloud synchronization, wLib runs a background REST server.

## The Local Daemon
Inside `main.py`, a daemon thread launches `start_extension_server()`, binding `http.server.HTTPServer` to `127.0.0.1:8183`. On startup, the app also synchronizes the bundled browser extension files into `~/.local/share/wLib/extension/` so the installed unpacked/XPI copy tracks the version shipped with the app.

## CORS Restrictions (Security Model)
Because the daemon binds to `localhost`, any website visited by the user *could* theoretically perform background requests against it.
To prevent malicious sites from sniffing or mutating the local database, `ExtensionRequestHandler._get_allowed_origin` rigidly blocks incoming requests unless the `Origin` header matches an exact whitelist:
- `chrome-extension://*`
- `moz-extension://*`
- `http://localhost:5173` (Development only)

*Any standard domain (e.g., `https://google.com`) executing `fetch('http://localhost:8183/')` will immediately encounter a CORS rejection.*

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
  - `playStatus` is optional enrichment for matching games only. When present, it uses the same canonical values as the desktop app (`Not Started`, `Plan to Play`, `Playing`, `Waiting For Update`, `On Hold`, `Completed`, `Abandoned`).

### 2. Focus the App & Open Game
**`GET /api/open?url={f95_url}`**
Requests that the OS brings the wLib window to the foreground and opens the modal to the specified game.
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
- **Response:**
  ```json
  {
    "success": true
  }
  ```

## Installed Extension Files

The packaged extension files used by browsers live in `~/.local/share/wLib/extension/`:

- `chrome/`: unpacked extension directory for Chromium-based browsers
- `firefox/wLib.xpi`: generated archive for Firefox temporary installs

wLib updates that directory automatically on startup when the bundled manifest version changes or the installed files are missing. The app also exposes the same sync path through **Open Extension Folder**, and the frontend shows a startup toast when extension files were refreshed so the user knows to reload the browser addon.

## Thread Widget Behavior

On F95 thread pages, the content script injects a floating widget near the upper-left viewport edge using rem-based offsets instead of anchoring it into the page rails.

- The expanded widget shows the wLib logo, a collapse control, a primary `Add to wLib` or `Open in wLib` action, and transient feedback text for open/add requests.
- When the thread already matches a library entry, the widget also renders a non-interactive `Status: <value>` line beneath the action using lightweight status-aware color treatment.
- Collapse state is ephemeral to the current page runtime. Collapsing compresses the card into a logo-only affordance that rests near the lower-left viewport edge; expanding reverses that transition.
- The latest-alpha page continues to reuse `GET /api/check` for its `In wLib` tile badges, but it only reads the `exists` field and ignores `playStatus`.
- Reduced-motion preferences are respected by shortening the collapse/expand transition to an effectively instant state change.
