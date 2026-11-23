# Feature comparison: wLib, xLib, Atlas, f69

Snapshot from 2026-11-16: wLib 0.4.8, XLibrary 3.13.0, Atlas 0.9.15 (nightly), f69 0.15.24. The wLib column was updated on 2026-11-23 for the changes after 0.4.8; the other columns were not checked again.

✅ supported · ⚠️ partial or limited · ❌ not found in the source

## General

| | wLib | xLib | Atlas | f69 |
|---|---|---|---|---|
| Stack | Python, PyWebView, Vue 3 | Electron (bundled) | Electron, React | Zig, dvui, SDL3 (native) |
| Source / license | Open, GPLv3+ | Closed source | Open, GPLv3 | Open, MIT |
| Has ads? | ❌ | ✅ | ❌ | ❌ |
| RAM usage (idle, Windows)* | ~137 MB | ~294 MB | ~246 MB | ~69 MB |
| Platforms | Linux, Windows | Windows, Linux, macOS | Windows, Linux | Linux, Windows |
| Packaging | AppImage, deb, rpm, AUR, tar.gz, Flatpak, Nix, Windows | AppImage, Windows, macOS | deb, pacman, AppImage, Windows | deb, rpm, AUR, Nix, portable, Windows zip |

\* Tested on 2026-11-16 on a Windows 11 VM (2 CPU cores, 12 GB RAM) with empty libraries, using wLib 0.4.7, XLibrary 3.13.0, Atlas 0.9.14 and f69 0.14.1. Each app ran for a 90-second warm-up, then 4 minutes of samples every 5 seconds. RAM is the median private bytes of the app and all its child processes.

## Library

| Feature | wLib | xLib | Atlas | f69 |
|---|---|---|---|---|
| Library views | Grid, list, compact, banner | Grid, list, table | Banner/thumbnail grid, tree | Grid, list table, Kanban |
| Collections | ✅ | ✅ | ✅ | ⚠️ labels |
| Saved filters | ✅ | ✅ | ✅ | ❌ |
| Wishlist | ✅ (alerts when completed) | ❌ (favorites only) | ✅ | ❌ |
| Personal ratings | ✅ | ✅ + community reviews | ✅ per-category | ✅ |
| Tag colors | ✅ | ❌ | ❌ | ✅ |
| Custom play statuses | ✅ | ❌ | ❌ | ❌ |
| Notes | ✅ Markdown, PDF/TXT/MD attachments, links | ✅ | ⚠️ imported journal notes | ✅ |
| Playtime tracking | ✅ | ✅ | ✅ | ✅ |
| Per-version play journal | ✅ | ✅ | ❌ | ✅ |
| Multiple executables per game | ✅ | ✅ | ✅ | ❌ |
| Bulk folder import | ✅ | ✅ (with undo) | ✅ (folder-scheme regex) | ✅ |
| Thread screenshots | ✅ | ✅ (+ custom screenshots) | ✅ (+ custom previews) | ✅ |
| Browse full catalog (not just library) | ❌ | ✅ Showcase | ✅ Browse mode | ❌ |
| Gamepad / Steam Deck UI | ✅ controller navigation, TV mode | ✅ | ❌ | ❌ |
| Themes | Light/dark, accent, 80–150% scale | ✅ | Theme builder, banner editor | Presets, accent |

## Sources and metadata

| Feature | wLib | xLib | Atlas | f69 |
|---|---|---|---|---|
| F95Zone | ✅ | ✅ | ✅ | ✅ |
| LewdCorner | ✅ (Plus detection, update checks, downloads) | ✅ | ✅ (tier detection) | ❌ |
| DLsite / VNDB | ✅ (+ Ryuugames) | ✅ | ❌ | ❌ |
| Steam / GOG / itch library | ❌ | ✅ Steam, itch | ✅ Steam, GOG | ❌ |
| Shared metadata catalog | ✅ catalog.db | ✅ catalog packages | ✅ AtlasDB | ✅ F95Indexer |
| In-app F95 login | ❌ (uses extension's session) | ⚠️ via plugins | ✅ | ✅ (password, TOTP, cookie) |
| Import F95 bookmarks | ✅ Watched and Bookmarked, through the extension | ⚠️ F95 lists import | ❌ | ✅ |

## Updates and downloads

| Feature | wLib | xLib | Atlas | f69 |
|---|---|---|---|---|
| Library update check | ✅ | ✅ | ✅ | ✅ |
| Skip / pin a version | ✅ skip | ❌ | ❌ | ✅ pin |
| Desktop notifications | ✅ (Linux) | ✅ | ❌ | ✅ |
| In-app downloads | ✅ via extension: MEGA, Pixeldrain, Gofile, Buzzheavier, Mediafire, Mixdrop +4; F95 and LewdCorner links; Downloads page with one queue (reorder, pause/resume, shared speed limit) | ✅ ~20 hosts, JDownloader hand-off; Downloads page with one queue (reorder, start now, speed limit, pause while playing or on battery); MEGA, Pixeldrain, Mediafire account sign-in | ✅ MEGA, Pixeldrain, Gofile, Buzzheavier; F95 and LewdCorner links | ✅ aria2: donor DDL, masked links, MEGAcmd |
| Torrents / RPDL | ✅ RPDL, with seeding, in the same Downloads queue; export .torrent/magnet or send to a client's watch folder | ✅ | ❌ | ✅ |
| Install from archive, keep saves | ✅ (+ archive password read from the thread) | ✅ | ✅ | ✅ |
| App self-update | ⚠️ checks GitHub | ✅ | ✅ stable/nightly | ❌ |

## Saves, mods and tools

| Feature | wLib | xLib | Atlas | f69 |
|---|---|---|---|---|
| Save backup / restore | ✅ | ✅ | ⚠️ during updates only | ✅ (+ import saves, chapters can share one save folder) |
| Apply patches, with rollback | ❌ | ✅ xdelta patches, version snapshots | ❌ | ❌ |
| Ren'Py `.rpa` extract, dev console | ✅ (+ URM toggle) | ❌ | ❌ | ✅ |
| RPG Maker MV/MZ decryption | ✅ (+ XP/VX/VX Ace archive extraction, MV/MZ plugin on/off, save editor for MV/MZ and XP/VX/VX Ace) | ❌ | ❌ | ✅ |
| Unity BepInEx | ✅ installer | ⚠️ detects mod folders | ❌ | ✅ recipe preset |
| Mod recipes / universal mods | ❌ | ❌ | ❌ | ✅ |
| Machine translation | ❌ | ✅ DeepL, OpenRouter, Ollama, OpenAI-compatible; MTool | ❌ | ❌ |
| Cheat Engine | ✅ Lunar Engine | ❌ | ❌ | ❌ |

## Launching

| Feature | wLib | xLib | Atlas | f69 |
|---|---|---|---|---|
| Engine detection | ✅ | ✅ | ✅ | ✅ |
| Wine | ✅ | ✅ | ✅ (auto on PATH) | ❌ |
| Proton / umu-launcher | ✅ umu, Proton-GE download | ✅ umu, GE-Proton/umu-proton download | ⚠️ manual wrapper | ❌ |
| Native Linux runs of Windows builds | ✅ RPGM runner (Auto Detect for XP to MZ, one-click install with mkxp-z), NW.js fixes | ⚠️ EasyRPG | ❌ | ✅ mkxp-z, Ren'Py SDK, NW.js conversion |
| Other runtimes | Java, built-in HTML player, Flash/Ruffle | Java, HTML player, Flash/Ruffle, DOSBox, ScummVM | Per-extension emulator mapping | ❌ |
| Japanese locale | ✅ Wine/Proton locale on Linux, Locale Emulator on Windows | ✅ Locale Emulator | ❌ | ❌ |
| Winecfg / Winetricks / runtime installs | ✅ | ❌ | ❌ | ❌ |
| GameMode / MangoHud | ✅ (+ gamescope) | ❌ | ❌ | ❌ |
| Sandbox | ✅ bubblewrap, Sandboxie-Plus | ⚠️ Sandboxie (Windows) | ❌ | ✅ bubblewrap, Sandboxie |
| Per-game internet block | ✅ Linux + Windows | ✅ Linux (bubblewrap, unshare), Windows firewall, macOS | ❌ | ⚠️ Linux only |
| Add to Steam as shortcut | ✅ Linux; changes apply after Steam exits | ✅ | ❌ | ❌ |
| Custom launch command | ✅ | ✅ | ✅ | ✅ |

## Data and integration

| Feature | wLib | xLib | Atlas | f69 |
|---|---|---|---|---|
| Import from other apps | F95Checker, Atlas, XLibrary, f69, Game-List, Playnite | F95Checker, Game-List, Playnite, older XLibrary | F95Checker, XLibrary | F95Checker, XLibrary |
| Export / backup | ✅ JSON, optional passphrase | ✅ local backups, plugin exports | ❌ | ✅ F95Checker DB export, auto DB backups |
| Cloud sync | ✅ folder, Dropbox, Google Drive (encrypted) | ✅ Google Drive | ❌ | ❌ |
| Browser extension | ✅ Chrome, Firefox (F95, LewdCorner, DLsite, VNDB, Ryuugames) | ✅ Chrome, Firefox | ✅ Chrome, Firefox, Edge (F95, LewdCorner) | ❌ (stub only) |
| Diagnostics page / logs | ✅ | ✅ | ✅ | ✅ |
