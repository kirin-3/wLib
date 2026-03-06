from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]

    with tempfile.TemporaryDirectory(prefix="wlib-smoke-") as temp_dir:
        temp_home = Path(temp_dir) / "home"
        temp_home.mkdir(parents=True, exist_ok=True)

        os.environ["HOME"] = str(temp_home)
        if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
            os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        os.environ["WLIB_PLAYWRIGHT_BROWSERS_PATH"] = str(
            temp_home / ".cache" / "ms-playwright"
        )

        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))

        import main as wlib_main
        from core.api import Api

        qt_env = wlib_main.configure_qt_runtime_environment()
        browsers_path = wlib_main.configure_playwright_browsers_path()
        webview_module = wlib_main.load_webview_module()

        if webview_module is None:
            raise RuntimeError("pywebview import failed during smoke check")

        api = Api()
        sync_result = api.sync_extension_files()

        if sync_result.get("success") is False:
            raise RuntimeError(
                f"extension sync failed during smoke check: {sync_result.get('error', 'unknown error')}"
            )

        print("Smoke check passed")
        print(f"Qt platform source: {qt_env['source']}")
        print(f"Playwright browsers path: {browsers_path}")
        print(f"Extension sync reason: {sync_result.get('reason', 'unknown')}")


if __name__ == "__main__":
    main()
