# SPDX-License-Identifier: GPL-3.0-or-later
import io
import json
from email.message import Message
from pathlib import Path
from threading import Event
from urllib.error import HTTPError
from urllib.request import Request

import pytest

from core.downloads import failures
from core.downloads.hosts import pixeldrain
from core.downloads.transfer import DownloadError, resume_part_name, transfer


class _DroppedStream(io.BytesIO):
    def read(self, size: int | None = -1) -> bytes:
        if self.tell():
            raise ConnectionResetError("connection dropped")
        return super().read(size)


@pytest.mark.parametrize("has_partial", [True, False], ids=["resume", "reconnect"])
def test_pixeldrain_range_rate_limit_keeps_partial_for_manual_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, has_partial: bool
) -> None:
    partial_data = b"PK\x03\x04" + b"a" * 36
    ranges: list[str | None] = []

    def open_request(request: Request, timeout: int = 30) -> io.BytesIO:
        if request.full_url.endswith("/info"):
            return io.BytesIO(json.dumps({"name": "game.zip", "size": 100}).encode())
        byte_range = request.get_header("Range")
        ranges.append(byte_range)
        if byte_range is None:
            return _DroppedStream(partial_data)
        raise HTTPError(request.full_url, 429, "Too Many Requests", Message(), None)

    monkeypatch.setattr(pixeldrain, "urlopen", open_request)
    monkeypatch.setattr("core.downloads.transfer.RESUME_RETRY_DELAY_SECONDS", 0)
    info = pixeldrain.probe("https://pixeldrain.com/u/abc", api_key="")
    part = tmp_path / resume_part_name(info)
    if has_partial:
        _ = part.write_bytes(partial_data)

    with pytest.raises(DownloadError) as error:
        _ = transfer(info, "job", Event(), lambda *_: None, directory=tmp_path)

    code = failures.classify(str(error.value))
    assert code == failures.QUOTA
    assert not failures.retryable(code)
    assert part.read_bytes() == partial_data
    assert ranges == (["bytes=40-"] if has_partial else [None, "bytes=40-"])
