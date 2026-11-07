# SPDX-License-Identifier: GPL-3.0-or-later
import json
import os
import sys
import time

import pytest

from core.downloads import seeding, torrent_settings, torrents
from core.downloads.torrents import TorrentSupervisor
from test_torrents import FAKE, GIB, log_commands, stage, torrent_bytes, wait_for  # type: ignore[import-not-found]  # noqa: F401


def test_decide_after_finish():
    assert seeding.decide_after_finish("ask") == seeding.ASK
    assert seeding.decide_after_finish("always") == seeding.SEED
    assert seeding.decide_after_finish("never") == seeding.STOP
    assert seeding.decide_after_finish("garbage") == seeding.ASK


def test_limit_reached():
    two_gb = 2 * GIB
    assert seeding.limit_reached(two_gb, two_gb, 0, 1.0, None)
    assert not seeding.limit_reached(two_gb - 1, two_gb, 0, 1.0, None)
    assert seeding.limit_reached(int(0.4 * two_gb), two_gb, 12 * 3600, 1.0, 12)
    assert not seeding.limit_reached(0, two_gb, 12 * 3600 - 1, 1.0, 12)
    assert not seeding.limit_reached(10**12, two_gb, 10**9, None, None)
    assert not seeding.limit_reached(10**12, two_gb, 10**9, 0, 0)


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("WLIB_DATA_DIR", str(tmp_path / "data"))
    return tmp_path / "data"


@pytest.fixture
def settings(monkeypatch):
    values = {"mode": "ask", "ratio": 1.0, "hours": None}
    monkeypatch.setattr(torrent_settings, "seed_mode", lambda: values["mode"])
    monkeypatch.setattr(torrent_settings, "seed_ratio", lambda: values["ratio"])
    monkeypatch.setattr(torrent_settings, "seed_hours", lambda: values["hours"])
    return values


@pytest.fixture
def make(tmp_path):
    created: list[TorrentSupervisor] = []
    clock = [1000.0]

    def build(mode="complete"):
        env = {**os.environ, "FAKE_MODE": mode, "FAKE_LOG": str(tmp_path / "fake.log"), "FAKE_STATE": str(tmp_path / "fake.count")}
        sup = TorrentSupervisor(
            command=lambda: ([sys.executable, FAKE], env),
            backoff=(0.05,), free_space_fn=lambda path: 100 * GIB,
            clock=lambda: clock[0], options=lambda: {},
        )
        created.append(sup)
        return sup

    build.clock = clock  # type: ignore[attr-defined]
    yield build
    for sup in created:
        sup._closing = True  # pyright: ignore[reportPrivateUsage]
        proc = sup._proc  # pyright: ignore[reportPrivateUsage]
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.wait()


def snap(sup, job_id):
    return next(j for j in sup.status() if j["job_id"] == job_id)


def finish(sup, game=1):
    job_id = sup.start(game, "Game", "1", 7, lambda: torrent_bytes())
    wait_for(lambda: stage(sup, job_id) == "done")
    return job_id


def folder_of(data_dir):
    return data_dir / "downloads" / "torrents" / torrents.parse_torrent(torrent_bytes()).info_hash


def status_event(sup, job_id, uploaded=0, state="seeding", up=0):
    sup._on_event({"event": "status", "torrents": [{"id": job_id, "state": state, "size": 100, "downloaded": 100, "down": 0, "up": up, "peers": 1, "uploaded": uploaded}]})  # pyright: ignore[reportPrivateUsage]


def test_ask_keeps_seeding_while_question_open(make, settings, tmp_path):
    sup = make()
    job_id = finish(sup)
    assert snap(sup, job_id)["seed_state"] == "awaiting_answer"
    assert "stop_seeding" not in [c["cmd"] for c in log_commands(tmp_path)]


def test_always_seeds_without_asking(make, settings):
    settings["mode"] = "always"
    sup = make()
    job_id = finish(sup)
    assert snap(sup, job_id)["seed_state"] == "seeding"


def test_never_stops_but_keeps_files_until_install_releases_them(make, settings, tmp_path, data_dir):
    settings["mode"] = "never"
    sup = make()
    job_id = finish(sup)
    assert snap(sup, job_id)["seed_state"] == "ended"
    wait_for(lambda: "stop_seeding" in [c["cmd"] for c in log_commands(tmp_path)])
    folder = folder_of(data_dir)
    assert folder.exists()
    data = snap(sup, job_id)["archive_path"]
    sup.mark_open(data)
    sup.release(data, installed=True)
    assert not folder.exists()


def test_install_dialog_open_keeps_folder_after_declining(make, settings, data_dir):
    sup = make()
    job_id = finish(sup)
    folder = folder_of(data_dir)
    archive = snap(sup, job_id)["archive_path"]
    sup.mark_open(archive)
    assert sup.answer_seed(job_id, False)
    assert folder.exists()
    sup.release(archive, installed=True)
    assert not folder.exists()


def test_seed_answer_and_dont_ask_again(make, settings, monkeypatch, tmp_path):
    from core import database

    saved: dict[str, str] = {}
    monkeypatch.setattr(database, "update_setting", lambda key, value: saved.update({key: value}))
    sup = make()
    job_id = finish(sup)
    assert sup.answer_seed(job_id, True, remember=True)
    assert snap(sup, job_id)["seed_state"] == "seeding" and saved == {"torrent_seed_mode": "always"}
    assert not sup.answer_seed(job_id, True)
    second = make()
    other = finish(second, game=2)
    assert second.answer_seed(other, False, remember=True)
    assert saved["torrent_seed_mode"] == "never" and snap(second, other)["seed_state"] == "ended"


def test_ratio_reached_stops_seeding(make, settings, tmp_path, data_dir):
    settings["mode"] = "always"
    sup = make()
    job_id = finish(sup)
    status_event(sup, job_id, uploaded=99)
    assert snap(sup, job_id)["seed_state"] == "seeding"
    status_event(sup, job_id, uploaded=100)
    assert snap(sup, job_id)["seed_state"] == "ended"
    wait_for(lambda: "stop_seeding" in [c["cmd"] for c in log_commands(tmp_path)])


def test_time_limit_reached_first_and_only_seeding_time_counts(make, settings):
    settings["mode"] = "always"
    settings["hours"] = 12
    sup = make()
    job_id = finish(sup)
    clock = make.clock
    job = sup._jobs[job_id]  # pyright: ignore[reportPrivateUsage]
    job.seeding_seconds = 12 * 3600 - 3
    status_event(sup, job_id, uploaded=40)
    clock[0] += 4
    status_event(sup, job_id, uploaded=40, state="paused")
    assert job.seeding_seconds == 12 * 3600 - 3
    clock[0] += 4
    status_event(sup, job_id, uploaded=40)
    assert snap(sup, job_id)["seed_state"] == "ended"


def test_restart_mid_seed_resumes_with_saved_ratio(make, settings, tmp_path, data_dir):
    folder = data_dir / "downloads" / "torrents" / ("cd" * 20)
    (folder / "data").mkdir(parents=True)
    (folder / "meta.torrent").write_bytes(torrent_bytes())
    (folder / "state.json").write_text(json.dumps({
        "job_id": "seedjob", "game_id": 1, "size": 100, "phase": "seeding", "uploaded": 50,
        "seeding_seconds": 600, "updated": time.time(),
    }))
    sup = make("stalled")
    sup.load()
    wait_for(lambda: any(c["cmd"] == "add" for c in log_commands(tmp_path)))
    add = next(c for c in log_commands(tmp_path) if c["cmd"] == "add")
    assert add["paused"] is False
    status_event(sup, "seedjob", uploaded=10)
    assert snap(sup, "seedjob")["ratio"] == 0.5


def test_unanswered_question_survives_restart_without_seeding(make, settings, tmp_path, data_dir):
    first = make()
    job_id = finish(first)
    assert json.loads((folder_of(data_dir) / "state.json").read_text())["phase"] == "awaiting_seed_answer"
    first._closing = True  # pyright: ignore[reportPrivateUsage]
    (tmp_path / "fake.log").unlink()
    second = make("stalled")
    second.load()
    again = snap(second, job_id)
    assert again["seed_state"] == "awaiting_answer"
    wait_for(lambda: any(c["cmd"] == "add" for c in log_commands(tmp_path)))
    assert next(c for c in log_commands(tmp_path) if c["cmd"] == "add")["paused"] is True
    assert second.answer_seed(job_id, True)
    wait_for(lambda: any(c["cmd"] == "resume" for c in log_commands(tmp_path)))


def test_stop_seeding_action(make, settings, tmp_path):
    settings["mode"] = "always"
    sup = make()
    job_id = finish(sup)
    assert sup.stop_seeding(job_id)
    assert snap(sup, job_id)["seed_state"] == "ended" and not sup.stop_seeding(job_id)


def test_ended_torrent_never_installed_is_kept(make, settings, data_dir):
    settings["mode"] = "never"
    sup = make()
    finish(sup)
    sup.cleanup(now=time.time())
    assert folder_of(data_dir).exists()


def test_speed_limits_reach_a_running_helper(make, monkeypatch, tmp_path):
    sup = make("stalled")
    sup.start(1, "Game", "1", 7, lambda: torrent_bytes())
    wait_for(lambda: any(c["cmd"] == "add" for c in log_commands(tmp_path)))
    monkeypatch.setattr(torrent_settings, "limits_kib", lambda: (100, 50))
    sup.apply_limits()
    wait_for(lambda: any(c["cmd"] == "set_limits" for c in log_commands(tmp_path)))
    cmd = next(c for c in log_commands(tmp_path) if c["cmd"] == "set_limits")
    assert cmd["download_kib"] == 100 and cmd["upload_kib"] == 50
