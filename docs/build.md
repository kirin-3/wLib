# Build & Packaging

The official method of distribution for wLib is a self-contained GNU/Linux `AppImage`, bundled via the automated script `scripts/build.sh`.

The packaging pipeline seamlessly integrates the Python backend and the Vue frontend into a single, generic distribution archive (`tar.gz`) alongside the AppImage.

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
3. **Library Cleanup (Crucial for AppImages)**: 
   - PyInstaller often aggressively scoops up core system libraries (`libstdc++`, `libvulkan`, `libdrm`, `GLib`, `GTK`) that should instead be provided by the host OS.
   - `build.sh` explicitly strips and deletes these bundled low-level libraries from the PyInstaller output. This ensures the application respects the host's Mesa/Nvidia drivers and doesn't crash on driver mismatch.
4. **AppImage Assembly**:
   - Uses `appimagetool` to convert the `dist/wlib-bin/` directory structure into a single executable `.AppImage` file.

## AppRun EntryPoint

Inside the generated AppImage, execution begins at an `AppRun` bash script wrapper instead of directly hitting the Python binary. This script acts as a safety layer for cross-distro GPU compatibility:

### GPU Detection Pipeline

The `AppRun` script executes a multi-stage GPU detection pipeline:

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

The AppRun script logs comprehensive launch context to `~/.local/share/wLib/appimage-launch.log`:

- Session type (X11/Wayland) and display server variables
- Qt platform selection source and renderer backend choice
- GPU detection reason, detected renderer, and direct rendering status
- SSL certificate paths and library path restoration

The Python backend mirrors this information to `~/.local/share/wLib/renderer-diagnostics.log` with additional Qt webview introspection and WebGL renderer probing from the browser context.

### Environment Restoration

Before launching the Python binary, `AppRun`:

- Restores the host's `LD_LIBRARY_PATH` from `LD_LIBRARY_PATH_ORIG` to prevent bundled libraries from shadowing host GPU drivers
- Configures SSL certificates from bundled certifi or system paths
- Sets `WLIB_APPIMAGE_LAUNCH_LOG` for Python-side diagnostic mirroring

## Manual Build Example

If you want to produce a release build locally:

```bash
# Provide an explicit version string
bash scripts/build.sh "1.2.0"
```

The resulting artifacts (`wLib-1.2.0-x86_64.AppImage` and `wLib-1.2.0.tar.gz`) will be placed in the `dist/` directory.
