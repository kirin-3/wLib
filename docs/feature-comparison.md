# Feature comparison: wLib, xLib, Atlas, f69

Snapshot from 2026-11-03

✅ supported · ⚠️ partial or limited · ❌ not found in the source

## General

| | wLib | xLib | Atlas | f69 |
|---|---|---|---|---|
| Stack | Python, PyWebView, Vue 3 | Electron (bundled) | Electron, React | Zig, dvui, SDL3 (native) |
| Source / license | Open, GPLv3+ | Closed source | Open, GPLv3 | Open, MIT |
| Has ads? | ❌ | ✅ | ❌ | ❌ |
| RAM usage (idle, Windows)* | ~125 MB | ~379 MB | ~281 MB | Not tested |
| Platforms | Linux, Windows | Windows, Linux, macOS | Windows, Linux | Linux, Windows |
| Packaging | AppImage, deb, rpm, AUR, tar.gz, Flatpak, Nix, Windows | AppImage, Windows, macOS | deb, pacman, AppImage, Windows | deb, rpm, AUR, Nix, portable, Windows zip |

\* Tested on a fresh Windows 11 VM with empty libraries, using wLib 0.4.4, xLibrary 3.11.4 and Atlas 0.9.14.

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
| Thread screenshots | ✅ | ✅ | ✅ (+ custom previews) | ✅ |
| Browse full catalog (not just library) | ❌ | ✅ Showcase | ✅ Browse mode | ❌ |
| Gamepad / Steam Deck UI | ✅ controller navigation, TV mode | ✅ | ❌ | ❌ |
| Themes | Light/dark, accent, 80–150% scale | ✅ | Theme builder, banner editor | Presets, accent |

## Sources and metadata

| Feature | wLib | xLib | Atlas | f69 |
|---|---|---|---|---|
| F95Zone | ✅ | ✅ | ✅ | ✅ |
| LewdCorner | ❌ | ✅ | ✅ (tier detection) | ❌ |
| DLsite / VNDB | ✅ (+ Ryuugames) | ✅ | ❌ | ❌ |
| Steam / GOG / itch library | ❌ | ✅ Steam, itch | ✅ Steam, GOG | ❌ |
| Shared metadata catalog | ✅ catalog.db | ✅ catalog packages | ✅ AtlasDB | ✅ F95Indexer |
| In-app F95 login | ❌ (uses extension's session) | ⚠️ via plugins | ✅ | ✅ (password, TOTP, cookie) |
| Import F95 bookmarks | ❌ | ⚠️ F95 lists import | ❌ | ✅ |

## Updates and downloads

| Feature | wLib | xLib | Atlas | f69 |
|---|---|---|---|---|
| Library update check | ✅ | ✅ | ✅ | ✅ |
| Skip / pin a version | ✅ skip | ❌ | ❌ | ✅ pin |
| Desktop notifications | ✅ (Linux) | ✅ | ❌ | ✅ |
| In-app downloads | ✅ via extension: MEGA, Pixeldrain, Gofile, Buzzheavier, Mediafire, Mixdrop +4 | ✅ ~20 hosts, JDownloader hand-off | ✅ MEGA, Pixeldrain, Gofile, Buzzheavier | ✅ aria2: donor DDL, masked links, MEGAcmd |
| Torrents / RPDL | ❌ | ✅ | ❌ | ✅ |
| Install from archive, keep saves | ✅ | ✅ | ✅ | ✅ |
| App self-update | ⚠️ checks GitHub | ✅ | ✅ stable/nightly | ❌ |

## Saves, mods and tools

| Feature | wLib | xLib | Atlas | f69 |
|---|---|---|---|---|
| Save backup / restore | ✅ | ✅ | ⚠️ during updates only | ✅ (+ import saves) |
| Ren'Py `.rpa` extract, dev console | ✅ (+ URM toggle) | ❌ | ❌ | ✅ |
| RPG Maker MV/MZ decryption | ✅ | ❌ | ❌ | ✅ |
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
| Native Linux runs of Windows builds | ✅ RPGM runner, NW.js fixes | ⚠️ EasyRPG | ❌ | ✅ mkxp-z, Ren'Py SDK, NW.js conversion |
| Other runtimes | Java, HTML (Windows) | Java, HTML player, Flash/Ruffle, DOSBox, ScummVM | Per-extension emulator mapping | ❌ |
| Japanese locale | ✅ | ✅ Locale Emulator | ❌ | ❌ |
| Winecfg / Winetricks / runtime installs | ✅ | ❌ | ❌ | ❌ |
| GameMode / MangoHud | ✅ | ❌ | ❌ | ❌ |
| Sandbox | ✅ bubblewrap, Sandboxie-Plus | ⚠️ Sandboxie (Windows) | ❌ | ✅ bubblewrap, Sandboxie |
| Per-game internet block | ✅ Linux + Windows | ⚠️ Windows firewall | ❌ | ⚠️ Linux only |
| Add to Steam as shortcut | ✅ Linux; changes apply after Steam exits | ✅ | ❌ | ❌ |
| Custom launch command | ✅ | ✅ | ✅ | ✅ |

## Data and integration

| Feature | wLib | xLib | Atlas | f69 |
|---|---|---|---|---|
| Import from other apps | F95Checker, Atlas, XLibrary, f69, Playnite | F95Checker, Game-List, older XLibrary | F95Checker, XLibrary | F95Checker, XLibrary |
| Export / backup | ✅ JSON, optional passphrase | ✅ local backups, plugin exports | ❌ | ✅ F95Checker DB export, auto DB backups |
| Cloud sync | ✅ folder, Dropbox, Google Drive (encrypted) | ✅ Google Drive | ❌ | ❌ |
| Browser extension | ✅ Chrome, Firefox (F95, DLsite, VNDB, Ryuugames) | ✅ Chrome, Firefox | ✅ Chrome, Firefox, Edge (F95, LewdCorner) | ❌ (stub only) |
| Diagnostics page / logs | ✅ | ✅ | ✅ | ✅ |
