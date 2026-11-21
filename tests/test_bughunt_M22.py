# SPDX-License-Identifier: GPL-3.0-or-later
"""M22: a restarted helper restores an older upload count; uploads after it must still count."""

import pytest

from core.downloads import torrent_settings
from core.downloads.torrents import TorrentSupervisor, _Job  # pyright: ignore[reportPrivateUsage]


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("WLIB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(torrent_settings, "seed_mode", lambda: "always")
    monkeypatch.setattr(torrent_settings, "seed_ratio", lambda: 1.0)
    monkeypatch.setattr(torrent_settings, "seed_hours", lambda: None)


def make_job(sup: TorrentSupervisor, stage: str, seed_state: str = "") -> _Job:
    job = _Job(1, "Game", "1", 7)
    job.stage = stage
    job.seed_state = seed_state
    job.total = 100
    sup._jobs[job.id] = job  # pyright: ignore[reportPrivateUsage]
    return job


def status(
    sup: TorrentSupervisor, job: _Job, uploaded: int, state: str = "seeding"
) -> None:
    sup._on_event(
        {
            "event": "status",
            "torrents": [
                {  # pyright: ignore[reportPrivateUsage]
                    "id": job.id,
                    "state": state,
                    "size": 100,
                    "downloaded": 100 if state == "seeding" else 30,
                    "down": 0,
                    "up": 0,
                    "peers": 1,
                    "uploaded": uploaded,
                }
            ],
        }
    )


def helper_restart(sup: TorrentSupervisor) -> None:
    # A new helper says "ready"; the supervisor adds the torrent to it again.
    sup._on_event({"event": "ready"})  # pyright: ignore[reportPrivateUsage]


def test_uploads_after_a_helper_restart_count_toward_the_ratio():
    sup = TorrentSupervisor(clock=lambda: 1000.0)
    job = make_job(sup, "done", "seeding")
    helper_restart(sup)
    status(sup, job, 80)
    assert job.uploaded == 80
    helper_restart(sup)
    status(sup, job, 70)  # resume.dat held an older count
    assert job.uploaded == 80
    status(sup, job, 90)  # 20 more bytes uploaded by the new helper
    assert job.uploaded == 100
    assert job.seed_state == "ended"


def test_upload_count_during_download_survives_a_helper_restart():
    sup = TorrentSupervisor(clock=lambda: 1000.0)
    job = make_job(sup, "downloading")
    helper_restart(sup)
    status(sup, job, 40, state="downloading")
    helper_restart(sup)
    status(sup, job, 30, state="downloading")
    assert job.uploaded == 40
    status(sup, job, 35, state="downloading")
    assert job.uploaded == 45


def test_saved_count_is_kept_when_the_helper_restores_a_lower_one():
    sup = TorrentSupervisor(clock=lambda: 1000.0)
    job = make_job(sup, "done", "seeding")
    job.uploaded = 50  # from state.json
    helper_restart(sup)
    status(sup, job, 10)
    assert job.uploaded == 50
    status(sup, job, 25)
    assert job.uploaded == 65


def test_saved_count_lower_than_the_helper_count_takes_the_helper_count():
    sup = TorrentSupervisor(clock=lambda: 1000.0)
    job = make_job(sup, "done", "seeding")
    job.uploaded = 20  # state.json is older than resume.dat
    helper_restart(sup)
    status(sup, job, 30)
    assert job.uploaded == 30
    status(sup, job, 30)
    assert job.uploaded == 30
