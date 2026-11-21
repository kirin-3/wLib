# SPDX-License-Identifier: GPL-3.0-or-later
import io
import json
from email.message import Message
from threading import Event
from urllib.error import HTTPError

import pytest

from core.downloads.hosts import buzzheavier, pixeldrain
from core.downloads.transfer import DownloadError, FileInfo, transfer


def test_transfer_progress_cancel_invalid_and_space(tmp_path, monkeypatch):
    progress = []
    data = b"PK\x03\x04" + b"a" * 20
    path = transfer(
        FileInfo("../game.zip", len(data), lambda: io.BytesIO(data)),
        "job",
        Event(),
        lambda done, total: progress.append((done, total)),
        directory=tmp_path,
    )
    assert path.read_bytes() == data and path.name == "game.zip"
    assert progress == [(len(data), len(data))]
    flag = Event()

    def cancel(done, total):
        flag.set()

    with pytest.raises(DownloadError, match="cancelled"):
        transfer(
            FileInfo("game.zip", len(data), lambda: io.BytesIO(data)),
            "other",
            flag,
            cancel,
            directory=tmp_path,
        )
    assert not (tmp_path / "other.part").exists()
    with pytest.raises(DownloadError, match="file invalid"):
        transfer(
            FileInfo("fake.zip", None, lambda: io.BytesIO(b"<html>")),
            "html",
            Event(),
            lambda *_: None,
            directory=tmp_path,
        )
    assert not (tmp_path / "html.part").exists()
    monkeypatch.setattr(
        "core.downloads.transfer.shutil.disk_usage",
        lambda _: type("Space", (), {"free": 0})(),
    )
    with pytest.raises(DownloadError, match="Not enough disk space"):
        transfer(
            FileInfo("large.zip", 1, lambda: pytest.fail("must not open stream")),
            "space",
            Event(),
            lambda *_: None,
            directory=tmp_path,
        )


def test_transfer_accepts_tarballs_and_keeps_the_double_extension(tmp_path):
    import bz2
    import gzip

    data = bz2.compress(b"tar data")
    path = transfer(
        FileInfo("Game-1.0-linux.tar.bz2", len(data), lambda: io.BytesIO(data)),
        "tarjob",
        Event(),
        lambda *_: None,
        directory=tmp_path,
    )
    assert path.name == "Game-1.0-linux.tar.bz2" and path.read_bytes() == data
    gz = gzip.compress(b"tar data")
    first = transfer(
        FileInfo("Game-1.0-linux.tar.gz", len(gz), lambda: io.BytesIO(gz)),
        "gz1",
        Event(),
        lambda *_: None,
        directory=tmp_path,
    )
    second = transfer(
        FileInfo("Game-1.0-linux.tar.gz", len(gz), lambda: io.BytesIO(gz)),
        "gz2",
        Event(),
        lambda *_: None,
        directory=tmp_path,
    )
    assert first.name == "Game-1.0-linux.tar.gz"
    assert second.name == "Game-1.0-linux-gz2.tar.gz"


def test_transfer_rejects_a_page_under_a_tarball_name(tmp_path):
    page = b"<!DOCTYPE html><html><body>Download</body></html>" + b" " * 600
    with pytest.raises(DownloadError, match="file invalid"):
        transfer(
            FileInfo("Game-1.0-linux.tar.xz", len(page), lambda: io.BytesIO(page)),
            "page",
            Event(),
            lambda *_: None,
            directory=tmp_path,
        )
    assert list(tmp_path.iterdir()) == []
    with pytest.raises(DownloadError, match="file invalid"):
        transfer(
            FileInfo(
                "Game-1.0-linux.bz2", 3, lambda: pytest.fail("must not open stream")
            ),
            "bare",
            Event(),
            lambda *_: None,
            directory=tmp_path,
        )


def test_pixeldrain_probe_and_error_mapping(monkeypatch):
    urls = []

    def urlopen(request, **kwargs):
        urls.append(request.full_url)
        return io.BytesIO(json.dumps({"name": "game.zip", "size": 123}).encode())

    monkeypatch.setattr(pixeldrain, "urlopen", urlopen)
    info = pixeldrain.probe("https://pixeldrain.com/u/abc", api_key="")
    assert info.name == "game.zip" and info.size == 123
    info.stream_factory().close()
    assert urls == [
        "https://pixeldrain.com/api/file/abc/info",
        "https://pixeldrain.com/api/file/abc?download",
    ]
    assert not pixeldrain.matches("https://pixeldrain.com/l/abc")
    assert pixeldrain.classify_error(404) == "link expired or removed"
    assert "quota" in pixeldrain.classify_error(429)


def test_buzzheavier_referer_and_challenge(monkeypatch):
    class Response(io.BytesIO):
        def __init__(self):
            super().__init__(b"PK\x03\x04")
            self.headers = Message()
            self.headers["Content-Disposition"] = 'attachment; filename="game.zip"'
            self.headers["Content-Length"] = "4"

    requests = []

    def urlopen(request, **kwargs):
        requests.append(request)
        return Response()

    monkeypatch.setattr(buzzheavier, "urlopen", urlopen)
    info = buzzheavier.probe(
        "https://buzzheavier.com/download", "https://buzzheavier.com/share"
    )
    assert info.name == "game.zip"
    assert requests[0].get_header("Referer") == "https://buzzheavier.com/share"
    info.stream_factory().close()
    headers = Message()
    headers["cf-mitigated"] = "challenge"

    def fail(request, **kwargs):
        raise HTTPError(request.full_url, 403, "challenge", headers, None)

    monkeypatch.setattr(buzzheavier, "urlopen", fail)
    with pytest.raises(DownloadError, match="blocked by a challenge"):
        buzzheavier.probe(
            "https://buzzheavier.com/download", "https://buzzheavier.com/share"
        )


def test_mega_decrypts_and_rejects_corruption(tmp_path):
    import base64
    import struct
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from core.downloads.hosts import mega

    plain = b"PK\x03\x04" + bytes(range(256)) * 1600
    key = bytes(range(16))
    nonce = bytes(range(8))
    macs = []
    offset = 0
    chunk_size = 128 * 1024
    while offset < len(plain):
        chunk = plain[offset : offset + chunk_size]
        offset += len(chunk)
        chunk_size = min(chunk_size + 128 * 1024, 1024 * 1024)
        cipher = Cipher(algorithms.AES(key), modes.CBC(nonce * 2)).encryptor()
        macs.append(
            (cipher.update(chunk + b"\0" * (-len(chunk) % 16)) + cipher.finalize())[
                -16:
            ]
        )
    cipher = Cipher(algorithms.AES(key), modes.CBC(b"\0" * 16)).encryptor()
    digest = (cipher.update(b"".join(macs)) + cipher.finalize())[-16:]
    a, b, c, d = struct.unpack(">4I", digest)
    meta = struct.pack(">2I", a ^ b, c ^ d)
    suffix = nonce + meta
    obfuscated = bytes(a ^ b for a, b in zip(key, suffix, strict=True)) + suffix
    encoded = base64.urlsafe_b64encode(obfuscated).rstrip(b"=").decode()
    assert mega.parse_link(f"https://mega.nz/file/abc#{encoded}")[1] == obfuscated
    assert mega.parse_link(f"https://mega.nz/#!abc!{encoded}")[1] == obfuscated
    with pytest.raises(DownloadError, match="missing its key"):
        mega.parse_link("https://mega.nz/file/abc")
    cipher = Cipher(algorithms.AES(key), modes.CTR(nonce + b"\0" * 8)).encryptor()
    encrypted = cipher.update(plain) + cipher.finalize()
    info = FileInfo(
        "mega.zip",
        len(plain),
        lambda: io.BytesIO(encrypted),
        mega.MegaDecryptor(obfuscated),
    )
    assert (
        transfer(
            info, "mega", Event(), lambda *_: None, directory=tmp_path
        ).read_bytes()
        == plain
    )
    corrupted = encrypted[:-1] + bytes([encrypted[-1] ^ 1])
    info = FileInfo(
        "bad.zip",
        len(plain),
        lambda: io.BytesIO(corrupted),
        mega.MegaDecryptor(obfuscated),
    )
    with pytest.raises(DownloadError, match="checksum"):
        transfer(info, "bad", Event(), lambda *_: None, directory=tmp_path)
    assert not (tmp_path / "bad.part").exists()


def test_mega_hashcash_matches_sdk_threshold():
    import base64
    import hashlib
    from core.downloads.hosts import mega

    token = bytes(range(48))
    token_text = base64.urlsafe_b64encode(token).rstrip(b"=").decode()
    solution = mega.solve_hashcash(f"1:255:123:{token_text}", budget=5)
    version, result_token, encoded_nonce = solution.split(":")
    nonce = base64.urlsafe_b64decode(encoded_nonce + "=" * (-len(encoded_nonce) % 4))
    threshold = ((((255 & 63) << 1) + 1) << ((255 >> 6) * 7 + 3)) & 0xFFFFFFFF
    assert version == "1" and result_token == token_text
    assert (
        int.from_bytes(hashlib.sha256(nonce + token * 262144).digest()[:4], "big")
        <= threshold
    )


def test_host_redirects_reject_unrelated_servers():
    from core.downloads.network import allowed

    assert allowed("https://cdn.pixeldrain.com/file", ("pixeldrain.com",))
    assert not allowed("https://pixeldrain.com.evil.example/file", ("pixeldrain.com",))
    assert not allowed("http://pixeldrain.com/file", ("pixeldrain.com",))
    assert not allowed("https://pixeldrain.com:8443/file", ("pixeldrain.com",))


def test_open_host_sets_user_agent_only_when_present_and_missing(monkeypatch):
    from urllib.request import Request

    from core.downloads import network

    sent: list[Request] = []

    class FakeOpener:
        def open(self, request: Request, timeout: int = 30) -> io.BytesIO:
            sent.append(request)
            return io.BytesIO(b"ok")

    monkeypatch.setattr(network, "build_opener", lambda *_args: FakeOpener())

    network.open_host(
        Request("https://buzzheavier.com/file"),
        ("buzzheavier.com",),
        user_agent="Mozilla/5.0 TestBrowser/1.0",
    )
    network.open_host(
        Request("https://buzzheavier.com/file"),
        ("buzzheavier.com",),
        user_agent="",
    )
    network.open_host(
        Request("https://buzzheavier.com/file", headers={"User-Agent": "Explicit/1.0"}),
        ("buzzheavier.com",),
        user_agent="Mozilla/5.0 TestBrowser/1.0",
    )

    assert sent[0].get_header("User-agent") == "Mozilla/5.0 TestBrowser/1.0"
    assert sent[1].get_header("User-agent") is None
    assert sent[2].get_header("User-agent") == "Explicit/1.0"


def test_cloudflare_refusal_detects_1010_and_closes_bounded_read():
    from core.downloads.network import cloudflare_refusal

    class BoundedBody(io.BytesIO):
        def __init__(self, data: bytes):
            super().__init__(data)
            self.sizes: list[int | None] = []

        def read(self, size: int | None = -1) -> bytes:
            self.sizes.append(size)
            return super().read(size)

    body_1010 = BoundedBody(b"  error code: 1010\n" + b"x" * 1024)
    exc_1010 = HTTPError("https://host/file", 403, "Forbidden", Message(), body_1010)
    assert cloudflare_refusal(exc_1010) is True
    assert body_1010.sizes == [512]
    assert body_1010.closed is True

    body_other = BoundedBody(b"<html>Access denied</html>")
    exc_other = HTTPError("https://host/file", 403, "Forbidden", Message(), body_other)
    assert cloudflare_refusal(exc_other) is False
    assert body_other.sizes == [512]
    assert body_other.closed is True

    exc_empty = HTTPError(
        "https://host/file", 403, "Forbidden", Message(), io.BytesIO()
    )
    assert cloudflare_refusal(exc_empty) is False
    exc_none = HTTPError("https://host/file", 403, "Forbidden", Message(), None)
    assert cloudflare_refusal(exc_none) is False

    body_404 = BoundedBody(b"error code: 1010")
    exc_404 = HTTPError("https://host/file", 404, "Not Found", Message(), body_404)
    assert cloudflare_refusal(exc_404) is False
    assert body_404.sizes == []


def _http_1010(url: str = "https://host/file") -> HTTPError:
    return HTTPError(url, 403, "Forbidden", Message(), io.BytesIO(b"error code: 1010"))


def test_cloudflare_1010_raises_challenge_on_every_host(monkeypatch):
    import base64
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from core.downloads.hosts import mega

    # Buzzheavier probe
    monkeypatch.setattr(
        buzzheavier,
        "urlopen",
        lambda req, **_: (_ for _ in ()).throw(_http_1010(req.full_url)),
    )
    with pytest.raises(DownloadError, match="^blocked by a challenge$"):
        buzzheavier.probe(
            "https://buzzheavier.com/download", "https://buzzheavier.com/share"
        )

    # Pixeldrain probe
    monkeypatch.setattr(
        pixeldrain,
        "urlopen",
        lambda req, **_: (_ for _ in ()).throw(_http_1010(req.full_url)),
    )
    with pytest.raises(DownloadError, match="^blocked by a challenge$"):
        pixeldrain.probe("https://pixeldrain.com/u/abc", api_key="")

    # Pixeldrain stream
    def pixeldrain_stream_1010(req, **_):
        if req.full_url.endswith("/info"):
            return io.BytesIO(json.dumps({"name": "game.zip", "size": 10}).encode())
        raise _http_1010(req.full_url)

    monkeypatch.setattr(pixeldrain, "urlopen", pixeldrain_stream_1010)
    info = pixeldrain.probe("https://pixeldrain.com/u/abc", api_key="")
    with pytest.raises(DownloadError, match="^blocked by a challenge$"):
        info.stream_factory()

    # MEGA _request (via probe)
    raw_key = bytes(range(32))
    encoded_key = base64.urlsafe_b64encode(raw_key).rstrip(b"=").decode("ascii")
    mega_url = f"https://mega.nz/file/abc#{encoded_key}"
    monkeypatch.setattr(
        mega,
        "urlopen",
        lambda req, **_: (_ for _ in ()).throw(_http_1010(req.full_url)),
    )
    with pytest.raises(DownloadError, match="^blocked by a challenge$"):
        mega.probe(mega_url)

    # MEGA file stream
    key, _, _ = mega._key_parts(raw_key)
    attrs = b'MEGA{"n":"game.zip"}'
    attrs += b"\0" * (-len(attrs) % 16)
    enc_attrs = (
        Cipher(algorithms.AES(key), modes.CBC(b"\0" * 16)).encryptor().update(attrs)
    )
    at_b64 = base64.urlsafe_b64encode(enc_attrs).rstrip(b"=").decode("ascii")

    class _JsonResp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    def mega_stream_1010(req, **_):
        if "g.api.mega.co.nz" in req.full_url:
            return _JsonResp(
                json.dumps(
                    [{"at": at_b64, "g": "https://gfs.mega.co.nz/dl/file", "s": 10}]
                ).encode()
            )
        raise _http_1010(req.full_url)

    monkeypatch.setattr(mega, "urlopen", mega_stream_1010)
    mega_info = mega.probe(mega_url)
    with pytest.raises(DownloadError, match="^blocked by a challenge$"):
        mega_info.stream_factory()


def test_every_host_request_carries_user_agent_including_range(monkeypatch):
    import base64
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from core.downloads import network
    from core.downloads.hosts import mega

    ua = "Mozilla/5.0 (X11; Linux x86_64) TestBrowser/120.0"

    class _Resp(io.BytesIO):
        def __init__(
            self, body: bytes, status: int = 200, headers: Message | None = None
        ):
            super().__init__(body)
            self.status = status
            self.headers = headers if headers is not None else Message()

        def __enter__(self):
            return self

        def __exit__(self, *_):
            self.close()
            return False

    # Pixeldrain: /info, ?download, Range resume
    pd_requests = []

    class _PdOpener:
        def open(self, req, timeout=30):
            pd_requests.append(req)
            if req.full_url.endswith("/info"):
                return _Resp(json.dumps({"name": "game.zip", "size": 100}).encode())
            if req.get_header("Range"):
                hdrs = Message()
                hdrs["Content-Range"] = "bytes 20-99/100"
                return _Resp(b"rest", status=206, headers=hdrs)
            return _Resp(b"PK\x03\x04")

    monkeypatch.setattr(network, "build_opener", lambda *_args: _PdOpener())
    pd_info = pixeldrain.probe(
        "https://pixeldrain.com/u/abc", api_key="", user_agent=ua
    )
    pd_info.stream_factory().close()
    assert pd_info.resume_factory is not None
    resumed = pd_info.resume_factory(20)
    assert resumed is not None
    resumed.close()
    assert len(pd_requests) == 3
    assert [r.get_header("User-agent") for r in pd_requests] == [ua, ua, ua]
    assert pd_requests[2].get_header("Range") == "bytes=20-"

    # Buzzheavier: probe and Range resume
    bz_requests = []

    class _BzOpener:
        def open(self, req, timeout=30):
            bz_requests.append(req)
            hdrs = Message()
            if req.get_header("Range"):
                hdrs["Content-Range"] = "bytes 10-99/100"
                return _Resp(b"rest", status=206, headers=hdrs)
            hdrs["Content-Disposition"] = 'attachment; filename="game.zip"'
            hdrs["Content-Length"] = "100"
            return _Resp(b"PK\x03\x04", status=200, headers=hdrs)

    monkeypatch.setattr(network, "build_opener", lambda *_args: _BzOpener())
    bz_info = buzzheavier.probe(
        "https://buzzheavier.com/download",
        "https://buzzheavier.com/share",
        user_agent=ua,
    )
    assert bz_info.resume_factory is not None
    bz_resumed = bz_info.resume_factory(10)
    assert bz_resumed is not None
    bz_resumed.close()
    bz_info.stream_factory().close()
    assert len(bz_requests) == 2
    assert [r.get_header("User-agent") for r in bz_requests] == [ua, ua]
    assert bz_requests[1].get_header("Range") == "bytes=10-"

    # MEGA: API _request and file stream
    raw_key = bytes(range(32))
    encoded_key = base64.urlsafe_b64encode(raw_key).rstrip(b"=").decode("ascii")
    key, _, _ = mega._key_parts(raw_key)
    attrs = b'MEGA{"n":"game.zip"}'
    attrs += b"\0" * (-len(attrs) % 16)
    enc_attrs = (
        Cipher(algorithms.AES(key), modes.CBC(b"\0" * 16)).encryptor().update(attrs)
    )
    at_b64 = base64.urlsafe_b64encode(enc_attrs).rstrip(b"=").decode("ascii")
    mega_requests = []

    class _MegaOpener:
        def open(self, req, timeout=30):
            mega_requests.append(req)
            if "g.api.mega.co.nz" in req.full_url:
                return _Resp(
                    json.dumps(
                        [{"at": at_b64, "g": "https://gfs.mega.co.nz/dl/file", "s": 10}]
                    ).encode()
                )
            return _Resp(b"0123456789")

    monkeypatch.setattr(network, "build_opener", lambda *_args: _MegaOpener())
    mega_info = mega.probe(f"https://mega.nz/file/abc#{encoded_key}", user_agent=ua)
    mega_info.stream_factory().close()
    assert len(mega_requests) == 2
    assert [r.get_header("User-agent") for r in mega_requests] == [ua, ua]


def test_mega_requests_https_urls_and_maps_file_errors(monkeypatch):
    from core.downloads.hosts import mega

    requests = []

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    def fake_open(request, timeout=30, user_agent=""):
        del timeout, user_agent
        requests.append(json.loads(request.data))
        return Response(json.dumps([{"e": -16}]).encode())

    monkeypatch.setattr(mega, "urlopen", fake_open)
    with pytest.raises(DownloadError, match="expired or removed"):
        mega._api("abc", sid="")
    assert requests[0][0]["ssl"] == 2


def test_pixeldrain_aliases_and_hotlink_limit():
    assert pixeldrain.matches("https://pixeldrain.net/u/abc")
    assert pixeldrain.classify_error(403) == "transfer quota or rate limit reached"


def test_pixeldrain_folder_links_are_reported_as_unsupported(monkeypatch):
    opened = []
    monkeypatch.setattr(pixeldrain, "urlopen", lambda *a, **k: opened.append(a))
    assert pixeldrain.matches("https://pixeldrain.com/d/abc123")
    with pytest.raises(DownloadError, match="folder links are not supported"):
        pixeldrain.probe("https://pixeldrain.com/d/abc123", api_key="")
    assert opened == []


class _Flaky(io.BytesIO):
    """Serves `data[start:]`, then drops the connection after `fail_after` bytes."""

    def __init__(self, data, start=0, fail_after=None):
        super().__init__(data[start:])
        self.fail_after = fail_after
        self.sent = 0

    def read(self, size=-1):
        if self.fail_after is not None and self.sent >= self.fail_after:
            raise ConnectionResetError("dropped")
        limit = (
            size if self.fail_after is None else min(size, self.fail_after - self.sent)
        )
        chunk = super().read(limit)
        self.sent += len(chunk)
        return chunk


def _resumable(
    data, offsets, *, fail_after=None, resume_fails_after=None, honour_range=True
):
    def resume(offset):
        offsets.append(offset)
        if not honour_range:
            return None
        return _Flaky(data, offset, resume_fails_after)

    return FileInfo(
        "game.zip",
        len(data),
        lambda: _Flaky(data, 0, fail_after),
        resume_factory=resume,
        resume_key="host:file-1",
    )


def test_transfer_reconnects_after_a_dropped_connection(tmp_path, monkeypatch):
    monkeypatch.setattr("core.downloads.transfer.RESUME_RETRY_DELAY_SECONDS", 0)
    data = b"PK\x03\x04" + bytes(range(256)) * 10
    offsets = []
    path = transfer(
        _resumable(data, offsets, fail_after=1000),
        "job",
        Event(),
        lambda *_: None,
        directory=tmp_path,
    )
    assert path.read_bytes() == data
    assert offsets == [1000]
    assert not list(tmp_path.glob("*.part"))


def test_failed_download_resumes_in_a_later_job(tmp_path, monkeypatch):
    monkeypatch.setattr("core.downloads.transfer.RESUME_RETRY_DELAY_SECONDS", 0)
    data = b"PK\x03\x04" + bytes(range(256)) * 10
    offsets = []
    with pytest.raises(DownloadError, match="network error"):
        transfer(
            _resumable(data, offsets, fail_after=700, resume_fails_after=0),
            "first",
            Event(),
            lambda *_: None,
            directory=tmp_path,
        )
    (part,) = tmp_path.glob("resume-*.part")
    assert part.stat().st_size == 700
    progress = []
    later = []
    path = transfer(
        _resumable(data, later),
        "second",
        Event(),
        lambda done, total: progress.append(done),
        directory=tmp_path,
    )
    assert path.read_bytes() == data
    assert later == [700] and progress[0] > 700
    assert not part.exists()


def test_transfer_starts_over_when_the_host_ignores_ranges(tmp_path):
    data = b"PK\x03\x04" + b"a" * 100
    offsets = []
    info = _resumable(data, offsets, honour_range=False)
    from core.downloads.transfer import _resume_part

    stale = _resume_part(tmp_path, info)
    stale.write_bytes(b"PK\x03\x04garbage")
    path = transfer(info, "job", Event(), lambda *_: None, directory=tmp_path)
    assert path.read_bytes() == data and offsets == [len(b"PK\x03\x04garbage")]


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError("timed out"),
        HTTPError("https://host/file", 429, "Too Many Requests", Message(), None),
        HTTPError("https://host/file", 503, "Service Unavailable", Message(), None),
    ],
)
def test_short_error_while_resuming_keeps_the_partial_file(tmp_path, error):
    from core.downloads.transfer import _resume_part

    data = b"PK\x03\x04" + b"a" * 100
    opened = []

    def resume(offset):
        raise error

    def stream():
        opened.append(0)
        return io.BytesIO(data)

    info = FileInfo(
        "game.zip", len(data), stream, resume_factory=resume, resume_key="host:file-1"
    )
    part = _resume_part(tmp_path, info)
    part.write_bytes(data[:40])
    with pytest.raises(DownloadError, match="network error"):
        transfer(info, "job", Event(), lambda *_: None, directory=tmp_path)
    assert part.read_bytes() == data[:40] and opened == []


def test_range_refused_with_a_4xx_starts_over(tmp_path):
    from core.downloads.transfer import _resume_part

    data = b"PK\x03\x04" + b"a" * 100

    def resume(offset):
        raise HTTPError(
            "https://host/file", 416, "Range Not Satisfiable", Message(), None
        )

    info = FileInfo(
        "game.zip",
        len(data),
        lambda: io.BytesIO(data),
        resume_factory=resume,
        resume_key="host:file-1",
    )
    _resume_part(tmp_path, info).write_bytes(data)  # Complete, but never published.
    path = transfer(info, "job", Event(), lambda *_: None, directory=tmp_path)
    assert path.read_bytes() == data and not list(tmp_path.glob("*.part"))


def test_cancel_drops_the_partial_file(tmp_path):
    data = b"PK\x03\x04" + b"a" * 100
    flag = Event()
    with pytest.raises(DownloadError, match="cancelled"):
        transfer(
            _resumable(data, []), "job", flag, lambda *_: flag.set(), directory=tmp_path
        )
    assert not list(tmp_path.glob("*.part"))


def test_open_range_accepts_only_a_matching_partial_answer():
    from core.downloads.transfer import open_range

    class Answer(io.BytesIO):
        def __init__(self, status, content_range):
            super().__init__(b"rest")
            self.status = status
            self.headers = Message()
            if content_range:
                self.headers["Content-Range"] = content_range

    sent = []
    good = open_range(
        lambda headers: sent.append(headers) or Answer(206, "bytes 10-99/100"), 10
    )
    assert good is not None and good.read() == b"rest"
    assert sent == [{"Range": "bytes=10-"}]
    assert open_range(lambda headers: Answer(200, ""), 10) is None
    assert open_range(lambda headers: Answer(206, "bytes 0-99/100"), 10) is None


def test_open_range_1010_fails_as_challenge_without_starting_over(tmp_path):
    from core.downloads.transfer import _resume_part, open_range

    data = b"PK\x03\x04" + b"a" * 100
    full_opened = []

    def resume(offset):
        return open_range(
            lambda _headers: (_ for _ in ()).throw(_http_1010("https://host/file")),
            offset,
        )

    def stream():
        full_opened.append(0)
        return io.BytesIO(data)

    info = FileInfo(
        "game.zip", len(data), stream, resume_factory=resume, resume_key="host:file-1"
    )
    part = _resume_part(tmp_path, info)
    assert part is not None
    part.write_bytes(data[:40])
    with pytest.raises(DownloadError, match="^blocked by a challenge$"):
        transfer(info, "job", Event(), lambda *_: None, directory=tmp_path)
    assert full_opened == []
    # Like a 429, the partial file stays for a manual retry.
    assert part.read_bytes() == data[:40]


# --- file-host jobs in the download queue (HostWorker) ----------------------

import threading  # noqa: E402
import time  # noqa: E402

PIXELDRAIN_URL = "https://pixeldrain.com/u/SECRETFILEID"


class _Gated(io.BytesIO):
    """Hands out `hold` bytes at once, then the rest only after `gate` is set."""

    def __init__(self, data, gate, offset=0, hold=0):
        super().__init__(data[offset:])
        self.gate = gate
        self.hold = hold
        self.sent = 0

    def read(self, size=-1):
        if self.sent >= self.hold:
            assert self.gate.wait(5)
        chunk = super().read(min(size, 64) if size and size > 0 else 64)
        self.sent += len(chunk)
        return chunk


def _resumable_info(data, gate, opened, hold=0):
    def stream():
        opened.append(0)
        return _Gated(data, gate, hold=hold if len(opened) == 1 else 0)

    def resume(offset):
        opened.append(offset)
        return _Gated(data, gate, offset)

    return FileInfo(
        "game.zip",
        len(data),
        stream,
        resume_factory=resume,
        resume_key="pixeldrain:file",
    )


def _host_queue(tmp_path, monkeypatch, probe, relink=None, **options):
    from core.database import init_db
    from core.downloads import manager as manager_module
    from core.downloads import workers as workers_module
    from core.downloads.manager import DownloadManager
    from core.downloads.pacing import SpeedLimiter
    from core.downloads.queue import DownloadQueue
    from core.downloads.workers import HostWorker

    monkeypatch.setattr("core.downloads.transfer.staging_dir", lambda: tmp_path)
    monkeypatch.setattr(manager_module, "staging_dir", lambda: tmp_path)
    monkeypatch.setattr(workers_module, "staging_dir", lambda: tmp_path)
    monkeypatch.setattr(manager_module.pixeldrain, "probe", probe)
    init_db()
    manager = DownloadManager(limiter=SpeedLimiter())
    queue = DownloadQueue(
        [HostWorker(manager, relink)],
        run_thread=False,
        free_space=lambda: 10**12,
        **options,
    )
    return manager, queue


def _resolve_to(url=PIXELDRAIN_URL, events=None):
    def resolve(progress, cancel):
        if events is not None:
            events.append("resolved")
        return {"ok": True, "url": url}

    return resolve


def _add_host(queue, game_id, resolve=None, host="pixeldrain"):
    from core.downloads.workers import HostContext

    source = {
        "label": "Win",
        "section": "",
        "platform": "windows",
        "host": host,
        "part": "",
    }
    return queue.add(
        "host", game_id, "Game", "1.0", source, HostContext(resolve or _resolve_to())
    ).id


def _until(queue, predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        queue.tick()
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_job_added_without_a_free_slot_resolves_at_once_and_transfers_later(
    tmp_path, monkeypatch
):
    from core.database import update_setting
    from core.downloads import queue_settings

    gate = threading.Event()
    data = b"PK\x03\x04" + b"a" * 400
    _, queue = _host_queue(
        tmp_path, monkeypatch, lambda url, **_: _resumable_info(data, gate, [])
    )
    update_setting(queue_settings.MAX_ACTIVE_KEY, "1")
    queue.apply_settings()
    first = _add_host(queue, 1)
    assert _until(queue, lambda: queue.get(first).state == "running")
    events = []
    second = _add_host(queue, 2, _resolve_to(events=events))
    # Resolved while no slot is free, then it waits.
    assert _until(
        queue, lambda: events == ["resolved"] and queue.get(second).state == "queued"
    )
    gate.set()
    assert _until(queue, lambda: queue.get(first).state == "ready")
    assert _until(queue, lambda: queue.get(second).state == "ready")
    assert queue.get(second).details["archive_path"]


def test_resolve_needs_the_user_then_continues(tmp_path, monkeypatch):
    waiting = threading.Event()
    resume = threading.Event()

    def resolve(progress, cancel):
        progress("needs_user")
        waiting.set()
        assert resume.wait(3)
        return {"ok": True, "url": PIXELDRAIN_URL}

    _, queue = _host_queue(
        tmp_path,
        monkeypatch,
        lambda url, **_: FileInfo("update.zip", 4, lambda: io.BytesIO(b"PK\x03\x04")),
    )
    job_id = _add_host(queue, 1, resolve)
    assert waiting.wait(2)
    assert _until(queue, lambda: queue.get(job_id).state == "attention")
    assert queue.get(job_id).reason == "browser_check"
    resume.set()
    assert _until(queue, lambda: queue.get(job_id).state == "ready")
    assert (tmp_path / "update.zip").exists()


def test_resolve_timeout_becomes_a_cause(tmp_path, monkeypatch):
    from core import extension_bridge

    def resolve(_progress, _cancel):
        raise extension_bridge.BridgeFailed("extension did not resolve in time")

    _, queue = _host_queue(tmp_path, monkeypatch, lambda url: pytest.fail("no probe"))
    job_id = _add_host(queue, 1, resolve)
    assert _until(queue, lambda: queue.get(job_id).state == "attention")
    job = queue.get(job_id)
    assert job.error_code == "check_timeout" and "timed out" in job.error_text


def test_paused_resumable_transfer_continues_from_its_partial_file(
    tmp_path, monkeypatch
):
    gate = threading.Event()
    opened = []
    data = b"PK\x03\x04" + bytes(range(256)) * 8
    manager, queue = _host_queue(
        tmp_path,
        monkeypatch,
        lambda url, **_: _resumable_info(data, gate, opened, hold=256),
    )
    job_id = _add_host(queue, 1)
    assert _until(queue, lambda: queue.get(job_id).bytes_done >= 256)
    assert queue.pause(job_id)
    gate.set()
    assert _until(queue, lambda: manager.snapshot(job_id)["outcome"] == "stopped")
    (part,) = tmp_path.glob("resume-*.part")
    kept = part.stat().st_size
    assert kept > 0 and queue.get(job_id).source["part_file"] == part.name
    assert queue.resume(job_id)
    assert _until(queue, lambda: queue.get(job_id).state == "ready")
    assert opened == [0, kept]
    assert (tmp_path / "game.zip").read_bytes() == data
    assert not list(tmp_path.glob("*.part"))


def test_host_worker_keeps_the_partial_file_for_the_same_mirror(tmp_path, monkeypatch):
    from core.downloads.queue import QueueJob
    from core.downloads.workers import HostWorker

    manager, _queue = _host_queue(tmp_path, monkeypatch, lambda url: None)
    worker = HostWorker(manager)
    part = tmp_path / "resume-abc.part"
    for keep in (True, False):
        part.write_bytes(b"PK\x03\x04")
        job = QueueJob(
            id=f"job{keep}",
            kind="host",
            game_id=1,
            state="attention",
            source={"host": "pixeldrain", "part_file": part.name},
        )
        worker.remove(job, keep_partial=keep)
        assert part.exists() is keep


def test_same_mirror_replacing_a_running_download_resumes_its_file(
    tmp_path, monkeypatch
):
    from core.downloads import transfer as transfer_module
    from core.downloads.workers import HostContext

    gate = threading.Event()
    opened = []
    data = b"PK\x03\x04" + bytes(range(256)) * 8
    probes = []

    def probe(url, **_):
        probes.append(url)
        if len(probes) > 1:
            # Let the replaced transfer release its partial file first.
            deadline = time.monotonic() + 5
            while transfer_module._active_parts and time.monotonic() < deadline:
                time.sleep(0.01)
        return _resumable_info(data, gate, opened, hold=256)

    _manager, queue = _host_queue(tmp_path, monkeypatch, probe)
    old = _add_host(queue, 1)
    assert _until(queue, lambda: queue.get(old).bytes_done >= 256)
    source = {
        "label": "Win",
        "section": "",
        "platform": "windows",
        "host": "pixeldrain",
        "part": "",
    }
    new = queue.add(
        "host", 1, "Game", "1.0", source, HostContext(_resolve_to()), replaces=old
    ).id
    gate.set()
    assert _until(queue, lambda: queue.get(new).state == "ready")
    assert len(opened) == 2 and opened[0] == 0 and opened[1] >= 256
    assert (tmp_path / "game.zip").read_bytes() == data
    assert not list(tmp_path.glob("*.part"))


def test_paused_mega_like_transfer_leaves_no_partial_file(tmp_path, monkeypatch):
    gate = threading.Event()
    opened = []
    data = b"PK\x03\x04" + bytes(range(256)) * 8

    def probe(url, **_):
        opened.append(0)
        return FileInfo(
            "game.zip",
            len(data),
            lambda: _Gated(data, gate, hold=256 if len(opened) == 1 else 0),
        )

    _, queue = _host_queue(tmp_path, monkeypatch, probe)
    job_id = _add_host(queue, 1, host="mega")
    assert _until(queue, lambda: queue.get(job_id).bytes_done >= 256)
    assert queue.get(job_id).can_continue is False
    assert queue.pause(job_id)
    gate.set()
    assert _until(queue, lambda: not list(tmp_path.glob("*.part")))
    assert queue.resume(job_id)
    assert _until(queue, lambda: queue.get(job_id).state == "ready")
    assert opened == [0, 0]


def test_can_continue_follows_the_host(tmp_path, monkeypatch):
    from core.downloads.queue import QueueJob
    from core.downloads.workers import HostWorker

    gate = threading.Event()
    data = b"PK\x03\x04" + b"a" * 100
    manager, queue = _host_queue(
        tmp_path, monkeypatch, lambda url, **_: _resumable_info(data, gate, [])
    )
    worker = HostWorker(manager)
    # Before the probe: by host.
    assert worker.snapshot(
        QueueJob(id="p", kind="host", game_id=1, source={"host": "pixeldrain"})
    ).can_continue
    assert not worker.snapshot(
        QueueJob(id="m", kind="host", game_id=1, source={"host": "mega"})
    ).can_continue
    # After the probe: from its FileInfo.
    job_id = _add_host(queue, 1)
    assert _until(queue, lambda: manager.snapshot(job_id)["part_name"] != "")
    assert queue.get(job_id).can_continue is True
    gate.set()


def test_network_failure_keeps_the_resumable_partial_for_the_retry(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("core.downloads.transfer.RESUME_RETRY_DELAY_SECONDS", 0)
    data = b"PK\x03\x04" + bytes(range(256)) * 10
    offsets = []
    attempts = []

    def probe(url, **_):
        attempts.append(1)
        fail = 700 if len(attempts) == 1 else None
        return _resumable(
            data, offsets, fail_after=fail, resume_fails_after=0 if fail else None
        )

    _, queue = _host_queue(tmp_path, monkeypatch, probe)
    job_id = _add_host(queue, 1)
    assert _until(queue, lambda: queue.get(job_id).state == "retry_wait")
    (part,) = tmp_path.glob("resume-*.part")
    assert part.stat().st_size == 700
    assert queue.retry(job_id)
    assert _until(queue, lambda: queue.get(job_id).state == "ready")
    assert offsets[-1] == 700


def test_cleanup_keeps_owned_partials_and_deletes_orphans(tmp_path, monkeypatch):
    import os

    from core.database import insert_download_job
    from core.downloads.queue import DownloadQueue

    manager, _ = _host_queue(tmp_path, monkeypatch, lambda url: pytest.fail("no probe"))
    owned = tmp_path / "resume-owned.part"
    orphan = tmp_path / "resume-orphan.part"
    job_part = tmp_path / "0123abcd.part"
    old_archive = tmp_path / "old.zip"
    for path in (owned, orphan, job_part, old_archive):
        path.write_bytes(b"x")
    os.utime(old_archive, (1, 1))
    os.utime(owned, (1, 1))
    insert_download_job(
        {
            "id": "keep",
            "kind": "host",
            "game_id": 1,
            "state": "paused",
            "source": json.dumps({"host": "pixeldrain", "part_file": owned.name}),
        }
    )
    from core.downloads.workers import HostWorker

    DownloadQueue([HostWorker(manager)], run_thread=False, free_space=lambda: 0).load()
    assert owned.exists()
    assert not orphan.exists() and not job_part.exists() and not old_archive.exists()


def test_cleanup_deletes_old_tarballs_and_keeps_recent_ones(tmp_path, monkeypatch):
    import os

    manager, _ = _host_queue(tmp_path, monkeypatch, lambda url: pytest.fail("no probe"))
    old = tmp_path / "Game-1.0-linux.tar.gz"
    recent = tmp_path / "Game-2.0-linux.tar.gz"
    other = tmp_path / "notes.gz"
    for path in (old, recent, other):
        path.write_bytes(b"x")
    os.utime(old, (1, 1))
    os.utime(other, (1, 1))
    manager.cleanup()
    assert not old.exists()
    assert recent.exists() and other.exists()


def test_failed_host_job_untouched_for_30_days_is_removed(tmp_path, monkeypatch):
    from core.database import insert_download_job, list_download_jobs
    from core.downloads.queue import DownloadQueue
    from core.downloads.workers import HostWorker

    manager, _ = _host_queue(tmp_path, monkeypatch, lambda url: pytest.fail("no probe"))
    part = tmp_path / "resume-old.part"
    part.write_bytes(b"x")
    old = time.time() - 31 * 86400
    insert_download_job(
        {
            "id": "old",
            "kind": "host",
            "game_id": 1,
            "state": "attention",
            "error_code": "removed",
            "updated_at": old,
            "created_at": old,
            "source": json.dumps({"host": "pixeldrain", "part_file": part.name}),
        }
    )
    queue = DownloadQueue([HostWorker(manager)], run_thread=False, free_space=lambda: 0)
    queue.load()
    assert queue.jobs() == [] and list_download_jobs() == []
    assert not part.exists()


def test_ready_job_whose_archive_was_cleaned_leaves_the_queue(tmp_path, monkeypatch):
    import os

    from core.database import insert_download_job
    from core.downloads.queue import DownloadQueue
    from core.downloads.workers import HostWorker

    manager, _ = _host_queue(tmp_path, monkeypatch, lambda url: pytest.fail("no probe"))
    fresh = tmp_path / "fresh.zip"
    stale = tmp_path / "stale.zip"
    for path in (fresh, stale):
        path.write_bytes(b"PK")
    os.utime(stale, (1, 1))
    for job_id, path in (("fresh", fresh), ("stale", stale)):
        insert_download_job(
            {
                "id": job_id,
                "kind": "host",
                "game_id": len(job_id),
                "state": "ready",
                "source": json.dumps({"host": "pixeldrain", "archive": path.name}),
            }
        )
    queue = DownloadQueue([HostWorker(manager)], run_thread=False, free_space=lambda: 0)
    queue.load()
    assert [job.id for job in queue.jobs()] == ["fresh"]
    queue.refresh()
    assert queue.get("fresh").details["archive_path"] == str(fresh)


def test_remove_deletes_the_partial_file_and_the_finished_archive(
    tmp_path, monkeypatch
):
    gate = threading.Event()
    data = b"PK\x03\x04" + bytes(range(256)) * 8
    opened = []
    manager, queue = _host_queue(
        tmp_path,
        monkeypatch,
        lambda url, **_: _resumable_info(data, gate, opened, hold=256),
    )
    job_id = _add_host(queue, 1)
    assert _until(queue, lambda: queue.get(job_id).bytes_done >= 256)
    assert queue.pause(job_id)
    gate.set()
    assert _until(queue, lambda: manager.snapshot(job_id)["outcome"] == "stopped")
    assert list(tmp_path.glob("resume-*.part"))
    assert queue.remove(job_id)
    assert not list(tmp_path.glob("*.part"))
    second = _add_host(queue, 2)
    assert _until(queue, lambda: queue.get(second).state == "ready")
    archive = tmp_path / "game.zip"
    assert archive.exists()
    assert queue.remove(second)
    assert not archive.exists()


def test_no_saved_row_or_log_line_holds_the_link(tmp_path, monkeypatch, capsys, caplog):
    import logging

    from core.database import list_download_jobs

    caplog.set_level(logging.DEBUG)
    masked = "https://f95zone.to/dl/SECRETMASKED/"
    ua = "Mozilla/5.0 SECRETUSERAGENT/1.0"
    data = b"PK\x03\x04" + b"a" * 100
    manager, queue = _host_queue(
        tmp_path,
        monkeypatch,
        lambda url, **_: FileInfo("game.zip", len(data), lambda: io.BytesIO(data)),
    )

    def resolve(progress, cancel):
        return {
            "ok": True,
            "url": PIXELDRAIN_URL,
            "share_url": masked,
            "user_agent": ua,
        }

    job_id = _add_host(queue, 1, resolve)
    assert _until(queue, lambda: queue.get(job_id).state == "ready")
    snapshot = json.dumps(manager.snapshot(job_id))
    source = json.dumps(queue.get(job_id).source)
    rows = json.dumps(list_download_jobs())
    status = json.dumps(queue.status())
    captured = capsys.readouterr()
    text = snapshot + source + rows + status + captured.out + captured.err + caplog.text
    for secret in (
        "SECRETFILEID",
        "SECRETMASKED",
        "SECRETUSERAGENT",
        "pixeldrain.com/u",
        "f95zone.to/dl",
    ):
        assert secret not in text


@pytest.mark.parametrize(
    ("raw_ua", "expected"),
    [
        (
            "Mozilla/5.0 (X11; Linux x86_64) Browser/1.0",
            "Mozilla/5.0 (X11; Linux x86_64) Browser/1.0",
        ),
        (None, ""),
        ("Mozilla/5.0\r\nX-Injected: 1", ""),
        ("A" * 600, ""),
        ("Mozilla/5.0\u00e9", ""),
        ("", ""),
        ("A" * 512, "A" * 512),
    ],
)
def test_manager_validates_and_forwards_resolved_user_agent(
    tmp_path, monkeypatch, raw_ua, expected
):
    seen_ua: list[str] = []
    data = b"PK\x03\x04" + b"a" * 20

    def probe(url, *, user_agent=""):
        seen_ua.append(user_agent)
        return FileInfo("game.zip", len(data), lambda: io.BytesIO(data))

    _, queue = _host_queue(tmp_path, monkeypatch, probe)

    def resolve(_progress, _cancel):
        result = {"ok": True, "url": PIXELDRAIN_URL}
        if raw_ua is not None:
            result["user_agent"] = raw_ua
        return result

    job_id = _add_host(queue, 1, resolve)
    assert _until(queue, lambda: queue.get(job_id).state == "ready")
    assert seen_ua == [expected]


def test_manager_clears_and_replaces_user_agent_after_drop_address(
    tmp_path, monkeypatch
):
    seen_ua: list[str] = []
    data = b"PK\x03\x04" + b"a" * 20

    def probe(url, *, user_agent=""):
        seen_ua.append(user_agent)
        if len(seen_ua) == 1:
            raise DownloadError("blocked by a challenge")
        return FileInfo("game.zip", len(data), lambda: io.BytesIO(data))

    def relink(_job):
        return lambda _progress, _cancel: {
            "ok": True,
            "url": PIXELDRAIN_URL,
            "user_agent": "SecondBrowser/2.0",
        }

    manager, queue = _host_queue(tmp_path, monkeypatch, probe, relink=relink)
    job_id = _add_host(
        queue,
        1,
        lambda _p, _c: {
            "ok": True,
            "url": PIXELDRAIN_URL,
            "user_agent": "FirstBrowser/1.0",
        },
    )
    assert _until(queue, lambda: queue.get(job_id).state == "attention")
    assert seen_ua == ["FirstBrowser/1.0"]

    # Explicit drop_address clears the stored User-Agent along with the address.
    manager.drop_address(job_id)
    assert manager._jobs[job_id].user_agent == ""

    # Retrying after a challenge relinks and resolves with the new User-Agent.
    assert queue.retry(job_id)
    assert _until(queue, lambda: queue.get(job_id).state == "ready")
    assert seen_ua == ["FirstBrowser/1.0", "SecondBrowser/2.0"]
