# pyright: reportMissingImports=false
import json
import os
import zipfile
from urllib.error import URLError
from unittest.mock import MagicMock

import pytest

from core.api import Api
from core.database import init_db, add_game, get_all_games, get_setting


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path, monkeypatch):
    db_file = tmp_path / "test_wlib_api.db"
    monkeypatch.setattr("core.database.DB_PATH", str(db_file))
    init_db()
    yield
    if os.path.exists(db_file):
        os.remove(db_file)


def test_check_for_updates_rejects_unknown_version(monkeypatch):
    api = Api()
    add_game(
        title="Demo",
        exe_path="/tmp/demo.exe",
        f95_url="https://f95zone.to/threads/demo.123/",
        version="1.0",
    )

    monkeypatch.setattr(
        api.scraper,
        "get_thread_version",
        lambda *_args, **_kwargs: {
            "success": True,
            "title": "Demo [Unknown]",
            "version": "Unknown",
        },
    )

    result = api.check_for_updates("https://f95zone.to/threads/demo.123/")

    assert result["success"] is False
    assert result["error_code"] == "extract_failed"


def test_check_for_updates_propagates_structured_scraper_error(monkeypatch):
    api = Api()

    monkeypatch.setattr(
        api.scraper,
        "get_thread_version",
        lambda *_args, **_kwargs: {
            "success": False,
            "code": "blocked",
            "error": "Blocked by anti-bot challenge while loading thread",
        },
    )

    result = api.check_for_updates("https://f95zone.to/threads/demo.123/")

    assert result["success"] is False
    assert result["error_code"] == "blocked"
    assert "Blocked" in result["error"]


def test_check_for_updates_succeeds_with_actionable_version(monkeypatch):
    api = Api()
    add_game(
        title="Demo",
        exe_path="/tmp/demo.exe",
        f95_url="https://f95zone.to/threads/demo.123/",
        version="1.0",
    )

    monkeypatch.setattr(
        api.scraper,
        "get_thread_version",
        lambda *_args, **_kwargs: {
            "success": True,
            "title": "Demo [v1.1]",
            "version": "1.1",
        },
    )

    result = api.check_for_updates("https://f95zone.to/threads/demo.123/")

    assert result["success"] is True
    assert result["version"] == "1.1"
    assert result["has_update"] is True


def test_check_for_updates_retries_blocked_with_headed_mode(monkeypatch):
    api = Api()
    add_game(
        title="Demo",
        exe_path="/tmp/demo.exe",
        f95_url="https://f95zone.to/threads/demo.123/",
        version="1.0",
    )

    calls = []

    def fake_get_thread_version(
        _url,
        headless=True,
        timeout_ms=60000,
        hold_open_seconds=0,
        include_metadata=False,
    ):
        calls.append((headless, timeout_ms, hold_open_seconds, include_metadata))
        if len(calls) == 1:
            return {
                "success": False,
                "code": "blocked",
                "error": "Blocked by anti-bot challenge while loading thread",
            }
        return {"success": True, "title": "Demo [v1.1]", "version": "1.1"}

    monkeypatch.setattr(api.scraper, "get_thread_version", fake_get_thread_version)

    result = api.check_for_updates("https://f95zone.to/threads/demo.123/")

    assert result["success"] is True
    assert result["version"] == "1.1"
    assert calls[0] == (True, 60000, 0, True)
    assert calls[1] == (False, 180000, 20, True)


def test_check_for_updates_backfills_missing_metadata(monkeypatch):
    api = Api()
    add_game(
        title="Demo",
        exe_path="/tmp/demo.exe",
        f95_url="https://f95zone.to/threads/demo.123/",
        version="1.0",
        tags="",
        engine="",
        cover_image="",
    )

    monkeypatch.setattr(
        api.scraper,
        "get_thread_version",
        lambda *_args, **_kwargs: {
            "success": True,
            "title": "Demo [v1.1]",
            "version": "1.1",
            "engine": "Ren'Py",
            "tags": ["3dcg", "sandbox"],
            "cover_image": "https://img.example/cover.jpg",
        },
    )

    result = api.check_for_updates("https://f95zone.to/threads/demo.123/")

    assert result["success"] is True
    assert result["metadata_updated"] == 1

    game = get_all_games()[0]
    assert game["engine"] == "Ren'Py"
    assert game["tags"] == "3dcg, sandbox"
    assert game["cover_image_path"] == "https://img.example/cover.jpg"


def test_add_game_backfills_missing_metadata(monkeypatch):
    api = Api()

    monkeypatch.setattr(
        api.scraper,
        "get_thread_metadata",
        lambda *_args, **_kwargs: {
            "success": True,
            "engine": "Unity",
            "tags": ["3d", "adventure"],
            "cover_image": "https://img.example/new-cover.jpg",
        },
    )

    result = api.add_game(
        title="Manual Add",
        exe_path="/tmp/manual.exe",
        f95_url="https://f95zone.to/threads/manual.321/",
        tags="",
        engine="",
        cover_image="",
    )

    assert result["id"] is not None
    assert result["metadata_updated"] == 1

    game = get_all_games()[0]
    assert game["engine"] == "Unity"
    assert game["tags"] == "3d, adventure"
    assert game["cover_image_path"] == "https://img.example/new-cover.jpg"


def test_add_game_rejects_duplicate_f95_url(monkeypatch):
    api = Api()

    monkeypatch.setattr(
        api.scraper,
        "get_thread_metadata",
        lambda *_args, **_kwargs: {"success": False},
    )

    first = api.add_game(
        title="First",
        exe_path="/tmp/first.exe",
        f95_url="https://f95zone.to/threads/dupe.777/",
    )
    second = api.add_game(
        title="Second",
        exe_path="/tmp/second.exe",
        f95_url="https://f95zone.to/threads/dupe.777/",
    )

    assert first["id"] is not None
    assert second["success"] is False
    assert second["error_code"] == "duplicate_url"


def test_get_settings_includes_playwright_path_default():
    api = Api()

    settings = api.get_settings()

    assert settings["playwright_browsers_path"] == os.path.expanduser(
        "~/.cache/ms-playwright"
    )


def test_save_settings_persists_playwright_path():
    api = Api()

    api.save_settings(
        {
            "proton_path": "",
            "wine_prefix_path": "",
            "enable_logging": False,
            "playwright_browsers_path": "/tmp/custom-playwright-cache",
        }
    )

    assert get_setting("playwright_browsers_path") == "/tmp/custom-playwright-cache"


def test_browse_file_uses_linux_fallback_order(monkeypatch, tmp_path):
    api = Api()
    selected_file = tmp_path / "game.exe"
    selected_file.write_text("demo")
    calls = []

    monkeypatch.setattr("core.api.sys.platform", "linux")
    monkeypatch.setattr(
        api,
        "_build_linux_browse_backends",
        lambda *_args, **_kwargs: [
            {"name": "portal", "command": ["portal"], "env": {"GTK_USE_PORTAL": "1"}},
            {"name": "zenity", "command": ["zenity"], "env": {}},
        ],
    )

    def fake_run_picker(command, env=None):
        calls.append((tuple(command), dict(env or {})))
        if command[0] == "portal":
            return {"success": False, "cancelled": False, "error": "portal unavailable"}
        return {"success": True, "cancelled": False, "path": str(selected_file)}

    monkeypatch.setattr(api, "_run_picker_command", fake_run_picker)
    monkeypatch.setattr(api, "_browse_qt_dialog", lambda *_args, **_kwargs: "")

    result = api.browse_file()

    assert result == str(selected_file)
    assert [command[0] for command, _env in calls] == ["portal", "zenity"]


def test_browse_file_falls_back_after_portal_error_like_cancel(monkeypatch, tmp_path):
    api = Api()
    selected_file = tmp_path / "game.exe"
    selected_file.write_text("demo")
    calls = []

    monkeypatch.setattr("core.api.sys.platform", "linux")
    monkeypatch.setattr(
        api,
        "_build_linux_browse_backends",
        lambda *_args, **_kwargs: [
            {
                "name": "portal",
                "command": ["portal"],
                "env": {"GTK_USE_PORTAL": "1"},
                "continue_on_cancel_error": True,
                "cancel_error_markers": ("failed to talk to portal",),
            },
            {"name": "zenity", "command": ["zenity"], "env": {}},
        ],
    )

    def fake_run_picker(command, env=None):
        calls.append((tuple(command), dict(env or {})))
        if command[0] == "portal":
            return {
                "success": False,
                "cancelled": True,
                "error": "Failed to talk to portal",
                "stderr": "Failed to talk to portal",
                "returncode": 1,
            }
        return {
            "success": True,
            "cancelled": False,
            "path": str(selected_file),
            "stderr": "",
            "returncode": 0,
        }

    monkeypatch.setattr(api, "_run_picker_command", fake_run_picker)
    monkeypatch.setattr(api, "_browse_qt_dialog", lambda *_args, **_kwargs: "")

    result = api.browse_file()

    assert result == str(selected_file)
    assert [command[0] for command, _env in calls] == ["portal", "zenity"]


def test_browse_file_stops_on_user_cancel_even_with_portal_stderr(monkeypatch):
    api = Api()
    calls = []

    monkeypatch.setattr("core.api.sys.platform", "linux")
    monkeypatch.setattr(
        api,
        "_build_linux_browse_backends",
        lambda *_args, **_kwargs: [
            {
                "name": "portal",
                "command": ["portal"],
                "env": {"GTK_USE_PORTAL": "1"},
                "continue_on_cancel_error": True,
                "cancel_error_markers": ("failed to talk to portal",),
            },
            {"name": "zenity", "command": ["zenity"], "env": {}},
        ],
    )

    def fake_run_picker(command, env=None):
        calls.append((tuple(command), dict(env or {})))
        return {
            "success": False,
            "cancelled": True,
            "error": "Dialog cancelled",
            "stderr": "GtkDialog mapped without transient parent",
            "returncode": 1,
        }

    monkeypatch.setattr(api, "_run_picker_command", fake_run_picker)
    monkeypatch.setattr(
        api,
        "_browse_qt_dialog",
        lambda *_args, **_kwargs: "/tmp/should-not-open",
    )

    result = api.browse_file()

    assert result == ""
    assert [command[0] for command, _env in calls] == ["portal"]


def test_build_linux_browse_backends_skips_portal_when_unavailable(monkeypatch):
    api = Api()

    def fake_which(command):
        mapping = {
            "zenity": "/usr/bin/zenity",
            "kdialog": "/usr/bin/kdialog",
        }
        return mapping.get(command)

    monkeypatch.setattr("shutil.which", fake_which)
    monkeypatch.setattr(api, "_desktop_portal_available", lambda _env: False)
    monkeypatch.setattr(api, "_build_host_open_env", lambda: {})
    monkeypatch.setattr(api, "_coerce_browse_directory", lambda _path: "/home/tester")

    backends = api._build_linux_browse_backends("file", "")

    assert [backend["name"] for backend in backends] == ["zenity", "kdialog"]


def test_browse_directory_returns_empty_string_when_linux_picker_cancelled(monkeypatch):
    api = Api()
    qt_calls = []

    monkeypatch.setattr("core.api.sys.platform", "linux")
    monkeypatch.setattr(
        api,
        "_build_linux_browse_backends",
        lambda *_args, **_kwargs: [
            {"name": "portal", "command": ["portal"], "env": {}}
        ],
    )
    monkeypatch.setattr(
        api,
        "_run_picker_command",
        lambda *_args, **_kwargs: {
            "success": False,
            "cancelled": True,
            "error": "Picker cancelled",
        },
    )
    monkeypatch.setattr(
        api,
        "_browse_qt_dialog",
        lambda *_args, **_kwargs: qt_calls.append(True) or "/tmp/should-not-open",
    )

    result = api.browse_directory()

    assert result == ""
    assert qt_calls == []


def test_get_browse_locations_includes_detected_mounts(monkeypatch, tmp_path):
    api = Api()
    home_dir = tmp_path / "home"
    downloads_dir = home_dir / "Downloads"
    removable_root = tmp_path / "run" / "media" / "tester"
    mounted_drive = removable_root / "USB Drive"
    mounted_other = tmp_path / "mnt" / "Games"

    downloads_dir.mkdir(parents=True)
    mounted_drive.mkdir(parents=True)
    mounted_other.mkdir(parents=True)

    monkeypatch.setattr("core.api.os.getuid", lambda: 1000)

    class FakePwdEntry:
        pw_name = "tester"

    monkeypatch.setattr("pwd.getpwuid", lambda _uid: FakePwdEntry())

    real_expanduser = os.path.expanduser
    monkeypatch.setattr(
        "core.api.os.path.expanduser",
        lambda path: str(home_dir) if path == "~" else real_expanduser(path),
    )
    monkeypatch.setattr(
        api,
        "_iter_proc_mounts",
        lambda: [
            ("/dev/sdb1", str(mounted_drive), "ext4"),
            ("tmpfs", "/run/user/1000", "tmpfs"),
            ("/dev/sdc1", str(mounted_other), "exfat"),
        ],
    )

    locations = api.get_browse_locations()["locations"]
    location_paths = {entry["path"] for entry in locations}

    assert str(home_dir) in location_paths
    assert str(downloads_dir) in location_paths
    assert str(mounted_drive) in location_paths
    assert str(mounted_other) in location_paths
    assert "/run/user/1000" not in location_paths


def test_iter_proc_mounts_decodes_escaped_paths(monkeypatch):
    api = Api()
    mount_data = "/dev/sdb1 /run/media/tester/USB\\040Drive ext4 rw 0 0\n"

    class FakeMountsFile:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def __iter__(self):
            return iter([mount_data])

    monkeypatch.setattr("builtins.open", lambda *_args, **_kwargs: FakeMountsFile())

    mounts = api._iter_proc_mounts()

    assert mounts == [("/dev/sdb1", "/run/media/tester/USB Drive", "ext4")]


def test_open_scraper_login_session_delegates_to_scraper(monkeypatch):
    api = Api()

    monkeypatch.setattr(
        api.scraper,
        "open_login_session",
        lambda login_url: {"success": True, "login_url": login_url},
    )

    result = api.open_scraper_login_session()

    assert result["success"] is True
    assert result["login_url"] == "https://f95zone.to/login/"


def test_reset_scraper_session_delegates_to_scraper(monkeypatch):
    api = Api()

    monkeypatch.setattr(
        api.scraper,
        "reset_browser_session",
        lambda: {"success": True, "message": "reset"},
    )

    result = api.reset_scraper_session()

    assert result["success"] is True
    assert result["message"] == "reset"


def test_open_folder_uses_host_env_outside_appimage_runtime(monkeypatch, tmp_path):
    api = Api()
    target_dir = tmp_path / "folder"
    target_dir.mkdir()

    popen_mock = MagicMock()
    monkeypatch.setenv("APPIMAGE", "/tmp/wLib.AppImage")
    monkeypatch.setenv("APPDIR", "/tmp/.mount_wLib")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/.mount_wLib/usr/bin/_internal")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/usr/lib:/lib")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setattr(
        "shutil.which", lambda cmd: "/usr/bin/xdg-open" if cmd == "xdg-open" else None
    )
    monkeypatch.setattr("subprocess.Popen", popen_mock)

    result = api.open_folder(str(target_dir))

    assert result["success"] is True
    popen_mock.assert_called_once()
    args, kwargs = popen_mock.call_args
    assert args[0] == ["/usr/bin/xdg-open", str(target_dir)]
    assert kwargs["env"]["LD_LIBRARY_PATH"] == "/usr/lib:/lib"
    assert kwargs["env"]["DISPLAY"] == ":0"
    assert kwargs["env"]["WAYLAND_DISPLAY"] == "wayland-0"
    assert "APPIMAGE" not in kwargs["env"]
    assert "APPDIR" not in kwargs["env"]
    assert kwargs["start_new_session"] is True


def test_open_extension_folder_uses_host_env_outside_appimage_runtime(
    monkeypatch, tmp_path
):
    api = Api()
    popen_mock = MagicMock()
    persistent_dir = tmp_path / "extension"
    real_expanduser = os.path.expanduser

    monkeypatch.setenv("APPIMAGE", "/tmp/wLib.AppImage")
    monkeypatch.setenv("APPDIR", "/tmp/.mount_wLib")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/.mount_wLib/usr/bin/_internal")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/usr/lib:/lib")
    monkeypatch.setattr(
        "shutil.which",
        lambda cmd: "/usr/bin/xdg-open" if cmd == "xdg-open" else None,
    )
    monkeypatch.setattr("subprocess.Popen", popen_mock)
    monkeypatch.setattr(
        "os.path.expanduser",
        lambda path: (
            str(persistent_dir)
            if path == "~/.local/share/wLib/extension"
            else real_expanduser(path)
        ),
    )

    result = api.open_extension_folder()

    assert result["success"] is True
    chrome_manifest_path = persistent_dir / "chrome" / "manifest.json"
    firefox_xpi_path = persistent_dir / "firefox" / "wLib.xpi"

    assert chrome_manifest_path.is_file()
    assert firefox_xpi_path.is_file()

    chrome_manifest = json.loads(chrome_manifest_path.read_text())
    assert chrome_manifest["background"]["service_worker"] == "background.js"
    assert "scripts" not in chrome_manifest["background"]

    with zipfile.ZipFile(firefox_xpi_path) as firefox_xpi:
        firefox_manifest = json.loads(firefox_xpi.read("manifest.json").decode("utf-8"))

    assert firefox_manifest["background"]["service_worker"] == "background.js"
    assert firefox_manifest["background"]["scripts"] == ["background.js"]

    popen_mock.assert_called_once()
    args, kwargs = popen_mock.call_args
    assert args[0] == ["/usr/bin/xdg-open", str(persistent_dir)]
    assert kwargs["env"]["LD_LIBRARY_PATH"] == "/usr/lib:/lib"
    assert "APPIMAGE" not in kwargs["env"]
    assert "APPDIR" not in kwargs["env"]


def test_get_extension_service_status_reports_reachable(monkeypatch):
    api = Api()
    captured = {}

    class FakeResponse:
        status = 200

        def read(self):
            return b'{"exists": false}'

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(request, timeout=0):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["origin"] = request.headers.get("Origin")
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    result = api.get_extension_service_status()

    assert result == {"success": True, "reachable": True}
    assert captured["url"] == "http://127.0.0.1:8183/api/check?url=__ping__"
    assert captured["timeout"] == 2
    assert captured["origin"] is None


def test_get_extension_service_status_reports_unreachable(monkeypatch):
    api = Api()

    def fake_urlopen(_request, timeout=0):
        raise URLError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    result = api.get_extension_service_status()

    assert result["success"] is True
    assert result["reachable"] is False
    assert "connection refused" in str(result["error"])
