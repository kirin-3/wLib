# SPDX-License-Identifier: GPL-3.0-or-later
"""Removing one download never deletes a partial file another download uses."""
import io
import threading
import time
from threading import Event

from core.downloads import transfer as transfer_module
from core.downloads.transfer import FileInfo, _resume_part, transfer


class _Held(io.BytesIO):
    """A stream that gives its first bytes, then waits for `gate`."""

    def __init__(self, data: bytes, gate: threading.Event, offset: int = 0) -> None:
        super().__init__(data[offset:])
        self._gate = gate
        self._first = True

    def read(self, size: int | None = -1) -> bytes:
        if self._first:
            self._first = False
            return super().read(16)
        _ = self._gate.wait(5)
        return super().read(size)


def _worker(tmp_path, monkeypatch):
    from core.downloads import manager as manager_module
    from core.downloads import workers as workers_module
    from core.downloads.manager import DownloadManager
    from core.downloads.pacing import SpeedLimiter
    from core.downloads.workers import HostWorker

    monkeypatch.setattr(transfer_module, "staging_dir", lambda: tmp_path)
    monkeypatch.setattr(manager_module, "staging_dir", lambda: tmp_path)
    monkeypatch.setattr(workers_module, "staging_dir", lambda: tmp_path)
    return HostWorker(DownloadManager(limiter=SpeedLimiter()))


def _job(job_id: str, part: str, state: str = "paused"):
    from core.downloads.queue import QueueJob

    return QueueJob(id=job_id, kind="host", game_id=1, state=state,
                    source={"host": "pixeldrain", "part_file": part})


def test_removing_a_paused_job_keeps_the_partial_another_job_is_writing(tmp_path, monkeypatch):
    worker = _worker(tmp_path, monkeypatch)
    data = b"PK\x03\x04" + b"a" * 200
    gate = threading.Event()
    info = FileInfo("game.zip", len(data), lambda: _Held(data, gate),
                    resume_factory=lambda offset: _Held(data, gate, offset),
                    resume_key="pixeldrain:file")
    part = _resume_part(tmp_path, info)
    assert part is not None
    result: list[object] = []

    def run() -> None:
        try:
            result.append(transfer(info, "jobB", Event(), lambda *_: None, directory=tmp_path))
        except Exception as exc:  # noqa: BLE001 - reported below
            result.append(exc)

    thread = threading.Thread(target=run)
    thread.start()
    deadline = time.monotonic() + 5
    while not (part.is_file() and part.stat().st_size) and time.monotonic() < deadline:
        time.sleep(0.01)
    # Job A paused earlier on the same file; the user removes it now.
    worker.remove(_job("jobA", part.name))
    assert part.is_file()
    gate.set()
    thread.join(5)
    assert len(result) == 1 and not isinstance(result[0], Exception), result
    assert isinstance(result[0], type(part)) and result[0].read_bytes() == data


def test_removing_a_job_keeps_the_partial_a_queued_job_continues_from(tmp_path, monkeypatch):
    worker = _worker(tmp_path, monkeypatch)
    part = tmp_path / "resume-abc.part"
    part.write_bytes(b"PK\x03\x04")
    other = _job("jobB", part.name)
    assert worker.restore(other)
    worker.remove(_job("jobA", part.name))
    assert part.is_file()
    worker.remove(other)
    assert not part.exists()
