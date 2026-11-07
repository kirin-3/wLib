# SPDX-License-Identifier: GPL-3.0-or-later
"""The supervisor against the real `--torrent-helper` process (no fake in between)."""
import time

import libtorrent as lt
import pytest

from core.downloads import torrent_settings, torrents
from core.downloads.bencode import parse_torrent
from core.downloads.torrents import TorrentSupervisor

pytestmark = [pytest.mark.slow, pytest.mark.filterwarnings("ignore::DeprecationWarning")]


def make_torrent(root, payload):
    source = root / "src"
    source.mkdir()
    (source / "game.bin").write_bytes(payload)
    storage = lt.file_storage()
    lt.add_files(storage, str(source / "game.bin"))
    creator = lt.create_torrent(storage, piece_size=16384)
    creator.set_priv(True)
    creator.add_tracker("http://127.0.0.1:9/announce/PASSKEY123?k=SECRET")
    lt.set_piece_hashes(creator, str(source))
    return lt.bencode(creator.generate())


def wait_for(predicate, timeout=30.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("condition not met")


def test_real_helper_finishes_seeds_and_stops_at_ratio(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("WLIB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(torrent_settings, "seed_mode", lambda: "always")
    monkeypatch.setattr(torrent_settings, "seed_ratio", lambda: 1.0)
    monkeypatch.setattr(torrent_settings, "seed_hours", lambda: None)
    payload = b"x" * 100_000
    data = make_torrent(tmp_path, payload)
    meta = parse_torrent(data)
    folder = tmp_path / "data" / "downloads" / "torrents" / meta.info_hash
    (folder / "data").mkdir(parents=True)
    (folder / "data" / "game.bin").write_bytes(payload)

    sup = TorrentSupervisor(free_space_fn=lambda path: 10**12)
    try:
        job_id = sup.start(1, "Game", "1.0", 7, lambda: data)
        snap = lambda: next(j for j in sup.status() if j["job_id"] == job_id)  # noqa: E731
        wait_for(lambda: snap()["stage"] == "done")
        assert snap()["seed_state"] == "seeding" and snap()["payload_kind"] == "folder"
        assert snap()["folder_path"] == str(folder / "data")
        # Real status events carry the fields the supervisor reads.
        wait_for(lambda: sup._jobs[job_id].last_status_at is not None)  # pyright: ignore[reportPrivateUsage]
        job = sup._jobs[job_id]  # pyright: ignore[reportPrivateUsage]
        assert job.total == len(payload) and sup.is_seeding_path(str(folder / "data"))
        # Resume data is written while the torrent seeds (the real helper saves right after it finishes).
        wait_for(lambda: (folder / "resume.dat").is_file())
        # Reaching the ratio target stops the real torrent.
        sup._on_event({"event": "status", "torrents": [{"id": job_id, "state": "seeding", "size": len(payload), "uploaded": len(payload), "up": 0, "peers": 0}]})  # pyright: ignore[reportPrivateUsage]
        assert snap()["seed_state"] == "ended"
        assert (folder / "state.json").is_file()
    finally:
        sup.shutdown()
    out = capsys.readouterr()
    assert "PASSKEY123" not in out.out + out.err
    assert torrents.supervisor is not sup
