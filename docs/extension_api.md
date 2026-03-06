# Browser Extension API

wLib bundles an optional companion web extension that integrates deeply with F95Zone in standard desktop browsers (Firefox, Chrome). To facilitate instantaneous data transfer without requiring cloud synchronization, wLib runs a background REST server.

## The Local Daemon
Inside `main.py`, a daemon thread launches `start_extension_server()`, binding `http.server.HTTPServer` to `127.0.0.1:8183`. The handler processes incoming HTTP operations and forwards signals directly into the Python main application context.

## CORS Restrictions (Security Model)
Because the daemon binds to `localhost`, any website visited by the user *could* theoretically perform background requests against it.
To prevent malicious sites from sniffing or mutating the local database, `ExtensionRequestHandler._get_allowed_origin` rigidly blocks incoming requests unless the `Origin` header matches an exact whitelist:
- `chrome-extension://*`
- `moz-extension://*`
- `http://localhost:5173` (Development only)

*Any standard domain (e.g., `https://google.com`) executing `fetch('http://localhost:8183/')` will immediately encounter a CORS rejection.*

## REST API Endpoints

### 1. Check if Game Exists 
**`GET /api/check?url={f95_url}`**
Allows the extension to decorate an F95Zone page based on ownership.
- **Request:** `http://localhost:8183/api/check?url=https://f95zone.to/threads/example.123/`
- **Response:**
  ```json
  {
    "exists": true,
    "version": "1.0.5"
  }
  ```

### 2. Focus the App & Open Game
**`GET /api/open?url={f95_url}`**
Requests that the OS brings the wLib window to the foreground and opens the modal to the specified game.
- **Action:** Triggers pywebview window activation. Emits JavaScript custom event `wlib-extension-open`.
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
