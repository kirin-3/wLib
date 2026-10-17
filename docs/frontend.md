# Frontend (Vue 3 + TypeScript)

## Library sync and backup encryption

`SyncView.vue` (the `/sync` route, its own left-navigation entry) hosts `LibrarySyncPanel.vue`. It offers None/Folder/Dropbox/Google Drive locations, the directory picker or browser sign-in (Connect Dropbox or Connect Google Drive, disabled with the backend's reason when `dropbox_available` or `gdrive_available` is false), disconnect, Sync now, last sync/device/error status, and the external privacy-policy link. Browser previews explicitly label mock data; `wlib-mock-sync` holds the mock status and can select `off`, `idle`, `syncing`, `needs_choice`, `needs_passphrase`, or `error` for UI checks.

All backend access stays in `ui/src/services/api.ts`, with typed `LibrarySyncStatus`, `LibrarySyncState`, and `LibrarySyncChoice`. The panel polls status, avoids stale poll responses overwriting actions, and shows first-connection and passphrase dialogs. Dialogs support Escape, trapped keyboard focus, and return focus to the opener. Cancel leaves sync paused, with a button to reopen the prompt.

Encryption asks for passphrase confirmation and warns that a forgotten passphrase cannot be recovered. Manual export has the same option; manual import shows a password field on `passphrase_required`, inspects again after entry, and forwards the passphrase when applying. Passwords stay in temporary component state and are cleared after success or cancellation. Mock encrypted imports use a filename containing `encrypted` and passphrase `mock-passphrase`, strictly for browser previews. Theme variables support both light and dark modes; panel/dialog controls reflow on narrow screens.

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
- **Auto-mode Wine controls**: on Linux, Game Detail hides the custom prefix, runner, RTP installer, and Cheat Engine controls in Auto Detect when the default executable and every launch target pass `isClearlyNativeTarget()` (`.sh`, `.jar`, `.html`, `.htm`, mirroring `launcher.execute_host_native`). The `hideWineControlsForAuto` computed only affects visibility; `isNativeLaunchMode` and the save paths are untouched, so stored prefix and runner values survive. Ambiguous targets (no extension, `.x86_64`) keep the controls visible.
- **Launch target typing**: `LaunchTarget` is part of `GameRecord`; `ApiService` mirrors backend methods for listing, creating, updating, deleting, and reordering additional game launch targets.
- **Library migration typing**: `LibraryBackupSection`, export options, inspect responses, warnings, and import result interfaces mirror the backend JSON migration API.
- **Mock fallback**: when `window.pywebview` is unavailable (for browser-only UI work at `http://localhost:5173`), API calls return structured mock responses to keep the app functional. Every bridge method the views call has a mock: actions that need no host report success, runtime/download actions report why they need the desktop app, and status payloads carry the same fields the desktop backend returns (`cancelling`, `created_ids`, `deleted`, `connecting`, …). `open_in_browser` opens http(s) URLs with `window.open`, and the mocked auto-check frequency persists in `localStorage`.
- **Startup extension status**: `App.vue` reads startup sync status so the UI can notify users when extension files were refreshed.
- **Platform capabilities**: `getPlatformCapabilities()` is cached once per desktop session. Conservative fallback capabilities expose only Auto Detect and hide platform-specific mutation actions if the backend contract cannot be loaded.
- **Library update indicators**: card, list, and compact layouts show the same read-only `.update-pill` (up arrow plus latest version, with an `sr-only` "Update available:" prefix); clicking it behaves like clicking the card. Marking a game as updated is the explicit **Mark as updated to <version>** item in the right-click / Shift+F10 actions menu, shown only when an update is available (`runContextAction("mark-updated")`). The per-card `.update-overlay-btn` appears only on `.game-card:hover`, `:focus-within`, or while a check runs (`:disabled`).
- **Finished version**: the game detail Info tab shows an editable **Finished version** input next to **Version** (placeholder "Not finished"; empty means not finished). Saving sends `finished_version` with the rest of the form. Clicking the **Completed** status chip quick-saves `play_status`; when the backend auto-fills the finished version, the response carries `finished_version`, and the modal applies it to the input and the saved-snapshot baseline so the form stays clean and a later full save keeps the value. The library's **New since finished** smart collection lists games whose installed version differs from the finished version after normalization (`hasNewSinceFinished` in `utils/libraryGames.ts`); it matches "differs", not "is newer", and persists like the other collections.
- **Library list view**: cells are single-line; engine and status cells truncate with a `title` tooltip. `.library-table-wrap` is a container query context that hides `.col-engine` and then `.col-last-played` as it narrows, so there is no horizontal scroll at 1200×800 with filters open. Games without a cover get a placeholder thumbnail so titles align.
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
- **Import / Export View**: top-level left-navigation route for JSON library/settings migration. `ui/src/views/LibraryBackupView.vue` adds a "From another app" tab that imports from F95Checker, Atlas, or XLibrary through `detectExternalLibrary`, `inspectExternalLibrary`, `importExternalLibrary`, and `browseExternalLibraryFile` (typed in `services/api.ts` with browser mocks): source picker, detected path or checked locations, preview counts with an Only-fill-missing/Overwrite toggle and section checkboxes, and a result summary linking to `/updates` while the follow-up metadata check runs.
- **Sync View**: top-level left-navigation route for library sync settings and status.
- **Extension View**: extension service status and folder shortcuts.

State is mostly local to components and composables; the app intentionally avoids a heavyweight global store.

## Game screenshot gallery

`GameRecord.screenshots?: string[]` carries remote screenshot URLs from the thread starter post. Browser mocks use data-URI SVG placeholders of different aspect ratios; one mock thread game keeps no screenshots to exercise the empty state.
- **Overview strip** (`GameDetailModal.vue`): shown only for games with an F95 URL. A fixed-height (`6rem`) horizontally scrolling row of lazy thumbnail buttons in stored order; thumbnails that fail to load are hidden. Thumbnail *i* opens the lightbox at `i + (hasCover ? 1 : 0)`. Thread games without screenshots show a hint plus a **Check thread now** button that calls the existing single-game update check (disabled while a check runs). Games without a thread URL show neither the strip nor the hint.
- **Lightbox** (`ui/src/components/ScreenshotLightbox.vue`, `<script setup lang="ts">`): props `images: string[]` and `startIndex: number | null` (`null` means closed), emits `close`. Teleported to `body` above the modal and registered with `useModalKeyboard`, so Esc closes only the lightbox, focus stays trapped inside it, and focus returns to the opener on close. The gallery is `[displayed cover if loaded, ...screenshots]`; the cover tile is a labelled **View cover and screenshots** button opening at 0 when the gallery is non-empty. The lightbox handles ←/→ on its own container with wrap-around (the modal tab-list never sees them), hides prev/next buttons for a single image, shows a position indicator (`X of N`), fits the image within 90% of the window keeping aspect ratio (scaling small images up and large ones down, refitted on resize), and shows an unavailable placeholder on load error. A backdrop click closes it; an image click does not. The backdrop is a fixed dark scrim in both themes with explicit light-on-dark controls; transitions are removed under `.motion-off`.

## Game notes and attachments

The game detail modal has a **Notes** tab after Overview, Launch, and Info (`detailTabs` in `GameDetailModal.vue`; arrow-key navigation wraps, so no new keyboard code). It holds one markdown document per game plus that game's PDF attachments.

- **Save model**: `GameRecord.progress?: string` is the Notes markdown. The modal loads `notes` from `g.progress` when the game ID changes, includes `progress` in `formSnapshot()` and the `save()` payload, and tracks it with the same Unsaved changes indicator and discard prompt as the other fields. Attaching or removing PDFs takes effect immediately without Save Changes. `notesEditing` (rendered vs editor) resets wherever `activeTab` resets to Overview.
- **Rendering**: `notesHtml` runs `marked.parse(..., { async: false })` followed by `DOMPurify.sanitize`, the same as UpdatesView, inside a scoped `.notes-markdown` block that uses the existing theme variables. An Edit/Preview toggle switches between the rendered view and a monospace textarea; empty notes show a hint with a Start writing action. Reopening the modal always shows the rendered view.
- **Links**: a single delegated click handler on the rendered container calls `preventDefault()` on any `<a>` click. `getSafeNotesLinkUrl()` in `ui/src/utils/notesLinks.ts` returns the href only for absolute `http:`/`https:` URLs; safe links open through `api.openInBrowser` with `notifyError` on failure, every other link does nothing, and the wLib window never navigates.
- **Attachments**: `GameAttachment` (`{name, size}`) with `listGameAttachments`, `addGameAttachment`, `openGameAttachment`, `removeGameAttachment`, and `browsePdfFile` wrappers in `services/api.ts`. The tab shows an Attach PDF button (busy while pending), names with human-readable sizes, open-on-click, and Remove with `confirm()`. Errors go through `notifyError`; an `attachment_missing` error refreshes the list. The list loads when the Notes tab is selected or the game changes. Browser mocks keep an in-memory list per game under `wlib-mock-attachments`, return `/mock/guide.pdf` for the picker, and report success for open.

## Backend-to-Frontend Events

Backend background tasks push updates into the UI using webview JS evaluation. The frontend listens via DOM events:

- **`wlib-playtime-tick`**: Includes `gameId`, interval `delta` seconds, and `isFinal`; final events clear running state as well as recording playtime
- **`wlib-extension-open`**: Emitted when the browser extension requests to open a game in wLib; includes the F95Zone URL
- **`wlib-extension-add`**: Emitted when the browser extension queues a new game to add; includes scraped metadata (title, URL, engine, tags)
- **`wlib-refresh-library`**: Emitted by frontend workflows such as backup import when the Library view should reload game data from the backend
- **`wlib-install-archive`**: Dispatched by Library/Updates views, the game detail modal, and download-offer toasts to open the global install dialog; detail is `{ gameId?, archivePath? }` (see `ui/src/utils/installArchive.ts`)

Keep these event names stable unless backend and frontend are updated together.

## Install From Archive

- **Modal**: `ui/src/components/modals/InstallArchiveModal.vue` is hosted once in `App.vue` so toasts and any view can open it with game context. It offers an archive picker, editable title/version/creator/URL, a destination preview from `inspect_archive`, a ticked "Remove old version" checkbox showing the exact folder (disabled with the guard's reason when not removable), an unticked "Delete archive after install" checkbox, progress polled from `get_archive_install_status()`, and a result summary including unmoved launch targets. With no library folder set it prompts to choose one first. A new request (another game or archive) arriving while the dialog is open re-seeds it, cancelling any inspect; the header, inspect and install all read the game the form was seeded for. A request that lands during an install waits: if the install fails the dialog re-seeds from it (the failure is reported as a toast), if it succeeds the result stays shown.
- **Entry points**: "Install from archive" beside "Import Folder" in `LibraryView.vue`, "Install update" beside "Mark updated" in `UpdatesView.vue` (with an "Archive found" badge fed by `findDownloadOffers()`), and "Install update" in the game detail toolbar when an update is available. Successful installs refresh via `wlib-refresh-library`.
- **Settings**: the "Library & Downloads" section edits `library_folder` (browse), `library_folder_pattern` (token hint plus a live example), `downloads_folder` (browse, with a "folder does not exist" note), and the detection toggle; all persist through the existing save bar.
- **Toast actions**: `Toast` carries an optional `{ label, run }` action rendered as a button in `App.vue`; action toasts never auto-dismiss. Covered by `ui/src/utils/toast.test.ts` (`npm run test:unit`).
- **Download scan triggers**: `App.vue` scans on mount, after an update check completes, and on window `focus`, throttled to one scan per minute in the UI. Each new offer shows one action toast ("Install update" opens the modal with the archive preselected; dismissing the toast dismisses the offer).

## Styling

The UI uses Tailwind utility classes plus CSS variables in `ui/src/style.css` for theming. Dark/light mode behavior should remain compatible across all updated components.

### Page layout

- Every top-level view opens with the shared header: `<h2 class="ui-page-heading ...">` containing an icon with `ui-page-heading-icon`, then a subtitle `<p>` with `border-left: 2px solid var(--brand)`. Copy the markup from `SettingsView.vue`.
- Sections are cards (`--bg-surface` background, `--border` border, `0.75rem` radius, `1.5rem` padding) with an `ui-section-heading` / `ui-section-icon` heading. Inputs and buttons copy the Settings tokens (`.settings-input`, `.settings-btn`) per view, and the main action uses `--brand` / `--on-brand`.
- Import / Export option rows use `backup-check-row` with `backup-option-icon`, a `backup-option-copy` title and description, and the checkbox on the right. Link the checkbox with `aria-labelledby` / `aria-describedby` when the title is not the whole label text.
- Link Settings fields to their visible label with `<label for>` (or `aria-labelledby`), and helper text with `aria-describedby`. Example placeholders start with "e.g.".
- `App.vue` scrolls `<main>` (the scroll container) to the top in the route `<transition>` `before-enter` hook, so a new page always opens at the top, with animations on or off.
- `GameDetailModal` has a fixed height (`h-[min(90vh,52rem)]`). The tab area scrolls inside it, so switching tabs doesn't move the dialog.

### Accent color

- `--brand` is the accent fill/text color and `--on-brand` is the label color for text and icons on accent fills. Use `color: var(--on-brand)` on any `background: var(--brand)` control; never `text-white` or `--text-inverse`.
- `--brand-glow`, `--brand-glow-strong`, `--shadow-brand`, and `--border-active` derive from `--brand` with `color-mix()` in the theme blocks. `--brand-deep` is the hand-picked magenta value by default and `color-mix(in oklab, var(--brand) 45%, black)` for any `data-accent`.
- Presets are `:root[data-accent="violet|blue|cyan|rose|orange"]` blocks (one per theme via `:root.light[...]`) that only set `--brand` and `--on-brand`. Magenta is the default and has no attribute. A custom color sets `data-accent="custom"` plus inline `--brand` / `--on-brand` (label color picked by WCAG contrast) and is applied unadjusted.
- The choice is stored in localStorage under `wlib-accent` (a preset name or `#rrggbb`), applied before mount by `utils/accentPreference.ts`, and is not part of library backup or sync.
- Play/launch actions use the fixed green `--play` / `--on-play` through the shared `.ui-play-btn` class and never follow the accent. Other primary actions use the accent fill. Native checkboxes and radios follow the accent through the global `accent-color: var(--brand)`.
- `--text-muted` is kept at 4.5:1 or better against surface and raised backgrounds in both themes.

### Text size

- Settings Appearance offers Small (14 px), Default (16 px), Large (18 px), and Extra large (20 px) via `utils/textSizePreference.ts` (localStorage key `wlib-text-size`), applied to `document.documentElement.style.fontSize` before mount by `main.ts`. Unknown values fall back to Default.
- Because layout uses `rem` units, the choice scales text and spacing together. The former `text-[10px]` / `text-[11px]` badges and counts are `text-[0.625rem]` / `text-[0.6875rem]` so small labels scale too.
- The choice is per-device and is not included in backup or sync.

### Library collections

- The library header is followed by a collection tab strip (`role="tablist"` with arrow-key navigation): All first, then each user collection in Settings order with its game count, then a `+` button with inline create. The strip scrolls sideways. Right-clicking a tab offers Rename (inline) and Delete (with confirmation; games stay in the library).
- The selected tab (`activeCollection` in `utils/libraryViewState.ts`, `""` means All) filters the library before search, quick filters, and sort. Counts ignore other filters. Deleting the open tab falls back to All; unknown saved names also fall back to All.
- Membership is edited from the game right-click menu (Collections checklist view with toggles, New collection that also adds the game, and Back) and from the Collections chip picker in `GameDetailModal.vue` (saved with the modal's Save; closing without saving changes nothing).
- Settings lists Collections next to custom play statuses with add, inline rename, move up/down, and delete with confirmation; the tab order follows Settings.
- The filters pane heading is "Quick filters" (not "Smart Collections") so "collection" only means user-made groups.

### Saved filter presets

- The filters pane top "Saved filters" block lists presets (`{name, filterCollection, filterStatuses, filterInstallState, filterEngines, filterTags, sortBy, sortDir}`) from the `library_filter_presets` setting. Clicking applies the preset; the delete button removes it without changing current filters; the name field saves the current filters and sort, asking before replacing an existing name.
- Applying replaces the built-in collection filter, statuses, install state, engines, tags, sort field and direction, after normalising through `normalizeFilterPreset` (unknown engines/tags dropped). It keeps the collection tab, layout, and search text. Search text is never stored in a preset.

### Path field indicators

- `components/PathStatus.vue` (`path`, `kind` props) debounces 300 ms, calls `api.checkPath`, ignores stale responses, and renders an icon plus text ("Found" or the backend reason) under the input. Empty fields show nothing: optional ones may stay empty, and required ones are checked when their form is submitted. Saving stays possible while a field shows not found.
- Kinds (`core/api.py:check_path`): `file` wants an existing file, `dir` an existing folder, `parent` an existing folder for a file wLib will create, `runner` an existing file when the value has separators or a command found on PATH otherwise. `~` is expanded.

| Field | Kind |
|---|---|
| Add Game and Game Detail executable, launch target paths | file |
| Install archive, backup import file, external library file | file |
| Settings library folder, Import Folder source | dir |
| Settings downloads folder | dir |
| Export destination | parent |
| Settings Wine prefix, per-game custom prefix | dir |
| Settings Proton / Wine path | runner |
| Settings RPGMaker runner | file |
