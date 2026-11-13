# SPDX-License-Identifier: GPL-3.0-or-later
"""TorrentSupervisor against a fake helper process that speaks the real protocol."""
import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

from core.downloads import torrents
from core.downloads.bencode import parse_torrent
from core.downloads.manager import manager
from core.downloads.torrents import (
    MSG_INVALID,
    MSG_KEEPS_STOPPING,
    MSG_LOW_DISK,
    MSG_RESTARTING,
    MSG_UNAVAILABLE,
    TorrentSupervisor,
)
from core.downloads.transfer import DownloadError

FAKE = str(Path(__file__).with_name("fake_torrent_helper.py"))
PASSKEY = "PASSKEY0123456789"
GIB = 1024**3


def torrent_bytes(size: int = 100, name: str = "game") -> bytes:
    announce = f"http://tracker.example/announce/{PASSKEY}?k=SECRETQUERY"
    return (
        f"d8:announce{len(announce)}:{announce}4:infod6:lengthi{size}e4:name{len(name)}:{name}"
        f"12:piece lengthi16384e6:pieces20:".encode()
        + b"x" * 20
        + b"7:privatei1eee"
    )


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("WLIB_DATA_DIR", str(tmp_path / "data"))
    return tmp_path / "data"


@pytest.fixture(autouse=True)
def default_seed_mode(monkeypatch):
    monkeypatch.setattr(torrents.torrent_settings, "seed_mode", lambda: "ask")


@pytest.fixture
def make(tmp_path, monkeypatch):
    created: list[TorrentSupervisor] = []

    def build(mode="complete", free=100 * GIB, **extra):
        env = {**os.environ, "FAKE_MODE": mode, "FAKE_LOG": str(tmp_path / "fake.log"), "FAKE_STATE": str(tmp_path / "fake.count"), **extra}
        sup = TorrentSupervisor(
            command=lambda: ([sys.executable, FAKE], env),
            backoff=(0.05, 0.05, 0.05),
            free_space_fn=lambda path: free,
        )
        created.append(sup)
        return sup

    yield build
    for sup in created:
        sup._closing = True  # pyright: ignore[reportPrivateUsage]
        proc = sup._proc  # pyright: ignore[reportPrivateUsage]
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.wait()


def wait_for(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError("condition not met")


def job_of(sup, job_id):
    return next(j for j in sup.status() if j["job_id"] == job_id)


def stage(sup, job_id):
    return job_of(sup, job_id)["stage"]


def log_commands(tmp_path) -> list[dict]:
    path = tmp_path / "fake.log"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def test_parse_torrent_reads_hash_size_and_private():
    meta = parse_torrent(torrent_bytes(size=123))
    assert meta.size == 123 and meta.private and len(meta.info_hash) == 40
    with pytest.raises(ValueError):
        parse_torrent(b"d3:fooi1ee")


def test_download_finishes_and_lays_out_the_folder(make, data_dir):
    sup = make()
    job_id = sup.start(1, "Game", "1.0", 7, lambda: torrent_bytes())
    wait_for(lambda: stage(sup, job_id) == "done")
    snap = job_of(sup, job_id)
    folder = data_dir / "downloads" / "torrents" / parse_torrent(torrent_bytes()).info_hash
    assert {p.name for p in folder.iterdir()} >= {"meta.torrent", "state.json", "data"}
    assert snap["kind"] == "torrent" and snap["archive_path"] == str(folder / "data" / "game.zip")
    assert json.loads((folder / "state.json").read_text())["phase"] == "awaiting_seed_answer"


def test_one_active_download_per_game(make):
    sup = make("stalled")
    sup.start(1, "Game", "1", 7, lambda: torrent_bytes())
    with pytest.raises(DownloadError):
        sup.start(1, "Game", "1", 7, lambda: torrent_bytes())


def test_fetch_failure_and_invalid_torrent(make):
    sup = make()

    def boom():
        raise DownloadError("Sign in to RPDL in Settings")

    first = sup.start(1, "A", "1", 1, boom)
    wait_for(lambda: stage(sup, first) == "failed")
    assert job_of(sup, first)["error"] == "Sign in to RPDL in Settings"
    second = sup.start(2, "B", "1", 2, lambda: b"<html>")
    wait_for(lambda: stage(sup, second) == "failed")
    assert job_of(sup, second)["error"] == MSG_INVALID


def test_disk_space_checked_before_start(make):
    sup = make(free=1 * GIB)
    job_id = sup.start(1, "Game", "1", 7, lambda: torrent_bytes(size=6 * GIB))
    wait_for(lambda: stage(sup, job_id) == "failed")
    error = job_of(sup, job_id)["error"]
    assert "6.0 GiB needed" in error and "1.0 GiB free" in error
    assert not list((Path(os.environ["WLIB_DATA_DIR"]) / "downloads" / "torrents").glob("*/meta.torrent"))


def test_low_disk_pauses_the_torrent(make, tmp_path):
    sup = make("stalled", free=100 * 1024 * 1024)
    job_id = sup.start(1, "Game", "1", 7, lambda: torrent_bytes())
    wait_for(lambda: stage(sup, job_id) == "paused")
    snap = job_of(sup, job_id)
    assert snap["error"] == MSG_LOW_DISK and snap["paused"] is True
    wait_for(lambda: any(c["cmd"] == "pause" for c in log_commands(tmp_path)))


def test_resume_refused_while_disk_is_low(make):
    sup = make("stalled", free=100 * 1024 * 1024)
    job_id = sup.start(1, "Game", "1", 7, lambda: torrent_bytes())
    wait_for(lambda: stage(sup, job_id) == "paused")
    assert sup.resume(job_id) is False


def test_peer_and_data_hints(make):
    now = [1000.0]
    sup = TorrentSupervisor(command=lambda: ([sys.executable, FAKE], {**os.environ}), clock=lambda: now[0], free_space_fn=lambda p: 100 * GIB)
    job = torrents._Job(1, "G", "1", 1)  # pyright: ignore[reportPrivateUsage]
    job.stage = "downloading"
    job.total = 100
    job.folder = None
    job.peerless_since = 1000.0
    sup._jobs[job.id] = job  # pyright: ignore[reportPrivateUsage]

    def status(peers, down):
        sup._on_event({"event": "status", "torrents": [{"id": job.id, "state": "downloading", "size": 100, "downloaded": 10, "down": down, "up": 0, "peers": peers, "uploaded": 0}]})  # pyright: ignore[reportPrivateUsage]
        return job_of(sup, job.id)["hint"]

    assert status(0, 0) == "Finding peers"
    now[0] += 21
    assert status(0, 0) == "Looking for peers"
    assert status(3, 0) == ""
    now[0] += 121
    assert status(3, 0) == "Connected, but no data is arriving"
    assert status(3, 5000) == ""


def test_single_crash_restarts_and_resumes(make, tmp_path):
    sup = make("crash_once")
    job_id = sup.start(1, "Game", "1", 7, lambda: torrent_bytes())
    wait_for(lambda: job_of(sup, job_id)["bytes"] == 30)
    assert job_of(sup, job_id)["stage"] == "downloading" and job_of(sup, job_id)["hint"] == ""
    adds = [c for c in log_commands(tmp_path) if c["cmd"] == "add"]
    assert len(adds) == 2


def test_restarting_message_while_helper_is_down(make):
    sup = make("crash_once")
    sup._backoff = (0.5,)  # pyright: ignore[reportPrivateUsage]
    job_id = sup.start(1, "Game", "1", 7, lambda: torrent_bytes())
    wait_for(lambda: job_of(sup, job_id)["hint"] == MSG_RESTARTING)


def test_repeated_crashes_give_up_and_keep_data(make, data_dir):
    sup = make("crash_always")
    job_id = sup.start(1, "Game", "1", 7, lambda: torrent_bytes())
    wait_for(lambda: stage(sup, job_id) == "failed", timeout=20)
    assert job_of(sup, job_id)["error"] == MSG_KEEPS_STOPPING
    folder = data_dir / "downloads" / "torrents" / parse_torrent(torrent_bytes()).info_hash
    assert (folder / "meta.torrent").is_file()


def test_missing_engine_is_reported(make):
    sup = make("noimport")
    job_id = sup.start(1, "Game", "1", 7, lambda: torrent_bytes())
    wait_for(lambda: stage(sup, job_id) == "failed")
    assert job_of(sup, job_id)["error"] == MSG_UNAVAILABLE


def test_unsafe_torrent_fails_and_leaves_no_folder(make, data_dir):
    sup = make("badpaths")
    job_id = sup.start(1, "Game", "1", 7, lambda: torrent_bytes())
    wait_for(lambda: stage(sup, job_id) == "failed")
    assert job_of(sup, job_id)["error"] == MSG_INVALID
    assert not any((data_dir / "downloads" / "torrents").iterdir())


def test_pause_resume_cancel(make, tmp_path, data_dir):
    sup = make("stalled")
    job_id = sup.start(1, "Game", "1", 7, lambda: torrent_bytes())
    wait_for(lambda: job_of(sup, job_id)["bytes"] == 30)
    assert sup.pause(job_id) and stage(sup, job_id) == "paused"
    assert sup.resume(job_id) and stage(sup, job_id) == "downloading"
    wait_for(lambda: {"pause", "resume"} <= {c["cmd"] for c in log_commands(tmp_path)})
    folder = data_dir / "downloads" / "torrents" / parse_torrent(torrent_bytes()).info_hash
    assert sup.cancel(job_id)
    assert stage(sup, job_id) == "cancelled" and not folder.exists()


def test_restart_readds_jobs_paused_and_remembers_the_user_pause(make, tmp_path, data_dir):
    root = data_dir / "downloads" / "torrents"
    for name, paused in (("aa" * 20, False), ("bb" * 20, True)):
        folder = root / name
        folder.mkdir(parents=True)
        (folder / "meta.torrent").write_bytes(torrent_bytes())
        (folder / "state.json").write_text(json.dumps({
            "job_id": name[:8], "game_id": 1 if not paused else 2, "title": "G", "version": "1", "size": 100,
            "phase": "paused" if paused else "downloading", "paused": paused, "updated": time.time(),
        }))
    sup = make("stalled")
    sup.load()
    wait_for(lambda: len([c for c in log_commands(tmp_path) if c["cmd"] == "add"]) == 2)
    adds = {c["id"]: c["paused"] for c in log_commands(tmp_path) if c["cmd"] == "add"}
    # The download queue decides when a torrent moves data again: both wait paused.
    assert adds == {"aaaaaaaa": True, "bbbbbbbb": True}
    assert {j["job_id"]: (j["stage"], j["loaded_paused"]) for j in sup.status()} == {
        "aaaaaaaa": ("paused", False), "bbbbbbbb": ("paused", True),
    }


def test_cleanup_orphans_old_paused_and_keeps_seeding(make, data_dir, tmp_path):
    root = data_dir / "downloads" / "torrents"
    now = time.time()

    def folder(name, phase, age_days):
        path = root / name
        path.mkdir(parents=True)
        (path / "data").mkdir()
        (path / "state.json").write_text(json.dumps({"job_id": name, "game_id": 1, "phase": phase, "paused": phase == "paused", "updated": now - age_days * 86400}))
        return path

    young = folder("young", "paused", 3)
    old = folder("old", "paused", 31)
    seeding = folder("seeding", "seeding", 10)
    failed_old = folder("failedold", "failed", 40)
    orphan = root / "orphan"
    (orphan / "data").mkdir(parents=True)
    sup = make("stalled")
    sup.load()
    # load() deletes the expired job before the helper can open its files.
    wait_for(lambda: any(c["cmd"] == "add" and c["id"] == "young" for c in log_commands(tmp_path)))
    assert "old" not in {c["id"] for c in log_commands(tmp_path) if c["cmd"] == "add"}
    sup.cleanup(now=now)
    assert young.exists() and seeding.exists()
    assert not old.exists() and not failed_old.exists() and not orphan.exists()


def test_cleanup_protects_open_folders(make, data_dir):
    root = data_dir / "downloads" / "torrents"
    held = root / "held"
    (held / "data").mkdir(parents=True)
    sup = make("stalled")
    sup.mark_open(str(held / "data"))
    sup.cleanup(now=time.time())
    assert held.exists()
    sup.release(str(held / "data"))
    sup.cleanup(now=time.time())
    assert not held.exists()


def test_download_manager_cleanup_leaves_torrent_area_alone(data_dir):
    folder = data_dir / "downloads" / "torrents" / "abc" / "data"
    folder.mkdir(parents=True)
    old = folder / "big.zip"
    old.write_bytes(b"x")
    os.utime(old, (1, 1))
    manager.cleanup()
    assert old.exists()


def test_forget_game_stops_seeding_torrent_and_deletes_folder(make, data_dir):
    root = data_dir / "downloads" / "torrents"
    folder = root / "seed1"
    (folder / "data").mkdir(parents=True)
    (folder / "state.json").write_text(json.dumps({"job_id": "s1", "game_id": 9, "phase": "seeding", "updated": time.time()}))
    sup = make("stalled")
    sup.load()
    sup.forget_game(9)
    assert not folder.exists() and sup.status() == []


def test_delete_game_forgets_its_torrents(data_dir, monkeypatch):
    from core.api import Api
    from core.database import add_game

    seen: list[int] = []
    monkeypatch.setattr(torrents.supervisor, "forget_game", seen.append)
    api = Api()
    game_id = add_game(title="Game", exe_path="index.html")
    assert api.delete_game(game_id)["success"] is True
    assert seen == [game_id]


def test_log_holds_no_passkey_or_announce_url(make, capsys, caplog, data_dir):
    sup = make()
    job_id = sup.start(1, "Game", "1", 7, lambda: torrent_bytes())
    wait_for(lambda: stage(sup, job_id) == "done")
    sup.shutdown()
    captured = capsys.readouterr()
    text = captured.out + captured.err + caplog.text + json.dumps(sup.status())
    for needle in (PASSKEY, "SECRETQUERY", "announce", "magnet:"):
        assert needle not in text
    state = next((data_dir / "downloads" / "torrents").glob("*/state.json")).read_text()
    assert PASSKEY not in state


def test_retry_after_write_error_adds_the_torrent_again(make, tmp_path):
    sup = make("error_once")
    job_id = sup.start(1, "Game", "1.0", 7, lambda: torrent_bytes())
    wait_for(lambda: stage(sup, job_id) == "failed")
    assert job_of(sup, job_id)["error"] == "Files could not be written"
    wait_for(lambda: any(c["cmd"] == "remove" for c in log_commands(tmp_path)))

    assert sup.resume(job_id)
    wait_for(lambda: sum(c["cmd"] == "add" for c in log_commands(tmp_path)) == 2)
    assert stage(sup, job_id) == "downloading"


def test_pause_before_the_helper_is_ready_does_not_fail_the_job(make, tmp_path):
    sup = make("stalled", FAKE_READY_DELAY="0.5")
    job_id = sup.start(1, "Game", "1.0", 7, lambda: torrent_bytes())
    wait_for(lambda: stage(sup, job_id) == "downloading")
    assert sup.pause(job_id)
    adds = wait_for(lambda: [c for c in log_commands(tmp_path) if c["cmd"] == "add"])
    assert adds[0]["paused"] is True
    time.sleep(0.2)
    assert stage(sup, job_id) == "paused"
    assert not any(c["cmd"] == "pause" for c in log_commands(tmp_path))


def test_unanswered_seed_question_pauses_the_torrent(make, tmp_path):
    sup = make()
    job_id = sup.start(1, "Game", "1.0", 7, lambda: torrent_bytes())
    wait_for(lambda: job_of(sup, job_id)["seed_state"] == "awaiting_answer")
    wait_for(lambda: any(c["cmd"] == "pause" and c["id"] == job_id for c in log_commands(tmp_path)))
    assert job_of(sup, job_id)["paused"] is True

    assert sup.answer_seed(job_id, True)
    wait_for(lambda: any(c["cmd"] == "resume" and c["id"] == job_id for c in log_commands(tmp_path)))
    assert job_of(sup, job_id)["seed_state"] == "seeding" and stage(sup, job_id) == "done"


def test_second_job_for_the_same_torrent_does_not_share_a_live_folder(make):
    sup = make()
    first = sup.start(1, "Game", "1.0", 7, lambda: torrent_bytes())
    wait_for(lambda: stage(sup, first) == "done")
    assert job_of(sup, first)["seed_state"] == "awaiting_answer"

    # Still waiting on the seed question: the folder is in use.
    second = sup.start(1, "Game", "1.0", 7, lambda: torrent_bytes())
    wait_for(lambda: stage(sup, second) == "failed")
    assert "already in your downloads" in job_of(sup, second)["error"]
    assert stage(sup, first) == "done"

    # Once nothing uses it, a new job takes the folder over.
    assert sup.answer_seed(first, False)
    third = sup.start(1, "Game", "1.0", 7, lambda: torrent_bytes())
    wait_for(lambda: stage(sup, third) == "done")
    info_hash = parse_torrent(torrent_bytes()).info_hash
    owners = [j for j in sup._jobs.values() if j.info_hash == info_hash]  # pyright: ignore[reportPrivateUsage]
    assert [j.id for j in owners] == [third]


def _torrent_folders():
    root = Path(os.environ["WLIB_DATA_DIR"]) / "downloads" / "torrents"
    return [p for p in root.iterdir() if p.is_dir()] if root.is_dir() else []


def test_cancel_during_disk_check_stops_the_start(make, tmp_path):
    sup = make()
    checking, release = threading.Event(), threading.Event()

    def slow_free(path):
        checking.set()
        _ = release.wait(5)
        return 100 * GIB

    sup._free_space = slow_free  # pyright: ignore[reportPrivateUsage]
    job_id = sup.start(1, "Game", "1", 7, lambda: torrent_bytes())
    assert checking.wait(5)
    assert sup.cancel(job_id)
    release.set()
    wait_for(lambda: stage(sup, job_id) == "cancelled")
    time.sleep(0.2)
    assert stage(sup, job_id) == "cancelled"
    assert _torrent_folders() == []
    assert not any(c["cmd"] == "add" for c in log_commands(tmp_path))


def test_cancel_while_files_are_written_deletes_the_folder(make, tmp_path, monkeypatch):
    sup = make()
    holder: dict[str, str] = {}
    original = Path.write_bytes

    def write_then_cancel(self, data):
        written = original(self, data)
        if self.name == "meta.torrent":
            _ = sup.cancel(holder["id"])
        return written

    monkeypatch.setattr(Path, "write_bytes", write_then_cancel)
    gate = threading.Event()

    def fetch():
        _ = gate.wait(5)
        return torrent_bytes()

    holder["id"] = sup.start(1, "Game", "1", 7, fetch)
    gate.set()
    wait_for(lambda: stage(sup, holder["id"]) == "cancelled")
    time.sleep(0.2)
    assert stage(sup, holder["id"]) == "cancelled"
    assert _torrent_folders() == []
    assert not any(c["cmd"] == "add" for c in log_commands(tmp_path))


# --- the download queue drives the supervisor (TorrentWorker) ----------------


def _queue(sup, **options):
    from core.database import init_db
    from core.downloads.queue import DownloadQueue
    from core.downloads.workers import TorrentWorker

    init_db()
    return DownloadQueue(
        [TorrentWorker(lambda: sup, fetch_for=lambda rpdl_id: torrent_bytes)],
        run_thread=False, free_space=lambda: 100 * GIB, **options,
    )


def _ticked(queue, predicate, timeout=10.0):
    def check():
        queue.tick()
        return predicate()

    return wait_for(check, timeout)


def _add(queue, game_id, size=100, name="game"):
    from core.downloads.workers import TorrentContext

    data = torrent_bytes(size=size, name=name)
    return queue.add("torrent", game_id, "Game", "1", {"rpdl_id": 7}, TorrentContext(lambda: data)).id


def test_queued_torrent_is_prepared_paused_and_moves_data_after_resume(make, tmp_path):
    from core.database import update_setting
    from core.downloads import queue_settings

    sup = make("stalled")
    queue = _queue(sup)
    update_setting(queue_settings.MAX_ACTIVE_KEY, "1")
    queue.apply_settings()
    first = _add(queue, 1, name="first")
    assert _ticked(queue, lambda: queue.get(first).state == "running")
    second = _add(queue, 2, name="second")
    # Fetched, checked and added to the helper paused, without a slot.
    assert _ticked(queue, lambda: queue.get(second).state == "queued")
    assert wait_for(lambda: any(c["cmd"] == "add" and c["id"] == second for c in log_commands(tmp_path)))
    adds = {c["id"]: c["paused"] for c in log_commands(tmp_path) if c["cmd"] == "add"}
    assert adds[second] is True
    assert stage(sup, second) == "paused"
    assert not any(c["cmd"] == "resume" and c["id"] == second for c in log_commands(tmp_path))
    # The queue gives it the slot: now it moves data.
    assert queue.pause(first)
    assert stage(sup, first) == "paused" and stage(sup, second) == "downloading"
    assert wait_for(lambda: any(c["cmd"] == "resume" and c["id"] == second for c in log_commands(tmp_path)))
    assert queue.get(second).source["info_hash"] == parse_torrent(torrent_bytes(name="second")).info_hash


def test_finished_torrent_becomes_ready(make):
    sup = make()
    queue = _queue(sup)
    job_id = _add(queue, 1)
    assert _ticked(queue, lambda: queue.get(job_id).state == "ready")
    assert queue.get(job_id).details["seed_state"] == "awaiting_answer"


def test_low_disk_pause_needs_the_user(make):
    sup = make("stalled", free=100 * 1024 * 1024)
    queue = _queue(sup)
    job_id = _add(queue, 1)
    assert _ticked(queue, lambda: queue.get(job_id).state == "attention")
    assert queue.get(job_id).error_code == "disk"


@pytest.mark.parametrize(
    ("mode", "code"),
    [("noimport", "engine"), ("badpaths", "invalid"), ("error_once", "write")],
)
def test_supervisor_failures_need_the_user(make, mode, code):
    sup = make(mode)
    queue = _queue(sup)
    job_id = _add(queue, 1)
    assert _ticked(queue, lambda: queue.get(job_id).state == "attention", timeout=20)
    assert queue.get(job_id).error_code == code


def test_fetch_failure_needs_the_user(make):
    sup = make("stalled")
    queue = _queue(sup)

    def boom():
        raise DownloadError("Sign in to RPDL in Settings")

    from core.downloads.workers import TorrentContext

    job_id = queue.add("torrent", 1, "Game", "1", {"rpdl_id": 7}, TorrentContext(boom)).id
    assert _ticked(queue, lambda: queue.get(job_id).state == "attention")
    assert queue.get(job_id).error_code == "sign_in"
    # Retry fetches the .torrent again (fetch_for) and runs.
    assert queue.retry(job_id)
    assert _ticked(queue, lambda: queue.get(job_id).state == "running")


def test_engine_restart_stays_running(make):
    sup = make("crash_once")
    sup._backoff = (0.5,)  # pyright: ignore[reportPrivateUsage]
    queue = _queue(sup)
    job_id = _add(queue, 1)
    assert _ticked(queue, lambda: job_of(sup, job_id)["hint"] == MSG_RESTARTING)
    queue.tick()
    assert queue.get(job_id).state == "running"
    assert _ticked(queue, lambda: job_of(sup, job_id)["bytes"] == 30)
    assert queue.get(job_id).state == "running"


def test_queue_adopts_torrents_from_an_earlier_version(make, tmp_path, data_dir):
    root = data_dir / "downloads" / "torrents"
    for name, paused, created in (("aa" * 20, False, 2.0), ("bb" * 20, True, 1.0)):
        folder = root / name
        folder.mkdir(parents=True)
        (folder / "meta.torrent").write_bytes(torrent_bytes())
        (folder / "state.json").write_text(json.dumps({
            "job_id": name[:8], "game_id": 1 if not paused else 2, "rpdl_id": 7, "title": "G",
            "version": "1", "size": 100, "phase": "paused" if paused else "downloading",
            "paused": paused, "created": created, "updated": time.time(),
        }))
    sup = make("stalled")
    sup.load()
    queue = _queue(sup)
    queue.load()
    # The one that was downloading starts first; the paused one stays paused.
    assert [(job.id, job.state) for job in queue.jobs()] == [("aaaaaaaa", "running"), ("bbbbbbbb", "paused")]
    assert _ticked(queue, lambda: stage(sup, "aaaaaaaa") == "downloading")


def test_queue_row_without_torrent_data_is_deleted(make, data_dir):
    from core.database import insert_download_job, list_download_jobs

    sup = make("stalled")
    queue = _queue(sup)
    insert_download_job({"id": "gone", "kind": "torrent", "game_id": 1, "state": "paused",
                         "source": json.dumps({"rpdl_id": 7, "info_hash": "cc" * 20})})
    queue.load()
    assert queue.jobs() == [] and list_download_jobs() == []


def test_remove_finished_torrent_deletes_its_folder_unless_it_seeds(make, data_dir, monkeypatch):
    monkeypatch.setattr(torrents.torrent_settings, "seed_mode", lambda: "never")
    sup = make()
    queue = _queue(sup)
    job_id = _add(queue, 1)
    assert _ticked(queue, lambda: queue.get(job_id).state == "ready")
    folder = data_dir / "downloads" / "torrents" / parse_torrent(torrent_bytes()).info_hash
    assert folder.is_dir()
    assert queue.remove(job_id)
    assert not folder.exists()
