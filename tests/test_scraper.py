import pytest
from unittest.mock import MagicMock, patch
from core.scraper import Scraper

@pytest.fixture
def scraper():
    return Scraper()

def test_get_thread_version_success(scraper):
    # Mock playwright
    with patch('core.scraper.sync_playwright') as mock_sync:
        mock_browser = MagicMock()
        mock_context = MagicMock()
        mock_page = MagicMock()

        mock_sync.return_value.start.return_value.chromium.launch_persistent_context.return_value = mock_context
        mock_context.new_page.return_value = mock_page

        # Scenario 1: "[v1.0] Game Title"
        mock_page.title.return_value = "[v1.0] Game Title"

        result = scraper.get_thread_version("http://example.com")

        assert result["success"] is True
        assert result["version"] == "1.0"

        # Scenario 2: "Game Title [Version 1.0]"
        mock_page.title.return_value = "Game Title [Version 1.0]"
        result = scraper.get_thread_version("http://example.com")
        assert result["version"] == "Version 1.0"

        # Scenario 3: "Game Title [v1.0b]"
        mock_page.title.return_value = "Game Title [v1.0b]"
        result = scraper.get_thread_version("http://example.com")
        assert result["version"] == "1.0b"

def test_get_thread_version_failure(scraper):
    with patch('core.scraper.sync_playwright') as mock_sync:
        mock_context = MagicMock()
        mock_page = MagicMock()

        mock_sync.return_value.start.return_value.chromium.launch_persistent_context.return_value = mock_context
        mock_context.new_page.return_value = mock_page

        # Simulate exception during page load
        mock_page.goto.side_effect = Exception("Timeout")

        result = scraper.get_thread_version("http://example.com")

        assert result["success"] is False
        assert "Timeout" in result["error"]

def test_no_version_found(scraper):
    with patch('core.scraper.sync_playwright') as mock_sync:
        mock_context = MagicMock()
        mock_page = MagicMock()

        mock_sync.return_value.start.return_value.chromium.launch_persistent_context.return_value = mock_context
        mock_context.new_page.return_value = mock_page

        mock_page.title.return_value = "Just a Game Title"

        result = scraper.get_thread_version("http://example.com")

        assert result["success"] is True
        assert result["version"] == "Unknown"
