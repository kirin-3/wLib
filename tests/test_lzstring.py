# SPDX-License-Identifier: GPL-3.0-or-later
import json
import random
from pathlib import Path

import pytest

from core.lzstring import LZStringError, compress_to_base64, decompress_from_base64

REFERENCE = json.loads(
    (
        Path(__file__).parent / "fixtures" / "rpgm_saves" / "lzstring_reference.json"
    ).read_text(encoding="utf-8")
)


@pytest.mark.parametrize(("text", "encoded"), REFERENCE)
def test_decodes_reference_output_from_javascript(text: str, encoded: str) -> None:
    assert decompress_from_base64(encoded) == text


@pytest.mark.parametrize(("text", "encoded"), REFERENCE)
def test_encodes_like_javascript(text: str, encoded: str) -> None:
    assert compress_to_base64(text) == encoded


def test_round_trips_random_text() -> None:
    rng = random.Random(7)
    alphabet = 'abcdef {}[]":,0123456789é日😀'
    for _ in range(40):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 400)))
        assert decompress_from_base64(compress_to_base64(text)) == text


@pytest.mark.parametrize("bad", ["!!!!", "abc$", "é"])
def test_invalid_alphabet_raises(bad: str) -> None:
    with pytest.raises(LZStringError):
        decompress_from_base64(bad)


def test_garbage_and_truncated_input_do_not_crash() -> None:
    encoded = compress_to_base64("hello world " * 20)
    for data in ("A", "AAAA", encoded[:10], encoded[: len(encoded) // 2], "////////"):
        try:
            decompress_from_base64(data)
        except LZStringError:
            pass
