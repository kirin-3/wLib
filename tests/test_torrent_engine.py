# SPDX-License-Identifier: GPL-3.0-or-later
"""The real torrent helper process, run against local private torrents (no peers)."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import libtorrent as lt
import pytest

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

REPO = Path(__file__).resolve().parent.parent
PASSKEY = "PASSKEY1234567890"
TRACKER = f"http://127.0.0.1:9/announce/{PASSKEY}?k=SECRETQUERY"


def make_torrent(folder: Path, payload: bytes = b"x" * 200_000, with_data: bool = False) -> bytes:
    """A private single-file torrent in `folder`; returns the .torrent bytes."""
    source = folder / "src"
    source.mkdir(parents=True)
    (source / "game.bin").write_bytes(payload)
    storage = lt.file_storage()
    lt.add_files(storage, str(source / "game.bin"))
    creator = lt.create_torrent(storage, piece_size=16384)
    creator.set_priv(True)
    creator.add_tracker(TRACKER)
    lt.set_piece_hashes(creator, str(source))
    meta = lt.bencode(creator.generate())
    (folder / "meta.torrent").write_bytes(meta)
    if with_data:
        data = folder / "data"
        data.mkdir()
        (data / "game.bin").write_bytes(payload)
    return meta


class Helper:
    def __init__(self, tmp_path: Path) -> None:
        env = {**os.environ, "WLIB_DATA_DIR": str(tmp_path / "appdata")}
        self.proc = subprocess.Popen(
            [sys.executable, str(REPO / "main.py"), "--torrent-helper"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, env=env, cwd=str(REPO),
        )
        self.events: list[dict] = []

    def send(self, **command) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(command) + "\n")
        self.proc.stdin.flush()

    def read_until(self, predicate, timeout: float = 20.0) -> dict:
        assert self.proc.stdout is not None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            line = self.proc.stdout.readline()
            if not line:
                break
            event = json.loads(line)
            self.events.append(event)
            if predicate(event):
                return event
        raise AssertionError(f"event not seen; got {self.events[-5:]}")

    def close(self) -> int:
        assert self.proc.stdin is not None and self.proc.stdout is not None
        self.proc.stdin.close()
        code = self.proc.wait(timeout=20)
        self.proc.stdout.close()
        return code


@pytest.fixture
def helper(tmp_path):
    h = Helper(tmp_path)
    h.send(cmd="hello", download_kib=0, upload_kib=0, port_mapping=False)
    h.read_until(lambda e: e["event"] == "ready")
    yield h
    if h.proc.poll() is None:
        h.proc.kill()
        h.proc.wait()


def _status_of(h: Helper, job: str, predicate=lambda t: True) -> dict:
    event = h.read_until(
        lambda e: e["event"] == "status" and any(t["id"] == job and predicate(t) for t in e["torrents"])
    )
    return next(t for t in event["torrents"] if t["id"] == job)


def test_status_pause_resume_limits_remove(helper, tmp_path):
    folder = tmp_path / "t1"
    make_torrent(folder)
    helper.send(cmd="add", id="j1", dir=str(folder), paused=False)
    first = _status_of(helper, "j1")
    assert first["size"] == 200_000 and first["finished"] is False and first["peers"] == 0
    assert "announce" not in first["tracker"]
    helper.send(cmd="pause", id="j1")
    assert _status_of(helper, "j1", lambda t: t["paused"])["state"] == "paused"
    helper.send(cmd="resume", id="j1")
    assert _status_of(helper, "j1", lambda t: not t["paused"])["state"] in ("downloading", "checking")
    helper.send(cmd="set_limits", download_kib=100, upload_kib=50)
    helper.send(cmd="remove", id="j1")
    helper.read_until(lambda e: e["event"] == "removed" and e["id"] == "j1")
    helper.send(cmd="shutdown")
    helper.read_until(lambda e: e["event"] == "bye")
    assert helper.proc.wait(timeout=20) == 0


def test_status_events_come_at_most_once_a_second(helper, tmp_path):
    folder = tmp_path / "t2"
    make_torrent(folder)
    helper.send(cmd="add", id="j", dir=str(folder), paused=False)
    start = time.monotonic()
    for _ in range(3):
        helper.read_until(lambda e: e["event"] == "status")
    assert time.monotonic() - start >= 1.5


def test_existing_data_finishes_and_seeds(helper, tmp_path):
    folder = tmp_path / "t3"
    make_torrent(folder, with_data=True)
    helper.send(cmd="add", id="j", dir=str(folder), paused=False)
    helper.read_until(lambda e: e["event"] == "finished" and e["id"] == "j")
    done = _status_of(helper, "j", lambda t: t["finished"])
    assert done["state"] == "seeding" and done["progress"] == 1.0
    helper.send(cmd="stop_seeding", id="j")
    helper.read_until(lambda e: e["event"] == "stopped")


def test_unsafe_paths_refused(helper, tmp_path):
    for index, path in enumerate([["..", "evil.txt"], ["sub", "..", "x"], ["a\\b"]]):
        folder = tmp_path / f"bad{index}"
        folder.mkdir()
        info = {
            "name": "pack", "piece length": 16384, "pieces": b"\0" * 20, "private": 1,
            "files": [{"length": 5, "path": path}],
        }
        (folder / "meta.torrent").write_bytes(lt.bencode({"info": info, "announce": TRACKER}))
        helper.send(cmd="add", id=f"b{index}", dir=str(folder), paused=False)
        event = helper.read_until(lambda e: e["event"] == "error" and e.get("id") == f"b{index}")
        assert event["code"] == "unsafe_paths"
    assert not (tmp_path / "evil.txt").exists()


def test_absolute_name_refused(helper, tmp_path):
    folder = tmp_path / "abs"
    folder.mkdir()
    info = {"name": "/etc", "piece length": 16384, "pieces": b"\0" * 20, "length": 5}
    (folder / "meta.torrent").write_bytes(lt.bencode({"info": info}))
    helper.send(cmd="add", id="a", dir=str(folder), paused=False)
    assert helper.read_until(lambda e: e["event"] == "error")["code"] == "unsafe_paths"


def test_no_event_carries_the_announce_url(helper, tmp_path):
    folder = tmp_path / "t4"
    make_torrent(folder)
    helper.send(cmd="add", id="j", dir=str(folder), paused=False)
    for _ in range(3):
        helper.read_until(lambda e: e["event"] == "status")
    helper.send(cmd="add", id="bad", dir=str(tmp_path / "missing"), paused=False)
    helper.read_until(lambda e: e["event"] == "error")
    blob = json.dumps(helper.events)
    for needle in (PASSKEY, "SECRETQUERY", "/announce"):
        assert needle not in blob


def test_commands_before_hello_are_refused(tmp_path):
    h = Helper(tmp_path)
    try:
        h.send(cmd="pause", id="x")
        assert h.read_until(lambda e: e["event"] == "error")["message"] == "Bad command"
        assert h.close() == 0
    finally:
        if h.proc.poll() is None:
            h.proc.kill()
            h.proc.wait()


def test_redact_tracker():
    from core.downloads.torrent_helper import redact_tracker

    assert redact_tracker(TRACKER) == "http://127.0.0.1"
    assert redact_tracker("https://t.example/announce?passkey=1") == "https://t.example"
    assert redact_tracker("garbage") == ""


def test_ready_reports_the_listen_port(tmp_path):
    h = Helper(tmp_path)
    try:
        h.send(cmd="hello")
        ready = h.read_until(lambda e: e["event"] == "ready")
        assert isinstance(ready["port"], int) and ready["port"] > 0
        assert h.close() == 0
    finally:
        if h.proc.poll() is None:
            h.proc.kill()
            h.proc.wait()


def _seeder(folder: Path, meta: bytes, payload_dir: Path, upload_kib: int):
    session = lt.session({
        "enable_dht": False, "enable_lsd": False, "enable_upnp": False, "enable_natpmp": False,
        "listen_interfaces": "127.0.0.1:0", "ignore_limits_on_local_network": False,
        "upload_rate_limit": upload_kib * 1024,
    }, 0)
    params = lt.add_torrent_params()
    params.ti = lt.torrent_info(meta)
    params.save_path = str(payload_dir)
    handle = session.add_torrent(params)
    deadline = time.monotonic() + 20
    while not handle.status().is_seeding and time.monotonic() < deadline:
        time.sleep(0.1)
    assert handle.status().is_seeding
    return session, handle


@pytest.mark.slow
def test_resume_does_not_redownload_finished_pieces(tmp_path):
    size = 4 * 1024 * 1024
    folder = tmp_path / "dl"
    meta = make_torrent(folder, payload=os.urandom(size))
    seeder, seed_handle = _seeder(folder, meta, folder / "src", upload_kib=600)

    def start() -> Helper:
        h = Helper(tmp_path)
        h.send(cmd="hello")
        ready = h.read_until(lambda e: e["event"] == "ready")
        h.send(cmd="add", id="j", dir=str(folder), paused=False)
        port = ready["port"]

        # A dial that lands before the helper has added the torrent is
        # dropped (seen on Windows), so dial again on every status until
        # the seeder is connected.
        def connected(t: dict) -> bool:
            if t["peers"] or t["seeds"]:
                return True
            seed_handle.connect_peer(("127.0.0.1", port))
            return False

        _status_of(h, "j", connected)
        return h

    first = start()
    try:
        _status_of(first, "j", lambda t: 0.35 <= t["progress"] < 0.9)
        first.send(cmd="pause", id="j")
        first.read_until(lambda e: e["event"] == "saved")
        first.send(cmd="shutdown")
        first.read_until(lambda e: e["event"] == "bye")
        first.proc.wait(timeout=20)
    finally:
        if first.proc.poll() is None:
            first.proc.kill()
    assert (folder / "resume.dat").is_file()

    second = start()
    try:
        resumed = _status_of(second, "j", lambda t: t["progress"] >= 0.3)
        assert resumed["progress"] >= 0.3
        second.send(cmd="resume", id="j")
        second.read_until(lambda e: e["event"] == "finished", timeout=60)
        assert (folder / "data" / "game.bin").stat().st_size == size
        assert seed_handle.status().all_time_upload < size * 1.2
    finally:
        second.proc.kill()
        second.proc.wait()


def test_stdin_eof_saves_resume_data_and_exits(helper, tmp_path):
    folder = tmp_path / "eof"
    make_torrent(folder, with_data=True)
    helper.send(cmd="add", id="j", dir=str(folder), paused=False)
    helper.read_until(lambda e: e["event"] == "finished")
    (folder / "resume.dat").unlink(missing_ok=True)
    assert helper.close() == 0
    assert (folder / "resume.dat").is_file()
