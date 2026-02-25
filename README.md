<p align="center">
  <img src="icon.svg" alt="wLib Logo" width="120" />
</p>

<h1 align="center">wLib</h1>

<p align="center">
  <b>A modern Linux game manager for F95Zone</b>
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License: MIT" /></a>
  <img src="https://img.shields.io/badge/platform-Linux-lightgrey.svg" alt="Platform: Linux" />
  <img src="https://img.shields.io/badge/python-3.11+-yellow.svg" alt="Python 3.11+" />
  <img src="https://img.shields.io/badge/vue-3-brightgreen.svg" alt="Vue 3" />
</p>

---

wLib is a native Linux desktop application for managing, launching, and updating your F95Zone game library. It wraps a beautiful Vue 3 frontend inside a PyWebView shell, launches games through Wine or Proton, and tracks updates by scraping F95Zone thread pages.

## ✨ Features

- **Game Library** — Add, organize, rate, and track your games with cover art, tags, and progress status
- **Wine / Proton Launcher** — Launch `.exe` games via Wine, Proton-GE, or run native Linux binaries, shell scripts, and `.jar` files
- **Universal Engine Support** — Seamlessly launch and manage games built on Ren'Py, Unity, Unreal Engine, Godot, RPG Maker (MV/MZ/VX/XP), Wolf RPG Editor, and native Linux engines
- **Dependency Installers** — One-click installers for common visual novel and RPG runtime dependencies (DirectX, VCRedist, fonts) and RTPs
- **F95Zone Update Checker** — Automatically checks for new game versions by scraping F95Zone threads via Playwright
- **Japanese Locale Mode** — Run games with `ja_JP.UTF-8` locale for untranslated Japanese titles
- **Cheat Engine Injection** — Auto-download and inject Lunar Engine (a Cheat Engine fork) into running games
- **Browser Extension** — A Chromium extension that adds "Add to wLib" buttons directly on F95Zone thread pages
- **In-App Updates** — Check for new wLib releases from GitHub and view changelogs
- **Dark & Light Themes** — Toggle between polished dark and light themes
- **AppImage & tar.gz Builds** — Automated CI/CD via GitHub Actions for portable distribution

## 📸 Screenshots

<!-- Add screenshots here after the first public release -->
<!-- ![Library View](docs/screenshots/library.png) -->
<!-- ![Settings View](docs/screenshots/settings.png) -->

## 📋 Requirements

### System Dependencies

| Dependency | Required | Purpose |
|------------|----------|---------|
| **Python 3.11+** | ✅ | Backend runtime |
| **Node.js 18+** | ⚙️ Build only | Compiles the Vue frontend |
| **Wine** | ✅ | Runs Windows game executables |
| **Winetricks** | ✅ | Installs Windows DLLs and runtime libraries |
| **GTK 3 / PyGObject** | ✅ | Native UI integration (file dialogs, system tray) |

> [!NOTE]
> The `wlib.sh` launcher script automatically creates a Python virtual environment and installs all pip dependencies for you. You only need the system-level packages listed above.

### Install System Packages

<details>
<summary><b>Ubuntu / Debian</b></summary>

```bash
sudo apt update
sudo apt install python3 python3-venv python3-gi python3-gi-cairo \
                 gir1.2-gtk-3.0 wine winetricks nodejs npm
```
</details>

<details>
<summary><b>Fedora / RHEL</b></summary>

```bash
sudo dnf install python3 python3-gobject gtk3 wine winetricks nodejs npm
```
</details>

<details>
<summary><b>Arch / Manjaro</b></summary>

```bash
sudo pacman -S python python-gobject gtk3 wine winetricks nodejs npm
```
</details>

## 🚀 Installation

### Option 1: AppImage (Recommended)

Download the latest `.AppImage` from the [Releases](https://github.com/kirin-3/wLib/releases) page:

```bash
chmod +x wLib-*.AppImage
./wLib-*.AppImage
```

### Option 2: tar.gz Archive

```bash
tar xzf wLib-*-linux-x86_64.tar.gz
cd wLib-*/
./wlib.sh
```

### Option 3: Run from Source

```bash
git clone https://github.com/kirin-3/wLib.git
cd wLib

# Build the Vue frontend
cd ui && npm install && npm run build && cd ..

# Launch (auto-creates venv and installs Python deps)
./wlib.sh
```

## 🛠️ Development

### Dev Mode with Hot Reload

For active development, wLib supports a Vite dev server with hot module replacement:

```bash
# Terminal 1: Start the Vite dev server
cd ui
npm install
npm run dev

# Terminal 2: Launch wLib in dev mode
DEV_MODE=1 python main.py
```

This connects PyWebView to `http://localhost:5173` so you get instant frontend updates without rebuilding.

### Project Structure

```
wLib/
├── main.py                 # Application entry point (PyWebView + HTTP receiver)
├── wlib.sh                 # Launcher script (venv setup, dep install, run)
├── requirements.txt        # Python dependencies
├── core/
│   ├── api.py              # PyWebView JS API (game CRUD, settings, launcher)
│   ├── database.py         # SQLite database layer
│   ├── launcher.py         # Wine/Proton game launcher
│   └── scraper.py          # F95Zone version scraper (Playwright)
├── ui/                     # Vue 3 + Vite frontend
│   ├── src/
│   │   ├── views/          # Library, Settings, Updates, Extension views
│   │   ├── components/     # Reusable UI components
│   │   └── style.css       # Global styles and theme system
│   └── package.json
├── extension/              # Chromium browser extension (Manifest V3)
│   ├── manifest.json
│   ├── content.js          # F95Zone page integration
│   └── background.js       # Service worker
├── scripts/
│   └── build.sh            # Build script (tar.gz + AppImage packaging)
└── .github/
    └── workflows/
        └── release.yml     # CI/CD: auto-build and publish releases
```

## 🌐 Browser Extension

wLib includes a Chromium browser extension that adds integration buttons directly on F95Zone thread pages. It communicates with the running wLib app over a local HTTP server on port `8183`.

### Installing the Extension

1. Open your Chromium-based browser (Chrome, Brave, Edge, Vivaldi, etc.)
2. Navigate to `chrome://extensions/`
3. Enable **Developer mode** (top-right toggle)
4. Click **Load unpacked** and select the `extension/` folder from your wLib directory
5. Visit any F95Zone thread — you'll see wLib integration buttons appear on the page

## 🐛 Reporting Bugs

Found a bug? Please [open an issue](https://github.com/kirin-3/wLib/issues/new?template=bug_report.yml) and include:

- **Steps to reproduce** the issue
- **Expected vs actual behavior**
- Your **Linux distribution** and version
- Your **Wine/Proton version** (if game-launch related)
- Any **error logs** (enable logging in Settings → Debug Logging)

## 🤝 Contributing

Contributions are welcome! Please read the [Contributing Guide](CONTRIBUTING.md) before submitting pull requests.

## 📄 License

This project is licensed under the [MIT License](LICENSE).
