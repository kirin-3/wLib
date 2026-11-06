# SPDX-License-Identifier: GPL-3.0-or-later
import base64
import hashlib
import io
import json
import os
from email.message import Message
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from core.api import Api
from core.downloads import accounts, credentials
from core.downloads.hosts import mega, pixeldrain
from core.downloads.transfer import DownloadError


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def _json(value):
    return Response(json.dumps(value).encode())


@pytest.fixture
def store(monkeypatch):
    values: dict[str, str] = {}
    monkeypatch.setattr(credentials, 'get', lambda key: values.get(key, ''))
    monkeypatch.setattr(credentials, 'put', values.update)
    return values


def test_pixeldrain_sends_the_api_key_as_basic_auth(monkeypatch, store):
    seen = []

    def urlopen(request, **_):
        seen.append(request.get_header('Authorization'))
        return _json({'name': 'game.zip', 'size': 4})

    monkeypatch.setattr(pixeldrain, 'urlopen', urlopen)
    store[credentials.PIXELDRAIN_API_KEY] = 'abcd-1234'
    info = pixeldrain.probe('https://pixeldrain.com/u/abc')
    info.stream_factory().close()
    expected = 'Basic ' + base64.b64encode(b':abcd-1234').decode()
    assert seen == [expected, expected]
    seen.clear()
    pixeldrain.probe('https://pixeldrain.com/u/abc', api_key='').stream_factory().close()
    assert seen == [None, None]
    assert 'API key' in pixeldrain.classify_error(401)


def test_pixeldrain_connect_verifies_and_hides_the_key(monkeypatch, store):
    def urlopen(request, **_):
        assert request.full_url == 'https://pixeldrain.com/api/user'
        if request.get_header('Authorization') != 'Basic ' + base64.b64encode(b':good-key-1').decode():
            raise HTTPError(request.full_url, 401, 'unauthorized', Message(), None)
        return _json({'username': 'kirin', 'subscription': {'name': 'Pro'}})

    monkeypatch.setattr(pixeldrain, 'urlopen', urlopen)
    api = Api.__new__(Api)
    assert api.connect_pixeldrain_account(' good-key-1 ') == {'success': True, 'username': 'kirin'}
    assert store[credentials.PIXELDRAIN_API_KEY] == 'good-key-1'
    status = api.get_download_accounts()
    assert status['pixeldrain'] == {'connected': True, 'username': 'kirin'}
    assert 'good-key-1' not in json.dumps(status)
    failed = api.connect_pixeldrain_account('wrong-key-1')
    assert not failed['success'] and 'API key' in failed['error']
    assert store[credentials.PIXELDRAIN_API_KEY] == 'good-key-1'
    assert not api.connect_pixeldrain_account('bad key')['success']
    assert api.disconnect_pixeldrain_account()['success']
    assert accounts.status()['pixeldrain'] == {'connected': False, 'username': ''}


def _ecb(key, data, decrypt=False):
    cipher = Cipher(algorithms.AES(key), modes.ECB())
    context = cipher.decryptor() if decrypt else cipher.encryptor()
    return context.update(data) + context.finalize()


def _mpi(value):
    raw = value.to_bytes((value.bit_length() + 7) // 8, 'big')
    return value.bit_length().to_bytes(2, 'big') + raw


def _b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()


def _fake_mega(password, *, version=2, mfa=False):
    """A minimal MEGA login server that issues an RSA-encrypted session id."""
    salt = os.urandom(32)
    master = os.urandom(16)
    if version == 2:
        derived = hashlib.pbkdf2_hmac('sha512', password.encode(), salt, 100000, 32)
        password_key, user_hash = derived[:16], _b64(derived[16:])
    else:
        password_key = mega._legacy_password_key(password.encode())
        user_hash = mega._legacy_user_hash('user@example.com', password_key)
    key = rsa.generate_private_key(public_exponent=65537, key_size=1024).private_numbers()
    private = _mpi(key.p) + _mpi(key.q) + _mpi(key.d) + _mpi(key.iqmp)
    private += b'\0' * (-len(private) % 16)
    sid_plain = b'\x01' + os.urandom(63)
    n = key.p * key.q
    csid = pow(int.from_bytes(sid_plain, 'big'), 65537, n)
    calls = []

    def urlopen(request, **_):
        command = json.loads(request.data)[0]
        calls.append((command, parse_qs(urlsplit(request.full_url).query)))
        if command['a'] == 'us0':
            return _json([{'v': version, 's': _b64(salt)}] if version == 2 else [{'v': 1}])
        if command['a'] == 'us':
            if command['uh'] != user_hash:
                return _json([-9])
            if mfa and command.get('mfa') != '123456':
                return _json([-26])
            return _json([{
                'k': _b64(_ecb(password_key, master)),
                'privk': _b64(_ecb(master, private)),
                'csid': _b64(_mpi(csid)),
            }])
        if command['a'] == 'sml':
            return _json([0])
        raise AssertionError(command)

    return urlopen, _b64(sid_plain[:43]), calls


@pytest.mark.parametrize('version', [1, 2])
def test_mega_login_decrypts_the_session_id(monkeypatch, store, version):
    urlopen, sid, calls = _fake_mega('hunter2', version=version)
    monkeypatch.setattr(mega, 'urlopen', urlopen)
    api = Api.__new__(Api)
    assert api.connect_mega_account('User@Example.com ', 'hunter2') == {'success': True, 'email': 'user@example.com'}
    assert store == {credentials.MEGA_SESSION: sid, credentials.MEGA_EMAIL: 'user@example.com'}
    assert 'hunter2' not in json.dumps(store)
    assert calls[1][0]['user'] == 'user@example.com'
    wrong = api.connect_mega_account('user@example.com', 'nope')
    assert wrong == {'success': False, 'error': 'Wrong MEGA email or password', 'needs_code': False}
    assert store[credentials.MEGA_SESSION] == sid
    assert api.disconnect_mega_account()['success']
    assert calls[-1][0] == {'a': 'sml'} and calls[-1][1]['sid'] == [sid]
    assert store[credentials.MEGA_SESSION] == ''


def test_mega_login_asks_for_the_two_factor_code(monkeypatch, store):
    urlopen, sid, _ = _fake_mega('pw', mfa=True)
    monkeypatch.setattr(mega, 'urlopen', urlopen)
    api = Api.__new__(Api)
    first = api.connect_mega_account('user@example.com', 'pw')
    assert first['needs_code'] is True and not first['success']
    assert not api.connect_mega_account('user@example.com', 'pw', '12ab')['success']
    assert api.connect_mega_account('user@example.com', 'pw', '123456')['success']
    assert store[credentials.MEGA_SESSION] == sid


def test_mega_downloads_use_the_stored_session(monkeypatch, store):
    urls = []
    answers = [[{'s': 1, 'at': 'x', 'g': 'https://gfs.mega.co.nz/dl'}], [-15]]

    def urlopen(request, **_):
        urls.append(parse_qs(urlsplit(request.full_url).query).get('sid'))
        return _json(answers.pop(0))

    monkeypatch.setattr(mega, 'urlopen', urlopen)
    store[credentials.MEGA_SESSION] = 'session-1'
    assert mega._api('abc')['s'] == 1
    with pytest.raises(DownloadError, match='session expired'):
        mega._api('abc')
    assert urls == [['session-1'], ['session-1']]
    answers.append([{'s': 1}])
    store[credentials.MEGA_SESSION] = ''
    mega._api('abc')
    assert urls[-1] is None
