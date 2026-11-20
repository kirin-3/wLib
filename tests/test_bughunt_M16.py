# SPDX-License-Identifier: GPL-3.0-or-later
"""M16: malformed RPDL data or an unexpected error must not leave a torrent job in "fetching"."""
from pathlib import Path

import pytest

from core.api_mixins.base import ApiBase
from core.downloads import torrents
from core.downloads.hosts import rpdl
from core.downloads.torrents import MSG_START_FAILED, TorrentSupervisor

_TORRENT = (
    b"d8:announce26:http://tracker.example/ann4:infod6:lengthi100e4:name4:game"
    + b"12:piece lengthi16384e6:pieces20:"
    + b"x" * 20
    + b"ee"
)


class _InlineThread:
    """Runs the target at start(), so the test sees the job's final stage."""

    def __init__(self, target: object, args: tuple[object, ...] = (), daemon: bool = False) -> None:
        self._target = target
        self._args = args

    def start(self) -> None:
        assert callable(self._target)
        self._target(*self._args)


@pytest.mark.parametrize("number", ["NaN", "Infinity"])
def test_malformed_details_do_not_break_the_checked_fetch(monkeypatch: pytest.MonkeyPatch, number: str):
    # json.loads accepts NaN and Infinity; int() of them raises ValueError or OverflowError.
    body = f'{{"data": {{"torrent_id": 7, "title": "Game", "file_size": {number}, "info_hash": "{"a" * 40}"}}}}'
    monkeypatch.setattr(rpdl, "fetch_torrent", lambda _id: _TORRENT)
    monkeypatch.setattr(rpdl, "_request", lambda *_a, **_k: (body.encode(), "application/json"))
    assert ApiBase._fetch_checked_rpdl_torrent(7) == _TORRENT  # pyright: ignore[reportPrivateUsage]


def _supervisor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TorrentSupervisor:
    monkeypatch.setenv("WLIB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(torrents.threading, "Thread", _InlineThread)
    return TorrentSupervisor(command=lambda: (["helper"], {}), options=lambda: {})


def _boom() -> bytes:
    raise RuntimeError("secret passkey text")


def test_unexpected_fetch_error_fails_the_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    sup = _supervisor(tmp_path, monkeypatch)
    job = sup._jobs[sup.start(1, "Game", "1.0", 7, _boom)]  # pyright: ignore[reportPrivateUsage]
    assert job.stage == "failed"
    assert job.error == MSG_START_FAILED
    err = capsys.readouterr().err
    assert "RuntimeError" in err
    assert "secret" not in err


def test_unexpected_error_after_cancel_marks_the_job_cancelled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    sup = _supervisor(tmp_path, monkeypatch)
    holder: dict[str, torrents._Job] = {}  # pyright: ignore[reportPrivateUsage]

    def fetch() -> bytes:
        job = next(iter(sup._jobs.values()))  # pyright: ignore[reportPrivateUsage]
        holder["job"] = job
        job.cancel.set()
        raise RuntimeError("boom")

    _ = sup.start(1, "Game", "1.0", 7, fetch)
    assert holder["job"].stage == "cancelled"
    assert holder["job"].error == ""
