# Frontend (Vue 3)

The user interface for wLib is a modern single-page application built entirely in Vue 3 using the Composition API (`<script setup>`, `ref()`, `computed()`), bundled by Vite, and styled exclusively with TailwindCSS and custom CSS variables.

Source files reside entirely in the `ui/src/` directory.

## Core Services

### `services/api.js`
This module is the backbone of frontend-to-backend communication. Because the UI is hosted inside `pywebview`, standard browser APIs like `fetch()` or `XMLHttpRequest` cannot natively communicate with the Python backend functions directly.
- **The Proxy `ApiService`**: Exposes Javascript wrapper methods that seamlessly return Promises resolving from `window.pywebview.api`.
- **Mock Fallback**: During rapid frontend prototyping, a developer might open `http://localhost:5173` in a standard desktop browser like Chrome. `ApiService` auto-detects if `window.pywebview` is injected. If missing, it immediately substitutes API calls with hardcoded mock data, preventing application crashes and allowing UI iteration without running the Python server.

### State & Routing
wLib uses standard `vue-router` to handle navigation between primary views:
- **Library View**: Displays the grid/list of known games.
- **Update View**: Tracks games that have remote F95Zone updates available.
- **Settings View**: Modifies launch preferences, backend settings, and theme options.

State is typically managed locally within components or shared via lightweight Composition API composables (`src/composables/`), as the application does not rely heavily on global stores like Pinia.

## Backend-to-Frontend Communication

While the UI relies on Promises to pull data from Python, the backend frequently pushes asynchronous background events (e.g. Scraper progress, Extension injection) via pywebview’s `evaluate_js` function.

To capture these, the Vue frontend attaches to standard `window.addEventListener` DOM events:
- **`wlib-playtime-tick`**: Emitted when a game process stops, updating the local UI without requiring a full page reload.
- **`wlib-extension-open`**: Triggered when the browser extension commands the app to focus.
- **`wlib-extension-add`**: Fired when the extension sends payload data. Vue handles this by catching the event and proactively un-hiding the "Add Game" modal, prepopulating the input fields with the provided payload (`title`, `f95_url`, `tags`).

## Styling Convention

We use **TailwindCSS** for primary utility-class styling combined with custom CSS variables (`--color-surface`, `--color-accent`) defined in `index.css`. This enables instantaneous, performant Dark/Light theme switching by merely toggling a `dark` class on the root HTML body node.
