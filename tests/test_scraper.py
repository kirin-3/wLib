# pyright: reportMissingImports=false
import os

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
    assert scraper._extract_version_from_title(None) == "Unknown"


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
    assert result["code"] == "invalid_url"
    assert result["error"] == "Invalid thread URL"


def test_non_actionable_version_detection():
    scraper = Scraper()

    assert scraper._is_non_actionable_version("Unknown")
    assert scraper._is_non_actionable_version("  ")
    assert scraper._is_non_actionable_version(None)
    assert not scraper._is_non_actionable_version("1.2.3")


def test_classify_page_issue_cloudflare():
    scraper = Scraper()

    class FakePage:
        def title(self):
            return "Just a moment..."

        def content(self):
            return "<html>Cloudflare challenge</html>"

    issue = scraper._classify_page_issue(FakePage())

    assert issue is not None
    assert issue["code"] == "blocked"


def test_get_thread_version_returns_blocked_on_challenge(monkeypatch):
    scraper = Scraper()

    class FakePage:
        def goto(self, *args, **kwargs):
            return None

        def wait_for_selector(self, *args, **kwargs):
            raise RuntimeError("selector timeout")

        def title(self):
            return "Cloudflare"

        def content(self):
            return "Checking your browser before accessing"

        def close(self):
            return None

    class FakeContext:
        def __init__(self, page):
            self._page = page

        def new_page(self):
            return self._page

        def close(self):
            return None

    class FakePlaywright:
        def __init__(self, page):
            self.chromium = self
            self._page = page

        def launch_persistent_context(self, **kwargs):
            return FakeContext(self._page)

    class FakeSyncPlaywright:
        def __init__(self, page):
            self._page = page

        def start(self):
            return FakePlaywright(self._page)

        def stop(self):
            return None

    monkeypatch.setattr(
        scraper, "_load_sync_playwright", lambda: lambda: FakeSyncPlaywright(FakePage())
    )

    result = scraper.get_thread_version("https://f95zone.to/threads/example.1/")

    assert result["success"] is False
    assert result["code"] == "blocked"


def test_get_thread_version_dependency_missing(monkeypatch):
    scraper = Scraper()

    monkeypatch.setattr(
        scraper,
        "_load_sync_playwright",
        lambda: (_ for _ in ()).throw(ModuleNotFoundError("playwright.sync_api")),
    )

    result = scraper.get_thread_version("https://f95zone.to/threads/example.1/")

    assert result["success"] is False
    assert result["code"] == "dependency_missing"
    assert "Playwright" in result["error"]


def test_normalize_cover_image_url_upgrades_thumb_path():
    scraper = Scraper()

    thumb_url = (
        "https://attachments.f95zone.to/2025/04/thumb/4773537_Screenshot_Banner.png"
    )
    normalized = scraper._normalize_cover_image_url(thumb_url)

    assert normalized == (
        "https://attachments.f95zone.to/2025/04/4773537_Screenshot_Banner.png"
    )


def test_open_login_session_waits_for_user_close(monkeypatch):
    scraper = Scraper()
    events = []
    monkeypatch.setenv("APPIMAGE", "/tmp/wLib.AppImage")
    monkeypatch.setenv("APPDIR", "/tmp/.mount_wLib")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/.mount_wLib/usr/bin/_internal")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/usr/lib:/lib")

    class FakePage:
        def goto(self, url, **kwargs):
            events.append(("goto", url, kwargs.get("wait_until")))

        def bring_to_front(self):
            events.append(("front",))

        def wait_for_event(self, name, timeout=0):
            events.append(("wait", name, timeout))

    class FakeContext:
        def __init__(self):
            self.pages = []
            self.closed = False

        def new_page(self):
            page = FakePage()
            self.pages.append(page)
            return page

        def close(self):
            self.closed = True

    class FakePlaywright:
        def __init__(self):
            self.chromium = self
            self.context = FakeContext()
            self.stopped = False
            self.launch_kwargs = {}

        def launch_persistent_context(self, **kwargs):
            self.launch_kwargs = kwargs
            return self.context

        def stop(self):
            self.stopped = True

    class FakeSyncPlaywright:
        def __init__(self, playwright):
            self._playwright = playwright

        def start(self):
            return self._playwright

    fake_playwright = FakePlaywright()
    monkeypatch.setattr(
        scraper,
        "_load_sync_playwright",
        lambda: lambda: FakeSyncPlaywright(fake_playwright),
    )

    result = scraper.open_login_session()

    assert result["success"] is True
    assert fake_playwright.launch_kwargs["headless"] is False
    assert fake_playwright.launch_kwargs["user_data_dir"].endswith("/browser_session")
    assert fake_playwright.launch_kwargs["env"]["LD_LIBRARY_PATH"] == "/usr/lib:/lib"
    assert "APPIMAGE" not in fake_playwright.launch_kwargs["env"]
    assert "APPDIR" not in fake_playwright.launch_kwargs["env"]
    assert ("wait", "close", 0) in events
    assert fake_playwright.context.closed is True
    assert fake_playwright.stopped is True


def test_build_browser_launch_env_drops_appimage_overrides(monkeypatch):
    scraper = Scraper()

    monkeypatch.setenv("APPIMAGE", "/tmp/wLib.AppImage")
    monkeypatch.setenv("APPDIR", "/tmp/.mount_wLib")
    monkeypatch.setenv("OWD", "/tmp")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/.mount_wLib/usr/bin/_internal")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/usr/lib:/lib")

    env = scraper._build_browser_launch_env()

    assert env["PATH"] == "/usr/bin:/bin"
    assert env["LD_LIBRARY_PATH"] == "/usr/lib:/lib"
    assert "APPIMAGE" not in env
    assert "APPDIR" not in env
    assert "OWD" not in env


def test_build_browser_launch_env_removes_bundled_library_path_without_orig(
    monkeypatch,
):
    scraper = Scraper()

    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/.mount_wLib/usr/bin/_internal")
    monkeypatch.delenv("LD_LIBRARY_PATH_ORIG", raising=False)

    env = scraper._build_browser_launch_env()

    assert "LD_LIBRARY_PATH" not in env


def test_reset_browser_session_clears_profile(tmp_path):
    scraper = Scraper()
    scraper.user_data_dir = str(tmp_path / "browser_session")
    os.makedirs(scraper.user_data_dir, exist_ok=True)

    old_file = os.path.join(scraper.user_data_dir, "Cookies")
    with open(old_file, "w", encoding="utf-8") as f:
        f.write("stale-cookie-data")

    result = scraper.reset_browser_session()

    assert result["success"] is True
    assert os.path.isdir(scraper.user_data_dir)
    assert os.listdir(scraper.user_data_dir) == []
