# SPDX-License-Identifier: GPL-3.0-or-later
import json
import time
from unittest.mock import Mock

import pytest

from core.database import init_db, list_download_jobs
from core.downloads.queue import READY, DownloadQueue
from core.downloads.torrents import TorrentSupervisor, torrents_dir
from core.downloads.workers import TorrentWorker


def _load_downloads(monkeypatch: pytest.MonkeyPatch) -> tuple[TorrentSupervisor, DownloadQueue]:
    supervisor = TorrentSupervisor()
    spawn = Mock(return_value=True)
    monkeypatch.setattr(supervisor, "_spawn", spawn)
    supervisor.load()
    spawn.assert_called_once_with()
    queue = DownloadQueue([TorrentWorker(lambda: supervisor)], run_thread=False)
    queue.load()
    return supervisor, queue


@pytest.mark.parametrize(
    ("phase", "seed_state"), [("seeding", "seeding"), ("awaiting_seed_answer", "awaiting_answer")]
)
def test_removed_torrent_stays_out_of_queue_after_restart(
    monkeypatch: pytest.MonkeyPatch, phase: str, seed_state: str
) -> None:
    init_db()
    folder = torrents_dir() / ("a" * 40)
    (folder / "data").mkdir(parents=True)
    _ = (folder / "state.json").write_text(json.dumps({
        "job_id": "seed-job", "game_id": 1, "rpdl_id": 7, "title": "Game", "version": "1",
        "phase": phase, "size": 100, "downloaded": 100, "updated": time.time(),
    }), encoding="utf-8")

    supervisor, queue = _load_downloads(monkeypatch)
    # An older state without a queue row is still eligible for adoption.
    assert [(job.id, job.state) for job in queue.jobs()] == [("seed-job", READY)]
    assert queue.remove("seed-job")
    assert list_download_jobs() == []
    assert supervisor.status()[0]["seed_state"] == seed_state
    assert folder.is_dir()

    restarted_supervisor, restarted_queue = _load_downloads(monkeypatch)
    assert restarted_supervisor.status()[0]["seed_state"] == seed_state
    assert restarted_queue.jobs() == []
    assert list_download_jobs() == []
    assert restarted_supervisor.stop_seeding("seed-job")


def test_removed_torrent_held_by_install_dialog_is_deleted_on_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    init_db()
    folder = torrents_dir() / ("b" * 40)
    (folder / "data").mkdir(parents=True)
    _ = (folder / "state.json").write_text(json.dumps({
        "job_id": "held-job", "game_id": 2, "rpdl_id": 8, "title": "Game", "version": "1",
        "phase": "finished", "size": 100, "downloaded": 100, "updated": time.time(),
    }), encoding="utf-8")

    # A finished torrent that does not seed needs no helper process.
    supervisor = TorrentSupervisor()
    monkeypatch.setattr(supervisor, "_spawn", Mock(return_value=True))
    supervisor.load()
    queue = DownloadQueue([TorrentWorker(lambda: supervisor)], run_thread=False)
    queue.load()
    assert [job.id for job in queue.jobs()] == ["held-job"]
    supervisor.mark_open(str(folder / "data"))
    assert queue.remove("held-job")
    # The dialog still holds the files, so they stay but are marked as removed.
    assert folder.is_dir()
    assert supervisor.status()[0]["queue_removed"] is True

    supervisor.release(str(folder / "data"))
    assert not folder.exists()
    assert supervisor.status() == []
