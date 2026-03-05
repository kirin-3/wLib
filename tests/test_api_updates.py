# pyright: reportMissingImports=false
import os
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
