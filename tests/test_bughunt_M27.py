# SPDX-License-Identifier: GPL-3.0-or-later
"""A refused range keeps the partial file until a full download opens."""

import io
import json
from email.message import Message
from threading import Event
from urllib.error import HTTPError

import pytest

from core.downloads.hosts import pixeldrain
from core.downloads.transfer import DownloadError, FileInfo, _resume_part, transfer


def test_pixeldrain_quota_on_resume_keeps_the_partial_file(tmp_path, monkeypatch):
    data = b"PK\x03\x04" + b"a" * 100

    def urlopen(request, **kwargs):
        if request.full_url.endswith("/info"):
            return io.BytesIO(
                json.dumps({"name": "game.zip", "size": len(data)}).encode()
            )
        # The hotlink/captcha limit answers ranged and full requests alike.
        raise HTTPError(request.full_url, 403, "Forbidden", Message(), None)

    monkeypatch.setattr(pixeldrain, "urlopen", urlopen)
    info = pixeldrain.probe("https://pixeldrain.com/u/abc", api_key="")
    part = _resume_part(tmp_path, info)
    assert part is not None
    part.write_bytes(data[:40])
    with pytest.raises(DownloadError, match="quota"):
        transfer(info, "job", Event(), lambda *_: None, directory=tmp_path)
    assert part.read_bytes() == data[:40]


def test_refused_range_restarts_once_the_full_download_opens(tmp_path):
    data = b"PK\x03\x04" + b"a" * 100

    def resume(offset):
        raise HTTPError("https://host/file", 403, "Forbidden", Message(), None)

    info = FileInfo(
        "game.zip",
        len(data),
        lambda: io.BytesIO(data),
        resume_factory=resume,
        resume_key="host:file-1",
    )
    part = _resume_part(tmp_path, info)
    assert part is not None
    part.write_bytes(b"PK\x03\x04garbage")
    path = transfer(info, "job", Event(), lambda *_: None, directory=tmp_path)
    assert path.read_bytes() == data and not list(tmp_path.glob("*.part"))


def test_refused_range_restart_checks_space_for_the_whole_file(tmp_path, monkeypatch):
    data = b"PK\x03\x04" + b"a" * 100
    opened = []

    def stream():
        opened.append(0)
        return io.BytesIO(data)

    def resume(offset):
        return None  # The host ignores the range.

    info = FileInfo(
        "game.zip", len(data), stream, resume_factory=resume, resume_key="host:file-1"
    )
    part = _resume_part(tmp_path, info)
    assert part is not None
    part.write_bytes(data[:40])
    monkeypatch.setattr(
        "core.downloads.transfer.shutil.disk_usage",
        lambda _: type("Space", (), {"free": 70})(),
    )
    with pytest.raises(DownloadError, match="Not enough disk space"):
        transfer(info, "job", Event(), lambda *_: None, directory=tmp_path)
    assert not part.exists()
