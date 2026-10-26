# Build & Packaging

`cryptography>=44.0.0` is a runtime dependency for library backup and sync encryption. Linux `scripts/build.sh` collects the package with `--collect-all`; the Windows script uses `packaging/windows/wlib.spec`, which collects its data, modules, and native binaries. `main.py --smoke-test` imports the AEAD module to verify packaging. AUR metadata and its PKGBUILD template include `python-cryptography`. Rebuild the Linux test image when requirements change.

The Linux test image includes `binutils` (PyInstaller's `objdump`), `curl` (appimagetool download), and `file` (AppImage assembly). Windows checkouts can contain CRLF shell files; normalize shell scripts in a temporary Linux source copy for packaging/clean checks. Keep Linux node_modules in the container or the named volume.

wLib ships Linux artifacts through `scripts/build.sh` and Windows x64 artifacts through `scripts/build-windows.ps1`. Windows output is a portable ZIP plus a per-user MSI, both derived from one canonical PyInstaller onedir payload.

The packaging pipeline integrates the Python backend, Vue frontend, bundled browser extension, release launcher, desktop file, icon, and license into one staged PyInstaller folder. Every release format is then produced from that same staged folder.

## Continuous Verification

Backend changes are validated separately from release packaging through `.github/workflows/python-checks.yml`.

- The workflow runs on pull requests, pushes to `master`, and manual dispatches on Ubuntu and Windows runners.
- It targets Python 3.12, matching the supported backend development toolchain and the GitHub Actions build environment.
- The Ubuntu job runs `bash scripts/check-python-clean.sh`, which creates a fresh virtual environment and installs `requirements-dev.txt`.
- The clean check executes `ruff`, `basedpyright`, `scripts/smoke_backend.py`, and the full `pytest` suite.
- The Windows job creates `.venv` and runs those tools directly in PowerShell; the Bash wrappers assume Linux virtual-environment paths.
- The Windows job also runs frontend unit tests, typecheck, and production build with Python 3.12 and Node 22. For local development, use Node 22.12+ to meet both Vite and unit-test requirements.

## Release Automation

`.github/workflows/release.yml` first validates the browser extension, signing the Firefox XPI for tagged releases and producing an unsigned XPI for manual branch builds. Tagged signing uses the `JWT_USER` and `JWT_SECRET` repository secrets for the add-on developer account. Linux (`ubuntu-22.04`) and Windows (`windows-2022`) then build in parallel using the same extension artifact. The Windows job runs both the frozen smoke check and MSI lifecycle validation before uploading its ZIP, MSI, and checksums.

Publishing waits for both platform builds and attaches their artifacts to one GitHub Release with a commit-based changelog. The publish job also adds the signed Firefox XPI as `wlib-firefox-<extension version>.xpi` and an `updates.json` manifest pointing at it, so installed Firefox add-ons update themselves. Branch dispatches upload test artifacts without publishing a release. Windows versions use three numeric parts; CI removes the leading `v` from the tag or dispatch input. Optional Authenticode signing uses the `WLIB_SIGNING_CERT_BASE64` and `WLIB_SIGNING_CERT_PASSWORD` repository secrets and the `WLIB_SIGNING_TIMESTAMP_URL` repository variable.

A separate `aur` job pushes the generated `wlib-bin` `PKGBUILD` and `.SRCINFO` to the AUR for stable tags when the `AUR_SSH_PRIVATE_KEY` secret is configured. Pre-release tags such as `v1.2.3-rc1` skip the job. Set the `WLIB_MAINTAINER` repository variable before a release; native package metadata and the AUR commit use it.

### Google Drive client secret

Google Drive sync uses a Google Desktop OAuth client. Google requires the client secret in token requests even with PKCE, but does not treat it as confidential for installed apps. It still stays out of the repository (GitHub push protection blocks `GOCSPX-` values).

- **Release builds:** add the repository secret `WLIB_GDRIVE_CLIENT_SECRET`. `release.yml` passes it to `scripts/build.sh` and `scripts/build-windows.ps1`. Each script writes it to the gitignored `core/_gdrive_secret.py` before PyInstaller runs and deletes the module afterwards (also on failure). Without the secret the build still succeeds and the Google Drive option reports that the build has no credentials; Folder and Dropbox sync are unaffected.
- **Development and source runs:** export the variable before starting wLib. The environment variable takes precedence over the generated module.

```bash
WLIB_GDRIVE_CLIENT_SECRET=GOCSPX-your-secret DEV_MODE=1 python main.py
```

- **Consent screen:** the OAuth consent screen must be set to **In production**. In Testing, Google expires refresh tokens after 7 days and users are silently disconnected.

## Windows Build Pipeline (`scripts/build-windows.ps1`)

Build on Windows x64 with 64-bit Python 3.12 and npm. The current Vite build needs Node 20.19+ or 22.12+; Node 22.12+ also supports the frontend unit tests. MSI builds additionally need the .NET SDK and WiX 6.0.2 on `PATH`. From the repository root in PowerShell:

```powershell
py -3.12 -m venv .venv
dotnet tool install --global wix --version 6.0.2
.\scripts\build-windows.ps1 -Version 1.2.0 -Python .\.venv\Scripts\python.exe
```

The script validates the MSI-compatible version, installs `requirements-windows.txt`, runs frontend typecheck/build, stages the common Chrome extension plus a supplied signed Firefox XPI (or an explicitly unsigned local fallback), and freezes `wLib.exe` with `packaging/windows/wlib.spec`. The onedir payload includes Qt WebEngine/PyWebView, certifi, Vue assets, and extension assets. Frozen `--smoke-test` runs against an isolated data directory without opening the UI or downloading external content.

Intermediate files are recreated under `build/windows/`; the canonical payload is `build/windows/frozen/wLib/`. Artifacts are written to `dist/windows/` by default:

- `wLib-<version>-windows-x64-portable.zip`
- `wLib-<version>-windows-x64.msi`
- `wLib-<version>-windows-x64-SHA256SUMS.txt`

Build options:

| Option | Behavior |
|--------|----------|
| `-Version 1.2.0` | Three numeric parts without a `v` prefix; defaults to `APP_VERSION` in `core/api.py` when omitted |
| `-Python <path>` | Python interpreter used for dependency installation and PyInstaller; defaults to `python` |
| `-SignedFirefoxXpi <path>` | Bundles an existing signed Firefox XPI; omitting it creates an unsigned local fallback |
| `-OutputDirectory <path>` | Overrides `dist/windows/` |
| `-SkipDependencies` | Reuses already-installed Python dependencies |
| `-SkipFrontend` | Reuses an existing `ui/dist/` build |
| `-SkipMsi` | Builds only the portable ZIP and checksums; does not require WiX |

Keep the whole extracted portable folder, including `_internal/`, beside `wLib.exe`. Both ZIP and MSI launches use `%LOCALAPPDATA%\wLib` for user data and browser downloads, so the portable ZIP does not store the library in its extraction directory.

The MSI installs per-user beneath `%LOCALAPPDATA%\Programs\wLib`, creates a Start Menu shortcut, and never targets `%LOCALAPPDATA%\wLib` user data. Its setup wizard (WiX UI extension, added automatically by the build script) offers an unchecked desktop-shortcut option and a "Launch wLib" option on the finish page; silent installs can pass `INSTALLDESKTOPSHORTCUT=1`. Validate install, repair, major upgrade, installed smoke, uninstall, shortcut cleanup, and data retention with:

```powershell
.\scripts\test-windows-msi.ps1 -MsiPath .\dist\windows\wLib-1.2.0-windows-x64.msi
```

Run the lifecycle check after a full local build, in a Windows account without an existing wLib MSI installation. It uses the canonical frozen payload and WiX to build a higher-version MSI, writes logs under `build/windows/msi-lifecycle/`, and refuses to replace an existing installation.

Authenticode signing is optional and requires `signtool.exe` on `PATH` (from the Windows SDK). Set `WLIB_SIGNING_CERT_PATH` to a PFX, optionally set `WLIB_SIGNING_CERT_PASSWORD`, and optionally override `WLIB_SIGNING_TIMESTAMP_URL`. Credentials are read only from the environment; unsigned builds emit a warning. Cross-compiling Windows releases from Linux is unsupported.

### Windows release qualification

The initial pipeline has been exercised on Windows 11 x64 with source/frozen smoke tests, normal UI startup, portable ZIP creation, and silent MSI install/repair/major-upgrade/uninstall. GitHub's `windows-2022` runner provides clean x64 packaging coverage. Before calling a release broadly Windows 10-qualified, repeat the visible UI and game-launch checks on a clean, fully patched Windows 10 x64 machine; that manual hardware/OS pass is not represented by the Server 2022 runner. Scraping uses guest HTTP requests with no browser download. Unsigned local artifacts may show Windows publisher warnings.

### Smoke Backend Test

The smoke test (`scripts/smoke_backend.py`) is a lightweight verification tool that:

- Runs backend initialization without opening the UI
- Uses temporary HOME and `WLIB_DATA_DIR` paths to avoid touching the user's library on either platform
- Tests Qt platform configuration and backend initialization
- Exercises extension file synchronization

Use it for quick CI checks or to verify backend changes before running the full app. See the [PowerShell smoke example](../CONTRIBUTING.md#smoke-backend-test) for Windows isolation. Linux contributors can run the same checks with `bash scripts/check-python.sh` inside an activated `.venv`, or use `bash scripts/check-python-clean.sh` to verify a fresh environment end-to-end.

## Linux Build Pipeline (`scripts/build.sh`)

When the build script executes, it follows these precise steps:

1. **Frontend Compilation**: 
   - (Recommended pre-check) run `npm run typecheck` in `ui/` to validate the TypeScript frontend before packaging.
   - Navigates into `ui/` and runs `npm run build`.
   - The Vite bundler compiles the Vue application and outputs static HTML/JS/CSS assets into `ui/dist/`.
2. **PyInstaller Bundling**: 
   - Executes PyInstaller against `main.py` to freeze the Python environment.
   - Uses `--add-data` explicitly to bundle the entire `ui/dist/` web root, the browser `extension/` assets, and application icons directly alongside the binary.
   - Outputs a standard dynamic binary folder into `dist/wlib-bin/`.
3. **Library Cleanup (Crucial for Binary Releases)**:
   - PyInstaller often aggressively scoops up core system libraries (`libstdc++`, `libvulkan`, `libdrm`, `GLib`, `GTK`) that should instead be provided by the host OS.
   - `build.sh` explicitly strips and deletes these bundled low-level libraries from the PyInstaller output. This ensures the application respects the host's Mesa/Nvidia drivers and doesn't crash on driver mismatch.
4. **Release Staging**:
   - Copies the PyInstaller output into `build/wLib-<version>-linux-x86_64/`.
   - Adds `wlib`, the shared release launcher from `packaging/wlib-launcher`.
   - Adds `wlib.desktop`, `wlib.png`, `icon.svg`, and `LICENSE`.
5. **Artifact Creation**:
   - Creates `dist/wLib-<version>-linux-x86_64.tar.gz` from the staged folder.
   - When `nfpm` is available, or when `WLIB_BUILD_NATIVE_PACKAGES=1`, creates `.deb` and `.rpm` from `packaging/nfpm.yaml`.
   - When the version is release-like, or when `WLIB_BUILD_AUR=1`, generates AUR `PKGBUILD` and `.SRCINFO` under `dist/aur/`.
   - Uses `appimagetool` to convert an AppDir copy of the staged folder into a single executable `.AppImage`.

## Shared Release Launcher

Release execution begins at the shared `wlib` bash launcher instead of directly hitting `wlib-bin`.

- tar.gz users run `./wlib` from the extracted folder.
- Native package users run `/usr/bin/wlib`, which resolves to `/opt/wlib/wlib`.
- AppImage users run `AppRun`, which delegates to the staged `wlib` launcher with AppImage-specific environment variables.

This script acts as a safety layer for cross-distro GPU compatibility:

### GPU Detection Pipeline

The shared launcher executes a multi-stage GPU detection pipeline:

1. **Environment Variable Checks**: Respects `LIBGL_ALWAYS_SOFTWARE=1` and `GALLIUM_DRIVER=llvmpipe|softpipe` for forced software rendering
2. **Crash Guard Check**: If `<data dir>/.gpu_crash_guard` exists from a previous crash, automatically falls back to software rendering
3. **glxinfo Probe**: Uses `glxinfo -B` to detect the OpenGL renderer string and direct rendering status
4. **DRM Render Nodes**: Checks for `/dev/dri/renderD*` devices as a fallback detection method
5. **GPU Vendor Detection**: Reads `/sys/class/drm/card*/device/vendor` to identify AMD (0x1002), Intel (0x8086), or NVIDIA (0x10de) GPUs
6. **Final Fallback**: Defaults to software rendering if no GPU is detected

### Renderer Selection Logic

- **Software Rendering**: Sets `QT_QUICK_BACKEND=software` when GPU is unavailable, crash guard is active, or software renderer is detected
- **Hardware Acceleration**: Leaves Qt to auto-select the renderer when a capable GPU is detected with direct rendering enabled
- **Crash Guard Persistence**: On successful GPU detection, creates `<data dir>/.gpu_crash_guard` before launch; removes it on clean shutdown

### Renderer Diagnostics Logging

The shared launcher logs comprehensive launch context to `<data dir>/wlib-launch.log` by default. AppImage launches continue to log to `<data dir>/appimage-launch.log`:

- Session type (X11/Wayland) and display server variables
- Qt platform selection source and renderer backend choice
- GPU detection reason, detected renderer, and direct rendering status
- SSL certificate paths and library path restoration

The Python backend mirrors this information to `<data dir>/renderer-diagnostics.log` with additional Qt webview introspection and WebGL renderer probing from the browser context.

### Environment Restoration

Before launching the Python binary, the shared launcher:

- Restores the host's `LD_LIBRARY_PATH` from `LD_LIBRARY_PATH_ORIG` to prevent bundled libraries from shadowing host GPU drivers
- Configures SSL certificates from bundled certifi or system paths
- Sets `WLIB_LAUNCH_LOG` for Python-side diagnostic mirroring
- Sets `WLIB_APPIMAGE_LAUNCH_LOG` as a compatibility alias for AppImage launches

## Native Package Builds

Native package builds use nFPM.

```bash
# Build AppImage/tar.gz and, if nfpm is installed, native packages
bash scripts/build.sh "1.2.0"

# Force native package generation and fail if nfpm is unavailable
WLIB_BUILD_NATIVE_PACKAGES=1 \
WLIB_MAINTAINER="Kirin <maintainer@example.com>" \
bash scripts/build.sh "1.2.0"
```

nFPM reads `packaging/nfpm.yaml` and receives these environment variables from the build script:

- `WLIB_PACKAGE_SOURCE`: staged release folder path.
- `WLIB_PACKAGE_VERSION`: package-manager version without a leading `v`.
- `WLIB_PACKAGE_RELEASE`: package revision, default `1`.
- `WLIB_MAINTAINER`: maintainer metadata for deb/rpm packages.

## Flatpak Bundle

The Flatpak bundle repackages the same staged PyInstaller release folder used by the tar.gz, deb and rpm. It needs `flatpak` and `flatpak-builder` on the build host, plus the Flathub remote for the `org.freedesktop.Platform`/`Sdk` 24.08 runtime.

```bash
# Build AppImage/tar.gz and, if flatpak-builder is installed, the Flatpak bundle
bash scripts/build.sh "1.2.0"

# Force Flatpak generation and fail if flatpak-builder is unavailable
WLIB_BUILD_FLATPAK=1 bash scripts/build.sh "1.2.0"

# Skip the Flatpak bundle explicitly
WLIB_BUILD_FLATPAK=0 bash scripts/build.sh "1.2.0"
```

`WLIB_BUILD_FLATPAK` accepts `auto|1|0` like `WLIB_BUILD_NATIVE_PACKAGES`. `auto` builds only when `flatpak-builder` exists, so local builds without Flatpak tooling still produce the other artifacts. The bundle is written to `dist/wLib-<version>-x86_64.flatpak` and starts through the shared `packaging/wlib-launcher`, keeping GPU detection, crash guard handling, SSL setup and launch logging.

The build prefers Flathub's `org.flatpak.Builder` when it is installed (the release workflow installs it): distro `flatpak-builder` packages as old as 1.2.x invoke the legacy `appstream-compose` binary that current SDKs no longer ship, while the modern builder runs `appstreamcli compose`. Its dependency install is wrapped in `dbus-run-session` when available, since headless runners provide no session bus. A `flatpak-builder` on `PATH` is used as fallback.

## Nix Package

`flake.nix` exposes `packages.x86_64-linux.wlib` (also `default`) and `overlays.default`; the derivation is `nix/package.nix`. Unlike the other Linux formats it builds from source instead of repackaging the PyInstaller folder:

- The Vue frontend builds with `buildNpmPackage` and Node 22. Dependencies come from `ui/package-lock.json` through `importNpmLock`, so there is no npm dependency hash to update when the lockfile changes.
- The Python side is a `python3.withPackages` environment from nixpkgs (pywebview, QtPy, PyQt6, PyQt6-WebEngine, certifi, cryptography). Add any new runtime dependency in `requirements.txt` to that list too.
- The version is read from `APP_VERSION` in `core/api.py`.
- `bin/wlib` runs the shared `packaging/wlib-launcher` with `WLIB_APP_DIR` pointing at `share/wlib`. The launcher's `./wlib-bin` is a wrapper that runs `main.py` with the Python environment and the Qt wrapper environment, so GPU detection, crash guard, SSL setup and launch logging work as in the other formats.
- Runtime tools (glxinfo, xdg-open, gdbus, 7zz, bsdtar, bubblewrap and, unless `withWine = false`, Wine and winetricks) are appended to `PATH`, so user-installed versions win.
- The install check runs `main.py --smoke-test` against temporary HOME and data directories.
- No signed Firefox XPI is bundled, so the extension sync writes the unsigned fallback; Firefox users install the signed XPI from the GitHub release.
- Store files are read-only with a 1970 mtime. The extension sync makes its copies owner-writable and builds the fallback XPI with `strict_timestamps=False`, so keep both when changing it.
- `flake.lock` pins nixpkgs; run `nix flake update` to move it forward.

```bash
nix build .#wlib
./result/bin/wlib
```

## AUR Metadata

The AUR package is `wlib-bin` because it repackages a prebuilt upstream binary release.

```bash
WLIB_MAINTAINER="Kirin <maintainer@example.com>" \
scripts/generate-aur-package.sh "v1.2.0"
```

The script expects the matching release tarball in `dist/`, computes its SHA-256 checksum, and writes:

- `dist/aur/PKGBUILD`
- `dist/aur/.SRCINFO`

The script itself does not publish to the AUR. The release workflow's `aur` job pushes these files to `wlib-bin` on stable tags when `AUR_SSH_PRIVATE_KEY` is configured.

## Manual Build Example

If you want to produce a release build locally:

```bash
# Provide an explicit version string
bash scripts/build.sh "1.2.0"
```

The resulting artifacts are placed in `dist/`. With nFPM available, the output includes:

- `wLib-1.2.0-x86_64.AppImage`
- `wLib-1.2.0-linux-x86_64.tar.gz`
- `wLib-1.2.0-linux-x86_64.deb`
- `wLib-1.2.0-linux-x86_64.rpm`
- `dist/aur/PKGBUILD`
- `dist/aur/.SRCINFO`

With `flatpak-builder` available, or with `WLIB_BUILD_FLATPAK=1`, it also includes:

- `wLib-1.2.0-x86_64.flatpak`
