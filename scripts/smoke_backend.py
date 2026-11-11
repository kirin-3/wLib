# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.catalog import CatalogClient


def check_torrent_helper() -> int:
    """Start `--torrent-helper`, expect `ready` for `hello`, and expect it to exit on EOF."""
    import json
    import subprocess

    from core import launcher

    proc = subprocess.Popen(
        launcher.self_command("--torrent-helper"),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=launcher.self_env(os.environ.copy()),
    )
    assert proc.stdin is not None and proc.stdout is not None
    try:
        _ = proc.stdin.write('{"cmd":"hello"}\n')
        proc.stdin.flush()
        event = json.loads(proc.stdout.readline() or "{}")
        if event.get("event") != "ready":
            raise RuntimeError(f"torrent helper did not start: {event.get('message', event)}")
        proc.stdin.close()
        if proc.wait(timeout=15) != 0:
            raise RuntimeError(f"torrent helper exited with code {proc.returncode}")
        return int(event.get("port") or 0)
    finally:
        if proc.poll() is None:
            proc.kill()


CATALOG_WAIT_SECONDS = 120


def wait_for_catalog_check(catalog: CatalogClient) -> None:
    """Wait for the startup catalog check, which runs on a daemon thread.

    It writes a temporary `.catalog-*` file in the data folder. On Windows an open
    file cannot be deleted, so the temporary HOME cleanup fails while it runs.
    """
    deadline = time.monotonic() + CATALOG_WAIT_SECONDS
    while catalog.status().get("checking"):
        if time.monotonic() > deadline:
            raise RuntimeError(f"catalog check still running after {CATALOG_WAIT_SECONDS} s")
        time.sleep(0.2)


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]

    with tempfile.TemporaryDirectory(prefix="wlib-smoke-") as temp_dir:
        temp_home = Path(temp_dir) / "home"
        temp_home.mkdir(parents=True, exist_ok=True)

        os.environ["HOME"] = str(temp_home)
        if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
            os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        os.environ["WLIB_DATA_DIR"] = str(temp_home / ".local" / "share" / "wLib")

        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))

        import main as wlib_main
        from core.api import Api

        qt_env = wlib_main.configure_qt_runtime_environment()
        webview_module = wlib_main.load_webview_module()

        if webview_module is None:
            raise RuntimeError("pywebview import failed during smoke check")

        api = Api()
        sync_result = api.sync_extension_files()

        if sync_result.get("success") is False:
            raise RuntimeError(
                f"extension sync failed during smoke check: {sync_result.get('error', 'unknown error')}"
            )

        port = check_torrent_helper()
        wait_for_catalog_check(api.catalog)

        print("Smoke check passed")
        print(f"Torrent helper listen port: {port}")
        print(f"Qt platform source: {qt_env['source']}")
        print(f"Extension sync reason: {sync_result.get('reason', 'unknown')}")


if __name__ == "__main__":
    main()
