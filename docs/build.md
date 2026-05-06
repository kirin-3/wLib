# Build & Packaging

wLib ships Linux binary release artifacts through `scripts/build.sh`: AppImage, tar.gz, `.deb`, `.rpm`, and AUR `wlib-bin` metadata.

The packaging pipeline integrates the Python backend, Vue frontend, bundled browser extension, release launcher, desktop file, icon, and license into one staged PyInstaller folder. Every release format is then produced from that same staged folder.

## Continuous Verification

Backend changes are validated separately from release packaging through `.github/workflows/python-checks.yml`.

- The workflow runs on pull requests, pushes to `main`, and manual dispatches.
- It targets Python 3.12, matching the supported backend development toolchain and the GitHub Actions build environment.
- It creates a fresh virtual environment, installs `requirements-dev.txt`, and runs `bash scripts/check-python-clean.sh`.
- The clean check executes `ruff`, `basedpyright`, `scripts/smoke_backend.py`, and the full `pytest` suite.

### Smoke Backend Test

The smoke test (`scripts/smoke_backend.py`) is a lightweight verification tool that:

- Runs backend initialization without opening the UI
- Uses an isolated temporary HOME directory to avoid touching user data
- Tests Qt platform configuration and Playwright browser path setup
- Exercises extension file synchronization
- Verifies SSL certificate configuration

Use it for quick CI checks or to verify backend changes before running the full app. For local use, contributors can run the same checks with `bash scripts/check-python.sh` inside an activated `.venv`, or use `bash scripts/check-python-clean.sh` to verify a fresh environment end-to-end.

## Build Pipeline (`scripts/build.sh`)

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
2. **Crash Guard Check**: If `~/.local/share/wLib/.gpu_crash_guard` exists from a previous crash, automatically falls back to software rendering
3. **glxinfo Probe**: Uses `glxinfo -B` to detect the OpenGL renderer string and direct rendering status
4. **DRM Render Nodes**: Checks for `/dev/dri/renderD*` devices as a fallback detection method
5. **GPU Vendor Detection**: Reads `/sys/class/drm/card*/device/vendor` to identify AMD (0x1002), Intel (0x8086), or NVIDIA (0x10de) GPUs
6. **Final Fallback**: Defaults to software rendering if no GPU is detected

### Renderer Selection Logic

- **Software Rendering**: Sets `QT_QUICK_BACKEND=software` when GPU is unavailable, crash guard is active, or software renderer is detected
- **Hardware Acceleration**: Leaves Qt to auto-select the renderer when a capable GPU is detected with direct rendering enabled
- **Crash Guard Persistence**: On successful GPU detection, creates `~/.local/share/wLib/.gpu_crash_guard` before launch; removes it on clean shutdown

### Renderer Diagnostics Logging

The shared launcher logs comprehensive launch context to `~/.local/share/wLib/wlib-launch.log` by default. AppImage launches continue to log to `~/.local/share/wLib/appimage-launch.log`:

- Session type (X11/Wayland) and display server variables
- Qt platform selection source and renderer backend choice
- GPU detection reason, detected renderer, and direct rendering status
- SSL certificate paths and library path restoration

The Python backend mirrors this information to `~/.local/share/wLib/renderer-diagnostics.log` with additional Qt webview introspection and WebGL renderer probing from the browser context.

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

## AUR Metadata

The AUR package is `wlib-bin` because it repackages a prebuilt upstream binary release.

```bash
WLIB_MAINTAINER="Kirin <maintainer@example.com>" \
scripts/generate-aur-package.sh "v1.2.0"
```

The script expects the matching release tarball in `dist/`, computes its SHA-256 checksum, and writes:

- `dist/aur/PKGBUILD`
- `dist/aur/.SRCINFO`

It does not publish to the AUR automatically.

## Manual Build Example

If you want to produce a release build locally:

```bash
# Provide an explicit version string
bash scripts/build.sh "1.2.0"
```

The resulting artifacts are placed in `dist/`. With nFPM available, the output includes:

- `wLib-1.2.0-linux-x86_64.AppImage`
- `wLib-1.2.0-linux-x86_64.tar.gz`
- `wLib-1.2.0-linux-x86_64.deb`
- `wLib-1.2.0-linux-x86_64.rpm`
- `dist/aur/PKGBUILD`
- `dist/aur/.SRCINFO`
