# Browser Extension API

wLib bundles an optional companion web extension that integrates deeply with F95Zone in standard desktop browsers (Firefox, Chrome). To facilitate instantaneous data transfer without requiring cloud synchronization, wLib runs a background REST server.

## The Local Daemon
In `core/extension_server.py`, `bind_extension_server()` binds a `ThreadingHTTPServer` subclass to `127.0.0.1:8183` at startup and a daemon thread serves it. The port also acts as the single-instance lock: a second wLib that cannot bind it calls `GET /api/open` to bring the running window forward and exits. Requests run in separate threads so waiting for the UI to process an open/add event does not block library checks. On startup, the app synchronizes bundled extension files into the resolved app-data folder (`~/.local/share/wLib/extension/` by default on Linux, `%LOCALAPPDATA%\wLib\extension` on Windows) so the installed unpacked/XPI copy tracks the app version.

## CORS Restrictions (Security Model)
Because the daemon binds to `localhost`, any website visited by the user *could* theoretically perform background requests against it.
Before dispatching any request, the daemon requires exactly one `Host` header matching `localhost:8183` or `127.0.0.1:8183` (case-insensitive). Missing, duplicate, and other Host values receive HTTP 403, preventing DNS rebinding from exposing the library through an external hostname.

Requests without an `Origin` header remain supported for local service checks and extension clients. When an `Origin` is present, `ExtensionRequestHandler._get_allowed_origin` accepts only:
- `chrome-extension://*`
- `moz-extension://*`
- `http://localhost:5173` (Development only)

*Any standard domain (e.g., `https://google.com`) executing `fetch('http://localhost:8183/')` will immediately encounter a CORS rejection.*

Pages can still fire a header-less `GET` without an `Origin` (for example `<img src="http://127.0.0.1:8183/api/open?...">`). Because `GET /api/open` has a side effect, it additionally requires an allowed extension `Origin` or the `X-wLib-Extension` header, which the extension background worker always sends. Pages cannot add that header without a CORS preflight, and preflights from non-extension origins are rejected.

## Extension Pairing
An extension `Origin` alone is not trusted: every installed browser extension has one, and local programs send none. Each extension install makes a random 64-hex-character token (`chrome.storage.local`, key `wlibPairingToken`) and sends it as the `X-wLib-Extension` header on every request and as `?token=` on the bridge socket. `core/extension_pairing.py` serves a token only after the user allowed it in the app.

- **First contact:** an unknown token becomes a pending request (at most 3 at once; an unanswered request expires after 10 minutes) and the server fires the `wlib-extension-pairing` window event. `App.vue` asks "Allow browser extension?" with the browser kind and a short code (the first 8 hex characters of the token's SHA-256, as `XXXX-XXXX`). The extension's options page shows the same code.
- **Storage:** allowed tokens are stored as SHA-256 hashes in `<data dir>/extension-pairing.json` (mode 0600). The file is device-local and is never exported or synced. A refusal lasts until wLib restarts.
- **Gated:** `/api/add`, `/api/wishlist`, `/api/thread-links`, `/api/bridge`, and `/api/open` with a `url` or `id`. These answer HTTP 403 with `error_code` `pairing_required`, `pairing_denied`, or `extension_outdated` (no token, or the old `1` value).
- **`/api/check`** always answers HTTP 200. For an unpaired caller it returns `exists: false`, the `error` and `error_code` above, and the version fields, without a library lookup. This lets an old Chromium extension see the new `extension_version` and reload itself.
- **Not gated:** a bare `GET /api/open` (it only shows the window; a second wLib instance sends it), `/covers/`, the OAuth callbacks, and the runner endpoints (they use `X-wLib-Runner`).
- **Removal:** the Extension page lists allowed extensions. Removing one deletes its hash and closes its bridge sockets. At most 16 extensions stay allowed: allowing another drops the oldest, and its bridge sockets close the same way.
- **Dev mode:** the Vite origin needs no token.
- **App bridge methods:** `get_extension_pairing()`, `resolve_extension_pairing(request_id, allow)`, `revoke_extension_pairing(pairing_id)`.

Because of that restriction, the extension does not call `GET /api/check` directly from the F95Zone page context. The content script sends a message to the extension background worker, and the worker performs the request from the extension origin.

## REST API Endpoints

### Local cover images and remote cover cache
**`GET /covers/<name>`** serves a user-selected local cover file from `<data dir>/covers/`. Names must match `^[0-9a-f]{32}\.(jpg|png|webp|gif|avif)$`; invalid names, missing files, traversal attempts and `/covers/` receive 404. The Host check still applies. This read-only route precedes the Origin gate to allow image loading in both packaged and dev views; it adds no CORS headers. Responses include the image Content-Type (`image/jpeg`, `image/png`, `image/webp`, `image/gif`, or `image/avif`), `X-Content-Type-Options: nosniff` and `Cache-Control: private, max-age=31536000, immutable`. Replacement uses a fresh random name. No listing endpoint exists.

**`GET /covers/cache/<name>`** serves a cached remote cover from `<data dir>/covers/cache/`. It shares the same random-name regex, content type mapping, nosniff protection, and private immutable cache header as `/covers/<name>`.

**`GET /covers/cards/<name>`** serves generated still and animated WebP card copies from `<data dir>/covers/cards/`. Names are restricted to a source's 32-hex stem followed by `.still.webp` or `.anim.webp`; there is no listing. These responses use the same Host check and cache and nosniff headers as the other cover routes.

### 1. Check if Game Exists 
**`GET /api/check?url={page_url}&id={source:id}`**
Allows the extension to decorate a supported page based on library ownership.
- **Request:** `http://localhost:8183/api/check?url=https://f95zone.to/threads/example.123/`
- **IDs:** `id=source:value` can be repeated. DLsite, VNDB and Ryuugames page URLs resolve through `core/game_lookup.py`, including one hop of installed catalog links. Exact work IDs match; creator links and titles never do. Checks have no side effects.
- **Matching Behavior:** wLib compares F95Zone threads by thread identity, not only by raw URL text. Equivalent URL variants such as slug changes, `page-*` paths, query strings, and fragments still resolve to the same game when the thread ID matches.
- **Response:**
  ```json
  {
    "exists": true,
    "playStatus": "Playing",
    "wishlisted": false,
    "app_version": "0.4.7",
    "extension_version": "1.2.7"
  }
  ```
- **Contract Notes:**
  - `canAttachThread: true` is present on F95 checks only when exactly one matching game has no F95 URL. The widget offers **Attach F95 thread** alongside Open; ambiguous matches never receive this flag.
  - `gameId` is the local integer ID when a game matches. An ambiguous identity returns one game to open and never modifies any of the matches.
  - `exists` remains the stable boolean consumed by both the thread widget and latest-alpha page badges.
  - `playStatus` is optional enrichment for matching games only. It may contain a canonical desktop status or a custom name (at most 40 characters).
  - `wishlisted` is present for matching games. It is `true` when `exe_path` is empty after trimming.
  - `app_version` is the running wLib app version without a leading `v`. It is present on every check response, including checks without a `url` parameter and checks whose library lookup fails.
  - `extension_version` is the manifest version currently installed in the synced Chromium extension folder. It is omitted when startup extension sync did not succeed or the installed version is unknown.
  - The server answers before the database is initialized at startup. A check with a `url` waits up to 8 seconds for it, then returns HTTP 503 with `{"exists": false, "error": "Application is not ready", "app_version": "..."}`.

### 2. Focus the App & Open Game
**`GET /api/open?url={page_url}&id={source:id}`**
Requests that the OS brings the wLib window to the foreground and opens the modal to the specified game.
- **Requires:** an extension `Origin` or the `X-wLib-Extension` header; otherwise HTTP 403. A call with `url` or `id` also requires a paired token (see Extension Pairing).
- **Action:** Supply a page URL; the query also accepts the same repeated IDs as `/api/check` to help resolve it. The response and `wlib-extension-open` detail include `gameId` when resolved, so games without an F95 thread can open. Triggers pywebview window activation. Emits JavaScript custom event `wlib-extension-open` using the stored library URL when an equivalent thread match is found.
- **Response:**
  ```json
  {
    "success": true
  }
  ```
  If the app window is not ready, the response is `{"success": false, "error": "Application window is not ready"}`.

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

- **Store pages:** The body also accepts `pageUrl` and `links: [{source, id, url}]`. A title and either an F95 thread URL or at least one valid HTTP(S) link are required. With a catalog, metadata for the page’s own ID takes priority over fallback page metadata. Without it, the extension’s title and cover are used.
- **Action:** Emits `wlib-extension-add` payload to the Vue frontend, which catches the event and displays the UI form pre-populated with the data above.
- **Errors:** `{"success": false, "error": "..."}` with HTTP 400 for an invalid body or `Content-Length`, HTTP 413 for a body over the 1 MB limit, or HTTP 503 while the app window is not ready yet.
- **Response:**
  ```json
  {
    "success": true
  }
  ```

### 4. Save to Wishlist
**`POST /api/wishlist`**
Saves the thread or store page directly as a library game with an empty executable path. The body uses the same 1 MB limit and origin rules as `/api/add`.
- **Request Body (JSON):** Also accepts `pageUrl` and `links` with the same catalog enrichment as `/api/add`. `title`, `url`, `version`, `coverImage`, `tags`, `rating`, `developer`, and `engine` are string fields. `tags` is a comma-separated string.
  ```json
  {
    "title": "Example Visual Novel",
    "url": "https://f95zone.to/threads/example.123/",
    "version": "1.0",
    "coverImage": "https://example.com/cover.jpg",
    "tags": "Romance, Sci-Fi",
    "rating": "4.5",
    "developer": "Example Studio",
    "engine": "Ren'Py"
  }
  ```
- **Action:** Calls the desktop API's `add_game` with `exe_path` set to an empty string, then emits `wlib-refresh-library` when the window is ready. The save can succeed while the window is unavailable.
- **Response:** The `add_game` result, including `success`, `id`, `title`, and `metadata_pending` on success. A duplicate thread returns HTTP 409 with `{"success": false, "error_code": "duplicate_url", "error": "..."}`. Invalid bodies return HTTP 400 and bodies over 1 MB return HTTP 413; an API that is not ready returns HTTP 503.

### 5. Bridge for Background Fetches
**`GET /api/bridge` (WebSocket)**
Lets a running browser retry a challenged or login-gated F95 request with its own cookies, without opening a tab. The app never sees cookies; it only receives the fetched HTML/JSON body.

- **Handshake:** `GET /api/bridge?token=<pairing token>` with `Upgrade: websocket`, `Connection: Upgrade`, `Sec-WebSocket-Key` (base64 of 16 bytes), and `Sec-WebSocket-Version: 13` over HTTP/1.1. Another version gets HTTP 426; other missing or invalid fields get HTTP 400. The server answers `101 Switching Protocols` with `Sec-WebSocket-Accept` (base64(SHA-1(key + GUID)), RFC 6455 sample `dGhlIHNhbXBsZSBub25jZQ==` → `s3pPLMBiTxaQ9kYGzzhZRbK+xOo=`).
- **Origin rules (stricter than the REST endpoints):** `Origin` must start with `chrome-extension://` or `moz-extension://`. Web origins (`https://example.com`), missing origins, missing upgrades (HTTP 400), and bad `Host` values (HTTP 403, same single-Host check as the other endpoints) are rejected before any message is exchanged. A missing or unpaired token receives HTTP 403 (see Extension Pairing).
- **Socket:** the upgraded socket uses a 60-second timeout (plain endpoints use 10 seconds). One handler thread is held per browser; idle stalled peers drop on timeout.
- **Protocol (JSON text messages):** the current extension sends protocol 2; the app also accepts protocol 1 for older clients.
  ```json
  // ext -> app (on connect, and on permission change)
  {"type": "hello", "protocol": 2, "extension_version": "1.2.13", "can_fetch": true, "can_fetch_lists": true, "can_fetch_lc": true, "can_resolve": true, "can_resolve_lc": true, "can_fetch_lc_images": true}
  {"type": "status", "can_fetch": false, "can_fetch_lc": true, "can_resolve_lc": false, "can_fetch_lc_images": true}
  {"type": "ping"}
  // app -> ext
  {"type": "pong"}
  {"type": "fetch", "id": "<uuid4>", "url": "https://f95zone.to/threads/123/"}
  {"type": "fetch", "id": "<uuid4>", "site": "lc", "url": "https://lewdcorner.com/threads/123/"}
  {"type": "fetch_image", "id": "<uuid4>", "url": "https://lewdcorner.com/attachments/cover-webp.891912/"}
  // ext -> app
  {"type": "fetch_result", "id": "...", "ok": true, "status": 200,
   "final_url": "https://f95zone.to/threads/slug.123/", "body": "<html>..."}
  {"type": "fetch_result", "id": "...", "ok": false, "error": "refused|timeout|too_large|offsite_redirect|network"}
  {"type": "fetch_image_chunk", "id": "...", "index": 0, "ok": true, "status": 200, "content_type": "image/webp",
   "final_url": "https://lewdcorner.com/attachments/cover-webp.891912/", "data": "<base64>", "last": false}
  {"type": "fetch_image_chunk", "id": "...", "index": 1, "data": "<base64>", "last": true}
  {"type": "fetch_image_chunk", "id": "...", "index": 0, "ok": false, "error": "refused|timeout|too_large|offsite_redirect|network", "last": true}
  ```
  A connection is eligible for fetches only after `hello` with protocol 1 or 2 and `can_fetch == true` (from `permissions.contains({origins: ["https://f95zone.to/*"]})`). Download resolution also requires `can_resolve == true`. Unknown message types are ignored. A JSON `{"type":"ping"}` is answered with JSON `{"type":"pong"}`; WebSocket ping frames are answered with pong frames, and close frames are answered with a close frame.
- **F95 fetch allowlist:** the extension fetches only these F95 URLs and refuses any other without a network request (`refused`):
  - `https://f95zone.to/threads/<thread id>/`
  - `https://f95zone.to/sam/checker.php?threads=<1 to 100 comma-separated thread ids>`
  - `https://f95zone.to/watched/threads?tab=games&unread=0&sort=title&page=<page>` (Watched games)
  - `https://f95zone.to/watched/threads?tab=bookmarks&bm=games&sort=title&page=<page>` (Bookmarked games)

  `<page>` is a whole number from 1 to 200 with no leading zero. The query parameters must be in this order, with no others; "Other threads", bookmarked posts and the other sorts are refused. A redirect that leaves `f95zone.to` fails with `offsite_redirect`.
- **List fetches:** `can_fetch_lists: true` in `hello` (extension 1.2.12 and later) says the extension accepts the two list URLs. wLib sends list URLs (`extension_bridge.fetch_list_page`) only to the newest connection that is eligible (`can_fetch`) and sends this flag. A connection without it stays eligible for thread pages and batch version requests; the F95 account import then reports `extension_outdated` ("update the extension"), and with no eligible connection `no_extension`.
- **Login:** Firefox sends no site cookies with background-page fetches, so F95 answers them as a guest. Cloudflare likewise challenges them without the browser's clearance cookie. When a background fetch returns a guest page (`data-logged-in="false"`) or a managed challenge page, the extension repeats it once from an open, non-discarded `f95zone.to` tab via `scripting.executeScript` (Firefox `content.fetch`, page cookies) and, if that works, uses a tab first for later fetches while one is open. Without an F95 tab it keeps the background result.
- **LewdCorner fetches:** `can_fetch_lc` in `hello` and `status` reports the optional `https://lewdcorner.com/*` permission. A missing flag means LC is unavailable. F95 eligibility still uses `can_fetch`. The app sends `site: "lc"` to the newest LC-capable connection and spaces requests at least two seconds apart. The browser extension uses its own session from its background context and accepts only these URLs:
  - `https://lewdcorner.com/threads/<thread id>/`
  - `https://lewdcorner.com/latest-updates/api?page=<1 to 10>&per_page=90&cat=Games&sort=date`

  The extension rejects responses that redirect outside `lewdcorner.com`.
- **LewdCorner images:** `can_fetch_lc_images` in `hello` and `status` is true when the extension holds `https://lewdcorner.com/*` (the same check as `can_fetch_lc`). A connection that does not send it, such as an older extension, gets no image requests. The app sends `fetch_image` only for LC attachment URLs; the extension refuses any URL that does not match `^https://lewdcorner\.com/attachments/<name>.<number>/$` or contains white space, without a network request (so `/proxy.php`, `/masked/`, thread pages and `/data/attachments/` thumbnails are refused). It fetches in its background context with `credentials: "include"`, never through a tab. A redirect may stay on `lewdcorner.com` (for example to `/data/attachments/...`); one that leaves it fails with `offsite_redirect`.
  - The first `fetch_image_chunk` (`index` 0) carries `ok`, `status`, `content_type` and `final_url`. The body comes only for an `image/*` answer of at most 20 MiB; any other answer, such as an HTTP 500 HTML page, sends `data: ""` with its status and content type. A larger image (by `Content-Length` or while streaming) fails with `too_large`.
  - Each chunk carries at most 4 MiB of image data as base64 (about 5.4 MiB), under the 8 MiB message limit. Chunks come in `index` order and the last one has `last: true`. wLib fails the request for a missing or out-of-order chunk, a total over 20 MiB, bad base64 or no answer in 60 seconds.
  - wLib keeps an image only when the status is 200, the type is JPEG, PNG, WebP, GIF or AVIF, and the image is complete. It never sends a request to `lewdcorner.com` itself.
- **Firefox lifetime:** Firefox suspends an idle event page after 30 seconds and does not count WebSocket traffic as activity. In Firefox, each 20-second ping also writes `wlibBridgeKeepalive` to `storage.local`; the resulting `storage.onChanged` event keeps the page alive while the bridge is connected.
- **Handshake:** the 101 reply uses an `HTTP/1.1` status line. Firefox drops WebSocket handshakes answered with `BaseHTTPRequestHandler`'s default `HTTP/1.0`.
- **CSP:** the manifest sets `extension_pages` to `script-src 'self'; object-src 'self'`. Firefox's MV3 default adds `upgrade-insecure-requests`, which rewrites `ws://localhost:8183` to `wss://` and stops the bridge connecting.
- **Limits:** wLib waits at most 30 seconds per `fetch` and never waits for an extension to connect (`unavailable` fails immediately). A dropped connection fails its pending requests at once. The extension refuses bodies over 4 MiB (`too_large`); wLib rejects bridge messages over 8 MiB, and fragmented messages of more than 1024 frames. Only the newest eligible connection receives a request.
- **Security notes:** the bridge requires both an extension-scheme origin and an allowed pairing token. Origin alone can be forged by a local process; pairing prevents an unknown caller from using the bridge until allowed. The app treats returned page content as untrusted. Cookies, session tokens, and clearance values are never sent to wLib; the manifest requests no `tabs` or `cookies`. The tab fallback finds F95 tabs through the `f95zone.to` host permission and runs only the allowlisted fetch in them.

## OAuth Callback Pages

The same server also serves the desktop sync sign-in redirects:

- **`GET /dropbox/callback`** forwards `code` and `state` to `complete_dropbox_callback`.
- **`GET /gdrive/callback`** forwards them to `complete_gdrive_callback`.

Each returns a small static HTML completion page with `Cache-Control: no-store` and `Content-Security-Policy: default-src 'none'`; success is HTTP 200 and a failed or expired sign-in is HTTP 400. These are top-level browser redirects for the OAuth flow, not extension API calls: they do not add CORS headers, and the normal single-`Host` check still applies. Configure the exact redirect URLs `http://127.0.0.1:8183/dropbox/callback` and `http://127.0.0.1:8183/gdrive/callback` in the provider consoles.

## Installed Extension Files

The bundled manifest is `1.2.13` and targets wLib `0.4.8`.

Extension version `1.1.0` adds the fetch-fallback bridge: `host_permissions` gains `https://f95zone.to/*` and `permissions` gains `alarms` (manifest `storage, alarms`; hosts `http://localhost:8183/*`, `https://f95zone.to/*`; no `tabs` or `cookies`). It keeps a WebSocket to `GET /api/bridge`, sends `hello` with `can_fetch` from `permissions.contains`, pings every 20 seconds, and reconnects on a 1-minute alarm with 5–60 second backoff. Firefox shows a host-permission prompt when updating from `1.0.x` to `1.1.0`; declining leaves `can_fetch=false`, so fallback reports `unavailable` with no errors on F95 pages. `MIN_APP_VERSION` stays `0.3.9`: older apps return 404 for `/api/bridge` and the extension backs off, with all other behavior unchanged.

Extension version `1.0.12` updates itself without manual steps. In Chromium browsers the background worker reloads the unpacked extension once per synced version after a library check reports a newer `extension_version`, so the next F95Zone page runs the new files. In Firefox the manifest declares `browser_specific_settings.gecko.update_url` (`https://github.com/kirin-3/wLib/releases/latest/download/updates.json`) and each tagged release publishes the signed XPI plus an `updates.json` manifest, so Firefox installs new versions on its own update schedule. `1.0.12` also introduced an update warning when the reachable app is older than its `MIN_APP_VERSION` floor (`0.3.9` originally, raised to `0.4.6` in extension `1.2.5`): the thread widget shows "Your wLib app is out of date. Some actions may not work." while keeping the buttons enabled. A response with no `app_version` counts as outdated; an unreachable app shows no warning.

Extension version `1.0.7` adds non-numeric title versions: after numeric/chapter/bare-v parsing fails, titles with at least two brackets use the trimmed first bracket as version. `Game [Final] [Dev]` sends version `Final` and developer `Dev`; a single developer bracket does not supply a version. The backend uses the same fallback and string comparison for updates. Release packaging requires a newly signed Firefox XPI.

The scraper's `_extract_version_from_title` mirrors these title rules exactly (both parsers are tested against `tests/version_title_cases.json`), so a version stored by the extension is not later reported as an update. Hyphenated releases such as `[v1.0.0-beta2]` are kept whole. 1.0.7 also sends `X-wLib-Extension` on open and add requests and reports wLib's `{"success": false}` replies as failures instead of success.

The packaged extension files used by browsers live in the resolved app-data folder (`~/.local/share/wLib/extension/` by default on Linux, `%LOCALAPPDATA%\wLib\extension` on Windows). **Open Extension Folder** resolves the actual location, including any `WLIB_DATA_DIR` or `XDG_DATA_HOME` override:

- `chrome/`: unpacked extension directory for Chromium-based browsers
- `firefox/wLib.xpi`: signed Firefox archive in release builds, or an unsigned generated fallback in local development builds

wLib updates that directory automatically on startup when the bundled manifest version changes, the bundled signed Firefox archive changes, or the installed files are missing. The Chromium folder is replaced with a staged swap instead of deleting the whole extension directory: new files are copied into `chrome.new` (with its manifest patched there), then `chrome` is renamed to `chrome.old` and `chrome.new` to `chrome` before `chrome.old` is removed, so the live path never holds a partially copied folder. Leftover `chrome.new`/`chrome.old` folders from an interrupted sync are removed at the start of the next sync. The Firefox XPI is written through a temporary file and moved into place. Release builds copy the signed Firefox XPI bundled with the app; local development builds generate an unsigned fallback XPI when no signed artifact is bundled. The app also exposes the same sync path through **Open Extension Folder**, and the frontend shows a startup toast when extension files were refreshed naming the new version: Chromium browsers pick it up on the next F95Zone page and Firefox updates automatically.

Firefox users install release XPIs through **Add-ons and themes** using **Install Add-on From File**. The manifest requires Firefox 140 or newer (142 on Android). The `about:debugging` temporary add-on flow is only relevant to local development and is not the normal release installation path.

On Windows, Chromium-based browsers load `%LOCALAPPDATA%\wLib\extension\chrome` as an unpacked extension, and Firefox installs `%LOCALAPPDATA%\wLib\extension\firefox\wLib.xpi`. MSI and portable ZIP builds synchronize to the same user-data folder. Keep wLib running for the extension to reach `127.0.0.1:8183`; the endpoint and security checks are identical on Linux and Windows.

## Thread Widget Behavior

On F95 thread pages, the content script injects a floating widget near the upper-right viewport edge by default using rem-based offsets instead of anchoring it into the page rails.

- The expanded widget shows the wLib logo, side and collapse controls, `Add to wLib` and `Wishlist` for an unsaved thread, or `Open in wLib` for a saved thread. It shows `Check again` after a failed check and displays action feedback, including when a game is already in wLib.
- When the thread already matches a library entry, the widget also renders a non-interactive status line beneath the action. An exe-less game says `Wishlisted`; an installed game shows its play status.
- The side arrow moves the widget between edges. Collapse compresses it into a logo button in the bottom corner on the current side; expanding reverses that transition. Side and collapse state are saved locally, shared across F95 and supported store pages, and updated live in open tabs.
- The latest-alpha page continues to reuse `GET /api/check` for its `In wLib` tile badges, but it only reads the `exists` field and ignores `playStatus`. Answers are cached per thread for the page's lifetime; a failed check (wLib closed or not ready) is not cached and is retried on a timer that backs off from 5 s to 60 s.
- Reduced-motion preferences are respected by shortening the collapse/expand transition to an effectively instant state change.

## Widget Connection Errors

The extension background script sends `{ success: false, failure, error, detail }` when a wLib request fails. The F95 and store widgets display `error`. The background console logs `detail`, which carries the original error name and message, including the HTTP status for a wLib server error.

- `offline` means the browser could not reach wLib. The widget asks the user to open wLib or reload the extension if wLib is already open. Connection failures log at debug level only.
- `timeout` means the request exceeded its time limit. The widget asks the user to try again.
- `foreign` means the answer on port 8183 could not be parsed as JSON. The widget says another program answered.
- `server` means wLib returned an HTTP error or `success: false`. The widget shows wLib's error text or asks the user to check the wLib log if the response has no error text.

The background logs timeouts, foreign responses, and server failures at warning level. Unexpected exceptions use the offline message and log at warning level. Wishlist replies also retain `error_code` and `attached`. If an extension reload invalidates the page's connection, the widget asks the user to refresh the page.

## First-post store link capture

**`POST /api/thread-links`** accepts `{url, links: [{source, id, url}]}` for a
library game's F95 thread. It uses the same origin rules and 1 MB body limit
as Wishlist. Success is HTTP 200; a missing game is 404, an invalid request is
400, a body over 1 MB is 413, a foreign origin is 403, and a database still initializing after 8
seconds at startup is 503. It replaces only links with `f95_post`
origin. An empty visible list removes those rows; the content script sends
nothing when the page still hides its links. This route never fetches F95.

Add and Wishlist reject duplicates by exact F95 or matchable store identity.
When exactly one matching game has no F95 thread, adding that thread attaches
it and starts the normal metadata fetch. Wishlist returns HTTP 409 with
`error_code: duplicate_url` and `attached: true` so the widget confirms that
the game is already saved. Ambiguous matches never attach or create a game.

## Optional store sites

Extension `1.2.0` requires wLib `0.4.2` and adds the `scripting` permission.
DLsite (`https://www.dlsite.com/*`), VNDB (`https://vndb.org/*`) and Ryuugames
(`https://www.ryuugames.com/*`) are **optional_host_permissions**. They are
absent from required hosts and static content-script matches. The toolbar
action opens `options.html`, with a permission switch for each site. Declining
a prompt resets its switch; turning it off revokes access.

The background registers `wlib-dlsite`, `wlib-vndb` and `wlib-ryuugames` scripts
only for granted sites and reconciles them on startup, update and permission
changes. Widgets appear only on work/VN/post pages, offer Open or Add/Wishlist,
show wishlisted and outdated-app states, and offer Retry when wLib is offline.
Page data is sent only to the local app; page scripts fetch no other pages.

### Download resolution bridge messages

Extensions implementing download resolution send `can_resolve: true` in their
`hello` message (protocol 2). The app sends `{ "type": "resolve", "id":
"...", "url": "https://f95zone.to/dl/…", "host": "mega" }`. Progress is
`{ "type": "resolve_progress", "id": "...", "stage": "opened" }` or
`stage: "needs_user"`. The final response is `{ "type": "resolve_result",
"id": "...", "ok": true, "url": "...", "host": "..." }`; failures use
`ok: false` and `error`, and a missing optional site permission is indicated by
`needs_permission`. Buzzheavier results also include `share_url` for the file request's Referer. The app sends `{ "type": "resolve_cancel", "id": "..." }` to cancel a pending resolve. Addresses retain their fragments. Resolve requests are
serialized and time out after 30 seconds, extended to five minutes when user
interaction is needed.

The LewdCorner downloads switch requests the optional `webRequest` permission and
is available while LewdCorner site access is enabled. The extension reports
`can_resolve_lc` in `hello` and `status` when it holds both permissions. LC resolves
share the two-second LC request gap when they start. The accepted URL is
`https://lewdcorner.com/masked/out?…`. The resolve window comes forward for the
user to pass the human check. After the user completes it, the extension can
press the page's own Continue button. It reads the redirect from
`/masked/continue` in the resolve tab, or the page's continue link if verification
is unnecessary. The Buzzheavier share page continues in that same resolve with
its own optional site permission.

For thread parsing, wLib reads the visible link's `href`. The parser never reads
`data-proxy-href` or requests `/proxy.php`. Verification answers, cookies, and
mirror addresses are excluded from persistent app data and logs.

## LewdCorner

Enable LewdCorner in the extension options to grant the optional site permission. The extension reads the current thread page and sends page data to the paired local wLib server. The latest-updates page checks its tiles in batches as they appear.

The paired `/api/check` and `/api/open` routes accept a canonical LC thread URL or a slug URL with a page, post, query, or fragment suffix. For example:

```http
GET /api/check?url=https%3A%2F%2Flewdcorner.com%2Fthreads%2Fgame.5583%2Fpost-123%23post-123
GET /api/open?url=https%3A%2F%2Flewdcorner.com%2Fthreads%2F5583%2F
```

Send the same URL in the JSON body for `POST /api/add` or `POST /api/wishlist`:

```json
{"url":"https://lewdcorner.com/threads/5583/","title":"Game","prefixes":[{"name":"Other","id":29},{"name":"Ren'Py","id":9}],"version":"0.5"}
```

The extension sends title prefixes as plain name strings in `prefixes`. Extension 1.2.13 and later also sends `prefix_ids`, a parallel list with each id read from the label link's `?prefix_id=<id>` (or `null`). The names stay strings so an older wLib still reads them; wLib ignores an id list whose length does not match. LC has two prefixes named "Other": id 22 is the engine and id 29 is the render prefix, which never sets the engine. A prefix without an id is matched by name only. The `wlib-extension-add` payload carries `engine`, `thread_completed`, `thread_onhold` and `thread_abandoned` from these prefixes. Without page prefixes, `thread_onhold`, `thread_abandoned` and `thread_states_seen_at` come together from the catalog entry.

A check can return `attach_lc_available: true` for an existing F95 partner. To attach the thread, send this body to `POST /api/wishlist`:

```json
{"action":"attach_lc","url":"https://lewdcorner.com/threads/5583/"}
```

A successful attach returns `{"success":true,"attached":true,"id":42}`. An already-owned LC thread returns HTTP 409 with `error_code: "duplicate_url"`. For badges, use `/api/check?batch=1` with repeated `url` parameters. Its `matches` object maps the submitted LC URLs that are already stored in the library to game IDs.
