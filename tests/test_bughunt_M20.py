# SPDX-License-Identifier: GPL-3.0-or-later
"""A broken event pipe must not stop the torrent helper from saving resume data."""

from __future__ import annotations

import io
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("libtorrent")

from core.downloads import torrent_helper as th  # noqa: E402

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


class BrokenPipeStream(io.StringIO):
    """Stdout of a helper whose parent exited: every write fails."""

    def __init__(self) -> None:
        super().__init__()
        self.writes = 0

    def write(self, s: str) -> int:
        self.writes += 1
        raise BrokenPipeError(32, "Broken pipe")


class ResumeAlert:
    def __init__(self, handle: object) -> None:
        self.handle = handle
        self.params = b"params"


class FailedAlert:
    pass


class FlushAlert:
    pass


class FakeHandle:
    def is_valid(self) -> bool:
        return True

    def save_resume_data(self) -> None:
        pass


class FakeSession:
    def __init__(self, handles: list[FakeHandle]) -> None:
        self.handles = handles
        self.popped = False

    def pop_alerts(self) -> list[object]:
        if self.popped:
            return []
        self.popped = True
        return [ResumeAlert(h) for h in self.handles]


def test_emit_drops_events_after_the_pipe_breaks() -> None:
    out = BrokenPipeStream()
    emit = th._emit_to(out)  # pyright: ignore[reportPrivateUsage]
    emit({"event": "saved", "id": "a"})
    emit({"event": "saved", "id": "b"})
    assert out.writes == 1


def test_emit_drops_events_after_the_pipe_closes() -> None:
    out = io.StringIO()
    out.close()
    emit = th._emit_to(out)  # pyright: ignore[reportPrivateUsage]
    emit({"event": "bye"})


def test_shutdown_saves_every_torrent_when_the_parent_is_gone(tmp_path: Any) -> None:
    emit = th._emit_to(BrokenPipeStream())  # pyright: ignore[reportPrivateUsage]
    engine: Any = th.Engine(emit, {})
    handles = [FakeHandle(), FakeHandle()]
    folders = []
    for index, handle in enumerate(handles):
        folder = tmp_path / f"t{index}"
        folder.mkdir()
        folders.append(folder)
        engine._entries[f"j{index}"] = th._Entry(f"j{index}", str(folder), handle)  # pyright: ignore[reportArgumentType]
    engine._session = FakeSession(handles)
    engine._lt = SimpleNamespace(
        save_resume_data_alert=ResumeAlert,
        save_resume_data_failed_alert=FailedAlert,
        cache_flushed_alert=FlushAlert,
        write_resume_data_buf=lambda params: params,
    )
    engine.shutdown(wait=2)
    for folder in folders:
        assert (folder / "resume.dat").read_bytes() == b"params"
