# SPDX-License-Identifier: GPL-3.0-or-later
"""The video check cache and in-place conversion keep newer data."""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core import web_video
from tests.test_web_video import INFO, FakeTools, mp4, sample_entry


def _game(root: Path) -> Path:
    (root / "media").mkdir(parents=True)
    (root / "index.html").write_text("<html></html>")
    (root / "media" / "a.mp4").write_bytes(mp4(sample_entry(b"avc1")))
    return root


def test_convert_keeps_a_file_that_changed_during_the_encode(tmp_path):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"original")
    tools = FakeTools()
    encode = tools.popen

    def popen(cmd, **kwargs):
        # An updater replaces the video while ffmpeg encodes the old copy.
        replacement = tmp_path / "update.tmp"
        replacement.write_bytes(b"newer update")
        os.replace(replacement, clip)
        return encode(cmd, **kwargs)

    with (
        patch("core.web_video.subprocess.run", side_effect=tools.run),
        patch("core.web_video.subprocess.Popen", side_effect=popen),
        patch("core.web_video.shutil.disk_usage", return_value=MagicMock(free=10**12)),
    ):
        result = web_video.Converter(INFO).convert(str(clip))
    assert result["ok"] is False and "changed" in result["error"]
    assert clip.read_bytes() == b"newer update"
    assert not Path(str(clip) + web_video.PARTIAL_SUFFIX).exists()


def test_read_error_is_not_cached_as_a_result(tmp_path):
    game = _game(tmp_path / "game")
    reader = MagicMock(return_value=None)
    first = web_video.scan_game(5, str(game), reader)
    assert first["needs"] == []
    data = json.loads(Path(web_video.scan_cache_path(5)).read_text())
    assert "media/a.mp4" not in data["files"]
    # The file is readable again, with the same size and mtime.
    second = web_video.scan_game(5, str(game))
    assert second["needs"] == ["media/a.mp4"]


def test_check_file_tells_read_errors_from_non_mp4_files(tmp_path):
    garbage = tmp_path / "garbage.mp4"
    garbage.write_bytes(b"not an mp4")
    assert web_video.check_file(str(garbage)) is False
    assert web_video.check_file(str(tmp_path / "missing.mp4")) is None
    good = tmp_path / "h264.mp4"
    good.write_bytes(mp4(sample_entry(b"avc1")))
    with patch("builtins.open", side_effect=PermissionError("denied")):
        assert web_video.check_file(str(good)) is None
    assert web_video.check_file(str(good)) is True
    assert web_video.file_needs_conversion(str(tmp_path / "missing.mp4")) is False


def test_play_anyway_is_kept_per_launch_folder(tmp_path):
    first = _game(tmp_path / "a")
    second = _game(tmp_path / "b")
    assert web_video.set_play_anyway(5, str(first))["play_anyway"] is True
    assert web_video.scan_game(5, str(second))["play_anyway"] is False
    assert web_video.cached_root(5) == str(second.resolve())
    assert web_video.set_play_anyway(5, str(second))["play_anyway"] is True
    assert web_video.scan_game(5, str(first))["play_anyway"] is True
    assert web_video.scan_game(5, str(second))["play_anyway"] is True
    # A changed set of files in one folder still asks again there.
    (first / "media" / "new.mp4").write_bytes(mp4(sample_entry(b"avc1")))
    assert web_video.scan_game(5, str(first))["play_anyway"] is False


def test_other_folders_are_capped(tmp_path, monkeypatch):
    monkeypatch.setattr(web_video, "MAX_OTHER_ROOTS", 2)
    roots = [_game(tmp_path / f"g{index}") for index in range(4)]
    for root in roots:
        _ = web_video.set_play_anyway(5, str(root))
    data = json.loads(Path(web_video.scan_cache_path(5)).read_text())
    assert list(data["other_roots"]) == [str(roots[1].resolve()), str(roots[2].resolve())]
    assert web_video.scan_game(5, str(roots[0]))["play_anyway"] is False


@pytest.mark.parametrize("value", [None, [], {"x": 1}, {"": "y"}])
def test_bad_other_roots_are_ignored(tmp_path, value):
    game = _game(tmp_path / "game")
    _ = web_video.scan_game(5, str(game))
    path = Path(web_video.scan_cache_path(5))
    data = json.loads(path.read_text())
    data["other_roots"] = value
    path.write_text(json.dumps(data))
    assert web_video.scan_game(5, str(game))["needs"] == ["media/a.mp4"]
