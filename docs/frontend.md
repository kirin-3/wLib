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
- **Launch mode typing**: `LaunchMode` is shared through `ui/src/utils/launchMode.ts` and the API bridge so Add Game, Game Detail, quick launch, and modal launch send the same runtime mode values, including the optional `rpgmaker_linux` external-runner mode.
- **Launch target typing**: `LaunchTarget` is part of `GameRecord`; `ApiService` mirrors backend methods for listing, creating, updating, deleting, and reordering additional game launch targets.
- **Library migration typing**: `LibraryBackupSection`, export options, inspect responses, warnings, and import result interfaces mirror the backend JSON migration API.
- **Mock fallback**: when `window.pywebview` is unavailable (for browser-only UI work at `http://localhost:5173`), API calls return structured mock responses to keep the app functional.
- **Startup extension status**: `App.vue` reads startup sync status so the UI can notify users when extension files were refreshed.
- **Platform capabilities**: `getPlatformCapabilities()` is cached once per desktop session. Conservative fallback capabilities expose only Auto Detect and hide platform-specific mutation actions if the backend contract cannot be loaded.

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

- **`wlib-playtime-tick`**: Emitted when playtime tracking updates for a running game; includes game ID and accumulated seconds
- **`wlib-extension-open`**: Emitted when the browser extension requests to open a game in wLib; includes the F95Zone URL
- **`wlib-extension-add`**: Emitted when the browser extension queues a new game to add; includes scraped metadata (title, URL, engine, tags)
- **`wlib-refresh-library`**: Emitted by frontend workflows such as backup import when the Library view should reload game data from the backend

Keep these event names stable unless backend and frontend are updated together.

## Styling

The UI uses Tailwind utility classes plus CSS variables in `ui/src/style.css` for theming. Dark/light mode behavior should remain compatible across all updated components.
