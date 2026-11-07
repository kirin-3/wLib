# SPDX-License-Identifier: GPL-3.0-or-later
"""MEGA public file links, streaming decryption and integrity verification."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import struct
import time
from threading import Event
from http.client import HTTPResponse
from typing import BinaryIO, cast, final
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request

from core.downloads import credentials
from core.downloads.network import open_host

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from core.downloads.transfer import DownloadError, FileInfo


def urlopen(request: Request, timeout: int = 30) -> object:
    return open_host(request, ("mega.co.nz", "mega.nz"), timeout)


def _decode(value: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))
    except (ValueError, UnicodeError) as exc:
        raise DownloadError('file invalid') from exc


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b'=').decode('ascii')


def parse_link(url: str) -> tuple[str, bytes]:
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.hostname not in ('mega.nz', 'mega.co.nz', 'www.mega.nz', 'www.mega.co.nz'):
        raise DownloadError('unsupported MEGA link')
    legacy = re.fullmatch(r'!([\w-]+)!([\w-]*)', parsed.fragment)
    modern = re.fullmatch(r'/file/([\w-]+)/?', parsed.path)
    if legacy:
        file_id, key = legacy.group(1), legacy.group(2)
    elif modern:
        file_id, key = modern.group(1), parsed.fragment
    else:
        raise DownloadError('unsupported MEGA link')
    if not key:
        raise DownloadError('MEGA link is missing its key')
    raw = _decode(key)
    if len(raw) != 32:
        raise DownloadError('file invalid')
    return file_id, raw


def matches(url: str) -> bool:
    try:
        _ = parse_link(url)
        return True
    except (DownloadError, ValueError):
        return False


def classify_error(status: int, body: str = '') -> str:
    if status in (404, 410) or body.strip(' []') in ('-9', '-16'):
        return 'link expired or removed'
    # 509: the file transfer itself hit the bandwidth quota.
    if status in (429, 509) or body.strip(' []') in ('-4', '-17', '-18'):
        return 'transfer quota or rate limit reached'
    return 'network error'


def solve_hashcash(challenge: str, budget: float = 30, cancel: Event | None = None) -> str:
    """Solve the bounded proof of work used by MEGA's SDK."""
    try:
        version, easy, timestamp, token_text = challenge.split(':')
        easiness = int(easy)
        _ = int(timestamp)
        token = _decode(token_text)
        if version != '1' or not 0 <= easiness <= 255 or len(token) != 48:
            raise ValueError
    except (ValueError, DownloadError) as exc:
        raise DownloadError('MEGA refused the request') from exc
    threshold = ((((easiness & 63) << 1) + 1) << ((easiness >> 6) * 7 + 3)) & 0xffffffff
    # The SDK hashes a 4-byte nonce followed by the token repeated 262144 times.
    repeated = token * 262144
    deadline = time.monotonic() + budget
    nonce = 0
    while time.monotonic() < deadline and nonce <= 0xffffffff:
        if cancel is not None and cancel.is_set():
            raise DownloadError('cancelled')
        prefix = nonce.to_bytes(4, 'big')
        hasher = hashlib.sha256(prefix)
        hasher.update(repeated)
        digest = hasher.digest()
        if int.from_bytes(digest[:4], 'big') <= threshold:
            return f'1:{token_text}:{_encode(prefix)}'
        nonce += 1
    raise DownloadError('MEGA refused the request; try another mirror')


def _key_parts(raw: bytes) -> tuple[bytes, bytes, bytes]:
    words = struct.unpack('>8I', raw)
    key = struct.pack('>4I', *(words[i] ^ words[i + 4] for i in range(4)))
    return key, raw[16:24], raw[24:32]


@final
class MegaDecryptor:
    """AES-CTR plus MEGA's per-chunk CBC-MAC and condensed meta-MAC."""
    def __init__(self, raw_key: bytes) -> None:
        self.key, self.nonce, self.expected = _key_parts(raw_key)
        self.cipher = Cipher(algorithms.AES(self.key), modes.CTR(self.nonce + b'\0' * 8)).decryptor()
        self.buffer = bytearray()
        self.chunk_size = 128 * 1024
        self.macs: list[bytes] = []

    def _chunk(self, data: bytes) -> None:
        padded = data + b'\0' * (-len(data) % 16)
        cipher = Cipher(algorithms.AES(self.key), modes.CBC(self.nonce * 2)).encryptor()
        encrypted = cipher.update(padded) + cipher.finalize()
        self.macs.append(encrypted[-16:])

    def update(self, data: bytes) -> bytes:
        plaintext = self.cipher.update(data)
        self.buffer.extend(plaintext)
        while len(self.buffer) >= self.chunk_size:
            chunk = bytes(self.buffer[:self.chunk_size])
            del self.buffer[:self.chunk_size]
            self._chunk(chunk)
            self.chunk_size = min(self.chunk_size + 128 * 1024, 1024 * 1024)
        return plaintext

    def finish(self) -> None:
        _ = self.cipher.finalize()
        if self.buffer:
            self._chunk(bytes(self.buffer))
            self.buffer.clear()
        cipher = Cipher(algorithms.AES(self.key), modes.CBC(b'\0' * 16)).encryptor()
        condensed = cipher.update(b''.join(self.macs)) + cipher.finalize() if self.macs else b'\0' * 16
        words = struct.unpack('>4I', condensed[-16:])
        actual = struct.pack('>2I', words[0] ^ words[1], words[2] ^ words[3])
        if not hmac.compare_digest(actual, self.expected):
            raise DownloadError('file invalid: MEGA checksum mismatch')


def _request(command: dict[str, object], sid: str = '', cancel: Event | None = None) -> object:
    """Send one API command, solving MEGA's hashcash challenge once if asked."""
    payload = json.dumps([command]).encode()
    headers = {'Content-Type':'application/json'}
    url = 'https://g.api.mega.co.nz/cs?id=1' + (f'&sid={sid}' if sid else '')
    for attempt in range(2):
        try:
            request = Request(url, data=payload, headers=headers)
            with cast(HTTPResponse, urlopen(request, timeout=30)) as response:
                result = cast(object, json.load(response))
            if isinstance(result, list):
                items = cast(list[object], result)
                return items[0] if len(items) == 1 else items
            return result
        except HTTPError as exc:
            challenge = exc.headers.get('X-Hashcash')
            if attempt == 0 and exc.code == 402 and challenge:
                headers['X-Hashcash'] = solve_hashcash(challenge, cancel=cancel)
                continue
            if exc.code == 402:
                raise DownloadError('MEGA refused the request; try another mirror') from exc
            raise DownloadError(classify_error(exc.code)) from exc
        except (URLError, OSError, ValueError) as exc:
            raise DownloadError('network error') from exc
    raise DownloadError('MEGA refused the request')


def _api(file_id: str, cancel: Event | None = None, sid: str | None = None) -> dict[str, object]:
    # ssl=2 asks for an https download URL; MEGA hands out http ones otherwise.
    # A session id makes MEGA count the transfer against the signed-in account.
    session = credentials.get(credentials.MEGA_SESSION) if sid is None else sid
    result = _request({'a':'g', 'g':1, 'ssl':2, 'p':file_id}, session, cancel)
    if result == ESID and session:
        raise DownloadError('MEGA session expired; sign in again in Settings')
    if isinstance(result, int):
        raise DownloadError(classify_error(0, str(result)))
    if not isinstance(result, dict):
        raise DownloadError('file invalid')
    fields = cast(dict[str, object], result)
    # A taken-down or blocked file answers with an error code in "e".
    if isinstance(fields.get('e'), int):
        raise DownloadError(classify_error(0, str(fields['e'])))
    return fields


ESID = -15
EMFAREQUIRED = -26


def _aes_ecb(key: bytes, data: bytes, *, decrypt: bool = False) -> bytes:
    cipher = Cipher(algorithms.AES(key), modes.ECB())  # MEGA wraps keys with ECB.
    context = cipher.decryptor() if decrypt else cipher.encryptor()
    return context.update(data) + context.finalize()


def _legacy_password_key(password: bytes) -> bytes:
    """MEGA's v1 key derivation: 65536 AES rounds keyed by the password."""
    padded = password + b'\0' * (-len(password) % 16)
    rounds = [Cipher(algorithms.AES(padded[i:i + 16]), modes.ECB()).encryptor() for i in range(0, len(padded), 16)]
    key = bytes.fromhex('93c467e37db0c7a4d1be3f810152cb56')
    for _ in range(65536):
        for cipher in rounds:
            key = cipher.update(key)
    return key


def _legacy_user_hash(email: str, key: bytes) -> str:
    data = email.encode()
    data += b'\0' * (-len(data) % 4)
    folded = [0, 0, 0, 0]
    for index, word in enumerate(cast(tuple[int, ...], struct.unpack(f'>{len(data) // 4}I', data))):
        folded[index % 4] ^= word
    block = struct.pack('>4I', *folded)
    cipher = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    for _ in range(0x4000):
        block = cipher.update(block)
    return _encode(block[:4] + block[8:12])


def _mpi(data: bytes, offset: int) -> tuple[int, int]:
    if offset + 2 > len(data):
        raise ValueError('truncated MPI')
    length = (int.from_bytes(data[offset:offset + 2], 'big') + 7) // 8
    end = offset + 2 + length
    if end > len(data):
        raise ValueError('truncated MPI')
    return int.from_bytes(data[offset + 2:end], 'big'), end


def _session_id(fields: dict[str, object], password_key: bytes) -> str:
    encrypted_master = fields.get('k')
    if not isinstance(encrypted_master, str):
        raise DownloadError('MEGA returned an unexpected login response')
    try:
        master = _aes_ecb(password_key, _decode(encrypted_master), decrypt=True)[:16]
        temporary = fields.get('tsid')
        if isinstance(temporary, str):
            raw = _decode(temporary)
            if len(raw) < 32 or not hmac.compare_digest(_aes_ecb(master, raw[:16]), raw[-16:]):
                raise ValueError('bad tsid')
            return temporary
        csid, privk = fields.get('csid'), fields.get('privk')
        if not isinstance(csid, str) or not isinstance(privk, str):
            raise ValueError('missing session')
        private = _aes_ecb(master, _decode(privk), decrypt=True)
        p, offset = _mpi(private, 0)
        q, offset = _mpi(private, offset)
        d, _ = _mpi(private, offset)
        encrypted_sid, _ = _mpi(_decode(csid), 0)
        if p < 2 or q < 2:
            raise ValueError('bad key')
        plain = pow(encrypted_sid, d, p * q)
        return _encode(plain.to_bytes((plain.bit_length() + 7) // 8, 'big')[:43])
    except (ValueError, DownloadError) as exc:
        # A wrong password still yields a "valid" response; decryption then fails.
        raise DownloadError('Wrong MEGA email or password') from exc


def login(email: str, password: str, code: str = '', cancel: Event | None = None) -> str:
    """Sign in and return a session id. The password is never stored."""
    email = email.strip().lower()
    if not re.fullmatch(r'[^@\s]+@[^@\s]+', email) or not password:
        raise DownloadError('Enter your MEGA email and password')
    code = code.strip()
    if code and not re.fullmatch(r'\d{6}', code):
        raise DownloadError('Enter the 6-digit code from your authenticator app')
    prelogin = _request({'a':'us0', 'user':email}, cancel=cancel)
    if not isinstance(prelogin, dict):
        raise DownloadError(_login_error(prelogin))
    info = cast(dict[str, object], prelogin)
    if info.get('v') == 2 and isinstance(info.get('s'), str):
        derived = hashlib.pbkdf2_hmac('sha512', password.encode(), _decode(cast(str, info['s'])), 100000, 32)
        password_key, user_hash = derived[:16], _encode(derived[16:])
    else:
        password_key = _legacy_password_key(password.encode())
        user_hash = _legacy_user_hash(email, password_key)
    command: dict[str, object] = {'a':'us', 'user':email, 'uh':user_hash}
    if code:
        command['mfa'] = code
    response = _request(command, cancel=cancel)
    if not isinstance(response, dict):
        raise DownloadError(_login_error(response))
    return _session_id(cast(dict[str, object], response), password_key)


def _login_error(result: object) -> str:
    if result == EMFAREQUIRED:
        return 'MEGA requires your two-factor code'
    if result in (-9, -2):
        return 'Wrong MEGA email or password'
    if result == -16:
        return 'This MEGA account is blocked'
    if result in (-3, -4, -6):
        return 'MEGA is limiting sign-in attempts; try again later'
    return 'MEGA sign-in failed'


def logout(sid: str) -> None:
    """End the session on MEGA's side; failures are ignored."""
    if not sid:
        return
    try:
        _ = _request({'a':'sml'}, sid)
    except DownloadError:
        pass


def probe(url: str, cancel: Event | None = None) -> FileInfo:
    file_id, raw_key = parse_link(url)
    result = _api(file_id, cancel)
    encrypted = result.get('at')
    file_url = result.get('g')
    size = result.get('s')
    if not isinstance(encrypted, str) or not isinstance(file_url, str) or not isinstance(size, int) or size < 0:
        raise DownloadError('file invalid')
    parsed = urlsplit(file_url)
    if parsed.scheme != 'https' or not parsed.hostname or not (parsed.hostname.endswith('.mega.co.nz') or parsed.hostname.endswith('.mega.nz')):
        raise DownloadError('invalid MEGA download URL')
    key, _, _ = _key_parts(raw_key)
    try:
        cipher = Cipher(algorithms.AES(key), modes.CBC(b'\0' * 16)).decryptor()
        attributes = cipher.update(_decode(encrypted)) + cipher.finalize()
        if not attributes.startswith(b'MEGA'):
            raise ValueError('invalid attributes')
        decoded = cast(object, json.loads(attributes[4:].rstrip(b'\0')))
        if not isinstance(decoded, dict):
            raise ValueError('invalid attributes')
        name = cast(dict[str, object], decoded).get('n')
        if not isinstance(name, str):
            raise ValueError('missing name')
    except (ValueError, UnicodeError) as exc:
        raise DownloadError('file invalid') from exc
    def stream() -> BinaryIO:
        try:
            return cast(BinaryIO, urlopen(Request(file_url), timeout=30))
        except HTTPError as exc:
            raise DownloadError(classify_error(exc.code)) from exc
    return FileInfo(name, size, stream, MegaDecryptor(raw_key))
