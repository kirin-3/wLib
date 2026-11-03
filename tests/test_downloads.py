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
    data = b'PK\x03\x04' + b'a' * 20
    path = transfer(FileInfo('../game.zip', len(data), lambda: io.BytesIO(data)), 'job', Event(), lambda done, total: progress.append((done, total)), directory=tmp_path)
    assert path.read_bytes() == data and path.name == 'game.zip'
    assert progress == [(len(data), len(data))]
    flag = Event()
    def cancel(done, total):
        flag.set()
    with pytest.raises(DownloadError, match='cancelled'):
        transfer(FileInfo('game.zip', len(data), lambda: io.BytesIO(data)), 'other', flag, cancel, directory=tmp_path)
    assert not (tmp_path / 'other.part').exists()
    with pytest.raises(DownloadError, match='file invalid'):
        transfer(FileInfo('fake.zip', None, lambda: io.BytesIO(b'<html>')), 'html', Event(), lambda *_: None, directory=tmp_path)
    assert not (tmp_path / 'html.part').exists()
    monkeypatch.setattr('core.downloads.transfer.shutil.disk_usage', lambda _: type('Space', (), {'free': 0})())
    with pytest.raises(DownloadError, match='Not enough disk space'):
        transfer(FileInfo('large.zip', 1, lambda: pytest.fail('must not open stream')), 'space', Event(), lambda *_: None, directory=tmp_path)


def test_pixeldrain_probe_and_error_mapping(monkeypatch):
    urls = []
    def urlopen(request, **kwargs):
        urls.append(request.full_url)
        return io.BytesIO(json.dumps({'name':'game.zip','size':123}).encode())
    monkeypatch.setattr(pixeldrain, 'urlopen', urlopen)
    info = pixeldrain.probe('https://pixeldrain.com/u/abc')
    assert info.name == 'game.zip' and info.size == 123
    info.stream_factory().close()
    assert urls == ['https://pixeldrain.com/api/file/abc/info','https://pixeldrain.com/api/file/abc?download']
    assert not pixeldrain.matches('https://pixeldrain.com/l/abc')
    assert pixeldrain.classify_error(404) == 'link expired or removed'
    assert 'quota' in pixeldrain.classify_error(429)


def test_buzzheavier_referer_and_challenge(monkeypatch):
    class Response(io.BytesIO):
        def __init__(self):
            super().__init__(b'PK\x03\x04')
            self.headers = Message()
            self.headers['Content-Disposition'] = 'attachment; filename="game.zip"'
            self.headers['Content-Length'] = '4'
    requests = []
    def urlopen(request, **kwargs):
        requests.append(request)
        return Response()
    monkeypatch.setattr(buzzheavier, 'urlopen', urlopen)
    info = buzzheavier.probe('https://buzzheavier.com/download', 'https://buzzheavier.com/share')
    assert info.name == 'game.zip'
    assert requests[0].get_header('Referer') == 'https://buzzheavier.com/share'
    info.stream_factory().close()
    headers = Message()
    headers['cf-mitigated'] = 'challenge'
    def fail(request, **kwargs):
        raise HTTPError(request.full_url, 403, 'challenge', headers, None)
    monkeypatch.setattr(buzzheavier, 'urlopen', fail)
    with pytest.raises(DownloadError, match='blocked by a challenge'):
        buzzheavier.probe('https://buzzheavier.com/download', 'https://buzzheavier.com/share')


def test_mega_decrypts_and_rejects_corruption(tmp_path):
    import base64
    import struct
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from core.downloads.hosts import mega

    plain = b'PK\x03\x04' + bytes(range(256)) * 1600
    key = bytes(range(16))
    nonce = bytes(range(8))
    macs = []
    offset = 0
    chunk_size = 128 * 1024
    while offset < len(plain):
        chunk = plain[offset:offset + chunk_size]
        offset += len(chunk)
        chunk_size = min(chunk_size + 128 * 1024, 1024 * 1024)
        cipher = Cipher(algorithms.AES(key), modes.CBC(nonce * 2)).encryptor()
        macs.append((cipher.update(chunk + b'\0' * (-len(chunk) % 16)) + cipher.finalize())[-16:])
    cipher = Cipher(algorithms.AES(key), modes.CBC(b'\0' * 16)).encryptor()
    digest = (cipher.update(b''.join(macs)) + cipher.finalize())[-16:]
    a, b, c, d = struct.unpack('>4I', digest)
    meta = struct.pack('>2I', a ^ b, c ^ d)
    suffix = nonce + meta
    obfuscated = bytes(a ^ b for a, b in zip(key, suffix, strict=True)) + suffix
    encoded = base64.urlsafe_b64encode(obfuscated).rstrip(b'=').decode()
    assert mega.parse_link(f'https://mega.nz/file/abc#{encoded}')[1] == obfuscated
    assert mega.parse_link(f'https://mega.nz/#!abc!{encoded}')[1] == obfuscated
    with pytest.raises(DownloadError, match='missing its key'):
        mega.parse_link('https://mega.nz/file/abc')
    cipher = Cipher(algorithms.AES(key), modes.CTR(nonce + b'\0' * 8)).encryptor()
    encrypted = cipher.update(plain) + cipher.finalize()
    info = FileInfo('mega.zip', len(plain), lambda: io.BytesIO(encrypted), mega.MegaDecryptor(obfuscated))
    assert transfer(info, 'mega', Event(), lambda *_: None, directory=tmp_path).read_bytes() == plain
    corrupted = encrypted[:-1] + bytes([encrypted[-1] ^ 1])
    info = FileInfo('bad.zip', len(plain), lambda: io.BytesIO(corrupted), mega.MegaDecryptor(obfuscated))
    with pytest.raises(DownloadError, match='checksum'):
        transfer(info, 'bad', Event(), lambda *_: None, directory=tmp_path)
    assert not (tmp_path / 'bad.part').exists()


def test_mega_hashcash_matches_sdk_threshold():
    import base64
    import hashlib
    from core.downloads.hosts import mega

    token = bytes(range(48))
    token_text = base64.urlsafe_b64encode(token).rstrip(b'=').decode()
    solution = mega.solve_hashcash(f'1:255:123:{token_text}', budget=5)
    version, result_token, encoded_nonce = solution.split(':')
    nonce = base64.urlsafe_b64decode(encoded_nonce + '=' * (-len(encoded_nonce) % 4))
    threshold = ((((255 & 63) << 1) + 1) << ((255 >> 6) * 7 + 3)) & 0xffffffff
    assert version == '1' and result_token == token_text
    assert int.from_bytes(hashlib.sha256(nonce + token * 262144).digest()[:4], 'big') <= threshold


def test_manager_limits_transfers_and_cleans_staging(tmp_path, monkeypatch):
    import os
    import threading
    import time
    from core.downloads import manager as module
    from core.downloads.manager import DownloadManager

    entered = []
    gate = threading.Event()
    lock = threading.Lock()
    class Stream(io.BytesIO):
        def read(self, size=-1):
            with lock:
                if self not in entered:
                    entered.append(self)
            assert gate.wait(3)
            return super().read(size)
    monkeypatch.setattr(module, 'staging_dir', lambda: tmp_path)
    monkeypatch.setattr('core.downloads.transfer.staging_dir', lambda: tmp_path)
    monkeypatch.setattr(module.pixeldrain, 'probe', lambda url: FileInfo('game.zip', 4, lambda: Stream(b'PK\x03\x04')))
    manager = DownloadManager()
    def resolve(progress, cancel):
        return {'ok': True, 'url': 'https://pixeldrain.com/u/file'}
    ids = [manager.start(i, resolve) for i in (1, 2, 3)]
    deadline = time.monotonic() + 2
    while len(entered) < 2 and time.monotonic() < deadline:
        time.sleep(.01)
    assert len(entered) == 2
    with pytest.raises(DownloadError, match='already running'):
        manager.start(1, resolve)
    gate.set()
    deadline = time.monotonic() + 3
    while any(job['stage'] != 'done' for job in manager.status()) and time.monotonic() < deadline:
        time.sleep(.01)
    assert {job['stage'] for job in manager.status()} == {'done'}
    assert len(ids) == 3
    old = tmp_path / 'old.zip'
    old.write_bytes(b'old')
    partial = tmp_path / 'partial.part'
    partial.write_bytes(b'partial')
    keep = tmp_path / 'keep.zip'
    keep.write_bytes(b'keep')
    for path in (old, keep):
        os.utime(path, (1, 1))
    manager.mark_open(str(keep))
    manager.cleanup(now=8 * 86400)
    assert not old.exists() and not partial.exists() and keep.exists()


def test_manager_reports_resolve_progress_and_completion(tmp_path, monkeypatch):
    import threading
    import time
    from core.downloads import manager as module
    from core.downloads.manager import DownloadManager

    waiting = threading.Event()
    resume = threading.Event()
    def resolve(progress, cancel):
        progress('needs_user')
        waiting.set()
        assert resume.wait(2)
        return {'ok': True, 'url': 'https://pixeldrain.com/u/file'}
    monkeypatch.setattr('core.downloads.transfer.staging_dir', lambda: tmp_path)
    monkeypatch.setattr(module.pixeldrain, 'probe', lambda url: FileInfo('update.zip', 4, lambda: io.BytesIO(b'PK\x03\x04')))
    manager = DownloadManager()
    manager.start(1, resolve)
    assert waiting.wait(1)
    assert manager.status()[0]['stage'] == 'needs_user'
    resume.set()
    deadline = time.monotonic() + 2
    while manager.status()[0]['stage'] != 'done' and time.monotonic() < deadline:
        time.sleep(.005)
    result = manager.status()[0]
    assert result['stage'] == 'done' and result['bytes'] == 4
    assert (tmp_path / 'update.zip').exists()


def test_host_redirects_reject_unrelated_servers():
    from core.downloads.network import allowed

    assert allowed('https://cdn.pixeldrain.com/file', ('pixeldrain.com',))
    assert not allowed('https://pixeldrain.com.evil.example/file', ('pixeldrain.com',))
    assert not allowed('http://pixeldrain.com/file', ('pixeldrain.com',))
    assert not allowed('https://pixeldrain.com:8443/file', ('pixeldrain.com',))


def test_mega_requests_https_urls_and_maps_file_errors(monkeypatch):
    from core.downloads.hosts import mega

    requests = []

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    def fake_open(request, timeout=30):
        requests.append(json.loads(request.data))
        return Response(json.dumps([{'e': -16}]).encode())

    monkeypatch.setattr(mega, 'urlopen', fake_open)
    with pytest.raises(DownloadError, match='expired or removed'):
        mega._api('abc')
    assert requests[0][0]['ssl'] == 2


def test_pixeldrain_aliases_and_hotlink_limit():
    assert pixeldrain.matches('https://pixeldrain.net/u/abc')
    assert pixeldrain.classify_error(403) == 'transfer quota or rate limit reached'


def test_manager_turns_resolve_timeout_into_a_cause(tmp_path, monkeypatch):
    import time as clock
    from core import extension_bridge
    from core.downloads.manager import DownloadManager

    def resolve(_progress, _cancel):
        raise extension_bridge.BridgeFailed('extension did not resolve in time')

    manager = DownloadManager()
    job_id = manager.start(1, resolve)
    deadline = clock.monotonic() + 2
    while manager.status()[0]['stage'] == 'resolving' and clock.monotonic() < deadline:
        clock.sleep(0.01)
    job = manager.status()[0]
    assert job['job_id'] == job_id and job['stage'] == 'failed'
    assert 'timed out' in str(job['error'])
