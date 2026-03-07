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
- **Mock fallback**: when `window.pywebview` is unavailable (for browser-only UI work at `http://localhost:5173`), API calls return structured mock responses to keep the app functional.
- **Startup extension status**: `App.vue` reads startup sync status so the UI can notify users when extension files were refreshed.

Always route backend calls through `ui/src/services/api.ts`; do not call `window.pywebview.api` directly from view components.

## State & Routing

wLib uses `vue-router` for top-level views:

- **Library View**: game browsing, filtering, sorting, quick launch, add/edit modals.
- **Updates View**: single and bulk update checks plus app release checks.
- **Settings View**: launcher/runtime settings, dependency install status, scraper session controls.
- **Extension View**: extension service status and folder shortcuts.

State is mostly local to components and composables; the app intentionally avoids a heavyweight global store.

## Backend-to-Frontend Events

Backend background tasks push updates into the UI using webview JS evaluation. The frontend listens via DOM events:

- `wlib-playtime-tick`
- `wlib-extension-open`
- `wlib-extension-add`

Keep these event names stable unless backend and frontend are updated together.

## Styling

The UI uses Tailwind utility classes plus CSS variables in `ui/src/style.css` for theming. Dark/light mode behavior should remain compatible across all updated components.
