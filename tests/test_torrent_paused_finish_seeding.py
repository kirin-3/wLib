# SPDX-License-Identifier: GPL-3.0-or-later
# pyright: reportPrivateUsage=false
"""A torrent that finishes while it is paused (user pause or queue hold) seeds per the setting."""
import json

import pytest

from core.downloads import torrent_settings
from core.downloads.torrents import TorrentSupervisor, _Job


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("WLIB_DATA_DIR", str(tmp_path / "data"))
    return tmp_path / "data"


def _setup(tmp_path, monkeypatch, mode):
    monkeypatch.setattr(torrent_settings, "seed_mode", lambda: mode)
    monkeypatch.setattr(torrent_settings, "seed_ratio", lambda: 1.0)
    monkeypatch.setattr(torrent_settings, "seed_hours", lambda: None)
    sup = TorrentSupervisor(command=lambda: (["false"], {}), free_space_fn=lambda path: 10**12, clock=lambda: 1000.0, options=lambda: {})
    sent: list[dict[str, object]] = []

    def send(command: dict[str, object]) -> bool:
        sent.append(command)
        return True

    monkeypatch.setattr(sup, "_send", send)
    job = _Job(1, "Game", "1", 7)
    folder = tmp_path / "torrent"
    folder.mkdir()
    job.folder = folder
    job.info_hash = "abc"
    job.total = 100
    job.bytes = 60
    job.stage = job.phase = "paused"
    job.paused = True
    job.added = True
    sup._jobs[job.id] = job
    return sup, job, sent, folder


@pytest.mark.parametrize("auto_paused", [False, True])
def test_always_seed_resumes_a_torrent_that_finished_paused(tmp_path, monkeypatch, auto_paused):
    sup, job, sent, folder = _setup(tmp_path, monkeypatch, "always")
    job.auto_paused = auto_paused
    sup._on_event({"event": "finished", "id": job.id})
    assert job.stage == "done" and job.seed_state == "seeding"
    assert job.paused is False and job.auto_paused is False
    assert {"cmd": "resume", "id": job.id} in sent
    state = json.loads((folder / "state.json").read_text())
    assert state["phase"] == "seeding" and state["paused"] is False
    # Seeding status now counts time and can reach the limits.
    sup._on_event({"event": "status", "torrents": [{"id": job.id, "state": "seeding", "up": 5, "peers": 1, "uploaded": 100}]})
    assert job.seed_state == "ended"
    assert {"cmd": "stop_seeding", "id": job.id} in sent


def test_ask_keeps_a_torrent_that_finished_paused_paused(tmp_path, monkeypatch):
    sup, job, sent, _ = _setup(tmp_path, monkeypatch, "ask")
    sup._on_event({"event": "finished", "id": job.id})
    assert job.seed_state == "awaiting_answer" and job.paused is True
    assert {"cmd": "resume", "id": job.id} not in sent
    assert sup.answer_seed(job.id, True)
    assert job.paused is False and {"cmd": "resume", "id": job.id} in sent


def test_never_seed_ends_a_torrent_that_finished_paused(tmp_path, monkeypatch):
    sup, job, sent, _ = _setup(tmp_path, monkeypatch, "never")
    sup._on_event({"event": "finished", "id": job.id})
    assert job.seed_state == "ended" and job.added is False
    assert sent == [{"cmd": "stop_seeding", "id": job.id}]


def test_always_seed_after_unpaused_finish_sends_no_resume(tmp_path, monkeypatch):
    sup, job, sent, _ = _setup(tmp_path, monkeypatch, "always")
    job.paused = False
    job.stage = job.phase = "downloading"
    sup._on_event({"event": "finished", "id": job.id})
    assert job.seed_state == "seeding" and sent == []
