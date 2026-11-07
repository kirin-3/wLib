# SPDX-License-Identifier: GPL-3.0-or-later
"""LZString Base64 codec, as used by RPG Maker MV for `.rpgsave` files.

A port of the reference JavaScript `compressToBase64` / `decompressFromBase64`.
Text is handled as UTF-16 code units, like JavaScript strings, so astral
characters round-trip through the same surrogate pairs the game writes.
"""

from __future__ import annotations

_KEY = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="
_KEY_INDEX = {char: index for index, char in enumerate(_KEY)}
_BITS_PER_CHAR = 6


class LZStringError(ValueError):
    """The input is not valid LZString Base64 data."""


def _to_units(text: str) -> list[str]:
    raw = text.encode("utf-16-le", "surrogatepass")
    return [
        chr(int.from_bytes(raw[i : i + 2], "little")) for i in range(0, len(raw), 2)
    ]


def _from_units(units: str) -> str:
    # surrogatepass keeps lone surrogates (valid in JS strings) so they round-trip.
    return units.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "surrogatepass")


class _BitWriter:
    def __init__(self) -> None:
        self.out: list[str] = []
        self.value: int = 0
        self.position: int = 0

    def write(self, value: int, count: int) -> None:
        for _ in range(count):
            self.value = (self.value << 1) | (value & 1)
            if self.position == _BITS_PER_CHAR - 1:
                self.position = 0
                self.out.append(_KEY[self.value])
                self.value = 0
            else:
                self.position += 1
            value >>= 1

    def finish(self) -> str:
        while True:
            self.value <<= 1
            if self.position == _BITS_PER_CHAR - 1:
                self.out.append(_KEY[self.value])
                break
            self.position += 1
        return "".join(self.out)


def compress_to_base64(text: str) -> str:
    units = _to_units(text)
    dictionary: dict[str, int] = {}
    to_create: set[str] = set()
    writer = _BitWriter()
    w = ""
    enlarge_in = 2
    dict_size = 3
    num_bits = 2

    def emit(token: str) -> None:
        nonlocal enlarge_in, num_bits
        if token in to_create:
            code = ord(token[0])
            if code < 256:
                writer.write(0, num_bits)
                writer.write(code, 8)
            else:
                writer.write(1, num_bits)
                writer.write(code, 16)
            enlarge_in -= 1
            if enlarge_in == 0:
                enlarge_in = 1 << num_bits
                num_bits += 1
            to_create.discard(token)
        else:
            writer.write(dictionary[token], num_bits)
        enlarge_in -= 1
        if enlarge_in == 0:
            enlarge_in = 1 << num_bits
            num_bits += 1

    for c in units:
        if c not in dictionary:
            dictionary[c] = dict_size
            dict_size += 1
            to_create.add(c)
        wc = w + c
        if wc in dictionary:
            w = wc
            continue
        emit(w)
        dictionary[wc] = dict_size
        dict_size += 1
        w = c
    if w != "":
        emit(w)
    writer.write(2, num_bits)
    result = writer.finish()
    return result + ("", "===", "==", "=")[len(result) % 4]


def decompress_from_base64(data: str) -> str:
    if data == "":
        return ""
    bits_parts: list[str] = []
    for char in data.replace(" ", "+"):
        index = _KEY_INDEX.get(char)
        if index is None:
            raise LZStringError("Not valid LZString Base64 data")
        bits_parts.append(format(index, "06b") if index < 64 else "000000")
    bits = "".join(bits_parts) + "0" * 12
    total = len(data) * _BITS_PER_CHAR
    pos = 0

    def read(count: int) -> int:
        nonlocal pos
        if pos > total + 6:
            raise LZStringError("LZString data is truncated")
        chunk = bits[pos : pos + count]
        pos += count
        return int(chunk[::-1], 2)

    dictionary: dict[int, str] = {}
    next_code = read(2)
    if next_code == 2:
        return ""
    if next_code > 1:
        raise LZStringError("Not valid LZString data")
    c = chr(read(8 if next_code == 0 else 16))
    dictionary[3] = c
    w = c
    result = [c]
    dict_size = 4
    enlarge_in = 4
    num_bits = 3
    while True:
        code = read(num_bits)
        if code in (0, 1):
            dictionary[dict_size] = chr(read(8 if code == 0 else 16))
            dict_size += 1
            code = dict_size - 1
            enlarge_in -= 1
        elif code == 2:
            return _from_units("".join(result))
        if enlarge_in == 0:
            enlarge_in = 1 << num_bits
            num_bits += 1
        entry = dictionary.get(code)
        if entry is None:
            if code != dict_size:
                raise LZStringError("Not valid LZString data")
            entry = w + w[0]
        result.append(entry)
        dictionary[dict_size] = w + entry[0]
        dict_size += 1
        enlarge_in -= 1
        w = entry
        if enlarge_in == 0:
            enlarge_in = 1 << num_bits
            num_bits += 1
