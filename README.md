<p align="center">
  <img src="icon.svg" alt="wLib Logo" width="120" />
</p>

<h1 align="center">wLib</h1>

<p align="center">
  <b>An open-source game manager for F95Zone, built for Linux and Windows</b>
</p>

<p align="center">
  <a href="https://github.com/kirin-3/wLib/releases"><img src="https://img.shields.io/github/downloads/kirin-3/wLib/total.svg" alt="Downloads" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-GPLv3%2B-blue.svg" alt="License: GPLv3 or later" /></a>
  <img src="https://img.shields.io/badge/platform-Linux%20%7C%20Windows-lightgrey.svg" alt="Platform: Linux and Windows" />
  <img src="https://img.shields.io/badge/python-3.12+-yellow.svg" alt="Python 3.12+" />
  <img src="https://img.shields.io/badge/vue-3-brightgreen.svg" alt="Vue 3" />
  <a href="https://thekirin.net/discord"><img src="https://img.shields.io/badge/discord-join-5865F2.svg?logo=discord&logoColor=white" alt="Join our Discord" /></a>
</p>

<p align="center">
  <a href="#installing">Install</a> ·
  <a href="#features">Features</a> ·
  <a href="#how-it-compares">Comparison</a> ·
  <a href="#browser-extension">Browser extension</a> ·
  <a href="docs/README.md">Developer docs</a>
</p>

---

wLib keeps your F95Zone games in one place. It tells you when a game has an update, downloads and installs it, keeps your saves, and launches the game, natively, through Wine or Proton, or straight from Steam.

wLib began as an open-source, Linux-first alternative to XLibrary, back when XLibrary only ran on Windows. XLibrary has since added Linux builds, and wLib still puts Linux first. It runs on Windows too, has no ads or accounts, and the app, interface and extension are all open source.

**Highlights**

- **Updates in one click.** Check the whole library against F95Zone, download the new build through the browser extension or as an RPDL torrent, and install it over the old one with your saves moved across.
- **Made for Linux.** Wine, Proton through umu-launcher, an RPG Maker runner, GameMode, MangoHud, Winetricks and per-game sandboxing are built in.
- **Steam Deck ready.** Controller navigation, a TV mode that turns on in Gaming Mode, and games added to Steam as shortcuts.
- **Light.** About 125 MB of RAM when idle, a third to a half of what the Electron-based managers use ([details](docs/feature-comparison.md)).
- **Your data stays yours.** Back up to a single JSON file, sync through a folder, Dropbox or Google Drive with optional encryption, and import from F95Checker, Atlas, XLibrary, f69 or Playnite.

## Features

### Library

- Grid, list, compact and banner views, with a card-size slider.
- Cover art (animated covers play always, on hover or never, and pause while a game runs), tags with colors, ratings, play status (including your own statuses), Markdown notes and PDF, TXT or MD attachments.
- Collections as tabs, saved filters, and active filters shown as removable chips.
- Select several games at once to change status, collections, favorites, tags, engine, launch mode, sandbox or Wine/Proton prefix, back up saves, refresh covers, check for updates, or remove them. Most actions can be undone for a few seconds, also with Ctrl+Z.
- A wishlist that tells you when a game is marked Completed.
- Playtime tracking and a per-version **Journal**. **Unplayed update** and **New since finished** collections show what's new.
- Thread screenshots in the detail window, served from the shared game catalog.
- Import a whole folder of installed games at once. wLib guesses the title, version and engine, and you can fix them afterwards.
- More than one executable per game, with one as the default for Play.

### Updates and downloads

- Check every game against F95Zone at once, or on a schedule. Games from Ryuugames without an F95 thread are checked against the catalog.
- **Download update** reads the thread's links through the extension, picks a suitable build, and downloads it from MEGA, Pixeldrain, Gofile, Buzzheavier, Mediafire and other hosts. Sign in to your own Pixeldrain or MEGA account to use it for downloads.
- **RPDL torrents** show up next to the thread's links, matched to the game's title and version. Downloads resume after a restart, and you choose whether to keep seeding, up to a ratio or time limit.
- Install a new game or an update from a `.zip`, `.7z` or `.rar`, including multi-part archives. wLib moves your saves over and removes the old version, and it also spots matching archives in your Downloads folder. Installs use every CPU core and are fast on NFS and SMB network drives too.
- Skip a version you don't want, and undo the skip any time.
- Desktop notifications for new releases and completed wishlist games (Linux).

### Saves, mods and tools

- Save backups with restore and merge after an update. Backups stay on your computer and are never synced.
- **Ren'Py:** developer console switch, `.rpa` extraction and the URM toggle.
- **RPG Maker MV/MZ:** decrypt encrypted assets and switch the game to load them, and edit saves (gold, items, actors, switches, variables, or the raw data) with a backup before each write.
- **Unity:** install, update or remove BepInEx 5 or 6, with a backup.

### Launching

- Detects Ren'Py, Unity, Unreal, Godot, RPG Maker (XP to MZ), Wolf RPG and native Linux games.
- On Linux you can pick native, Wine, Proton (through umu-launcher, with a Proton-GE download), an RPG Maker Linux runner or your own command for each game. You also get:
  - automatic fixes for common black screens in RPG Maker and NW.js games
  - a Japanese locale option and a Wayland toggle
  - Winecfg, Winetricks and one-click runtime installs (DirectX, VC++, fonts, RPG Maker RTPs)
  - GameMode and MangoHud, globally or per game
  - Cheat Engine (Lunar Engine) for Wine and Proton games
  - your own tools in **Settings > Custom tools**, opened from a running game's menu
- Add wLib and your installed games to Steam as non-Steam shortcuts (Linux). Games started from Steam still go through wLib's launcher and record playtime.
- **Run in sandbox** uses bubblewrap on Linux and Sandboxie-Plus on Windows. If the tool is missing, the game doesn't launch, so it never runs unsandboxed by accident.
- **No internet** cuts a single game off from the network on both Linux and Windows.
- HTML and Flash games play in a built-in player with playtime, Stop, sandbox and No internet. Each game keeps its own saves. Flash runs through Ruffle. You can still open a game in your browser instead.
- On Windows, `.exe`, `.bat` and `.cmd` files run directly and `.jar` games run through Java.

### Controller and Steam Deck

- Navigate the whole app with a gamepad, including dialogs, menus and Settings, with on-screen button hints.
- TV mode makes the interface bigger and collapses the sidebar. It turns on by itself in Steam Gaming Mode, where text fields open Steam's on-screen keyboard.

### Sync and backup

- Back up everything to one JSON file, optionally protected with a passphrase, and restore it on another machine, including between Linux and Windows.
- Sync between computers through a shared folder (Syncthing, MEGA and so on), Dropbox or Google Drive, with optional encryption. wLib keeps the last five synced copies so you can go back, and asks you when both computers changed. File paths and launch settings stay on each computer. The [privacy policy](PRIVACY.md) explains exactly what is synced.
- Import your library from F95Checker, Atlas, XLibrary, f69 or Playnite.

### Interface

Light and dark themes, an accent color, text size, interface scale from 80% to 150%, and a reduced-motion toggle. Settings save as you change them and have a search box. The **Diagnostics** page copies a report for bug reports.

## How it compares

A short version of the [full feature comparison](docs/feature-comparison.md), read from each app's source on 2026-11-03.

| | wLib | XLibrary | Atlas | f69 |
|---|---|---|---|---|
| Source | Open, GPLv3+ | Closed | Open, GPLv3 | Open, MIT |
| Ads | No | Yes | No | No |
| RAM when idle (Windows)\* | ~125 MB | ~379 MB | ~281 MB | Not tested |
| Platforms | Linux, Windows | Windows, Linux, macOS | Windows, Linux | Linux, Windows |
| In-app update downloads | Yes | Yes | Yes | Yes |
| Torrents (RPDL) | Yes | Yes | No | Yes |
| Proton / umu-launcher | Yes | Yes | Manual | No |
| Sandbox and per-game internet block | Yes | Windows only | No | Yes (internet block Linux only) |
| Controller / Steam Deck UI | Yes | Yes | No | No |
| Add games to Steam | Yes (Linux) | Yes | No | No |
| Ren'Py and RPG Maker tools | Yes | No | No | Yes |
| GameMode, MangoHud, Winetricks | Yes | No | No | No |
| Cloud sync | Folder, Dropbox, Google Drive | Google Drive | No | No |
| Import from other apps | 5 apps | 3 apps | 2 apps | 2 apps |

\* Tested on a fresh Windows 11 VM with empty libraries, using wLib 0.4.4, XLibrary 3.11.4 and Atlas 0.9.14.

Other apps have things wLib doesn't yet, such as LewdCorner support, browsing the full catalog and machine translation. The [full comparison](docs/feature-comparison.md) lists them.

## Screenshots

<table>
  <tr>
    <td align="center"><b>Library</b></td>
    <td align="center"><b>List view</b></td>
  </tr>
  <tr>
    <td><img src="docs/screenshots/library.png" alt="Library" /></td>
    <td><img src="docs/screenshots/library-list.png" alt="List view" /></td>
  </tr>
  <tr>
    <td align="center"><b>Game details</b></td>
    <td align="center"><b>Launch options</b></td>
  </tr>
  <tr>
    <td><img src="docs/screenshots/game-overview.png" alt="Game details" /></td>
    <td><img src="docs/screenshots/game-launch.png" alt="Launch options" /></td>
  </tr>
  <tr>
    <td align="center"><b>Updates</b></td>
    <td align="center"><b>TV mode on Steam Deck</b></td>
  </tr>
  <tr>
    <td><img src="docs/screenshots/updates.png" alt="Updates" /></td>
    <td><img src="docs/screenshots/tv-mode.png" alt="TV mode on Steam Deck" /></td>
  </tr>
</table>

<details>
<summary>More screenshots</summary>

<table>
  <tr>
    <td align="center"><b>Compact view</b></td>
    <td align="center"><b>Banner view</b></td>
  </tr>
  <tr>
    <td><img src="docs/screenshots/library-compact.png" alt="Compact view" /></td>
    <td><img src="docs/screenshots/library-banner.png" alt="Banner view" /></td>
  </tr>
  <tr>
    <td align="center"><b>Selecting several games</b></td>
    <td align="center"><b>Play journal</b></td>
  </tr>
  <tr>
    <td><img src="docs/screenshots/library-bulk.png" alt="Selecting several games" /></td>
    <td><img src="docs/screenshots/game-journal.png" alt="Play journal" /></td>
  </tr>
  <tr>
    <td align="center"><b>Ren'Py tools</b></td>
    <td align="center"><b>Game info</b></td>
  </tr>
  <tr>
    <td><img src="docs/screenshots/game-tools.png" alt="Ren'Py tools" /></td>
    <td><img src="docs/screenshots/game-info.png" alt="Game info" /></td>
  </tr>
  <tr>
    <td align="center"><b>Browser extension</b></td>
    <td align="center"><b>Sync</b></td>
  </tr>
  <tr>
    <td><img src="docs/screenshots/extension.png" alt="Browser extension" /></td>
    <td><img src="docs/screenshots/sync.png" alt="Sync" /></td>
  </tr>
  <tr>
    <td align="center"><b>Backup and restore</b></td>
    <td align="center"><b>Import from another app</b></td>
  </tr>
  <tr>
    <td><img src="docs/screenshots/import-export.png" alt="Backup and restore" /></td>
    <td><img src="docs/screenshots/import-other-apps.png" alt="Import from another app" /></td>
  </tr>
  <tr>
    <td align="center"><b>Settings</b></td>
    <td align="center"><b>Appearance</b></td>
  </tr>
  <tr>
    <td><img src="docs/screenshots/settings-general.png" alt="Settings" /></td>
    <td><img src="docs/screenshots/settings-appearance.png" alt="Appearance" /></td>
  </tr>
  <tr>
    <td align="center"><b>Wine and Proton</b></td>
    <td align="center"><b>Steam</b></td>
  </tr>
  <tr>
    <td><img src="docs/screenshots/settings-wine.png" alt="Wine and Proton" /></td>
    <td><img src="docs/screenshots/settings-steam.png" alt="Steam" /></td>
  </tr>
</table>

</details>

## Installing

Download wLib from the [Releases](https://github.com/kirin-3/wLib/releases) page.

| System | Download |
|---|---|
| Windows | `.msi` installer or portable `.zip` |
| Any Linux distro | AppImage, `.tar.gz` or Flatpak |
| Debian, Ubuntu, Mint | `.deb` |
| Fedora and other RPM distros | `.rpm` |
| Arch | AUR package `wlib-bin` |
| NixOS and Nix | the repository's flake |

Your library is stored in `~/.local/share/wLib` on Linux and `%LOCALAPPDATA%\wLib` on Windows. Every Linux format uses the same folder, so you can switch formats without losing games.

### Windows

- **Installer** (`wLib-<version>-windows-x64.msi`) installs for your user account and adds a Start Menu shortcut. Later versions install over it.
- **Portable** (`wLib-<version>-windows-x64-portable.zip`): extract it anywhere and run `wLib.exe`.

You don't need Python, Wine or anything else. The builds aren't signed yet, so the first time Windows shows "Windows protected your PC". Click **More info**, then **Run anyway**. The downloads come with SHA-256 checksums.

### Linux

```bash
# AppImage (needs libfuse2 on Ubuntu 22.04 and newer)
chmod +x wLib-*.AppImage && ./wLib-*.AppImage

# Debian, Ubuntu, Mint
sudo apt install ./wLib-*-linux-x86_64.deb

# Fedora
sudo dnf install ./wLib-*-linux-x86_64.rpm

# Arch
paru -S wlib-bin

# Plain archive
tar xzf wLib-*-linux-x86_64.tar.gz && ./wLib-*/wlib
```

<details>
<summary><b>Flatpak</b> (immutable distros, Steam Deck desktop mode)</summary>

```bash
flatpak install --user wLib-<version>-x86_64.flatpak
flatpak run io.github.kirin_3.wLib
```

Games and runners still run on the host, so install Wine/Proton and winetricks on the host. The bundle includes 7-Zip, so you don't need an extractor on the host for `.7z` and `.rar` installs.

</details>

<details>
<summary><b>NixOS and Nix</b></summary>

```bash
nix run github:kirin-3/wLib
```

To install it, add the flake as an input and put `inputs.wlib.packages.x86_64-linux.default` in `environment.systemPackages` or `home.packages`, or apply `inputs.wlib.overlays.default` and use `pkgs.wlib`. The package brings Wine, winetricks, 7-Zip, bsdtar and bubblewrap. A Wine or winetricks you install yourself takes precedence, and `wlib.override { withWine = false; }` leaves them out. Proton-GE, native Linux games and the RPG Maker runner are built for regular distros, so on NixOS they need `programs.nix-ld.enable = true;` (or `steam-run`). The Nix build has no signed Firefox add-on, so install `wlib-firefox-<version>.xpi` from the [releases page](https://github.com/kirin-3/wLib/releases).

</details>

### Optional extras

| You want to | Install |
|---|---|
| Play Windows games on Linux | Wine, or Proton (wLib can download Proton-GE) |
| Use the runtime installers on Linux | Winetricks |
| Play `.jar` games | Java |
| Install from `.7z` or `.rar` | 7-Zip on Windows; `7z`, `bsdtar` or `unrar` on Linux (included in Flatpak and Nix) |
| Run games in a sandbox | bubblewrap on Linux, Sandboxie-Plus on Windows (wLib can install either from **Settings > Sandbox**) |
| Use GameMode or MangoHud | `gamemode`, `mangohud` |

## Browser extension

The Chrome and Firefox extension adds **Add to wLib**, **Wishlist** and **Open in wLib** buttons to F95Zone threads, and to DLsite, VNDB and Ryuugames pages once you turn those sites on in its options. It also handles in-app downloads, and it retries requests with your browser's sign-in when F95Zone challenges wLib.

It only talks to wLib on your own computer (port `8183`), so wLib has to be running. It updates itself, so you only install it once. Click **Open Extension Folder** in wLib to find the files.

**Chrome, Brave, Edge and other Chromium browsers:** open `chrome://extensions/`, turn on **Developer mode**, click **Load unpacked** and pick the `chrome` folder.

**Firefox:** open **Add-ons and themes**, click the gear icon, choose **Install Add-on From File...** and pick `firefox/wLib.xpi`. Use the `.xpi`, not the `extension/` folder from this repository.

If you installed the extension before wLib 0.3.9, restart Chrome once, or install the Firefox `.xpi` one more time, to get automatic updates. If the extension says wLib is out of date, update the app.

## Troubleshooting

**The window is blank or crashes on startup (Linux).** wLib checks your GPU when it starts, and after a crash with hardware acceleration it switches to software rendering on the next launch. Details are in `renderer-diagnostics.log` in the data folder.

**A Windows game shows a black screen on Linux.** Try Proton instead of Wine, and install the game's runtimes with the one-click installers.

**Logs for a bug report.** Open **Diagnostics** from the Settings footer to copy a report or open the log folder (`logs/wlib.log` in the data folder). **Enable Debug Logging** in Settings saves a game's launch output next to its executable.

## Reporting bugs

[Open an issue](https://github.com/kirin-3/wLib/issues/new?template=bug_report.yml) with:

- what you did, what you expected, and what happened
- your OS: the Windows version, or your Linux distro and X11 or Wayland
- how you installed wLib
- your Wine or Proton version, if the game uses one
- logs, if you have them

## Building from source

You need Python 3.12, Node.js 22.12 or newer, and Git.

**Linux:**

```bash
git clone https://github.com/kirin-3/wLib.git
cd wLib
cd ui && npm ci && npm run build && cd ..
./wlib.sh
```

`wlib.sh` sets up a virtual environment the first time you run it. GTK 3 and PyGObject are optional and add native desktop integration.

**Windows (PowerShell):**

```powershell
git clone https://github.com/kirin-3/wLib.git
Set-Location wLib
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-windows.txt
Set-Location ui; npm ci; npm run build; Set-Location ..
.\.venv\Scripts\python.exe main.py
```

Release packages are covered in [Build & Packaging](docs/build.md).

## Contributing

Help is welcome, from an RPG Maker quirk you tracked down to a cleanup in the interface code. Read the [contributing guide](CONTRIBUTING.md) before opening a pull request.

For live reload, run `DEV_MODE=1 python main.py` from the repository root after `npm ci` in `ui/` (on PowerShell, set `$env:DEV_MODE = "1"` first). wLib starts the Vite dev server and loads the interface from it. `npm run dev` in `ui/` also works on its own in a normal browser, with fake data. `python scripts/smoke_backend.py` checks the backend without opening a window.

The [developer documentation](docs/README.md) explains how the code fits together.

## License

wLib is licensed under the [GNU General Public License v3.0 or later](LICENSE). The [privacy policy](PRIVACY.md) explains what wLib stores and sends.

The Flatpak bundle includes [7-Zip](https://www.7-zip.org/) for `.7z` and `.rar` extraction, under the [GNU LGPL, BSD 3-clause license and unRAR restriction](https://www.7-zip.org/license.txt). Its license ships at `/app/share/licenses/7zip/License.txt`.
