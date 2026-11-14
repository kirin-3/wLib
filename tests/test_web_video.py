# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core import web_video


def box(kind: bytes, payload: bytes = b"", large: bool = False) -> bytes:
    if large:
        return struct.pack(">I4sQ", 1, kind, len(payload) + 16) + payload
    return struct.pack(">I4s", len(payload) + 8, kind) + payload


def esds(object_type: int) -> bytes:
    decoder = bytes([0x04, 13, object_type]) + b"\x00" * 12
    es = bytes([0x03, 3 + len(decoder)]) + b"\x00\x01\x00" + decoder
    return box(b"esds", b"\x00\x00\x00\x00" + es)


def sample_entry(code: bytes, object_type: int | None = None) -> bytes:
    if code == b"mp4a" or code == b"Opus":
        payload = b"\x00" * 6 + b"\x00\x01" + b"\x00" * 20
        if object_type is not None:
            payload += esds(object_type)
        return box(code, payload)
    return box(code, b"\x00" * 78)


def track(entry: bytes) -> bytes:
    stsd = box(b"stsd", b"\x00\x00\x00\x00" + struct.pack(">I", 1) + entry)
    return box(b"trak", box(b"mdia", box(b"minf", box(b"stbl", stsd))))


def mp4(*entries: bytes, moov_at_end: bool = False, large: bool = False) -> bytes:
    ftyp = box(b"ftyp", b"isom\x00\x00\x02\x00")
    moov = box(b"moov", b"".join(track(entry) for entry in entries), large=large)
    mdat = box(b"mdat", b"\x00" * 64)
    return ftyp + mdat + moov if moov_at_end else ftyp + moov + mdat


@pytest.mark.parametrize(
    ("data", "needs"),
    [
        (mp4(sample_entry(b"avc1")), True),
        (mp4(sample_entry(b"avc3")), True),
        (mp4(sample_entry(b"hvc1")), True),
        (mp4(sample_entry(b"hev1")), True),
        (mp4(sample_entry(b"mp4a", 0x40)), True),
        (mp4(sample_entry(b"mp4a")), True),
        (mp4(sample_entry(b"mp4a", 0x6B)), False),
        (mp4(sample_entry(b"mp4a", 0x69)), False),
        (mp4(sample_entry(b"av01"), sample_entry(b"Opus")), False),
        (mp4(sample_entry(b"vp09")), False),
        (mp4(sample_entry(b"vp09"), sample_entry(b"mp4a", 0x40)), True),
        (mp4(sample_entry(b"avc1"), moov_at_end=True), True),
        (mp4(sample_entry(b"avc1"), large=True), True),
    ],
)
def test_file_needs_conversion(tmp_path, data, needs):
    path = tmp_path / "clip.mp4"
    path.write_bytes(data)
    assert web_video.file_needs_conversion(str(path)) is needs


def test_truncated_or_garbage_file_does_not_need_conversion(tmp_path):
    data = mp4(sample_entry(b"avc1"), moov_at_end=True)
    path = tmp_path / "clip.mp4"
    path.write_bytes(data[:-40])
    assert web_video.file_needs_conversion(str(path)) is False
    path.write_bytes(b"not a video at all")
    assert web_video.file_needs_conversion(str(path)) is False
    assert web_video.file_needs_conversion(str(tmp_path / "missing.mp4")) is False


def test_moov_read_is_capped(tmp_path, monkeypatch):
    monkeypatch.setattr(web_video, "MAX_MOOV_BYTES", 32)
    path = tmp_path / "clip.mp4"
    path.write_bytes(mp4(sample_entry(b"avc1")))
    # The stsd box is past the cap, so the reader does not see it.
    assert web_video.file_needs_conversion(str(path)) is False


# ── Folder walk and cache ──


@pytest.fixture
def game(tmp_path):
    root = tmp_path / "game"
    (root / "media" / "sub").mkdir(parents=True)
    (root / "index.html").write_text("<html></html>")
    (root / "media" / "a.mp4").write_bytes(mp4(sample_entry(b"avc1")))
    (root / "media" / "sub" / "B.M4V").write_bytes(mp4(sample_entry(b"vp09")))
    (root / "media" / "c.m4a").write_bytes(mp4(sample_entry(b"mp4a", 0x40)))
    (root / "media" / "d.webm").write_bytes(b"x")
    return root


def test_walk_is_case_insensitive_and_stays_in_root(game, tmp_path):
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"x")
    os.symlink(outside, game / "link.mp4")
    os.symlink(tmp_path, game / "loop")
    names = [relative for relative, _path in web_video.iter_media_files(str(game))]
    assert names == ["media/a.mp4", "media/c.m4a", "media/sub/B.M4V"]


def test_scan_reuses_unchanged_files(game):
    reader = MagicMock(side_effect=web_video.file_needs_conversion)
    first = web_video.scan_game(5, str(game), reader)
    assert first["needs"] == ["media/a.mp4", "media/c.m4a"] and reader.call_count == 3
    reader.reset_mock()
    second = web_video.scan_game(5, str(game), reader)
    assert reader.call_count == 0 and second == first
    target = game / "media" / "sub" / "B.M4V"
    target.write_bytes(mp4(sample_entry(b"hev1")))
    third = web_video.scan_game(5, str(game), reader)
    assert reader.call_count == 1 and "media/sub/B.M4V" in third["needs"]
    assert third["fingerprint"] != first["fingerprint"]


def test_root_change_resets_cache(game, tmp_path):
    _ = web_video.scan_game(5, str(game))
    web_video.set_play_anyway(5, str(game))
    moved = tmp_path / "moved"
    shutil.copytree(game, moved)
    reader = MagicMock(return_value=False)
    result = web_video.scan_game(5, str(moved), reader)
    assert reader.call_count == 3 and result["needs"] == [] and result["root"] == str(moved.resolve())
    data = json.loads(Path(web_video.scan_cache_path(5)).read_text())
    assert data["play_anyway"] is None


def test_play_anyway_holds_until_the_set_changes(game):
    assert web_video.scan_game(5, str(game))["play_anyway"] is False
    assert web_video.set_play_anyway(5, str(game))["play_anyway"] is True
    assert web_video.scan_game(5, str(game))["play_anyway"] is True
    (game / "media" / "new.mp4").write_bytes(mp4(sample_entry(b"avc1")))
    assert web_video.scan_game(5, str(game))["play_anyway"] is False


def test_cache_write_is_atomic_and_cache_can_be_deleted(game):
    _ = web_video.scan_game(5, str(game))
    folder = Path(web_video.scan_cache_dir())
    assert [path.name for path in folder.iterdir()] == ["5.json"]
    assert web_video.cached_root(5) == str(game.resolve())
    web_video.mark_converted(5, "media/a.mp4", 10, 20)
    # The recorded size does not match, so the file is read again.
    assert web_video.scan_game(5, str(game), MagicMock(return_value=False))["needs"] == ["media/c.m4a"]
    assert web_video.delete_scan_cache(5) is True
    assert web_video.delete_scan_cache(5) is False


def test_mark_converted_skips_the_file_next_time(game):
    _ = web_video.scan_game(5, str(game))
    path = game / "media" / "a.mp4"
    info = path.stat()
    web_video.mark_converted(5, "media/a.mp4", info.st_size, info.st_mtime_ns)
    reader = MagicMock(return_value=True)
    assert web_video.scan_game(5, str(game), reader)["needs"] == ["media/c.m4a"]
    assert reader.call_count == 0


def test_launch_check(game, monkeypatch):
    monkeypatch.setattr(web_video, "ffmpeg_info", lambda: {"available": False})
    result = web_video.launch_check(5, str(game))
    assert result is not None and result["code"] == "web_media_unsupported"
    assert result["media"] == {"count": 2, "ffmpeg": {"available": False}}
    web_video.set_play_anyway(5, str(game))
    assert web_video.launch_check(5, str(game)) is None


# ── ffmpeg discovery ──

ENCODERS = """Encoders:
 V..... = Video
 ------
 V....D libx264              libx264 H.264
 V....D libsvtav1            SVT-AV1
 V....D libvpx-vp9           libvpx VP9
 A....D aac                  AAC
 A....D libopus              libopus Opus
"""


def test_parse_encoders():
    assert web_video.parse_encoders(ENCODERS) == {"libx264", "libsvtav1", "libvpx-vp9", "aac", "libopus"}


def _which(found: dict[str, str]):
    return lambda name: found.get(name)


@pytest.mark.parametrize(
    ("text", "available", "video", "reason"),
    [
        (ENCODERS, True, "libsvtav1", ""),
        (ENCODERS.replace("libsvtav1", "libaom-av1"), True, "libvpx-vp9", ""),
        (ENCODERS.replace("libsvtav1", "x").replace("libvpx-vp9", "y"), False, "", "no_video_encoder"),
        (ENCODERS.replace("libopus", "opus"), False, "libsvtav1", "no_audio_encoder"),
    ],
)
def test_probe_ffmpeg_encoders(monkeypatch, text, available, video, reason):
    monkeypatch.setattr(web_video, "host_system_layout", lambda: (False, {}))
    which = _which({"ffmpeg": "/usr/bin/ffmpeg", "ffprobe": "/usr/bin/ffprobe", "pacman": "/usr/bin/pacman"})
    info = web_video.probe_ffmpeg(which, lambda _cmd, _timeout: text)
    assert (info["available"], info["video_encoder"], info["reason"]) == (available, video, reason)


def test_probe_ffmpeg_missing(monkeypatch):
    monkeypatch.setattr(web_video, "host_system_layout", lambda: (False, {}))
    monkeypatch.setattr(web_video, "is_windows", lambda: False)
    monkeypatch.setattr(web_video, "is_flatpak", lambda: False)
    run = MagicMock()
    info = web_video.probe_ffmpeg(_which({"apt": "/usr/bin/apt"}), run)
    assert info["available"] is False and info["reason"] == "missing"
    assert info["install"]["command"] == "sudo apt install ffmpeg"
    run.assert_not_called()


def test_install_hints():
    which = _which({"dnf": "/usr/bin/dnf"})
    assert web_video.install_hint(which, windows=True)["command"] == "winget install Gyan.FFmpeg"
    linux = web_video.install_hint(which, windows=False, flatpak=False, nixos=False)
    assert linux == {"command": "sudo dnf install ffmpeg-free", "package_manager": "dnf", "platform": "linux", "host": False}
    nixos = web_video.install_hint(which, windows=False, flatpak=False, nixos=True)
    assert nixos["package_manager"] == "nixos" and "pkgs.ffmpeg" in nixos["command"]
    flatpak = web_video.install_hint(which, windows=False, flatpak=True, nixos=False)
    assert flatpak["host"] is True and flatpak["command"] == "sudo dnf install ffmpeg-free"
    assert web_video.install_hint(_which({}), windows=False, flatpak=False, nixos=False)["command"] == ""


# ── Conversion ──

INFO: web_video.FfmpegInfo = {
    "available": True,
    "ffmpeg": "/usr/bin/ffmpeg",
    "ffprobe": "/usr/bin/ffprobe",
    "video_encoder": "libsvtav1",
    "audio_encoder": "libopus",
    "reason": "",
    "install": {"command": "", "package_manager": "", "platform": "linux", "host": False},
}


def _probe_json(video: str | None, audio: str | None, duration: float) -> str:
    streams = []
    if video:
        streams.append({"codec_type": "video", "codec_name": video})
    if audio:
        streams.append({"codec_type": "audio", "codec_name": audio})
    return json.dumps({"streams": streams, "format": {"duration": str(duration)}})


class FakeTools:
    """Fakes ffprobe (subprocess.run) and ffmpeg (subprocess.Popen)."""

    def __init__(self, output=("av1", "opus", 10.0), encode_code=0, write=True):
        self.output = output
        self.encode_code = encode_code
        self.write = write
        self.commands: list[list[str]] = []

    def run(self, cmd, **_kwargs):
        self.commands.append(cmd)
        path = cmd[-1]
        if path.endswith(web_video.PARTIAL_SUFFIX):
            text = _probe_json(*self.output)
        else:
            text = _probe_json("h264", "aac", 10.0)
        return subprocess.CompletedProcess(cmd, 0, text, "")

    def popen(self, cmd, **_kwargs):
        self.commands.append(cmd)
        if self.write:
            Path(cmd[-1]).write_bytes(b"converted")
        process = MagicMock()
        process.returncode = self.encode_code
        process.communicate.return_value = (b"", b"boom\nencoder failed" if self.encode_code else b"")
        return process


@pytest.fixture
def clip(tmp_path):
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"original")
    os.chmod(path, 0o640)
    return path


def _convert(clip, tools, free=10**12):
    usage = MagicMock(free=free)
    with (
        patch("core.web_video.subprocess.run", side_effect=tools.run),
        patch("core.web_video.subprocess.Popen", side_effect=tools.popen),
        patch("core.web_video.shutil.disk_usage", return_value=usage),
    ):
        return web_video.Converter(INFO).convert(str(clip))


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits")
def test_convert_replaces_the_file(clip):
    tools = FakeTools()
    result = _convert(clip, tools)
    assert result["ok"] and clip.read_bytes() == b"converted"
    assert oct(clip.stat().st_mode & 0o777) == oct(0o640)
    assert result["size"] == len(b"converted")
    encode = tools.commands[1]
    assert encode[encode.index("-c:v") + 1] == "libsvtav1" and encode[encode.index("-c:a") + 1] == "libopus"
    assert encode[-3:] == ["-f", "mp4", str(clip) + web_video.PARTIAL_SUFFIX]
    assert not Path(str(clip) + web_video.PARTIAL_SUFFIX).exists()


@pytest.mark.parametrize(
    "tools",
    [
        FakeTools(encode_code=1),
        FakeTools(output=("h264", "opus", 10.0)),
        FakeTools(output=("av1", "opus", 4.0)),
        FakeTools(output=(None, "opus", 10.0)),
    ],
)
def test_failed_convert_keeps_the_original(clip, tools):
    result = _convert(clip, tools)
    assert not result["ok"] and result["error"]
    assert clip.read_bytes() == b"original"
    assert not Path(str(clip) + web_video.PARTIAL_SUFFIX).exists()


def test_low_disk_space_keeps_the_original(clip):
    tools = FakeTools()
    with pytest.raises(web_video.LowDiskSpace):
        _ = _convert(clip, tools, free=100)
    assert clip.read_bytes() == b"original" and tools.commands == []


def test_vp9_fallback_and_stream_copy():
    info: web_video.FfmpegInfo = {**INFO, "video_encoder": "libvpx-vp9"}
    h264: web_video.StreamInfo = {"video": ["h264"], "audio": ["aac"], "duration": 1.0}
    cmd = web_video.encode_command(info, "in.mp4", "out", h264)
    assert cmd[cmd.index("-c:v") + 1] == "libvpx-vp9" and "-deadline" in cmd
    vp9: web_video.StreamInfo = {"video": ["vp9"], "audio": ["aac"], "duration": 1.0}
    cmd = web_video.encode_command(info, "in.mp4", "out", vp9)
    assert cmd[cmd.index("-c:v") + 1] == "copy" and "-pix_fmt" not in cmd
    assert cmd[cmd.index("-c:a") + 1] == "libopus"


def test_verify_output_duration_tolerance():
    source: web_video.StreamInfo = {"video": ["h264"], "audio": [], "duration": 100.0}
    ok: web_video.StreamInfo = {"video": ["av1"], "audio": [], "duration": 101.9}
    late: web_video.StreamInfo = {"video": ["av1"], "audio": [], "duration": 102.5}
    assert web_video.verify_output(source, ok) is None
    assert web_video.verify_output(source, late) == "duration does not match"


def test_cleanup_partials(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "x.mp4.wlib-partial").write_bytes(b"x")
    (tmp_path / "x.mp4").write_bytes(b"x")
    assert web_video.cleanup_partials(str(tmp_path)) == 1
    assert (tmp_path / "x.mp4").exists() and not (tmp_path / "a" / "x.mp4.wlib-partial").exists()


def test_flatpak_runs_ffmpeg_on_the_host_and_refuses_portal_paths(clip, monkeypatch):
    monkeypatch.setattr("core.host_platform.is_flatpak", lambda: True)
    tools = FakeTools()
    result = _convert(clip, tools)
    assert result["ok"]
    assert all(cmd[0] == "env" and "flatpak-spawn" in cmd for cmd in tools.commands)
    assert web_video.portal_path_error("/run/user/1000/doc/abc/game", flatpak=True)
    assert web_video.portal_path_error("/run/user/1000/doc/abc/game", flatpak=False) is None
    assert web_video.portal_path_error("/home/me/games/game", flatpak=True) is None


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="needs ffmpeg")
def test_real_ffmpeg_converts_a_tiny_clip(tmp_path, monkeypatch):
    monkeypatch.setattr(web_video, "is_flatpak", lambda: False)
    info = web_video.probe_ffmpeg(shutil.which)
    if not info["available"]:
        pytest.skip("ffmpeg has no AV1/VP9 or Opus encoder")
    encoders = web_video.parse_encoders(subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout)
    if "libx264" not in encoders or "aac" not in encoders:
        pytest.skip("ffmpeg cannot make an H.264 test clip")
    clip = tmp_path / "clip.mp4"
    _ = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=128x72:rate=10",
            "-f", "lavfi", "-i", "sine=frequency=440", "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", str(clip),
        ],
        check=True,
    )
    assert web_video.file_needs_conversion(str(clip))
    result = web_video.Converter(info).convert(str(clip))
    assert result["ok"], result["error"]
    assert not web_video.file_needs_conversion(str(clip))


def test_cover_art_is_not_a_video_track():
    text = json.dumps({
        "streams": [
            {"codec_type": "audio", "codec_name": "aac", "disposition": {"attached_pic": 0}},
            {"codec_type": "video", "codec_name": "png", "disposition": {"attached_pic": 1}},
        ],
        "format": {"duration": "3.0"},
    })
    source = web_video.parse_probe(text)
    assert source["video"] == [] and source["audio"] == ["aac"]
    encode = web_video.encode_command(INFO, "in.m4a", "out.m4a.wlib-partial", source)
    assert encode[encode.index("-map") + 1] == "0:V?"
    output = web_video.parse_probe(_probe_json(None, "opus", 3.0))
    assert web_video.verify_output(source, output) is None


def test_ffmpeg_is_looked_for_again_until_found(monkeypatch):
    missing = {**INFO, "available": False, "reason": "missing"}
    found = iter([missing, INFO])
    probe = MagicMock(side_effect=lambda: next(found))
    monkeypatch.setattr(web_video, "probe_ffmpeg", probe)
    monkeypatch.setattr(web_video, "_ffmpeg_info", None)
    assert web_video.ffmpeg_info()["available"] is False
    assert web_video.ffmpeg_info()["available"] is True
    assert web_video.ffmpeg_info()["available"] is True
    assert probe.call_count == 2
