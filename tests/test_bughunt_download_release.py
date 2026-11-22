"""A saved F95 download keeps its release when only the starter post names it."""

import pytest

from core import extension_bridge, scraper, update_downloads
from core.downloads.manager import DownloadManager
from core.downloads.queue import QueueJob
from core.downloads.transfer import DownloadError
from core.update_downloads import UpdateDownloads

THREAD = "https://f95zone.to/threads/game.12345/"


def _page(version: str, url: str) -> str:
    # The title has no version; only the starter post gives it.
    return (
        '<html data-logged-in="true"><h1 class="p-title-value">Game [Dev]</h1>'
        '<article class="message-threadStarterPost"><div class="bbWrapper">'
        f"<b>Version</b>: {version}<br>"
        f'<b>DOWNLOAD</b><br><b>Win</b>: <a href="{url}">PIXELDRAIN</a><br>'
        "</div></article></html>"
    )


@pytest.fixture
def thread(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    page = {"html": _page("1.2", "https://pixeldrain.com/u/old")}
    monkeypatch.setattr(extension_bridge, "is_available", lambda: True)
    monkeypatch.setattr(
        scraper, "_f95_get_via_extension", lambda _url: (200, page["html"])
    )
    monkeypatch.setattr(
        update_downloads.database, "get_game", lambda _: {"f95_url": THREAD}
    )
    return page


def test_download_links_read_the_starter_post_version(thread: dict[str, str]):
    result = scraper.fetch_thread_download_links(THREAD)
    assert result["success"] is True
    assert result["version"] == "1.2"


def test_title_version_still_wins(monkeypatch: pytest.MonkeyPatch):
    html = _page("1.2", "https://pixeldrain.com/u/old").replace(
        "Game [Dev]", "Game [v1.3] [Dev]"
    )
    monkeypatch.setattr(extension_bridge, "is_available", lambda: True)
    monkeypatch.setattr(scraper, "_f95_get_via_extension", lambda _url: (200, html))
    # The title spelling stays, so jobs saved before keep relinking.
    assert scraper.fetch_thread_download_links(THREAD)["version"] == "v1.3"


def _saved_job() -> QueueJob:
    service = UpdateDownloads(DownloadManager())
    picker = service.get_links(1, "f95")
    assert picker["success"] is True
    link_id = str(picker["builds"][0]["mirrors"][0]["link_id"])  # pyright: ignore[reportIndexIssue, reportUnknownArgumentType]
    link = service.links[link_id]
    return QueueJob(
        id="saved",
        kind="host",
        game_id=1,
        state="queued",
        version=link.version,
        source={
            "site": "f95",
            **dict(zip(update_downloads.KEY_FIELDS, link.key, strict=True)),
        },
    )


def test_restart_does_not_relink_to_a_new_release(thread: dict[str, str]):
    job = _saved_job()
    # The author posts 1.3 under the same build and mirror labels.
    thread["html"] = _page("1.3", "https://pixeldrain.com/u/new")
    # After a restart nothing is held in memory.
    with pytest.raises(DownloadError, match="Mirror no longer available"):
        _ = UpdateDownloads(DownloadManager()).relink(job)


def test_restart_relinks_the_same_release(thread: dict[str, str]):
    job = _saved_job()
    thread["html"] = _page("1.2", "https://pixeldrain.com/u/again")
    resolve = UpdateDownloads(DownloadManager()).relink(job)
    assert resolve(lambda _stage: None, None)["url"] == "https://pixeldrain.com/u/again"  # pyright: ignore[reportArgumentType]


def test_job_saved_as_unknown_before_the_change_still_relinks(
    thread: dict[str, str],
):
    # Jobs saved before downloads read the starter post stored "Unknown".
    job = _saved_job()
    job.version = "Unknown"
    thread["html"] = _page("1.2", "https://pixeldrain.com/u/again")
    resolve = UpdateDownloads(DownloadManager()).relink(job)
    assert resolve(lambda _stage: None, None)["url"] == "https://pixeldrain.com/u/again"  # pyright: ignore[reportArgumentType]


def test_bare_title_version_wins_over_the_post(monkeypatch: pytest.MonkeyPatch):
    # A thread read stores the bare title version; downloads must agree.
    html = _page("0.4", "https://pixeldrain.com/u/old").replace(
        "Game [Dev]", "Game v0.5"
    )
    monkeypatch.setattr(extension_bridge, "is_available", lambda: True)
    monkeypatch.setattr(scraper, "_f95_get_via_extension", lambda _url: (200, html))
    result = scraper.fetch_thread_download_links(THREAD)
    assert result["version"] == "0.5"
    assert scraper.Scraper()._extract_page(html)["version"] == "0.5"  # pyright: ignore[reportPrivateUsage]
