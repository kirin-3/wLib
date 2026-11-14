# Frontend (Vue 3 + TypeScript)

## Tag colors and diagnostics

`utils/tagColors.ts` owns the shared reactive color map, trimmed lowercase lookup,
one-time loading, optimistic per-tag saves with rollback, and reloads on
`wlib-refresh-library`. `LibraryView.vue` and `components/modals/GameDetailModal.vue`
use `components/TagColorPicker.vue`. Right-click or Shift+F10 opens the picker;
the detail modal also offers a labeled tag-name button. Arrow keys move between
swatches, Enter/Space selects, and Escape returns focus to the pill. Each current
option exposes `aria-checked`; selected library filters keep `aria-pressed`, their
color, a check icon, and a stronger border.

`style.css` defines `--tag-<swatch>-bg`, `-text`, and `-border` in both themes for
red, orange, yellow, green, teal, blue, purple, and pink. Shared `data-tag-color`
rules apply them to pills and picker buttons; neutral tags retain their styling.
The contrast test reads the actual tokens and checks 4.5:1 text contrast and 3:1
border contrast. Colors are independent of the app accent.

`views/DiagnosticsView.vue` is routed at `/diagnostics`, reached from the Settings
footer with a return link, and leaves main navigation unchanged. App, Renderer,
Storage and library, Extension, and Tools sections use definition lists. Basic
and tool calls start in parallel; missing values, checking states, and retryable
errors are explicit. Refresh reruns both requests. Open log folder reports errors
through the existing toast system. Copy report uses backend-redacted text and
`utils/copyText.ts`: Clipboard API (with a 1.5-second timeout for webviews that
leave permission requests pending), legacy `execCommand`, then a selected read-only
textarea for manual copying. Browser previews identify unavailable desktop data.

Design follows wLib's existing typography, theme tokens, and compact utility
layout (ENERGY 1 / RHYTHM 1 / MOTION 1). Definition lists group facts for bug
reports; spacing separates their subjects. The accent singles out Copy report.
Tag hues encode user assignments; checks and borders distinguish filter selection
without relying on hue. Only the anchored picker uses elevation, to separate its
controls from library content. No new artwork or decorative motion is needed.

## Library sync and backup encryption

`SyncView.vue` (the `/sync` route, its own left-navigation entry) hosts `LibrarySyncPanel.vue`. It offers None/Folder/Dropbox/Google Drive locations, the directory picker or browser sign-in (Connect Dropbox or Connect Google Drive, disabled with the backend's reason when `dropbox_available` or `gdrive_available` is false), disconnect, Sync now, Pause sync and Resume, last sync/device/error status, and the external privacy-policy link. For `offline` ("Sync location is offline") the reason shows as muted text, not an alert. For `paused` ("Sync is paused") a line says changes on this machine are not synced until Resume; Resume (`sync-primary`) replaces Pause sync (`sync-btn`) while `status.paused` is true, and Sync now is disabled. Browser previews explicitly label mock data; `wlib-mock-sync` holds the mock status and can select `off`, `idle`, `syncing`, `needs_choice`, `needs_passphrase`, `error`, `offline`, or `paused` (with `paused: true`) for UI checks; the mock `pause_sync`/`resume_sync` change it like the backend.

All backend access stays in `ui/src/services/api.ts`, with typed `LibrarySyncStatus`, `LibrarySyncState`, and `LibrarySyncChoice`, plus `pauseSync()` and `resumeSync()`.

`App.vue` polls sync status every 15 seconds and passes it to `trackSyncProblem()` (`ui/src/utils/syncProblem.ts`, tested in `syncProblem.test.ts`). `error`, `needs_choice` and `needs_passphrase` mark the Sync nav item and show one "Library sync" error toast per problem (`state:last_error`); `syncing` keeps the previous problem, so a retry that fails the same way shows nothing new. `idle`, `off`, `offline` and `paused` close the open sync toast and clear the marker; the nav item title then says "Sync location is offline" or "Sync is paused", with no dot. While `/sync` is open the problem is recorded but no toast shows. The panel polls status, avoids stale poll responses overwriting actions, and shows first-connection and passphrase dialogs. Dialogs support Escape, trapped keyboard focus, and return focus to the opener. Cancel leaves sync paused, with a button to reopen the prompt.

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
- **Timestamp displays**: Game Detail's Overview footer shows **Exe modified** from `getExecutableModifiedTime(path)` and **Thread updated** from the stored thread edit/check timestamps. Loading, never-checked, and unavailable states are explicit; a missing executable path is not queried.
- **Library migration typing**: `LibraryBackupSection`, export options, inspect responses, warnings, and import result interfaces mirror the backend JSON migration API.
- **Mock fallback**: when `window.pywebview` is unavailable (for browser-only UI work at `http://localhost:5173`), API calls return structured mock responses to keep the app functional. The mocks live in `services/mock/`: `invoke()` loads `services/mock/backend.ts` on demand with a dynamic `import()` on the first call that has no bridge method, so Vite emits them as a separate chunk. Every bridge method the views call has a mock: actions that need no host report success, runtime/download actions report why they need the desktop app, and status payloads carry the same fields the desktop backend returns (`cancelling`, `created_ids`, `deleted`, `connecting`, …). `open_in_browser` opens http(s) URLs with `window.open`, and the mocked auto-check frequency persists in `localStorage`.
- **Startup extension status**: `App.vue` reads startup sync status so the UI can notify users when extension files were refreshed.
- **Platform capabilities**: `getPlatformCapabilities()` is cached once per desktop session. Conservative fallback capabilities expose only Auto Detect and hide platform-specific mutation actions if the backend contract cannot be loaded.
- **Library update indicators**: card, list, compact, and banner layouts show the same read-only `.update-pill` (up arrow plus latest version, with an `sr-only` "Update available:" prefix); clicking it behaves like clicking the card. Marking a game as updated is the explicit **Mark as updated to <version>** item in the right-click / Shift+F10 actions menu, shown only when an update is available (`runContextAction("mark-updated")`). The per-card `.update-overlay-btn` appears only on `.game-card:hover`, `:focus-within`, while a check runs (`:disabled`), or under `@media (hover: none)`.
- **Finished version**: the game detail Info tab shows an editable **Finished version** input next to **Version** (placeholder "Not finished"; empty means not finished). Saving sends `finished_version` with the rest of the form. Choosing **Completed** in the header play status menu quick-saves `play_status`; when the backend auto-fills the finished version, the response carries `finished_version`, and the modal applies it to the input and the saved-snapshot baseline so the form stays clean and a later full save keeps the value. The library's **New since finished** smart collection lists games whose installed version differs from the finished version after normalization (`hasNewSinceFinished` in `utils/libraryGames.ts`); it matches "differs", not "is newer", and persists like the other collections.
- **Journal tab**: Game Detail groups sessions by normalized version, newest played first, displaying the most recent raw spelling, total time, session count, last played date, and each session's start date/time and duration. Empty versions appear as Unknown version. Before journal accounts for total playtime outside recorded sessions. Loading, retryable error, and empty states are explicit. Only the newest open session of a currently running game shows In progress; tick events refresh it without closing the modal.
- **Unplayed update**: this remembered built-in collection contains installed games with two known, different normalized installed/last-played versions. A read-only Unplayed badge appears in card, list, compact, and banner layouts alongside any available-update indicator. Five minutes of accumulated session time on a version updates the last-played field and clears the badge live. Never-played and wishlisted games are excluded; New since finished keeps its existing meaning.
- **Skip controls**: the Library game actions menu offers Skip version <latest version> or Stop skipping <skipped version>. In Game Detail, Skip version is in the update split button's "More update actions" menu; after a skip the split button is replaced by "Version <version> skipped" and a plain "Stop skipping <version>" button, which receives focus. A skipped game shows no update badge in the modal. The shared availability helper hides it from indicators, counts, Updates available, and the Updates view, including Mark as updated. Checks still run, a different latest release becomes available, and changing the installed version clears the skip. Both actions confirm with toasts and preserve play status.
- **Desktop notifications**: the Updates page's **Settings** button opens the "Update settings" dialog (`components/UpdateSettingsDialog.vue`), which places a Desktop notifications checkbox under the frequency buttons on Linux, hidden on Windows through platform capabilities. It defaults on and follows general settings through backup/sync. App reports focus, blur, and visibility changes through the typed bridge. Browser mocks support sessions via `wlib-mock-play-sessions`, skip changes, focus reporting, and the persisted toggle.
- **Game Detail header**: the title row ends with an icon-only **Favorite** toggle (heart, `aria-pressed`, saves at once through `toggleFavorite`/`saveQuickFields` and never marks the form unsaved) and **More actions**. The badge row under the developer holds the engine, version and update badges, the collection chips and "+ Collection". The next row holds the play status `MenuButton` (accessible name "Play status: <status>", radio items with each status icon, canonical then custom; a choice saves at once through `setPlayStatus`) and, when an update is available and not skipped, the update split `MenuButton`: its main part is **Download update** for a game with an F95 thread (disabled while the game runs or its download is unfinished) and **Install update** otherwise; its "More update actions" menu lists Install update (when it is not the main part) and Skip version. The toolbar row below keeps Open in Browser, Check Updates, the download status and, for a wishlisted game, Install from archive. The Overview tab has no Play Status block. The header stays fixed while the tab area scrolls, and its menus open inside the modal without clipping at a 600px-high window.
- **Library list view**: cells are single-line; engine and status cells truncate with a `title` tooltip. `.library-table-wrap` is a container query context that hides `.col-engine` and then `.col-last-played` as it narrows, so there is no horizontal scroll at 1200×800 with filters open. Games without a cover get an initials thumbnail at the same 32px size (see [Library cards and rows](#library-cards-and-rows)) so titles align.
- **Running Games**: `getRunningGames()` seeds Library's running IDs on mount and reconciles successful launches. Cards in every layout and the Game Detail footer replace Play with Stop; target choices are hidden while running. `stopGame()` requests termination, and the final playtime tick clears state even with zero seconds. HTML launches remain untracked.
- **Runner Selection**: Game Detail shows Runner (Wine / Proton) independently of the custom prefix toggle, preserving undiscovered stored paths as custom options. The Settings global runner field uses a native datalist while accepting free text; choices include `GE-Proton` (latest) and `UMU-Proton` alongside system Wine and Steam/Lutris Proton builds.
- **Proton and umu bridge**: `services/api.ts` mirrors `getUmuStatus`, `getUmuDownloadStatus`, `getUmuRuntimeStatus`, `downloadUmu`, `prepareUmuRuntime`, and `cancelUmuRuntimePrepare` with typed responses and browser mocks.
- **Banner view**: the fourth layout (`layoutMode === "banner"`, persisted like the others) reuses compact's image-only tile branch through the `isImageTileLayout` flag. Tiles are 2:1 with a centered cover in columns of at least `420px * var(--card-scale)` (315px to 630px across the card-size slider, `.library-cards--banner`). The overlay gradient, title, and Play/launch-target controls (`.banner-reveal`) sit at `opacity: 0` at rest and show on `.game-card:hover`, `:focus-within`, `.is-running`, an open launch-target menu (`.is-menu-open`), or `@media (hover: none)`. Opacity keeps Play clickable and focusable while hidden. Tiles show no rating, developer, version, playtime, status, or update-check button; the card `aria-label` carries the title.
- **Custom Statuses**: Settings adds/removes `custom_play_statuses`, validates names inline, and confirms the number of affected games before removal. Filters combine canonical choices, Settings names, then values found on games. Game Detail lists custom statuses after the canonical ones in its play status menu; menu items and badges use `IconTag` plus `ui-status-tone-custom`; compact cards still omit status metadata. Custom filters persist across navigation.
- **URM**: Settings selects `urm_rpa_path` through `browseUrmFile()`. The Tools tab uses `getUrmStatus()` (through `getGameTools()`) and `setUrmInstalled()` for the Ren'Py-only toggle, with a Settings hint if no valid source exists.
- **Browser Testing**: Mocks persist games under `wlib-mock-games` and settings under `wlib-mock-settings`. Set `wlib-mock-platform` to `linux` and reload to exercise Linux controls and sample runner suggestions; the default remains conservative. Running/URM mock state lasts for the browser session. Mock Ren'Py detection uses engine metadata because no filesystem is available.

Windows capabilities offer Auto Detect and native Windows targets. The shared launch-mode UI retains an imported Linux mode with an **Unavailable on this platform** label so users can switch it to Auto Detect; saving other fields preserves hidden per-game runtime values. Extension paths come from the backend and use Windows separators when appropriate.

Always route backend calls through `ui/src/services/api.ts`; do not call `window.pywebview.api` directly from view components.

## Confirmation dialogs

Use `confirmAction({ title, message, confirmLabel, danger })` from `utils/confirm` for actions that cannot be undone. It returns a `Promise<boolean>` and queues concurrent requests. Supply an action-specific label and set `danger` for destructive changes. The global dialog provides keyboard and controller navigation, defaults to Cancel, and follows the current theme. Do not introduce native `confirm`, `alert`, or `prompt` dialogs.

## State & Routing

wLib uses `vue-router` for top-level views:

- **Library View**: game browsing, filtering, sorting, quick launch, add/edit modals, per-game launch mode selection, and conditional launch-target selection for multi-part games.
- **Updates View**: single and bulk update checks plus app release checks. The header holds **Settings** (the "Update settings" dialog: Weekly/Monthly/Off with `aria-pressed`, and Desktop notifications) and **Check All Games**, which becomes **Cancel** while a check runs; under the subtitle sits "Last checked: <date> · <frequency>". One `role="status"` block under the header shows progress ("Checking: <game>", count, bar), the outcome, a status error with "Try again", or the first-run hint, and is hidden when empty. Sections follow in this order: the wLib release card (only when newer), "Updates Available" (or "No updates available."), then "Last Check Results". Each row shows checkbox, cover, the title with `installed → latest` at the same size (latest in `--success-text`), the developer and the download status link, and one split `MenuButton` (`data-update-row-action`). Its main part is "Install found archive" when `findDownloadOffers()` found one, else "Download update" for games with an F95 thread, else "Install from file"; "More update actions" lists the other install or download action, Mark updated, Skip version <latest>, Re-check and Open F95Zone thread, each disabled item with its reason. "Install from file" calls `installUpdate(game, false)` so the found archive is not preselected. When a row leaves while focus is in it, focus goes to the main part of the row now in its place, else the previous row, else Check All Games.
- **Downloads View**: every in-app download (file host or RPDL torrent) in one ordered list, after Updates in the sidebar. See [Downloads page](#downloads-page).
- **Settings View**: general behavior (debug logging, save-backup retention, custom statuses, collections), library and download paths, appearance, platform-aware Wine/Proton or Windows environment controls, the RPGMaker Linux runner, and URM. Windows hides Wine/Proton, prefix, Wayland, RPGMaker Linux runner, and Cheat Engine actions while preserving imported values; Linux retains the full runtime UI. Settings text and path fields save on blur, Enter, or Browse; list edits and toggles save immediately. Each edited field shows its own saving, saved, or error feedback, and pending edits flush when leaving. A searchable section list provides navigation and stable links. Each section's heading block (the section's first child) is `position: sticky` inside its card with the card background and a bottom border, so it stays at the top of `<main>` until the next card pushes it away; the rule is an unscoped style in `SettingsView.vue` so it also reaches sections from child components. Keyboard Shortcuts is the last section (`#keyboard-shortcuts`, keywords "keys hotkeys shortcut"). Automatic update frequency and desktop notifications live in the Updates view's Settings dialog.
- **Import / Export View**: top-level left-navigation route for JSON library/settings migration. `ui/src/views/LibraryBackupView.vue` adds a "From another app" tab that imports from F95Checker, Atlas, XLibrary, f69, or Playnite through `detectExternalLibrary`, `inspectExternalLibrary`, `importExternalLibrary`, and `browseExternalLibraryFile` (typed in `services/api.ts` with browser mocks): source picker, detected path or checked locations, preview counts with an Only-fill-missing/Overwrite toggle and section checkboxes, and a result summary linking to `/updates` while the follow-up metadata check runs.
- **Sync View**: top-level left-navigation route for library sync settings and status.
- **Extension View**: extension service status and folder shortcuts. Like the other pages (except Library), it scrolls in `<main>`, not in its own column, so the scroll bar is at the window's right edge.
- **Diagnostics View**: secondary route from the Settings footer for copying a redacted report and opening the log folder.

State is mostly local to components and composables; the app intentionally avoids a heavyweight global store.

**App sidebar** (`App.vue`): the Collapse navigation control sits at the end of the logo row when the sidebar is expanded, and Expand navigation sits above the logo when it is collapsed; the TV layout hides it. The footer shows the theme switch (visible label "Light Mode"/"Dark Mode", accessible name "Switch to … Mode"), GitHub ("View Source on GitHub") and Buy Me a Coffee as labeled `nav-link` rows when expanded and as icons with tooltips when collapsed, followed by the app version in mono text in both states. When a newer release is out, the version is a link to `/updates` that names the new version. The sidebar scrolls when the window is too short for the nav and footer.

## Shared games list
`composables/useGamesStore.ts` exports `gamesStore`, the one library list for `App.vue`, the Library, Updates and Settings views (`utils/gamesStore.ts` holds the logic). Views call `gamesStore.refresh()` and read `gamesStore.games`; do not call `api.getGames()` in a view.

- `refresh()` calls made in the same tick share one load. A call made during a load gets one more load after it. It resolves `false` on failure and keeps the old list.
- A game whose data did not change keeps its object across loads, and an identical reload keeps the same array, so its card does not render again.
- `games` is a `shallowRef`. Change a game locally with `gamesStore.patch(id, fields)`; an edit of the object in place does not update the page.
- The Library puts the first 120 cards or rows in the page (`RENDER_BATCH`) and adds 120 more when the user scrolls near the end. A filter, sort, search or layout change starts again from 120. Counts, selection and bulk actions use the full filtered list.

## Undo and bulk actions

Mark as updated, Skip version, Stop skipping, library removal, and bulk changes offer Undo for eight seconds. Hovering a toast or focusing one of its controls holds it; the timer restarts on release. Ctrl+Z invokes the newest Undo outside editable fields and dialogs. In controller mode, a new Undo offer receives focus so its timer stays held until focus leaves. Removal hides games immediately and deletes them only after the offer ends; closing or reloading does not flush pending removals.

In the Library, Ctrl-click toggles a game and Shift-click selects a visible range. The Select button and game menu support selection without modifiers. The bulk bar (`BulkActionBar.vue`) groups its actions into menus plus a separate Remove button: **Status** (Set status, Favorite, Unfavorite), **Collections** (Add to / Remove from collection), **Updates** (Check for updates, Mark as updated, Skip version), **Runtime** (Linux only: Set Wine/Proton, Set launch mode, Sandbox on, Sandbox off), **Metadata** (Add tag, Remove tag, Set engine), and **Maintenance** (Back up saves, Refresh F95 info, Uninstall, Move). Field changes run through `runBulk` and share one skip/changed toast and one Undo; Controller Back closes an open menu before it clears the selection. Set Wine/Proton (`BulkWineDialog.vue`) gives every idle, unsandboxed, non-native game one shared prefix and/or runner (shared-prefix games run one at a time). Sandbox off asks for confirmation and never touches network blocking. Tag edits send `tags_user_edited: true` so F95 backfill stops refilling them; only Refresh F95 info changes such tags, and it keeps the user's changes. Back up saves runs one game at a time with a single summary toast and no Undo. Refresh F95 info (disabled with "Selected games have no F95 thread" or while an update check runs) starts the shared update check with `checkAllUpdates(ids, { refreshInfo: true })` and toasts "Refreshing F95 info for N games; M skipped.". The backend refreshes each game's remote cover URL, F95 rating and F95 tags (merged with the user's tag changes). When the run finishes, also after Cancel, `App.vue` reads `changedInfoOfFinishedRefresh(status)` (`utils/updateCheck.ts`: results with `info_changed`) and raises one "F95 info changed for N games." toast whose Undo calls `updateGame(id, previous)` for each game, restoring the cover URL, rating, read time, tags and last F95 tag list that changed. Filters prune hidden games from the selection. In Updates, row checkboxes and Select all enable Mark updated, Skip version, and Re-check for selected rows.

## Uninstall and move

The Game Detail "More actions" menu and the bulk Maintenance menu offer "Uninstall..." and "Move..." for installed games. Both dialogs ask the backend for a plan (`planGameUninstall`, `planGameMove`) and show each game's root folder with a "Change..." button (`browseDirectory`; the backend checks the new folder again), the reason for each game that cannot go, and, for a move, the size and the target. The rules for the default of "Back up saves first", the disabled confirm button, the move progress and the summary text live in `utils/gameFiles.ts`.

- `UninstallDialog.vue` shows "Back up saves first" when a listed game has a saves folder, ticked when a saves folder is inside its root, and "No save folder found" per game otherwise. "Also remove from library" is unticked by default, so the game stays as Wishlist. On confirm, `utils/pendingUninstalls.ts` changes the game in the list at once (wishlisted or hidden) and shows an Undo toast. `uninstallGames` runs once, when the toast expires; a failed game comes back and one error toast names how many failed and the first reason. Nothing is stored before then, so a reload shows the game as it was.
- `MoveGameDialog.vue` lists the library folders and "Other folder..." as the destination, then calls `startGameMove`. Move has no Undo.
- `composables/useGameMoves.ts` polls `getMoveStatus` while the Library is open: every 500 ms while moves run and every 5 s otherwise, through `startPoller` (slower while the window is inactive). `moveStateFor(id)` drives the "Moving N%" bar and the "Waiting" label on `GameCard.vue` and `GameRow.vue`, where Play is disabled. The queue summary shows as one toast.
- `components/library/MoveQueuePanel.vue` sits in a sticky stack above the bulk bar, after the last row, with "Moving X of Y", the title, the bytes, the speed, "Cancel" and "Cancel all".
- While a game is moving, the Game Detail modal shows its executable, prefix and cover fields as read-only.

## Built-in player video dialog
- `launchGameFast` in `LibraryView.vue` opens `components/WebMediaDialog.vue` when a launch returns `code: "web_media_unsupported"` (with `media: {count, ffmpeg}`) or `"web_media_converting"`. The dialog has three steps (`utils/webMedia.ts`: `initialStep`): **choices** (the count, "Convert videos", "Use system browser", "Play anyway"; when `ffmpeg.available` is false there is no Convert button and an ffmpeg note shows `ffmpegProblem()` and `ffmpegInstallHint()`), **confirm** (the "replaced, cannot be undone" warning) and **progress** (done/total, current file, Cancel conversion, Close). It uses `useModalKeyboard`, so Escape, the backdrop and controller Back close it without a launch.
- The parent handles the choices: "Use system browser" saves `html_player: "browser"` through `updateGame`, reloads the games and launches again; "Play anyway" calls `setWebMediaPlayAnyway` and launches again; "Replace and convert" calls `startWebMediaConversion` through the composable and switches the dialog to progress (`showProgress()`).
- `composables/useWebMediaConversion.ts` keeps `webMediaStatus` per game and polls `getWebMediaConversionStatus` every 500 ms while a job runs, also after the dialog closes (`createWebMediaWatcher` in `utils/webMedia.ts`). When the job ends it shows one notification with the converted and failed counts (`summaryText`) and a Play action (not after an error). A `web_media_converting` launch result follows the running job and opens the dialog in progress.
- Browser preview: `services/mock/webMedia.ts`. An HTML game whose title or path contains `h264` reports 12 videos; localStorage `wlib-mock-ffmpeg = "missing"` shows the install hint.

## Game screenshot gallery

The modal reads screenshots live from the catalog, falling back to `GameRecord.screenshots` from the thread starter post. F95 thumbnails use the preview host, fall back to full images on error, and the lightbox uses full images. Browser mocks start with no seeded library; a simulated update check fills a game's missing screenshots with data-URI SVG placeholders so the strip and lightbox can be exercised. Games without a thread URL show neither.
- **Overview strip** (`GameDetailModal.vue`): shown only for games with an F95 URL. A fixed-height (`6rem`) horizontally scrolling row of lazy thumbnail buttons in stored order; thumbnails are hidden if both preview and full image fail. Thumbnail *i* opens the lightbox at `i + (hasCover ? 1 : 0)`. Thread games without screenshots show a note when present in the catalog; otherwise they show a hint plus a **Check thread now** button that calls the existing single-game update check (disabled while a check runs). Games without a thread URL show neither the strip nor the hint.
- **Lightbox** (`ui/src/components/ScreenshotLightbox.vue`, `<script setup lang="ts">`): props `images: string[]` and `startIndex: number | null` (`null` means closed), emits `close`. Teleported to `body` above the modal and registered with `useModalKeyboard`, so Esc closes only the lightbox, focus stays trapped inside it, and focus returns to the opener on close. The gallery is `[displayed cover if loaded, ...screenshots]`; the cover tile is a labelled **View cover and screenshots** button opening at 0 when the gallery is non-empty. The lightbox handles ←/→ on its own container with wrap-around (the modal tab-list never sees them), hides prev/next buttons for a single image, shows a position indicator (`X of N`), fits the image within 90% of the window keeping aspect ratio (scaling small images up and large ones down, refitted on resize), and shows an unavailable placeholder on load error. A backdrop click closes it; an image click does not. The backdrop is a fixed dark scrim in both themes with explicit light-on-dark controls; transitions are removed under `.motion-off`.

## Game saves

The **Saves** section sits at the end of the Launch tab in `GameDetailModal.vue` and replaces the old footer "Find Saves" button, panel and state. It is `components/GameSavesSection.vue` (props `gameId`, `active`).

- **Loading**: it calls `api.getGameSaves(id)` the first time the Launch tab is shown for a game, drops its state when the game changes, ignores stale responses, and reloads from "Search again" and after every change.
- **Folder**: with an override it shows the path with `PathStatus`; without one it lists the detected folders with Open and "Use this", marks the one in use, and says why Backup and Import… are off when several or no folders were found. The game's own folder is listed as a lead only. "Choose folder…" uses `api.browseDirectory`, and "Use detected folder" clears the override. Actions call `setGameSaveFolder`, `backupGameSaves`, `importGameSaves`, `restoreGameSavesBackup` and `openGameSaveBackup`; Open on a folder uses `openFolder`.
- **Backups**: newest first with a readable label, Open and Restore. While a save call runs every action is disabled and a status line shows what is running.
- **Helpers**: `utils/gameSaves.ts` holds `getSaveActionState` (which actions are available and the message when they are not) and `formatBackupLabel`, tested in `gameSaves.test.ts`.
- **Styling**: the section defines its own scoped `.saves-btn` and `.saves-label` because the modal's button styles are scoped to the modal; it uses theme tokens only.
- **Settings**: the General section has "Save backups to keep per game" (`Keep all`, newest 1, 3, 5, 10, 20, or a custom 1–99). It saves through `saveSettings({ save_backup_keep })` as soon as it changes and reverts with an error toast if the save fails.
- **Browser mocks**: `services/mock/gameSaves.ts` keeps a per-game override and backup list in localStorage (`wlib-mock-game-saves`), so the section works in a plain browser.

## Game tools

`GameDetailModal.vue` shows a **Tools** tab (`components/GameToolsTab.vue`, props `gameId`, `engine`, `active`) for Ren'Py, RPG Maker MV/MZ and Unity games. The tab list is a computed list: `loadGameExtras` calls `api.getGameToolSupport(id)` and Tools is added when it reports an engine; if the engine disappears while Tools is active the modal falls back to Overview. The URM toggle, state and handlers moved here from the Launch tab.

- **Loading**: `api.getGameTools(id)` runs the first time the tab is shown for a game; the tab drops its state and polling when the game changes, ignores stale responses, and reloads after every change.
- **Ren'Py**: Developer console switch plus an "Also enable developer mode" checkbox (changing it rewrites the script only while the console is on), the archive list (name, format, size) with a "Where to extract" radio (folder via `api.browseDirectory`, or into the game folder) and Extract, and the URM switch.
- **RPG Maker**: Decrypt (encrypted counts, key source, missing copies) and the "Use decrypted files in-game" switch, which shows why it is disabled.
- **Unity**: The Unity Tools section displays detected build details and installed BepInEx state. Windows games can load versions, install or update, uninstall with optional user-data removal, and restore a backup. It warns about incompatible plugin switches and IL2CPP first-launch requirements; Linux-native Unity games show an unsupported message.
- **Runs**: extraction and decryption block their API call; the tab polls `getGameToolStatus()` every 500 ms and shows a progress bar (placed first in the tab, above the fold) with Cancel (`cancelGameTool()`). Every control is disabled while any tool call runs.
- **Helpers**: `utils/gameTools.ts` holds `formatToolProgress`, `toolProgressPercent`, `getExtractState`, `getDecryptState`, `getUseDecryptedState` (availability plus the reason), `consoleHint`, `keySourceLabel` and `formatToolBytes`, tested in `gameTools.test.ts`.
- **Styling**: scoped `.tools-card`, `.tools-btn` and progress-bar styles with theme tokens only; it shares the modal's fixed size and scrolls inside the tab area.
- **Browser mocks**: `services/mock/backend.ts` infers the engine from the mock game's `engine` label and keeps console, decrypted and switch state for the session. Extract and Decrypt run one file per `MockBackend._mockToolStepMs` so polling sees `done` advance, and Cancel works.

## RPG Maker save editor
The Tools tab of an RPG Maker MV/MZ game has a Save editor card (`GameToolsTab.vue`) listing slots with modified time and play time. Open shows `components/modals/RpgmSaveEditor.vue` (teleported to `body`) with Party, Actors, Switches, Variables and Raw tabs; the Raw tree is `RpgmRawNode.vue`, expanded lazily and capped at 200 rows per node.
- Edits go into a patch map (`utils/rpgmSaveEditor.ts`) keyed by the JSON path of the leaf, so curated and Raw edits of the same value share one entry and the last wins. Level edits are sent first so an explicit experience edit made afterwards still wins.
- Values are validated in the UI (gold 0 to 9,999,999, counts 0 to 9,999, level at least 1, bonuses may be negative); Save stays disabled while any field is invalid.
- A dirty indicator, Discard and a confirm on close protect unsaved edits. Save shows the backup name or the refusal message (game running, save changed since opened, backup failed).
- Browser mocks in `services/mock/rpgm.ts` serve two slots and a small MV-shaped save, and record write calls in `globalThis.__wlibMockRpgmWrites` for checks.

## Game notes and attachments

The game detail modal has a **Notes** tab after Overview, Journal, Launch, and Info (`detailTabs` in `GameDetailModal.vue`; arrow-key navigation wraps, so no new keyboard code). It holds one markdown document per game plus saved web links and that game's PDF, TXT and MD attachments.

- **Save model**: `GameRecord.progress?: string` is the Notes markdown. The modal loads `notes` from `g.progress` when the game ID changes, includes `progress` in `formSnapshot()` and the `save()` payload, and tracks it with the same Unsaved changes indicator and discard prompt as the other fields. Attaching or removing files takes effect immediately without Save Changes. `notesEditing` (rendered vs editor) resets wherever `activeTab` resets to Overview.
- **Rendering**: `notesHtml` runs `marked.parse(..., { async: false })` followed by `DOMPurify.sanitize`, the same as UpdatesView, inside a scoped `.notes-markdown` block that uses the existing theme variables. An Edit/Preview toggle switches between the rendered view and a monospace textarea; empty notes show a hint with a Start writing action. Reopening the modal always shows the rendered view.
- **Saved links**: URL and optional label fields add HTTP(S) links; users can remove them, save with Save Changes, and open them in the system browser. Link edits participate in unsaved-change and discard handling.
- **Rendered links**: a single delegated click handler on the rendered container calls `preventDefault()` on any `<a>` click. `getSafeNotesLinkUrl()` in `ui/src/utils/notesLinks.ts` returns the href only for absolute `http:`/`https:` URLs; safe links open through `api.openInBrowser` with `notifyError` on failure, every other link does nothing, and the wLib window never navigates.
- **Attachments**: `GameAttachment` (`{name, size}`) with `listGameAttachments`, `addGameAttachment`, `openGameAttachment`, `removeGameAttachment`, and `browseAttachmentFile` wrappers in `services/api.ts`. The tab shows an Attach file button (busy while pending), names with human-readable sizes, open-on-click, and Remove with `confirm()`. Errors go through `notifyError`; an `attachment_missing` error refreshes the list. The list loads when the Notes tab is selected or the game changes. Browser mocks persist a per-game list in `localStorage` under `wlib-mock-attachments`, return `/mock/guide.md` for the picker, and report success for open.

## Library heading and toolbar

- **Heading row**: the heading, the accent-bordered subtitle and the **Add game** split button share one row. `.library-header` is a container query context: below `44rem` the subtitle is hidden, so the heading and Add game never wrap; an `sr-only` copy keeps the subtitle for screen readers.
- **Add game**: a split `MenuButton` (`components/MenuButton.vue`). The main part opens Add Game. The menu button, named "More ways to add", lists **Import folder** and **Install from archive**, which open the same dialogs as the old buttons.
- **Toolbar**: one `flex-wrap` row holds the search field (`flex: 1 1 12rem`), then a group with the sort menu and direction button, Filters, Select, the game count, the card size slider and the layout toggle. The group wraps below the search field, and inside itself, before the page could scroll sideways.
- **Sort**: in grid, compact and banner layouts a radio `MenuButton` names the current field and lists A-Z, Newest, Recent, Playtime, F95 rating and My rating. Choosing a field calls `setSortField`, which applies `chooseSortField()` from `utils/libraryViewState.ts`: a new field starts with `defaultSortDir()` (ascending for title, version, play status and engine, otherwise descending); the active field keeps its direction. The direction button reverses `sortDir`, and its accessible name states the current direction. List view hides both and sorts from its column headers through `toggleSort` (`toggleSortState()`). The saved view state format does not change.
- **Select**: has an icon and the same `.library-toolbar-btn` border, height and hover style as Filters; `aria-pressed` is true while select mode is on or games are selected.
- **MenuButton**: plain or split mode, `role="menu"` with `menuitem` or `menuitemradio` items, opens on click, Enter, Space, Down or controller A, moves with Up, Down, Home and End (`utils/menuKeyboard.ts`, wrapping and skipping disabled items), closes on Escape, controller B, Tab or a click outside, and returns focus to its menu button. A disabled item shows its reason. Controller support comes from the `role="menu"` overlay handling in `spatialFocus.ts` and `controllerActions.ts`. Hosts style the trigger with `:deep(.menu-button-trigger)`.

## Library cards and rows

`LibraryView.vue` renders each game through `components/library/GameCard.vue` (grid, compact and banner layouts) and `components/library/GameRow.vue` (the list `<tr>`). A change to one game, such as a selection toggle, a playtime tick, a game starting or stopping, an update-check spinner or a launch-target menu, re-renders only that game's card or row.

Keep it that way:

- Pass primitive props (`selected`, `running`, `menuOpen`, `updating`, `updateBusy`, `selectionMode`, `primary`, `layoutMode`) plus the `game` record. Do not read per-game fields in the `LibraryView` template.
- Bind handlers as method references (`@activate="handleCardClick"`). The components emit with the game (`emit('activate', game, event)`), so the parent never creates a per-game closure that would change the props on every render.
- Pure per-game helpers (`getGamePlayStatusMeta`, `formatPlaytime`, `formatLastPlayed`, `getEffectiveLaunchTargets`, `hasAdditionalLaunchTargets(game, running)`) live in `utils/libraryGames.ts`.
- Keep `.game-card`, `.game-row`, `data-game-id` and `data-controller-primary` on the component roots; controller navigation, selection and scrolling query them.
- **Cover markers**: a favorite shows a heart (`IconHeartFilled`, accessible name "Favorite") in every layout, in the Favorites quick filter and in the Game Detail Favorite toggle. Star icons are only for ratings. In grid layout the F95 rating badge reads "F95 <value>" with no star and has the accessible name "F95 rating <value>".
- **Cover placeholder**: `components/CoverPlaceholder.vue` (prop `title`) is the one placeholder for a game without a cover. It shows up to two initials from `titleInitials()` on the tag swatch from `titleSwatch()` (`utils/coverPlaceholder.ts`), set as `data-tag-color` so it uses the `--tag-bg`/`--tag-text` tokens. The background is a 135° gradient from `--tag-bg` to a 35% mix of `--tag-bg` with `--bg-surface`; `--tag-text` passes 4.5:1 against both ends, so it passes along the whole gradient in both themes. The same title always gets the same swatch. A title with no letters or digits shows the controller icon. The root uses `container-type: size`, so the initials and icon scale with the box. The placeholder is `aria-hidden`, because the title is already named next to it. The parent sets size and corner radius: cards, compact and banner tiles fill the cover area, the list thumbnail is 32px, Updates rows use 40px, and the Game Detail header fills `.game-cover` (it uses the saved `game.title`, so the color does not change while the title is edited).
- Card and row styles are scoped in the components. Rules that depend on the parent (`.library-cards--banner ...`) still match because only the last compound selector gets the component's scope attribute.

## Import Folder

`LibraryView.vue` opens `components/modals/ImportFolderModal.vue` from **Import folder** in the **Add game** menu, next to **Install from archive**. The modal scans a folder with `api.scanGamesFolder(path)`, lists games already in the library separately and disables their checkboxes, and selects every new game by default. The folder input uses `PathStatus` (`dir`) and Enter starts a scan.

`api.importGames(games)` calls `add_game` once per selected row and dispatches `wlib-refresh-library` after any success, so the library refreshes once instead of per game. Titles, versions, and engines are guesses from `core/game_scan.py`; the modal explains that users should check them after importing. Browser mocks use a small fixed scan result.

## Backend-to-Frontend Events

Backend background tasks push updates into the UI using webview JS evaluation. The frontend listens via DOM events:

- **`wlib-playtime-tick`**: Includes `gameId`, interval `delta` seconds, and `isFinal`; final events clear running state as well as recording playtime
- **`wlib-extension-open`**: Emitted when the browser extension requests to open a game in wLib; includes the URL and an optional integer `gameId`; the library opens by ID when present
- **`wlib-extension-add`**: Emitted when the browser extension queues a new game to add; includes scraped metadata (title, URL, engine, tags) and `links: [{source, id, url}]` for store pages, which can omit the F95 URL
- **`wlib-refresh-library`**: Emitted by frontend workflows such as backup import when the Library view should reload game data from the backend
- **`wlib-install-archive`**: Dispatched by Library/Updates views, the game detail modal, and download-offer toasts to open the global install dialog; detail is `{ gameId?, archivePath?, seed? }`; the seed carries `{title, version, creator, engine, f95Url, coverImage, tags, rating}` (see `ui/src/utils/installArchive.ts`)

Keep these event names stable unless backend and frontend are updated together.

## Install From Archive

- **Modal**: `ui/src/components/modals/InstallArchiveModal.vue` is hosted once in `App.vue` so toasts and any view can open it with game context. It offers an archive picker, editable title/version/creator/engine/URL (updates start with the stored engine), an initial destination preview from `inspectArchive`, a ticked "Remove old version" checkbox showing the exact folder (disabled with the guard's reason when not removable), an unticked "Delete archive after install" checkbox, progress polled from `get_archive_install_status()`, and a result summary including unmoved launch targets. With no library folder set it prompts to choose one first. A new request (another game or archive) arriving while the dialog is open re-seeds it, cancelling any inspect; the header, inspect and install all read the game the form was seeded for. A request that lands during an install waits: if the install fails the dialog re-seeds from it (the failure is reported as a toast), if it succeeds the result stays shown.
- **Folder and part choices**: the modal loads `listLibraryFolders()` on open. Multiple folders appear in a dropdown with free space; unavailable entries are disabled. A single folder is plain text. Updates preselect the deepest containing folder unless the user has chosen another; changing folders refreshes the destination. With none configured, browsing saves both the list and default. Games with additional launch targets show a "Part to update" selector with "Default" preselected; changing it re-inspects the archive with `target_id`, updating the old-folder preview. Other targets inside that folder are named in a warning that cleanup keeps the folder if they cannot move. Settings shows `PathStatus` and free space for each folder, disables removing the default while others exist, and removes only the list entry, leaving files/games intact.
- **Live preview and lookup**: edits to title, version, creator, or engine call `previewInstallDestination()` after a 250 ms debounce. Superseded responses are discarded. `engine_pending` shows a note that the engine will be detected from the extracted files. For new, unseeded installs, committing the URL (or opening with a prefilled URL) calls `lookupThreadDetails()`. It fills only untouched fields, carries cover/tags into the install, and shows a non-blocking message on failure. Updates and extension seeds skip this lookup.
- **Extension hand-off**: the Add Game dialog offers "Install from archive" for `action=import`. It captures the current edited fields plus cover, tags, and rating, closes, clears the query, and dispatches `openInstallArchive({seed})`. `App.vue` passes the seed into the install modal; choosing an archive preserves these values and new installs store the supplied metadata immediately.
- **Entry points**: "Install from archive" next to "Import folder" in the Library's **Add game** menu (`LibraryView.vue`), the update split button of each row in `UpdatesView.vue` ("Install found archive" as the main part when `findDownloadOffers()` found one, "Install from file" in the main part or the menu), and "Install update" in the game detail update split button (main part without an F95 thread, otherwise its menu). Successful installs refresh via `wlib-refresh-library`.
- **Settings**: the "Library & Downloads" section edits `library_folders` (add via browse, remove, choose default) and `library_folder` (default), `library_folder_pattern` (token hint plus a live example), `downloads_folder` (browse, with a "folder does not exist" note), the detection toggle, and the four device-local download queue settings: speed limit (`download_limit_kib`, KiB/s for file hosts and torrents together, 0 for no limit), downloads at the same time (`download_max_active`, 1 to 5), "Pause downloads while a game runs" (`download_pause_while_playing`, on by default) and "Pause downloads on battery power" (`download_pause_on_battery`, off by default). The Downloads page shows the same four. Each saves on its own (blur, Enter, Browse, or list add/remove).
- **Toast actions**: `Toast` carries an optional `{ label, run }` action rendered as a button in `App.vue`; action toasts never auto-dismiss. `notify()` and `notifyError()` return the toast id for `dismissToast()`. A toast with the same type, title and text as an open non-Undo toast replaces it (newest position, new action and timer); Undo toasts never replace each other. Covered by `ui/src/utils/toast.test.ts` (`npm run test:unit`).
- **Download scan triggers**: `App.vue` scans on mount, after an update check completes, and on window `focus`, throttled to one scan per minute in the UI. Each new offer shows one action toast ("Install update" opens the modal with the archive preselected; dismissing the toast dismisses the offer).

## Styling

The UI uses Tailwind utility classes plus CSS variables in `ui/src/style.css` for theming. Dark/light mode behavior should remain compatible across all updated components.

### Page layout

- Every top-level view opens with the shared header: `<h2 class="ui-page-heading ...">` containing an icon with `ui-page-heading-icon`, then a subtitle `<p>` with `border-left: 2px solid var(--brand)`. Copy the markup from `SettingsView.vue`.
- Sections are cards (`--bg-surface` background, `--border` border, `0.75rem` radius, `1.5rem` padding) with an `ui-section-heading` / `ui-section-icon` heading. Inputs and buttons copy the Settings tokens (`.settings-input`, `.settings-btn`) per view, and the main action uses `--brand` / `--on-brand`.
- Import / Export option rows use `backup-check-row` with `backup-option-icon`, a `backup-option-copy` title and description, and the checkbox on the right. Link the checkbox with `aria-labelledby` / `aria-describedby` when the title is not the whole label text.
- Link Settings fields to their visible label with `<label for>` (or `aria-labelledby`), and helper text with `aria-describedby`. Example placeholders start with "e.g.".
- `App.vue` scrolls `<main>` (the scroll container) to the top in the route `<transition>` `before-enter` hook, so a new page always opens at the top, with animations on or off.
- `GameDetailModal` has a fixed height (`h-[min(calc(var(--app-vh)*0.9),52rem)]`). The tab area scrolls inside it, so switching tabs doesn't move the dialog.

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

### Interface scale

- Settings Appearance offers 80%–150% in 10% steps through `utils/interfaceScalePreference.ts` (localStorage key `wlib-interface-scale`), defaulting to 100%. `main.ts` loads it before mount; root CSS `zoom: var(--ui-scale, 1)` scales text, spacing, and fixed pixel sizes together.
- Text size remains independent: Interface scale multiplies the chosen root font size. Reset returns Interface scale to 100% without changing Text size. Both choices stay on this device and are excluded from backup, export, and sync.
- New window-sized surfaces must use `--app-vw` / `--app-vh` instead of raw viewport units, because root zoom also scales those units. Measure and clamp popups with viewport coordinates (`getBoundingClientRect()`, `innerWidth/Height`), then use `toCssPx()` once when writing CSS positions or dimensions.

### Motion

- Settings Appearance offers **Animations** on or off via `utils/motionPreference.ts` (localStorage key `wlib-ui-motion`). `main.ts` applies the saved choice before mount by toggling `.motion-off` on the root element.
- Settings Appearance also offers **Animate covers** (`Always`, `On hover`, `Never`; default `On hover`) via `utils/coverAnimation.ts` (localStorage key `wlib-cover-animation`, device-local, not in backup or sync). Library cards, list thumbnails and Updates entries render `CoverImage.vue`: the still card copy with the animated copy on top, and `html[data-cover-animation]` plus CSS decides which plays, so hover never re-renders. Covers stay still while Animations are off or while any game runs (`composables/useRunningGames.ts`). The detail modal and lightbox keep the original cover.
- Turning motion off removes non-essential transitions and hover effects. Loading indicators stay visible. The choice is per-device and is not included in backup or sync.

### Library view preferences

- Card size is saved as `cardSize` (0–4, default 2) in `wlib-library-view-state`, shared by grid, compact, and banner layouts. The toolbar slider is hidden in list view. Invalid or missing values use the default while preserving other preferences. Card size is excluded from filter presets, backup, export, and sync.

### Library collections

- The library header is followed by a collection tab strip (`role="tablist"` with arrow-key navigation): All first, then each user collection in Settings order with its game count, then a `+` button with inline create. The strip scrolls sideways. Right-clicking a tab offers Rename (inline) and Delete (with confirmation; games stay in the library).
- The selected tab (`activeCollection` in `utils/libraryViewState.ts`, `""` means All) filters the library before search, quick filters, and sort. Counts ignore other filters. Deleting the open tab falls back to All; unknown saved names also fall back to All.
- Membership is edited from the game right-click menu (Collections checklist view with toggles, New collection that also adds the game, and Back) and from the Collections chip picker in `GameDetailModal.vue` (saved with the modal's Save; closing without saving changes nothing).
- Settings lists Collections next to custom play statuses with add, inline rename, move up/down, and delete with confirmation; the tab order follows Settings.
- The filters pane heading is "Quick filters" (not "Smart Collections") so "collection" only means user-made groups.
- The Sources section always lists F95Zone, DLsite, VNDB and Ryuugames (`LIBRARY_SOURCES` in `utils/libraryGames.ts`). `gameSources()` gives a game `f95` for a non-empty `f95_url` plus each matching `links[].source`; selected sources match any-of. Stored as keys in `filterSources`.

### Saved filter presets

- The filters pane top "Saved filters" block lists presets (`{name, filterCollection, filterStatuses, filterInstallState, filterSources, filterEngines, filterTags, sortBy, sortDir}`) from the `library_filter_presets` setting. Clicking applies the preset; the delete button removes it without changing current filters; the name field saves the current filters and sort, asking before replacing an existing name.
- Applying replaces the built-in collection filter, statuses, install state, sources, engines, tags, sort field and direction, after normalising through `normalizeFilterPreset` (unknown sources/engines/tags dropped). It keeps the collection tab, layout, and search text. Search text is never stored in a preset.

### Path field indicators

- `components/PathStatus.vue` (`path`, `kind` props) debounces 300 ms, calls `api.checkPath`, ignores stale responses, and renders an icon plus text ("Found" or the backend reason) under the input. Empty fields show nothing: optional ones may stay empty, and required ones are checked when their form is submitted. Saving stays possible while a field shows not found.
- Kinds (`core/api_mixins/library.py:check_path`): `file` wants an existing file, `dir` an existing folder, `parent` an existing folder for a file wLib will create, `runner` an existing file when the value has separators or a command found on PATH otherwise. `~` is expanded.

| Field | Kind |
|---|---|
| Add Game and Game Detail executable, launch target paths | file |
| Install archive, backup import file, external library file | file |
| Settings library folder, Import Folder source | dir |
| Settings downloads folder | dir |
| Export destination | parent |
| Settings Wine prefix, per-game custom prefix | dir |
| Settings Proton / Wine path | runner |
| Settings RPGMaker runner | runner |

## Proton settings (umu-launcher)

Settings includes `UmuSettings.vue` at `/settings#umu` when `supportsLinuxRuntimes`
is true. It displays umu-launcher availability (system binary or wLib download),
detected version, and whether Python 3 is installed on the host. It also reports
Steam Linux Runtime readiness ("Ready" or "Not prepared"), and download /
runtime preparation background job progress with downloaded MiB.

Preparation failure reasons are classified with user-friendly messages for
insufficient disk space, lack of internet connection, missing host Python, or
general errors, with a collapsible "Details" disclosure showing the raw error
log tail.

Actions include:
- **Download umu**: downloads the verified zipapp for non-system installations.
- **Prepare runtime** / **Update runtime**: runs `umu-run createprefix` in the
  background for the selected global runner.
- **Cancel**: aborts in-progress runtime preparation.
- **Recheck**: refreshes umu and runtime status.

Status is polled every 1 second while a download or runtime preparation job runs.
`services/api.ts` mirrors `getUmuStatus`, `getUmuDownloadStatus`,
`getUmuRuntimeStatus`, `downloadUmu`, `prepareUmuRuntime`, and
`cancelUmuRuntimePrepare` with typed responses and browser mocks.

## Built-in web player controls

Settings → General has "Play HTML games in the built-in player" (`html_builtin_player`, on by default). Game Detail's Launch tab shows an "Opens in" select (Use Settings default / Built-in player / System browser, stored as the game's `html_player`) only while the selected target is `.html` or `.htm`; `.swf` targets always use the player. `utils/webPlayer.ts` mirrors the backend resolver (`core/web_launch.py`): `resolveWebPlayer` returns `builtin`, `browser` or `null` (not a web launch, for example Wine/Proton or Custom Command on Linux), and `sandboxSwitchState` and `networkSwitchState` take that result, so only system-browser launches disable "Run in sandbox" and "No internet". When a launch result carries `notice: "html_player_first_use"`, `LibraryView.vue` shows a one-time toast (`launchNoticeMessage`) saying the game now uses the built-in player, that browser saves do not carry over, and how to switch the game back to System browser. The browser mock in `services/mock/backend.ts` tracks built-in player games as running and returns the notice once.

Settings → General also has "Web player DevTools (F12)" (`web_player_devtools`, on by default, row `general-static-6`, exported and synced). A change applies to the next player launch; an open player keeps the value it started with. While it is on, F12 in the player shows or hides a Chromium DevTools pane docked on the right (about 60/40), and the context menu's "Inspect" shows the pane and inspects the clicked element. The pane is made on the first F12 and later only hidden, so its state stays until the window closes; the DevTools close button hides only the pane. F11 and a game's fullscreen request act on the whole window, so the pane keeps its visibility in and out of fullscreen, and F12 still works there. While the setting is off, the player binds no F12 (the game receives it) and the context menu has no "Inspect".

## Sandbox controls

Settings includes `SandboxSettings.vue` at `/settings#sandbox`. It shows tool
readiness, Recheck, the distro install command with Copy, a host-specific Install
action with polled progress, and the official download page. Unusable tools show
the failure reason without an Install button. Windows also offers a validated
custom `Start.exe` picker and Clear. "Sandbox new games" saves immediately;
"Enable for all games" and "Disable for all games" ask for confirmation and
report the changed count. Running games keep their current mode.

Game Detail's Launch tab saves "Run in sandbox" with the other launch settings.
Games that open in the system browser disable the switch with an explanation. A missing tool leaves the
stored preference visible and editable, with a link to Settings → Sandbox.
"Open sandbox folder" opens the game's persistent storage. Launch failures
`sandbox_missing` and `sandbox_unusable` offer a Settings action in the error
toast; `prefix_in_use` preserves the backend explanation naming the running game.
`utils/sandbox.ts` centralizes status, switch-state and error-code presentation.
All controls use the existing theme variables and work in light and dark themes.

Game Detail's Launch tab also offers a "No internet" switch (`block_network`) to
disconnect network access for the game, preserved across backup and sync. On
Linux it runs in a private network namespace using bubblewrap (`--unshare-net`
in the sandbox, or a lightweight root-loopback wrapper when unsandboxed). On
Windows it requires Sandboxie. Launch failure codes formatted by
`utils/sandbox.ts` include `network_block_unavailable` (missing bubblewrap on
Linux), `network_block_needs_sandbox` (Windows without Sandboxie), and
`proton_missing_offline` (Proton runner codename not resolved to an installed build
before an offline launch).

`services/api.ts` mirrors `get_sandbox_status`, `recheck_sandbox_status`,
`get_sandbox_install_info`, `install_sandbox_tool`, `get_sandbox_install_status`,
`set_sandbox_for_all_games` and `open_game_sandbox_folder`, with typed responses
and browser mocks. Mock new games follow the persisted default and bulk actions
report actual changes; host installation and folder opening remain unavailable
in a normal browser.


## Game catalog

Settings includes `CatalogSettings.vue` after Sandbox, at `/settings#catalog`.
It reads `api.getCatalogStatus()` and shows the on/off toggle (saved right away
with `saveSettings({catalog_enabled})`; turning it off asks for confirmation
because the local copy is deleted), the build time, last check, last error, and
a per-source table of entry counts and update times from
`utils/catalog.ts`. "Check now" calls `api.checkCatalogNow()` and the block polls
status every 1.5 s while `checking` is true. It also carries the VNDB / ODbL
attribution. In a normal browser both calls return mock data, and the mock
follows the stored `catalog_enabled` setting.

## Custom tools

The Linux-only `companion_tools` capability gates all three surfaces. Settings includes `CompanionToolsSettings.vue` before Sandbox, at `/settings#custom-tools`. Its list supports add/edit/delete with confirmation, inline errors, Wine/Native executable pickers using the existing runner-file fallback, Command lines, and a Show on all games default. Help explains `%game_dir%`, `%game_exe%`, leading `VAR=value`, quoting and direct command execution. Changes save immediately and use theme variables for light and dark mode.

Game Detail's Launch tab mounts `GameCompanionTools.vue` whenever the modal opens. Each checkbox saves its override immediately, shows a muted default label when inherited, and offers Reset for explicit overrides. An empty list links to Settings > Custom tools and closes the modal. These saves are independent of the modal's other launch settings.

Library context menus fetch launchable tools only for running games. Supported visible tools appear after Play/Stop with dividers, share the menu's keyboard navigation, and close the menu on successful launch. Failure uses the existing launch-error toast and names the tool. Requests are discarded if the menu changes or closes; stopped games render no tool entries.

The typed bridge mirrors all seven companion methods, with persistent browser mock definitions and per-game overrides. Set `wlib-mock-platform` to `linux` in localStorage to expose the Linux controls; mocks capture Wine support when a game starts and enforce duplicate launches per game.

## Store links

`GameRecord.links` carries portable `{source, id, url}` metadata. The Add Game
modal accepts links from `wlib-extension-add`, shows them read-only with site
labels, and forwards them through `api.addGame`. A store import can be saved
without an executable, creating a wishlist entry. Query helpers in
`utils/storeLinks.ts` preserve link-only imports and validate `gameId` for
`wlib-extension-open`.

The Overview tab of Game Detail lists store links as site-labelled buttons
that call `api.openInBrowser`. Open in Browser and the library's thread
context action use the F95 URL when available, otherwise the first store
link; the action is hidden when neither exists. Both themes use the shared
modal and action styles. Browser mocks store links and apply the same exact-ID
duplicate/attachment rules as the backend.

## Local covers and Playnite import

`utils/gameCover.ts` provides `gameCoverSrc` and `gameCoverError`. The library
(grid, compact, banner and list), Updates, game detail and the screenshot lightbox prefer
`cover_local_url`, falling back once to `cover_image_path` after a load error.
The backend supplies the local URL. The game detail cover field offers
“Choose image file…” and “Remove local cover”, which apply immediately, refresh
the library and show inline errors without saving other form edits.
The picker includes JPEG, PNG, WebP, GIF and AVIF. The backend detects image
content even when an AVIF source has a `.png` name, stores it as `.avif`, and
serves `image/avif` for direct browser rendering through the same image and
fallback flow.
`services/api.ts` mirrors `chooseGameCover`, `setGameCoverFile` and
`clearGameCover`, with browser mocks.

`LibraryBackupView.vue` lists Playnite under “From another app”. Its preview
shows a checked Covers option only when offered by the source, plus the number
of covers to copy. Locked libraries ask the user to close Playnite. The
Playnite browser mock provides the committed fixture's preview counts.


### Startup and cover display

Inter Variable (weights 400–800) ships through `@fontsource-variable/inter` and is imported once in `main.ts`. Interface fonts, scripts, styles and icons load from the bundle. Before the first library response, `libraryDisplayState` selects skeletons for the active layout; a failed first load offers Retry. Later refreshes retain the current games. Skeleton animation follows the motion preference and reduced motion.

All cover views try the local cover, then `cover_cache_url`, then the remote `cover_image_path`, falling back on image errors. Settings → Library & Downloads shows the device-local cache count and size (KB below 1 MB), with a confirmed purge that suspends caching until next launch.

### Wine prefix and performance controls

On Linux, Settings offers Winecfg and Winetricks for the prefix and runner currently in the form, plus global GameMode and MangoHud switches. The game Launch tab offers per-game Default, On, and Off choices and provides prefix-tool buttons when a custom prefix is enabled. Unavailable tools are disabled with a note; GameMode is also disabled for sandboxed games. When the game's launch mode is RPGMaker Linux, the MangoHud setting stays editable (launch targets can use other modes) and shows the note "MangoHud is not used with the RPGMaker Linux runner."

Below them, the Gamescope row (`wine-proton-static-6`, found by "gamescope" and "scaler") has the global switch and the window mode, output and game resolution, scaler, filter, FPS limit and extra-argument fields. `useGamescopeSettings()` (`ui/src/composables/useGamescopeSettings.ts`) saves each field on its own: selects and the switch on change, text fields on blur or Enter after the `ui/src/utils/gamescope.ts` checks (the same rules as `core/gamescope.py`). A field error is shown under the field and nothing is saved; a failed save puts back the old value. `api.getGamescopeStatus()` drives the "not installed" state: the controls are greyed out and the row shows the install command for the host's package manager (or the NixOS option) with Copy command and Check again (`getGamescopeStatus(true)`). The game Launch tab adds a Gamescope select next to GameMode/MangoHud (`Default (On/Off)`, On, Off; greyed when gamescope is not installed). While gamescope is enabled for the game (`gamescopeEnabledForGame()`), it shows the per-game game resolution, scaler, filter and extra arguments; empty means the Settings value. They save with Save Changes and count for unsaved changes. In the browser mock, gamescope reads as missing unless `localStorage["wlib-mock-gamescope"]` is `installed`.



### Update downloads

`UpdateDownloadModal.vue` is the build picker: thread builds and mirrors, RPDL torrents, patch guidance and the browser fallback. It shows no progress. "Download" adds the download to the queue, closes the picker and shows a notification with "Open Downloads" (it adds "It starts when the game closes" when the game runs and "Pause downloads while a game runs" is on). When adding fails, the picker stays open and shows the reason. Opened from a failed download's "Choose another", it passes `replaces` so the new download takes the failed one's place. The Updates view and the game detail dialog offer "Download update" for eligible games; it is disabled while the game runs or has an unfinished download. Both show the game's download status (`gameDownloadStatus`: Queued, Downloading N%, Paused, Download needs you, Ready to install) as a button that opens the Downloads page. `updateDownloads.ts` owns platform ordering, mirror preference and next-step messages.

### Downloads page

`views/DownloadsView.vue` lists the queue from `getDownloadQueue` in up to six sections, each shown only when it has a row: **Needs you** (failures, a browser check, and a finished torrent whose seeding question is open), **Downloading** (with "N of M slots in use"), **Up next** (preparing, waiting, retrying and held), **Paused**, **Ready to install** (finished downloads plus `findDownloadOffers()` archives that no download covers) and **Seeding**.

- **Rows**: `components/downloads/DownloadRow.vue` shows the title, version, source (host or "RPDL torrent", build label, part), the state in words, a progress bar, and bytes, percent, speed, time left and peers. A failure shows the cause in plain words with the actions that fit its code (Retry, Choose another, Open Settings, Remove). Running rows offer Pause and Remove; waiting rows offer Download now, Pause, Move up, Move down, To the top and Remove; paused rows offer Resume, Download now and Remove; ready rows offer Install update (disabled with "Close the game to install the update" while it runs) and Remove. Remove asks first when the row holds downloaded data. Pause asks first when the host cannot continue a download (MEGA), because the data is lost.
- **Reorder**: "Up next" rows move with Move up, Move down, To the top or native drag; `moveOrder` builds the new order and `reorderDownloads` saves it. After the move, focus goes back to the moved row's button (the other move button at the top or bottom).
- **Offers**: a detected archive row has Install update and Dismiss (`dismissDownloadOffer`; the file stays). Remove on a finished in-app download deletes the staged archive.
- **Seeding**: a finished torrent with an open seeding question asks on its row under "Needs you" (Seed, Don't seed, "Don't ask again") when the dialog did not open automatically. Seeding torrents show ratio, target and upload speed with "Stop seeding".
- **Summary**: `components/downloads/DownloadSpeedSummary.vue` shows the combined speed, an inline SVG chart of the last five minutes with a text alternative (`chartSummary`), the peak and the time left for all, or "Nothing is downloading." when idle. Below it, the disk forecast line warns when the downloads do not fit. A hold banner says why downloads wait (a running game or battery power) with "Resume now". `components/downloads/DownloadQuickSettings.vue` is the "Download settings" dialog (opened by **Settings** in the page header): the four queue settings, each saved at once, and a link "Open Settings › Library & Downloads" to `/settings#library-downloads` that closes the dialog.
- **Toolbar and empty state**: Pause all and Resume all when there are downloads, and Settings always; with no downloads, the page says how to start one and offers "Open Updates".
- **Announcements**: a polite live region announces state changes (`stateChangeAnnouncement`); focus does not move.
- **Sidebar**: the "Downloads" entry after "Updates" shows the count of unfinished downloads and a red mark when one needs the user (`sidebarBadge`), in the open and the collapsed sidebar.
- **Composable**: `composables/useUpdateDownloads.ts` is the one poller for the queue, its summary, the seeding torrents and the running games. It polls every 500 ms while bytes move, every 2 s while a download is unfinished and every 10 s otherwise (`downloadPollMs`), and asks for the speed history only while the page is open (`wantHistory`). It exposes the commands (`reorder`, `startNow`, `pause`, `resume`, `pauseAll`, `resumeAll`, `retry`, `remove`, `resumeNow`, `stopSeeding`, `answerSeed`), each through `runQueueCommand`, which reports a refusal and refreshes the list. When a download finishes, the install dialog opens by itself only when no other download is unfinished, the game does not run and no install dialog is open (`shouldAutoOpen`); otherwise a notification offers it and the row waits under "Ready to install".
- **Pure helpers**: `utils/downloadQueue.ts` holds the sections, the row text, the failure sentences and actions per code, time left, the forecast and hold text, the sidebar badge, reorder, the auto-open rule and the poll rate, with unit tests in `utils/downloadQueue.test.ts`.
- **Browser mock**: `services/mock/downloads.ts` simulates the queue (preparing, waiting, running, finished). Set the `localStorage` key `wlib-mock-download-demo` to `1` to start with a row in every section.

### RPDL torrents

- **Account**: `FileHostAccounts.vue` has an RPDL row (username and password, sign-up link, a note that the password is stored on this device). It calls `connectRpdlAccount` and `disconnectRpdlAccount`.
- **Picker**: `UpdateDownloadModal.vue` loads an "RPDL torrents" section on its own, so an RPDL failure never hides the thread's builds and the reverse. It uses `findRpdlTorrents(gameId, latest_version, refresh)`. Each row shows title, size, seeders, leechers, upload date and marks ("Matches version", "Different version", "No seeders"). A clear match replaces the file-host preselection only when the user is signed in and has not chosen anything yet (`rpdlPreselected`). Without an account the list still shows, with a sign-in note. Loading, failure plus Retry, and the refresh button are separate from the builds.
- **Export**: when a torrent is chosen and the user is signed in, the RPDL section shows **Save .torrent** (`browseDirectory`, then `saveRpdlTorrent`; shows the saved path) and **Copy magnet** (`getRpdlMagnet`, then `copyText`; shows "Magnet link copied", or an error when the clipboard write fails). The magnet is used only for the clipboard write and is never stored. Export errors show in their own callout in the RPDL section.
- **Send to client**: when `torrent_watch_folder` is set, the primary button is **Send to client** (`rpdlPrimaryAction`, `rpdlPrimaryLabel`). It calls `sendRpdlTorrentToWatchFolder` and shows a success callout (`torrentSentMessage`) that names the folder and says to use "Install from archive" when the client finishes. No progress card is shown. A **Download in wLib instead** link runs the normal `startRpdlDownload` once and does not change the setting.
- **Passkey warning**: before the first Save .torrent, Copy magnet or Send to client on this device, `ConfirmDialog.vue` warns that the file and the link hold the private RPDL passkey. Accept saves `torrent_export_warning_accepted` and runs the action; declining does nothing and the warning shows again next time.
- **Progress**: a torrent download is a row on the Downloads page. It shows "Fetching the torrent", "Finding peers" or "Looking for peers", then the percentage with both speeds, peers and time left.
- **Seeding question**: when the install dialog opens by itself for a finished torrent whose `seed_state` is `awaiting_answer`, `useUpdateDownloads.ts` asks "Keep seeding <game> until ratio <target>?" through `ConfirmDialog.vue` (`confirmChoice` adds `cancelLabel` and a `checkboxLabel`; `seedPromptText` builds the text, including the second-copy note for a game folder), sends `answerSeedPrompt(jobId, seed, remember)`, and then opens the install dialog with the torrent's archive or folder. Closing the question counts as "Don't seed". Otherwise the question waits on the torrent's row on the Downloads page. A seeding torrent shows ratio, target and upload speed under "Seeding" with "Stop seeding".
- **Install dialog**: `InstallArchiveModal.vue` learns `source_kind`, `torrent_source` and `torrent_seeding` from `inspectArchive`, sends `source_kind` to `installArchive`, and replaces "Delete archive after install" with a note for torrent files.
- **Settings**: `TorrentSettings.vue` is a "Torrents" section with "After a torrent finishes" (Ask each time, Always seed, Never seed), the ratio and hour limits, the device-local upload limit (the download limit is the shared queue limit in "Library & Downloads"), the router port mapping switch, and a device-local **Watch folder** field (Browse with `browseDirectory`, Clear, the backend's error text, and the passkey warning as help text). Fields save on change through `saveSettings`.
- **Pure helpers**: `utils/torrentDownloads.ts` (formatting, status lines, seeding question, RPDL marks, the Download or Send to client action and the sent message) and `utils/torrentSettings.ts` (field parsing) carry the logic and have unit tests.

### Controller navigation and TV mode

Standard Gamepad API controllers work while the wLib window is focused. The D-pad and left stick move focus; A selects, B goes back or closes the top dialog, X plays, Y focuses library search, Menu/Start opens a game's actions, LB/RB switch collections or detail tabs, LT/RT page, and the right stick scrolls. Arrow keys and Escape use the same movement and back actions while native text and menu controls retain their keyboard behavior. In Linux Gaming Mode, controller activation of a text field requests Steam's on-screen keyboard. A select opened with A uses a controller-friendly option list; mouse interaction retains the native select.

The focused screen supplies button hints; pointer input hides the controller focus treatment and hints. Settings → Appearance → TV mode stores Auto, On or Off locally. Auto follows Gaming Mode. TV mode temporarily uses 150% interface scale, collapses the sidebar, strengthens the controller focus ring, and keeps hints visible. The saved interface scale and sidebar preference return when TV mode ends; TV mode is not included in library backup or sync.

Game Detail places collection membership chips and the "+ Collection" picker in the header badge row, and removal in the More actions menu; membership changes are saved with the form. The Launch tab links to Sandbox settings when the tool is missing.

The Library shows active filter chips below the toolbar, with individual removal and Clear all. Sort labels distinguish F95 rating from My rating, and layout controls announce their selected state.
