# SPDX-License-Identifier: GPL-3.0-or-later
"""A saved download keeps its build kind, so an old build cannot take its place."""

import pytest

from core import update_downloads
from core.downloads.manager import DownloadManager
from core.downloads.queue import QueueJob
from core.downloads.transfer import DownloadError
from core.update_downloads import UpdateDownloads


def _build(kind, url):
    return {
        "label": "Win",
        "section": "",
        "platform": "windows",
        "kind": kind,
        "mirrors": [
            {
                "url": url,
                "host": "pixeldrain",
                "label": "PIXELDRAIN",
                "part": "",
                "split": False,
            }
        ],
    }


def _service(monkeypatch, reads):
    monkeypatch.setattr(
        update_downloads.database,
        "get_game",
        lambda _: {"f95_url": "https://f95zone.to/threads/1/", "title": "Game"},
    )
    monkeypatch.setattr(
        update_downloads.scraper, "fetch_thread_download_links", lambda _: reads[0]
    )
    return UpdateDownloads(DownloadManager())


def _saved(service, link_id):
    link = service.links[link_id]
    source = dict(zip(update_downloads.KEY_FIELDS, link.key, strict=True))
    return QueueJob(
        id="saved",
        kind="host",
        game_id=1,
        state="queued",
        version=link.version,
        source=source,
    )


def _url(service, job):
    return service.relink(job)(lambda _stage: None, None)["url"]


def test_an_added_old_build_does_not_take_the_current_builds_place(monkeypatch):
    reads = [
        {
            "success": True,
            "version": "v2",
            "builds": [
                _build("old", "https://pixeldrain.com/u/v0"),
                _build("full", "https://pixeldrain.com/u/v2"),
            ],
        }
    ]
    service = _service(monkeypatch, reads)
    builds = service.get_links(1)["builds"]
    job = _saved(service, builds[1]["mirrors"][0]["link_id"])
    assert job.source["kind"] == "full"
    service.clear(1)
    # The author adds one more old build to the spoiler above the current one.
    reads[0] = {
        "success": True,
        "version": "v2",
        "builds": [
            _build("old", "https://pixeldrain.com/u/vA"),
            _build("old", "https://pixeldrain.com/u/v0"),
            _build("full", "https://pixeldrain.com/u/v2"),
        ],
    }
    assert _url(service, job) == "https://pixeldrain.com/u/v2"


def test_job_saved_without_a_kind_refuses_an_ambiguous_mirror(monkeypatch):
    reads = [
        {
            "success": True,
            "version": "v2",
            "builds": [
                _build("old", "https://pixeldrain.com/u/v0"),
                _build("full", "https://pixeldrain.com/u/v2"),
            ],
        }
    ]
    service = _service(monkeypatch, reads)
    # Saved with a mirror identity but before the key held the build kind.
    job = QueueJob(
        id="saved",
        kind="host",
        game_id=1,
        state="queued",
        version="v2",
        source={
            "site": "f95",
            "label": "Win",
            "section": "",
            "platform": "windows",
            "host": "pixeldrain",
            "part": "",
            "mirror": "PIXELDRAIN",
            "copy": 1,
        },
    )
    with pytest.raises(DownloadError, match="more than one"):
        _ = service.relink(job)
    reads[0] = {
        "success": True,
        "version": "v2",
        "builds": [_build("full", "https://pixeldrain.com/u/v2")],
    }
    service.clear(1)
    assert _url(service, job) == "https://pixeldrain.com/u/v2"


def test_job_saved_before_build_kinds_relinks_a_second_mirror_in_the_same_kind(
    monkeypatch,
):
    first = _build("full", "https://pixeldrain.com/u/first")
    second = _build("full", "https://pixeldrain.com/u/second")
    first["mirrors"].extend(second["mirrors"])
    service = _service(
        monkeypatch, [{"success": True, "version": "v2", "builds": [first]}]
    )
    job = QueueJob(
        id="saved",
        kind="host",
        game_id=1,
        state="queued",
        version="v2",
        source={
            "site": "f95",
            "label": "Win",
            "section": "",
            "platform": "windows",
            "host": "pixeldrain",
            "part": "",
            "mirror": "PIXELDRAIN",
            "copy": 1,
        },
    )
    assert _url(service, job) == "https://pixeldrain.com/u/second"
