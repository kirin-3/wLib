# SPDX-License-Identifier: GPL-3.0-or-later
"""M12: a bencoded RPDL torrent without valid metadata must fail the job, not strand it in "fetching"."""

from pathlib import Path

import pytest

from core.api_mixins.base import ApiBase
from core.downloads import torrents
from core.downloads.hosts import rpdl
from core.downloads.torrents import MSG_INVALID, TorrentSupervisor
from core.downloads.transfer import DownloadError

# Valid bencode, but no "info" dictionary: parse_torrent raises ValueError.
_NO_INFO = b"d8:announce3:abce"
_HASH = "a" * 40


@pytest.fixture
def rpdl_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rpdl, "fetch_torrent", lambda _id: _NO_INFO)
    monkeypatch.setattr(rpdl, "details", lambda _id: {"info_hash": _HASH})


@pytest.mark.usefixtures("rpdl_reply")
def test_checked_fetch_turns_a_parse_error_into_the_invalid_torrent_error():
    with pytest.raises(DownloadError) as caught:
        ApiBase._fetch_checked_rpdl_torrent(7)  # pyright: ignore[reportPrivateUsage]
    assert str(caught.value) == MSG_INVALID


@pytest.mark.usefixtures("rpdl_reply")
def test_job_with_unparseable_rpdl_torrent_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("WLIB_DATA_DIR", str(tmp_path / "data"))
    sup = TorrentSupervisor(command=lambda: (["helper"], {}), options=lambda: {})
    monkeypatch.setattr(torrents.threading, "Thread", _InlineThread)
    job_id = sup.start(
        1,
        "Game",
        "1.0",
        7,
        lambda: ApiBase._fetch_checked_rpdl_torrent(7),  # pyright: ignore[reportPrivateUsage]
    )
    job = sup._jobs[job_id]  # pyright: ignore[reportPrivateUsage]
    assert job.stage == "failed"
    assert job.error == MSG_INVALID


class _InlineThread:
    """Runs the target at start(), so the test sees the job's final stage."""

    def __init__(
        self, target: object, args: tuple[object, ...] = (), daemon: bool = False
    ) -> None:
        self._target = target
        self._args = args

    def start(self) -> None:
        assert callable(self._target)
        self._target(*self._args)
