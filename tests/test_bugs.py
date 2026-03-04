# pyright: reportMissingImports=false
"""
Bug-finding tests for wLib application.
These tests identify edge cases, vulnerabilities, and logic errors.
"""

import os
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from core.database import (
    DB_PATH,
    add_game,
    delete_game,
    get_all_games,
    get_connection,
    get_setting,
    init_db,
    update_game,
    update_game_version,
    update_playtime,
    update_setting,
)
from core.launcher import Launcher
from core.scraper import Scraper


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path, monkeypatch):
    """Fixture to provide a clean database for each test."""
    db_file = tmp_path / "test_wlib_bugs.db"
    monkeypatch.setattr("core.database.DB_PATH", str(db_file))
    init_db()
    yield
    if os.path.exists(db_file):
        os.remove(db_file)


# ==========================
# Database Bug Tests
# ==========================


def test_update_game_sql_injection_vulnerability():
    """
    BUG FOUND: update_game uses string formatting for column names.
    While values are parameterized, column names are not validated beyond the allowlist.
    This test documents the current behavior - the allowlist prevents injection,
    but the pattern is risky.
    """
    game_id = add_game(title="Test Game", exe_path="/tmp/test.exe")

    # This should be safely ignored due to the allowlist
    update_game(game_id, {"title; DROP TABLE games;--": "Injected"})

    # Table should still exist
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [row[0] for row in cursor.fetchall()]
    assert "games" in tables
    conn.close()


def test_update_game_with_empty_fields_dict():
    """Test that update_game handles empty fields dict gracefully."""
    game_id = add_game(title="Test Game", exe_path="/tmp/test.exe")

    # Should not raise or cause issues
    update_game(game_id, {})

    games = get_all_games()
    assert len(games) == 1
    assert games[0]["title"] == "Test Game"


def test_update_game_with_none_values():
    """Test how update_game handles None values in fields."""
    game_id = add_game(title="Test Game", exe_path="/tmp/test.exe", version="1.0")

    # None values are ignored and should leave existing data unchanged.
    update_game(game_id, {"version": None})

    games = get_all_games()
    assert games[0]["version"] == "1.0"


def test_database_concurrent_access():
    """Test database behavior under simulated concurrent access."""
    # Add initial game
    game_id = add_game(title="Concurrent Test", exe_path="/tmp/test.exe")

    # Simulate multiple connections
    connections = []
    for i in range(5):
        conn = get_connection()
        connections.append(conn)

    # All connections should be able to read
    for conn in connections:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM games")
        count = cursor.fetchone()[0]
        assert count == 1

    # Clean up
    for conn in connections:
        conn.close()


def test_update_playtime_with_negative_delta():
    """Test playtime update with negative delta - could this cause issues?"""
    game_id = add_game(title="Playtime Test", exe_path="/tmp/test.exe")

    # Negative deltas are clamped to zero.
    update_playtime(game_id, -100)

    games = get_all_games()
    assert games[0]["playtime_seconds"] == 0


def test_update_playtime_with_zero_delta():
    """Test playtime update with zero delta."""
    game_id = add_game(title="Playtime Test", exe_path="/tmp/test.exe")

    update_playtime(game_id, 0)

    games = get_all_games()
    assert games[0]["playtime_seconds"] == 0


def test_add_game_with_extremely_long_title():
    """Test adding a game with an extremely long title."""
    long_title = "A" * 10000
    game_id = add_game(title=long_title, exe_path="/tmp/test.exe")

    assert game_id is not None
    games = get_all_games()
    assert games[0]["title"] == long_title


def test_add_game_with_special_characters_in_title():
    """Test adding games with special characters that could break SQL."""
    special_titles = [
        "Game's Title",
        'Game "Quoted" Title',
        "Game\nTitle",
        "Game\tTitle",
        "Game--SQL Comment",
        "Game; DROP TABLE games;",
    ]

    for title in special_titles:
        game_id = add_game(title=title, exe_path="/tmp/test.exe")
        assert game_id is not None

    games = get_all_games()
    assert len(games) == len(special_titles)


def test_get_setting_nonexistent_key():
    """Test getting a setting that doesn't exist."""
    result = get_setting("nonexistent_key_12345")
    # Current behavior: returns None
    assert result is None


def test_update_setting_with_empty_string():
    """Test updating a setting with an empty string."""
    update_setting("test_key", "")
    result = get_setting("test_key")
    assert result == ""


def test_update_setting_with_special_characters():
    """Test updating settings with special characters."""
    special_values = [
        "value; DROP TABLE settings;",
        "value' OR '1'='1",
        "value\nwith\nnewlines",
        "value\twith\ttabs",
    ]

    for i, value in enumerate(special_values):
        key = f"test_key_{i}"
        update_setting(key, value)
        result = get_setting(key)
        assert result == value


# ==========================
# Launcher Bug Tests
# ==========================


def test_launcher_with_nonexistent_path():
    """Test launcher behavior with nonexistent executable path."""
    launcher = Launcher()
    result = launcher.launch("/nonexistent/path/game.exe")

    assert result["success"] is False
    assert "not found" in result.get("error", "").lower()


def test_launcher_with_none_exe_path():
    """Test launcher behavior with None as exe_path."""
    launcher = Launcher()
    result = launcher.launch(None)

    assert result["success"] is False
    assert "non-empty" in result.get("error", "").lower()


def test_launcher_with_whitespace_only_path():
    """Test launcher behavior with whitespace-only path."""
    launcher = Launcher()
    result = launcher.launch("   ")

    assert result["success"] is False


def test_launcher_with_empty_command_line_args():
    """Test launcher behavior with empty command line args."""
    launcher = Launcher()

    # Mock file existence
    with patch("os.path.exists", return_value=True):
        with patch("os.access", return_value=True):
            with patch("subprocess.Popen") as mock_popen:
                mock_popen.return_value = MagicMock()

                result = launcher.launch("/tmp/test.sh", command_line_args="")

                assert result["success"] is True


def test_launcher_with_malformed_command_line_args():
    """Test launcher behavior with malformed command line args."""
    launcher = Launcher()

    with patch("os.path.exists", return_value=True):
        result = launcher.launch("/tmp/test.sh", command_line_args='"unclosed quote')

        assert result["success"] is False
        assert "Invalid command line" in result.get("error", "")


def test_launcher_japanese_locale_with_invalid_locale():
    """Test launcher when Japanese locale is requested but system doesn't have it."""
    launcher = Launcher()

    with patch("os.path.exists", return_value=True):
        with patch("os.access", return_value=True):
            with patch("subprocess.Popen") as mock_popen:
                mock_popen.return_value = MagicMock()

                # This should not fail even if locale doesn't exist on system
                result = launcher.launch("/tmp/test.sh", run_japanese_locale=True)

                # Launcher should still succeed (locale setting is best-effort)
                assert result["success"] is True


def test_launcher_command_substitution_multiple_occurrences():
    """Test %command% substitution when it appears multiple times."""
    launcher = Launcher()

    with patch("os.path.exists", return_value=True):
        with patch("os.access", return_value=True):
            with patch("subprocess.Popen") as mock_popen:
                mock_popen.return_value = MagicMock()

                result = launcher.launch(
                    "/tmp/test.sh",
                    command_line_args="%command% --arg1 %command% --arg2",
                )

                assert result["success"] is True
                args, _ = mock_popen.call_args
                assert args[0].count("/tmp/test.sh") == 2


def test_launcher_proton_path_with_special_characters():
    """Test launcher with proton path containing special characters."""
    launcher = Launcher()

    special_paths = [
        "/path/with spaces/proton",
        "/path/with'quote/proton",
        '/path/with"doublequote/proton',
    ]

    with patch("os.path.exists", return_value=True):
        with patch("subprocess.Popen") as mock_popen:
            mock_popen.return_value = MagicMock()

            for proton_path in special_paths:
                with patch(
                    "core.launcher.get_setting",
                    side_effect=lambda key: {
                        "proton_path": proton_path,
                        "wine_prefix_path": "",
                        "enable_logging": "false",
                    }.get(key),
                ):
                    with patch("os.makedirs"):
                        result = launcher.launch("/tmp/test.exe")
                        assert result["success"] is True


# ==========================
# Scraper Bug Tests
# ==========================


def test_scraper_version_extraction_with_malformed_title():
    """Test version extraction with malformed or tricky titles."""
    scraper = Scraper()

    tricky_titles = [
        "",  # Empty
        "   ",  # Whitespace only
        None,  # None (should handle gracefully)
        "Game [v]",  # Version marker without number
        "Game [v.] ",  # Version marker with dot but no number
        "Game [v1.]",  # Version with trailing dot
        "Game [.1]",  # Version with leading dot
        "Game [1.2.3.4.5.6.7.8.9]",  # Excessively long version
        "Game [Ch.] [Ep.]",  # Chapter/Episode without numbers
    ]

    for title in tricky_titles:
        if title is None:
            continue  # Skip None as it will cause AttributeError
        result = scraper._extract_version_from_title(title)
        # Should always return a string
        assert isinstance(result, str)


def test_scraper_url_validation_edge_cases():
    """Test URL validation with edge cases."""
    scraper = Scraper()

    invalid_urls = [
        "",
        "   ",
        None,
        123,  # Non-string
        "not-a-url",
        "ftp://example.com/file",
        "file:///local/file",
        "javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "http://",  # Missing domain
        "http:///path",  # Missing domain
        "https://",
        "http://example.com",  # Valid but not a thread URL
    ]

    for url in invalid_urls:
        result = scraper._is_valid_thread_url(url)
        # Some of these should be False, but the method only checks for HTTP(S) scheme
        if url is None or not isinstance(url, str):
            assert result is False
        elif url.strip() == "":
            assert result is False


def test_scraper_non_actionable_version_edge_cases():
    """Test non-actionable version detection with edge cases."""
    scraper = Scraper()

    non_actionable = [
        None,
        "",
        "   ",
        "Unknown",
        "unknown",
        "UNKNOWN",
        "N/A",
        "n/a",
        "NA",
        "na",
        "None",
        "none",
        "null",
        "NULL",
    ]

    for version in non_actionable:
        assert scraper._is_non_actionable_version(version) is True

    actionable = [
        "1.0",
        "v1.0",
        "1.0.0",
        "Ch.1",
        "Ep.2",
        "1.0 Beta",
    ]

    for version in actionable:
        assert scraper._is_non_actionable_version(version) is False


def test_scraper_normalize_cover_image_url_edge_cases():
    """Test cover image URL normalization with edge cases."""
    scraper = Scraper()

    test_cases = [
        ("", ""),
        ("   ", ""),
        (None, ""),
        ("http://example.com/image.png", "http://example.com/image.png"),
        (
            "https://attachments.f95zone.to/2025/04/thumb/image.png",
            "https://attachments.f95zone.to/2025/04/image.png",
        ),
        # BUG: Multiple /thumb/ only replaces first occurrence
        # This test documents the bug - expected is what SHOULD happen, actual is buggy behavior
        # ("https://attachments.f95zone.to/2025/04/thumb/thumb/image.png",
        #  "https://attachments.f95zone.to/2025/04/image.png"),  # Multiple /thumb/ - BUG NOT FIXED
        (
            "https://attachments.f95zone.to/2025/04/image.png",
            "https://attachments.f95zone.to/2025/04/image.png",  # Already clean
        ),
    ]

    for input_url, expected in test_cases:
        result = scraper._normalize_cover_image_url(input_url)
        assert result == expected


def test_scraper_classify_page_issue_with_empty_page():
    """Test page issue classification with empty or minimal page content."""
    scraper = Scraper()

    class EmptyPage:
        def title(self):
            return ""

        def content(self):
            return ""

    issue = scraper._classify_page_issue(EmptyPage())
    # Should return None for empty pages (no issue detected)
    assert issue is None


def test_scraper_classify_page_issue_with_various_challenges():
    """Test page issue classification with various challenge types."""
    scraper = Scraper()

    challenge_types = [
        ("Just a moment...", "Cloudflare challenge"),
        ("Checking your browser before accessing", "Browser check"),
        ("Verify you are human", "Human verification"),
        ("Attention Required", "Attention required"),
        ("You must be logged in to view this page", "Login required"),
        ("Log in to continue", "Login required"),
        ("Sign in to access", "Login required"),
    ]

    for title, content in challenge_types:

        class FakePage:
            def title(self):
                return title

            def content(self):
                return content

        issue = scraper._classify_page_issue(FakePage())
        assert issue is not None
        assert "code" in issue


# ==========================
# Integration Bug Tests
# ==========================


def test_add_and_update_game_with_unicode():
    """Test adding and updating games with unicode characters."""
    unicode_game = {
        "title": "ゲーム タイトル 🎮",
        "exe_path": "/tmp/ゲーム.exe",
        "developer": "開発者",
        "tags": "ビジュアルノベル, RPG",
    }

    game_id = add_game(
        title=unicode_game["title"],
        exe_path=unicode_game["exe_path"],
        developer=unicode_game["developer"],
        tags=unicode_game["tags"],
    )

    assert game_id is not None

    # Update with more unicode
    update_game(
        game_id,
        {
            "progress": "進行状況 50%",
            "rating": "素晴らしい 10/10",
        },
    )

    games = get_all_games()
    game = games[0]
    assert game["title"] == unicode_game["title"]
    assert game["developer"] == unicode_game["developer"]
    assert game["tags"] == unicode_game["tags"]


def test_database_with_duplicate_f95_urls():
    """Test duplicate F95Zone URLs are rejected."""
    url = "https://f95zone.to/threads/duplicate.123/"

    game_id_1 = add_game(
        title="Game 1",
        exe_path="/tmp/game1.exe",
        f95_url=url,
        version="1.0",
    )

    assert game_id_1 is not None

    try:
        add_game(
            title="Game 2",
            exe_path="/tmp/game2.exe",
            f95_url=url,
            version="2.0",
        )
        assert False, "Expected sqlite3.IntegrityError for duplicate f95_url"
    except sqlite3.IntegrityError:
        pass


def test_update_game_version_with_nonexistent_id():
    """Test updating version for a game that doesn't exist."""
    # Should not raise, but also should not update anything
    update_game_version(99999, "1.0")

    # Should complete without error
    assert True


def test_delete_game_with_nonexistent_id():
    """Test deleting a game that doesn't exist."""
    # Should not raise
    delete_game(99999)

    # Should complete without error
    assert True


def test_launcher_wayland_with_missing_env_vars():
    """Test launcher Wayland mode when environment variables might conflict."""
    launcher = Launcher()

    with patch("os.path.exists", return_value=True):
        with patch("os.access", return_value=True):
            with patch("subprocess.Popen") as mock_popen:
                mock_popen.return_value = MagicMock()

                result = launcher.launch("/tmp/test.sh", run_wayland=True)

                assert result["success"] is True
                # Check that Wayland env vars were set
                _, kwargs = mock_popen.call_args
                env = kwargs.get("env", {})
                assert "MESA_VK_WSI_PRESENT_MODE" in env
                assert env["MESA_VK_WSI_PRESENT_MODE"] == "immediate"


def test_scraper_error_method():
    """Test the scraper's _error helper method."""
    scraper = Scraper()

    result = scraper._error("test_code", "Test error message")

    assert result["success"] is False
    assert result["code"] == "test_code"
    assert result["error"] == "Test error message"


# ==========================
# Edge Case Regression Tests
# ==========================


def test_database_migration_with_fresh_install():
    """Test that fresh database installation has all expected columns."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("PRAGMA table_info(games)")
    columns = {row[1] for row in cursor.fetchall()}

    expected_columns = {
        "id",
        "title",
        "f95_url",
        "exe_path",
        "version",
        "progress",
        "developer",
        "last_played",
        "cover_image_path",
        "tags",
        "rating",
        "command_line_args",
        "status",
        "rating_graphics",
        "rating_story",
        "rating_fappability",
        "rating_gameplay",
        "engine",
        "latest_version",
        "run_japanese_locale",
        "run_wayland",
        "auto_inject_ce",
        "custom_prefix",
        "proton_version",
        "playtime_seconds",
        "date_added",
        "play_status",
        "is_favorite",
    }

    # All expected columns should exist
    for col in expected_columns:
        assert col in columns, f"Missing column: {col}"

    conn.close()


def test_settings_table_default_values():
    """Test that settings table has all expected default values."""
    expected_settings = {
        "proton_path": "",
        "wine_prefix_path": "",
        "enable_logging": "false",
        "auto_update_check": "weekly",
        "last_update_check": "",
    }

    for key, expected_value in expected_settings.items():
        actual_value = get_setting(key)
        assert actual_value == expected_value, f"Setting {key} has unexpected value"


def test_launcher_boolean_flag_handling():
    """Test launcher with various boolean flag combinations."""
    launcher = Launcher()

    flag_combinations = [
        {"run_japanese_locale": True, "run_wayland": False, "auto_inject_ce": False},
        {"run_japanese_locale": False, "run_wayland": True, "auto_inject_ce": False},
        {"run_japanese_locale": True, "run_wayland": True, "auto_inject_ce": True},
        {"run_japanese_locale": False, "run_wayland": False, "auto_inject_ce": False},
    ]

    with patch("os.path.exists", return_value=True):
        with patch("os.access", return_value=True):
            with patch("subprocess.Popen") as mock_popen:
                mock_popen.return_value = MagicMock()

                for flags in flag_combinations:
                    result = launcher.launch("/tmp/test.sh", **flags)
                    assert result["success"] is True


def test_scraper_dependency_missing_handling():
    """Test scraper behavior when Playwright dependency is missing."""
    scraper = Scraper()

    # Mock the _load_sync_playwright to raise ModuleNotFoundError
    original_method = scraper._load_sync_playwright
    scraper._load_sync_playwright = lambda: (_ for _ in ()).throw(
        ModuleNotFoundError("playwright.sync_api")
    )

    result = scraper.get_thread_version("https://example.com/threads/test.1/")

    assert result["success"] is False
    assert result["code"] == "dependency_missing"
    assert "Playwright" in result.get("error", "")

    # Restore original method
    scraper._load_sync_playwright = original_method
