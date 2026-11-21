# SPDX-License-Identifier: GPL-3.0-or-later
"""Video conversion keeps embedded subtitle tracks."""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from core import web_video

INFO: web_video.FfmpegInfo = {
    "available": True,
    "ffmpeg": "ffmpeg",
    "ffprobe": "ffprobe",
    "video_encoder": "libsvtav1",
    "audio_encoder": "libopus",
    "reason": "",
    "install": {
        "command": "",
        "package_manager": "",
        "platform": "linux",
        "host": False,
    },
}


def test_probe_reads_subtitle_tracks():
    text = json.dumps(
        {
            "streams": [
                {"codec_type": "video", "codec_name": "h264"},
                {"codec_type": "audio", "codec_name": "aac"},
                {"codec_type": "subtitle", "codec_name": "mov_text"},
            ],
            "format": {"duration": "2.0"},
        }
    )
    assert web_video.parse_probe(text).get("subtitle") == ["mov_text"]


def test_encode_command_maps_and_copies_subtitles():
    streams: web_video.StreamInfo = {
        "video": ["h264"],
        "audio": ["aac"],
        "duration": 1.0,
        "subtitle": ["mov_text"],
    }
    cmd = web_video.encode_command(INFO, "in.mp4", "out", streams)
    assert "-sn" not in cmd
    maps = [cmd[i + 1] for i, part in enumerate(cmd) if part == "-map"]
    assert "0:s?" in maps
    assert cmd[cmd.index("-c:s") + 1] == "copy"


def test_verify_output_refuses_lost_subtitles():
    source: web_video.StreamInfo = {
        "video": ["h264"],
        "audio": ["aac"],
        "duration": 2.0,
        "subtitle": ["mov_text"],
    }
    lost: web_video.StreamInfo = {
        "video": ["av1"],
        "audio": ["opus"],
        "duration": 2.0,
        "subtitle": [],
    }
    kept: web_video.StreamInfo = {
        "video": ["av1"],
        "audio": ["opus"],
        "duration": 2.0,
        "subtitle": ["mov_text"],
    }
    assert web_video.verify_output(source, lost) == "subtitles are missing"
    assert web_video.verify_output(source, kept) is None


@pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="needs ffmpeg"
)
def test_real_ffmpeg_keeps_a_mov_text_track(tmp_path, monkeypatch):
    monkeypatch.setattr(web_video, "is_flatpak", lambda: False)
    info = web_video.probe_ffmpeg(shutil.which)
    if not info["available"]:
        pytest.skip("ffmpeg has no AV1/VP9 or Opus encoder")
    encoders = web_video.parse_encoders(
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True
        ).stdout
    )
    if "libx264" not in encoders or "aac" not in encoders:
        pytest.skip("ffmpeg cannot make an H.264 test clip")
    srt = tmp_path / "subs.srt"
    _ = srt.write_text("1\n00:00:00,000 --> 00:00:00,800\nHello\n", encoding="utf-8")
    clip = tmp_path / "clip.mp4"
    _ = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=128x72:rate=10",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440",
            "-i",
            str(srt),
            "-map",
            "0",
            "-map",
            "1",
            "-map",
            "2",
            "-t",
            "1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-c:s",
            "mov_text",
            str(clip),
        ],
        check=True,
    )
    result = web_video.Converter(info).convert(str(clip))
    assert result["ok"], result["error"]
    assert not web_video.file_needs_conversion(str(clip))
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "csv=p=0",
            str(clip),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert "subtitle" in probe
