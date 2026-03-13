# Build & Packaging

The official method of distribution for wLib is a self-contained GNU/Linux `AppImage`, bundled via the automated script `scripts/build.sh`.

The packaging pipeline seamlessly integrates the Python backend and the Vue frontend into a single, generic distribution archive (`tar.gz`) alongside the AppImage.

## Continuous Verification

Backend changes are validated separately from release packaging through `.github/workflows/python-checks.yml`.

- The workflow runs on pull requests, pushes to `main`, and manual dispatches.
- It targets Python 3.12, matching the supported backend development toolchain and the GitHub Actions build environment.
- It creates a fresh virtual environment, installs `requirements-dev.txt`, and runs `bash scripts/check-python-clean.sh`.
- The clean check executes `ruff`, `basedpyright`, `scripts/smoke_backend.py`, and the full `pytest` suite.

For local use, contributors can run the same checks with `bash scripts/check-python.sh` inside an activated `.venv`, or use `bash scripts/check-python-clean.sh` to verify a fresh environment end-to-end.

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

- **GPU Probing**: It briefly evaluates local hardware using `glxinfo` or by parsing `/sys/class/drm/` to determine if GPU acceleration is safely available.
- **Renderer Fallback**: Dynamically decides whether to leave Qt on its default accelerated path or force a software fallback via `QT_QUICK_BACKEND=software`. On GPU-capable hosts, the AppImage now prefers Qt's auto-selected renderer rather than forcing `QT_QUICK_BACKEND=opengl`.
- **Crash Guard**: Implements a generic "GPU crash guard": if the AppImage crashed fatally during its previous accelerated startup attempt, the second consecutive run will automatically force a fallback to software rendering to bypass broken hardware drivers.
- **Renderer Diagnostics**: The launcher now logs the selected Qt backend, the GPU detection reason, detected OpenGL renderer/direct-rendering status, and related Qt environment to both the terminal and `~/.local/share/wLib/renderer-diagnostics.log`. AppImage launches also mirror this context into `~/.local/share/wLib/appimage-launch.log`.

## Manual Build Example

If you want to produce a release build locally:

```bash
# Provide an explicit version string
bash scripts/build.sh "1.2.0"
```

The resulting artifacts (`wLib-1.2.0-x86_64.AppImage` and `wLib-1.2.0.tar.gz`) will be placed in the `dist/` directory.
