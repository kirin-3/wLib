# SPDX-License-Identifier: GPL-3.0-or-later
"""M18: a cancelled preparation must not delete the folder of a replacement job of the same torrent."""
import json
import threading
from pathlib import Path

import pytest

from core.downloads import torrents
from core.downloads.bencode import parse_torrent
from core.downloads.torrents import TorrentSupervisor, _Job  # pyright: ignore[reportPrivateUsage]

GIB = 1024**3


def _torrent_bytes() -> bytes:
    return (
        b"d8:announce26:http://tracker.example/ann4:infod6:lengthi100e4:name4:game"
        + b"12:piece lengthi16384e6:pieces20:"
        + b"x" * 20
        + b"ee"
    )


class _Pipe:
    def write(self, text: str) -> int:
        return len(text)

    def flush(self) -> None:
        pass


class _FakeProc:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.stdin = _Pipe()
        self.stdout = None
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        return 0


@pytest.fixture
def sup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TorrentSupervisor:
    monkeypatch.setenv("WLIB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(torrents.subprocess, "Popen", _FakeProc)
    supervisor = TorrentSupervisor(command=lambda: (["helper"], {}), options=lambda: {}, free_space_fn=lambda _p: 100 * GIB)
    monkeypatch.setattr(supervisor, "_read", lambda proc: None)
    return supervisor


def _add(sup: TorrentSupervisor) -> _Job:
    job = _Job(1, "Game", "1.0", 7)
    sup._jobs[job.id] = job  # pyright: ignore[reportPrivateUsage]
    return job


def test_cancelled_preparation_keeps_the_replacement_folder(sup: TorrentSupervisor, monkeypatch: pytest.MonkeyPatch):
    data = _torrent_bytes()
    folder = torrents.torrents_dir() / parse_torrent(data).info_hash
    entered = threading.Event()
    release = threading.Event()
    real_write = Path.write_bytes
    calls: list[int] = []

    def write_bytes(self: Path, content: bytes) -> int:
        calls.append(1)
        if len(calls) == 1:
            # The first job's metadata write stalls until the replacement is ready.
            entered.set()
            assert release.wait(10)
        return real_write(self, content)

    monkeypatch.setattr(Path, "write_bytes", write_bytes)

    first = _add(sup)
    worker = threading.Thread(target=sup._run, args=(first, lambda: data))  # pyright: ignore[reportPrivateUsage]
    worker.start()
    assert entered.wait(10)
    assert sup.cancel(first.id)

    second = _add(sup)
    sup._run(second, lambda: data)  # pyright: ignore[reportPrivateUsage]
    assert second.stage == "downloading" and second.folder == folder

    release.set()
    worker.join(10)
    assert not worker.is_alive()

    assert first.stage == "cancelled"
    assert (folder / "meta.torrent").is_file()
    assert json.loads((folder / "state.json").read_text())["job_id"] == second.id
    assert second.folder == folder


def test_cancelled_preparation_still_deletes_its_own_folder(sup: TorrentSupervisor, monkeypatch: pytest.MonkeyPatch):
    data = _torrent_bytes()
    folder = torrents.torrents_dir() / parse_torrent(data).info_hash
    real_write = Path.write_bytes
    job = _add(sup)

    def write_bytes(self: Path, content: bytes) -> int:
        # cancel() runs while the metadata is written, before the folder is set.
        _ = sup.cancel(job.id)
        return real_write(self, content)

    monkeypatch.setattr(Path, "write_bytes", write_bytes)
    sup._run(job, lambda: data)  # pyright: ignore[reportPrivateUsage]

    assert job.stage == "cancelled"
    assert not folder.exists()
