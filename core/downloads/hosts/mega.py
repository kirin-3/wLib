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
    if status == 429 or body.strip(' []') in ('-4', '-17', '-18'):
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


def _api(file_id: str, cancel: Event | None = None) -> dict[str, object]:
    # ssl=2 asks for an https download URL; MEGA hands out http ones otherwise.
    payload = json.dumps([{'a':'g', 'g':1, 'ssl':2, 'p':file_id}]).encode()
    headers = {'Content-Type':'application/json'}
    for attempt in range(2):
        try:
            request = Request('https://g.api.mega.co.nz/cs?id=1', data=payload, headers=headers)
            with cast(HTTPResponse, urlopen(request, timeout=30)) as response:
                result = cast(object, json.load(response))
            if isinstance(result, list):
                items = cast(list[object], result)
                if len(items) == 1:
                    result = items[0]
            if isinstance(result, int):
                raise DownloadError(classify_error(0, str(result)))
            if not isinstance(result, dict):
                raise DownloadError('file invalid')
            fields = cast(dict[str, object], result)
            # A taken-down or blocked file answers with an error code in "e".
            if isinstance(fields.get('e'), int):
                raise DownloadError(classify_error(0, str(fields['e'])))
            return fields
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
