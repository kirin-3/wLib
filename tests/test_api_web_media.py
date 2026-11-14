# SPDX-License-Identifier: GPL-3.0-or-later
"""Video conversion job, busy reason and launch codes for the built-in player."""
from __future__ import annotations

import json
import threading
import time

import pytest

import core.game_files as gf
from core import web_video
from core.api import Api
from core.database import add_game, init_db, update_setting

AVAILABLE: web_video.FfmpegInfo = {
    "available": True,
    "ffmpeg": "/usr/bin/ffmpeg",
    "ffprobe": "/usr/bin/ffprobe",
    "video_encoder": "libsvtav1",
    "audio_encoder": "libopus",
    "reason": "",
    "install": {"command": "", "package_manager": "", "platform": "linux", "host": False},
}


@pytest.fixture
def env(tmp_path, monkeypatch):
    for name in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(name, str(tmp_path / "home"))
    init_db()
    library = tmp_path / "Library"
    library.mkdir()
    update_setting("library_folders", json.dumps([str(library)]))
    update_setting("library_folder", str(library))
    folder = library / "Game"
    folder.mkdir()
    (folder / "index.html").write_text("<html></html>")
    for index in range(4):
        (folder / f"clip{index}.mp4").write_bytes(b"h264")
    game_id = add_game(title="Game", exe_path=str(folder / "index.html"))
    assert game_id is not None
    monkeypatch.setattr(web_video, "ffmpeg_info", lambda refresh=False: AVAILABLE)
    monkeypatch.setattr(web_video, "file_needs_conversion", lambda path: open(path, "rb").read() == b"h264")
    api = Api()
    monkeypatch.setattr(api, "get_running_games", lambda: [])
    return api, game_id, folder


class FakeConvert:
    """Replaces Converter.convert; `gate` holds each file until released."""

    def __init__(self, fail: tuple[str, ...] = (), gate: threading.Event | None = None):
        self.fail = fail
        self.gate = gate
        self.started = threading.Event()
        self.calls: list[str] = []

    def method(self):
        return lambda converter, path: self(converter, path)

    def __call__(self, _converter, path):
        self.calls.append(path)
        self.started.set()
        if self.gate is not None:
            assert self.gate.wait(5)
        if path.endswith(self.fail):
            return {"ok": False, "error": "boom", "size": 0, "mtime_ns": 0}
        with open(path, "wb") as handle:
            handle.write(b"av1")
        import os

        info = os.stat(path)
        return {"ok": True, "error": "", "size": info.st_size, "mtime_ns": info.st_mtime_ns}


def _wait_done(api, game_id):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        status = api.get_web_media_conversion_status(game_id)
        if status["finished"]:
            return status
        time.sleep(0.01)
    raise AssertionError("conversion did not finish")


def test_conversion_counts_and_status(env, monkeypatch):
    api, game_id, folder = env
    (folder / "old.mp4.wlib-partial").write_bytes(b"x")
    fake = FakeConvert(fail=("clip2.mp4",))
    monkeypatch.setattr(web_video.Converter, "convert", fake.method())
    assert api.get_web_media_conversion_status(game_id)["running"] is False
    assert api.start_web_media_conversion(game_id)["success"] is True
    status = _wait_done(api, game_id)
    assert (status["total"], status["done"], status["converted"], status["failed"]) == (4, 4, 3, 1)
    assert status["failed_files"] == ["clip2.mp4"] and status["error"] == "" and not status["cancelled"]
    assert not (folder / "old.mp4.wlib-partial").exists()
    assert web_video.scan_game(game_id, str(folder))["needs"] == ["clip2.mp4"]


def test_cancel_then_resume_skips_converted_files(env, monkeypatch):
    api, game_id, _folder = env
    gate = threading.Event()
    fake = FakeConvert(gate=gate)
    monkeypatch.setattr(web_video.Converter, "convert", fake.method())
    assert api.start_web_media_conversion(game_id)["success"] is True
    assert fake.started.wait(5)
    assert api.cancel_web_media_conversion(game_id)["success"] is True
    gate.set()
    status = _wait_done(api, game_id)
    assert status["cancelled"] and status["converted"] == 1 and status["total"] == 4
    resumed = FakeConvert()
    monkeypatch.setattr(web_video.Converter, "convert", resumed.method())
    assert api.start_web_media_conversion(game_id)["success"] is True
    status = _wait_done(api, game_id)
    assert status["total"] == 3 and status["converted"] == 3 and len(resumed.calls) == 3


def test_refusals(env, monkeypatch, tmp_path):
    api, game_id, _folder = env
    gate = threading.Event()
    fake = FakeConvert(gate=gate)
    monkeypatch.setattr(web_video.Converter, "convert", fake.method())
    other = tmp_path / "Other"
    other.mkdir()
    other_id = add_game(title="Other", exe_path=str(other / "index.html"))
    monkeypatch.setattr(api, "get_running_games", lambda: [other_id])
    assert "Close the game" in api.start_web_media_conversion(other_id)["error"]
    assert api.start_web_media_conversion(game_id)["success"] is True
    assert fake.started.wait(5)
    assert "Another game" in api.start_web_media_conversion(game_id)["error"]
    assert api.cancel_web_media_conversion(other_id)["success"] is False
    gate.set()
    _ = _wait_done(api, game_id)


def test_refused_while_moving(env, monkeypatch, tmp_path):
    api, game_id, _folder = env
    gate = threading.Event()
    started = threading.Event()

    def held(job, cancel, on_bytes, set_total):
        started.set()
        assert gate.wait(5)
        return gf.MoveOutcome("cancelled")

    monkeypatch.setattr(api._game_moves, "_runner", held)
    _ = api.start_game_move([game_id], str(tmp_path))
    assert started.wait(5)
    result = api.start_web_media_conversion(game_id)
    assert result["success"] is False and result["error"] == gf.MOVING_BUSY
    _ = api.cancel_game_move(True)
    gate.set()


def test_refused_without_ffmpeg(env, monkeypatch):
    api, game_id, _folder = env
    monkeypatch.setattr(web_video, "ffmpeg_info", lambda refresh=False: {**AVAILABLE, "available": False})
    assert api.start_web_media_conversion(game_id)["code"] == "ffmpeg_unavailable"


def test_converting_game_is_busy_everywhere(env, monkeypatch, tmp_path):
    from core.game_saves import GameSavesError
    from core.game_tools import GameToolError

    api, game_id, _folder = env
    gate = threading.Event()
    fake = FakeConvert(gate=gate)
    monkeypatch.setattr(web_video.Converter, "convert", fake.method())
    assert api.start_web_media_conversion(game_id)["success"] is True
    assert fake.started.wait(5)
    busy = web_video.WEB_MEDIA_BUSY
    with api._status_lock:
        assert api._game_busy_reason(game_id) == busy
    with pytest.raises(GameToolError, match="being converted"):
        with api._tool_run(game_id, "extract"):
            pass
    with pytest.raises(GameSavesError, match="being converted"):
        with api._save_operation(game_id, writes=True):
            pass
    assert api.uninstall_games([game_id], {})["error"] == busy
    # A queued move skips the game with the busy reason when it runs.
    _ = api.start_game_move([game_id], str(tmp_path))
    deadline = time.monotonic() + 5
    summary = None
    while summary is None and time.monotonic() < deadline:
        summary = api.get_move_status().get("summary")
        time.sleep(0.01)
    assert busy in json.dumps(summary)
    launch = api.launch_game(game_id)
    assert launch["code"] == "web_media_converting" and launch["error"] == busy
    gate.set()
    _ = _wait_done(api, game_id)
    with api._status_lock:
        assert api._game_busy_reason(game_id) is None


def test_shutdown_stops_the_job(env, monkeypatch):
    api, game_id, _folder = env
    gate = threading.Event()
    fake = FakeConvert(gate=gate)
    monkeypatch.setattr(web_video.Converter, "convert", fake.method())
    killed = threading.Event()
    monkeypatch.setattr(web_video.Converter, "kill", lambda _self: (killed.set(), gate.set()))
    assert api.start_web_media_conversion(game_id)["success"] is True
    assert fake.started.wait(5)
    api._web_media_shutdown()
    status = _wait_done(api, game_id)
    assert killed.is_set() and status["cancelled"] and len(fake.calls) == 1


def test_play_anyway_needs_a_check_first(env):
    api, game_id, folder = env
    assert api.set_web_media_play_anyway(game_id)["success"] is False
    _ = web_video.scan_game(game_id, str(folder))
    assert api.set_web_media_play_anyway(game_id)["success"] is True
    assert web_video.scan_game(game_id, str(folder))["play_anyway"] is True
