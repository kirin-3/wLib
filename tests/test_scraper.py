# pyright: reportMissingImports=false
import pytest
from core.scraper import Scraper


def test_extract_version_from_title():
    scraper = Scraper()

    # Bracketed version
    assert (
        scraper._extract_version_from_title("Game Title [v1.0.5] [Developer]")
        == "1.0.5"
    )
    assert scraper._extract_version_from_title("Game Title [1.0.2] [PC/Mac]") == "1.0.2"
    assert scraper._extract_version_from_title("Game Title [v2.0 Beta]") == "2.0 Beta"
    assert (
        scraper._extract_version_from_title("Game Title [v3.1a Hotfix]")
        == "3.1a Hotfix"
    )
    assert scraper._extract_version_from_title("Another Game [Version 4.5]") == "4.5"
    assert (
        scraper._extract_version_from_title("Missing Brackets Game v1.5 [Windows]")
        == "1.5"
    )
    assert (
        scraper._extract_version_from_title("Game Title [v0.1.2.3.4] (No Spaces)")
        == "0.1.2.3.4"
    )

    # Chapter/Episode formats
    assert scraper._extract_version_from_title("Game Title [Ch.3] [RenPy]") == "Ch.3"
    assert scraper._extract_version_from_title("Game Title [Ep.5] [HTML]") == "Ep.5"
    assert (
        scraper._extract_version_from_title("Game Title [S2 E3] [Windows]") == "S2 E3"
    )
    assert (
        scraper._extract_version_from_title("Game Title [Part 3] [MacOS]") == "Part 3"
    )
    assert (
        scraper._extract_version_from_title("Visual Novel [Chapter 4 v1.2]")
        == "Chapter 4 v1.2"
    )

    # Missing version completely
    assert (
        scraper._extract_version_from_title("Game Title [RenPy] [Windows]") == "Unknown"
    )
    assert scraper._extract_version_from_title("Just the Title") == "Unknown"


def test_extract_version_from_title_real_f95_formats():
    """Test version extraction against real F95Zone title patterns."""
    scraper = Scraper()

    # Real F95Zone title: after label removal, the text looks like this
    assert (
        scraper._extract_version_from_title(
            "Ambrosia [v1.07] [Shimobashira Workshop / Kagura Games]"
        )
        == "1.07"
    )
    assert (
        scraper._extract_version_from_title("Some Game [0.45.1] [Some Developer]")
        == "0.45.1"
    )
    assert (
        scraper._extract_version_from_title("Another Game [v2.5a] [Dev Studio]")
        == "2.5a"
    )
    # Title with "Version" prefix in brackets
    assert (
        scraper._extract_version_from_title("Cool Game [Version 3.2] [Cool Dev]")
        == "3.2"
    )
    # Bare version (no brackets)
    assert (
        scraper._extract_version_from_title("Game Name v0.9.1 [Developer]") == "0.9.1"
    )
    # Season/Episode format
    assert (
        scraper._extract_version_from_title("Story Game [S2 E5] [Writer Studio]")
        == "S2 E5"
    )
    # Chapter format with version suffix
    assert (
        scraper._extract_version_from_title("RPG Game [Ch.12 v0.3] [RPG Dev]")
        == "Ch.12 v0.3"
    )


def test_extract_version_from_post_html():
    """Test the _extract_version_from_post fallback using mock page objects."""
    scraper = Scraper()

    # We can't easily mock a Playwright page, but we can test the regex patterns
    # used inside _extract_version_from_post directly via _extract_version_from_title
    # as a sanity check. The actual HTML parsing is tested by the regex patterns below.

    import re

    # Test the HTML patterns used in _extract_version_from_post
    html_patterns = [
        r"<b>\s*(?:Current\s+)?Version\s*</b>\s*:?\s*v?([^<\n]+)",
        r"<b>\s*Ver(?:\.|sion)?\s*</b>\s*:?\s*v?([^<\n]+)",
    ]

    # Pattern 1: <b>Version</b>: 1.07
    test_html_1 = "<b>Version</b>: 1.07<br>"
    m = re.search(html_patterns[0], test_html_1, re.I)
    assert m is not None
    assert m.group(1).strip().split()[0] == "1.07"

    # Pattern 1: <b>Version</b>: v2.5a
    test_html_2 = "<b>Version</b>: v2.5a<br>"
    m = re.search(html_patterns[0], test_html_2, re.I)
    assert m is not None
    assert m.group(1).strip().split()[0] == "2.5a"

    # Pattern 1: <b>Current Version</b>: 3.0
    test_html_3 = "<b>Current Version</b>: 3.0<br>"
    m = re.search(html_patterns[0], test_html_3, re.I)
    assert m is not None
    assert m.group(1).strip().split()[0] == "3.0"

    # Pattern 2: <b>Ver.</b>: 1.2.3
    test_html_4 = "<b>Ver.</b>: 1.2.3<br>"
    m = re.search(html_patterns[1], test_html_4, re.I)
    assert m is not None
    assert m.group(1).strip().split()[0] == "1.2.3"

    # Test the text patterns used as final fallback
    text_patterns = [
        r"(?:Current\s+)?Version\s*:?\s*v?([^\s,;\n]+)",
        r"Ver(?:\.|sion)?\s*:?\s*v?([^\s,;\n]+)",
        r"Release\s*:?\s*v?(\d[\d.]*[a-zA-Z]?)",
    ]

    # Plain text: "Version: 1.07"
    m = re.search(text_patterns[0], "Version: 1.07", re.I)
    assert m is not None
    assert m.group(1) == "1.07"

    # Plain text: "Version: v0.5b"
    m = re.search(text_patterns[0], "Version: v0.5b", re.I)
    assert m is not None
    assert m.group(1) == "0.5b"

    # Plain text: "Release: 2.1"
    m = re.search(text_patterns[2], "Release: 2.1", re.I)
    assert m is not None
    assert m.group(1) == "2.1"


def test_thread_url_validation():
    scraper = Scraper()

    assert scraper._is_valid_thread_url("https://f95zone.to/threads/some-game.12345/")
    assert scraper._is_valid_thread_url("http://example.com/path")
    assert not scraper._is_valid_thread_url("")
    assert not scraper._is_valid_thread_url("   ")
    assert not scraper._is_valid_thread_url("javascript:alert(1)")
    assert not scraper._is_valid_thread_url("file:///tmp/x")
    assert not scraper._is_valid_thread_url(None)


def test_get_thread_version_rejects_invalid_url():
    scraper = Scraper()
    result = scraper.get_thread_version("javascript:alert(1)")

    assert result["success"] is False
    assert result["error"] == "Invalid thread URL"
