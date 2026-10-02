# Frontend (Vue 3 + TypeScript)

The wLib UI is a Vue 3 SPA built with the Composition API, TypeScript, and Vite.

- Vue SFCs use `<script setup lang="ts">`.
- Type checking is enforced with `vue-tsc` (`npm run typecheck`).
- Production bundles are built with Vite (`npm run build`).
- Source files live in `ui/src/`.

## Core Entry Points

- `ui/src/main.ts`: app bootstrap and plugin registration.
- `ui/src/router/index.ts`: route definitions.
- `ui/src/services/api.ts`: the only frontend-to-backend bridge.

## Backend Bridge (`services/api.ts`)

The UI runs inside PyWebView and calls backend methods via `window.pywebview.api`.

- **Typed API surface**: `ApiService` exposes typed methods and shared response interfaces used by views/components.
- **Launch mode typing**: `LaunchMode` is shared through `ui/src/utils/launchMode.ts` and the API bridge, including `rpgmaker_linux` and Linux-only `custom`. Custom Command relabels the arguments field as Command, explains `%command%`, and hides Wine/Proton controls.
- **Launch target typing**: `LaunchTarget` is part of `GameRecord`; `ApiService` mirrors backend methods for listing, creating, updating, deleting, and reordering additional game launch targets.
- **Library migration typing**: `LibraryBackupSection`, export options, inspect responses, warnings, and import result interfaces mirror the backend JSON migration API.
- **Mock fallback**: when `window.pywebview` is unavailable (for browser-only UI work at `http://localhost:5173`), API calls return structured mock responses to keep the app functional.
- **Startup extension status**: `App.vue` reads startup sync status so the UI can notify users when extension files were refreshed.
- **Platform capabilities**: `getPlatformCapabilities()` is cached once per desktop session. Conservative fallback capabilities expose only Auto Detect and hide platform-specific mutation actions if the backend contract cannot be loaded.
- **Running Games**: `getRunningGames()` seeds Library's running IDs on mount and reconciles successful launches. Cards in every layout and Game Detail replace Play with Stop; target choices are hidden while running. `stopGame()` requests termination, and the final playtime tick clears state even with zero seconds. HTML launches remain untracked.
- **Runner Selection**: Game Detail shows Runner (Wine / Proton) independently of the custom prefix toggle, preserving undiscovered stored paths as custom options. The Settings global runner field uses a native datalist while accepting free text.
- **Custom Statuses**: Settings adds/removes `custom_play_statuses`, validates names inline, and confirms the number of affected games before removal. Filters combine canonical choices, Settings names, then values found on games. Detail chips and badges use `IconTag` plus `ui-status-tone-custom`; compact cards still omit status metadata. Custom filters persist across navigation.
- **URM**: Settings selects `urm_rpa_path` through `browseUrmFile()`. Detail uses `getUrmStatus()` and `setUrmInstalled()` for the Ren'Py-only toggle, with a Settings hint if no valid source exists.
- **Browser Testing**: Mocks persist games under `wlib-mock-games` and settings under `wlib-mock-settings`. Set `wlib-mock-platform` to `linux` and reload to exercise Linux controls and sample runner suggestions; the default remains conservative. Running/URM mock state lasts for the browser session. Mock Ren'Py detection uses engine metadata because no filesystem is available.

Windows capabilities offer Auto Detect and native Windows targets. The shared launch-mode UI retains an imported Linux mode with an **Unavailable on this platform** label so users can switch it to Auto Detect; saving other fields preserves hidden per-game runtime values. Extension paths come from the backend and use Windows separators when appropriate.

Always route backend calls through `ui/src/services/api.ts`; do not call `window.pywebview.api` directly from view components.

## State & Routing

wLib uses `vue-router` for top-level views:

- **Library View**: game browsing, filtering, sorting, quick launch, add/edit modals, per-game launch mode selection, and conditional launch-target selection for multi-part games.
- **Updates View**: single and bulk update checks plus app release checks.
- **Settings View**: platform-aware launcher/runtime settings and scraper controls. Windows hides Wine/Proton, prefix, Wayland, RPGMaker Linux runner, and Cheat Engine actions while preserving imported values; Linux retains the full runtime UI.
- **Import / Export View**: top-level left-navigation route for JSON library/settings migration.
- **Extension View**: extension service status and folder shortcuts.

State is mostly local to components and composables; the app intentionally avoids a heavyweight global store.

## Backend-to-Frontend Events

Backend background tasks push updates into the UI using webview JS evaluation. The frontend listens via DOM events:

- **`wlib-playtime-tick`**: Includes `gameId`, interval `delta` seconds, and `isFinal`; final events clear running state as well as recording playtime
- **`wlib-extension-open`**: Emitted when the browser extension requests to open a game in wLib; includes the F95Zone URL
- **`wlib-extension-add`**: Emitted when the browser extension queues a new game to add; includes scraped metadata (title, URL, engine, tags)
- **`wlib-refresh-library`**: Emitted by frontend workflows such as backup import when the Library view should reload game data from the backend

Keep these event names stable unless backend and frontend are updated together.

## Styling

The UI uses Tailwind utility classes plus CSS variables in `ui/src/style.css` for theming. Dark/light mode behavior should remain compatible across all updated components.
