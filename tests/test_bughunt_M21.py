# SPDX-License-Identifier: GPL-3.0-or-later
"""Torrent staging files hold the RPDL passkey, so only the owner can read them."""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

from core.downloads import torrents
from core.downloads.torrents import TorrentSupervisor

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")

FAKE = str(Path(__file__).with_name("fake_torrent_helper.py"))


def _torrent_bytes() -> bytes:
    announce = "http://tracker.example/announce/PASSKEY0123456789"
    return (
        f"d8:announce{len(announce)}:{announce}4:infod6:lengthi100e4:name4:game"
        "12:piece lengthi16384e6:pieces20:".encode()
        + b"x" * 20
        + b"7:privatei1eee"
    )


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@pytest.fixture(autouse=True)
def open_umask(tmp_path, monkeypatch):
    monkeypatch.setenv("WLIB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(torrents.torrent_settings, "seed_mode", lambda: "ask")
    old = os.umask(0o022)
    yield
    _ = os.umask(old)


def test_new_torrent_folder_and_meta_are_owner_only(tmp_path):
    sup = TorrentSupervisor(
        command=lambda: (
            [sys.executable, FAKE],
            {
                **os.environ,
                "FAKE_MODE": "complete",
                "FAKE_LOG": str(tmp_path / "fake.log"),
                "FAKE_STATE": str(tmp_path / "fake.count"),
            },
        ),
        free_space_fn=lambda path: 1 << 40,
    )
    job = torrents._Job(1, "Game", "1", 7)  # pyright: ignore[reportPrivateUsage]
    meta = torrents.parse_torrent(_torrent_bytes())
    try:
        sup._prepare(job, meta, _torrent_bytes())  # pyright: ignore[reportPrivateUsage]
    finally:
        sup._closing = True  # pyright: ignore[reportPrivateUsage]
        proc = sup._proc  # pyright: ignore[reportPrivateUsage]
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.wait()
    root = torrents.torrents_dir()
    folder = root / meta.info_hash
    assert _mode(root) & 0o077 == 0
    assert _mode(folder) & 0o077 == 0
    assert _mode(folder / "meta.torrent") == 0o600
    assert (folder / "meta.torrent").read_bytes() == _torrent_bytes()


def test_meta_written_again_replaces_a_wide_open_file(tmp_path):
    path = tmp_path / "meta.torrent"
    _ = path.write_bytes(b"old")
    path.chmod(0o644)
    torrents._write_private(path, b"new")  # pyright: ignore[reportPrivateUsage]
    assert _mode(path) == 0o600
    assert path.read_bytes() == b"new"


def test_load_tightens_files_an_older_version_left_readable():
    root = torrents.staging_dir() / "torrents"
    folder = root / ("a" * 40)
    folder.mkdir(parents=True)
    _ = (folder / "meta.torrent").write_bytes(_torrent_bytes())
    _ = (folder / "resume.dat").write_bytes(b"resume")
    _ = (folder / "state.json").write_text(
        json.dumps({"phase": "failed", "job_id": "x", "updated": 9e12})
    )
    for path in (root, folder, folder / "meta.torrent", folder / "resume.dat"):
        path.chmod(0o755 if path.is_dir() else 0o644)
    TorrentSupervisor().load()
    assert _mode(root) & 0o077 == 0
    assert _mode(folder) & 0o077 == 0
    assert _mode(folder / "meta.torrent") == 0o600
    assert _mode(folder / "resume.dat") == 0o600


def test_helper_resume_data_is_owner_only(tmp_path):
    th = pytest.importorskip("core.downloads.torrent_helper")
    path = tmp_path / "resume.dat"
    _ = (tmp_path / "resume.dat.tmp").write_bytes(b"stale")
    (tmp_path / "resume.dat.tmp").chmod(0o644)
    th._write_atomic(str(path), b"resume")  # pyright: ignore[reportPrivateUsage]
    assert _mode(path) == 0o600
    assert path.read_bytes() == b"resume"
    assert not (tmp_path / "resume.dat.tmp").exists()
