# SPDX-License-Identifier: GPL-3.0-or-later
import struct
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest

from core.backup_format import BackupValidationError
from core.litedb import LiteDBReader, MAX_DOCUMENT_SIZE, decode_bson

FIXTURE = Path(__file__).parent / "fixtures/playnite/library/games.db"
SMALL = FIXTURE.parent.parent / "small-library"


@pytest.mark.parametrize(
    "file,collection",
    [
        ("completionstatuses", "CompletionStatus"),
        ("companies", "Company"),
        ("categories", "Category"),
        ("tags", "Tag"),
        ("features", "GameFeature"),
        ("genres", "Genre"),
    ],
)
def test_missing_collection_is_explicitly_optional(file, collection):
    reader = LiteDBReader(SMALL / f"{file}.db")
    assert reader.read_collection(collection, allow_missing=True) == []
    with pytest.raises(BackupValidationError, match="not a Playnite library"):
        reader.read_collection(collection)


def test_required_game_collection_and_present_empty_collection():
    with pytest.raises(BackupValidationError, match="not a Playnite library"):
        LiteDBReader(SMALL / "categories.db").read_collection("Game")
    assert LiteDBReader(SMALL.parent / "empty-games.db").read_collection("Game") == []


@pytest.mark.parametrize("mutation", ["length", "duplicate", "page", "alias"])
def test_missing_collection_still_validates_directory(tmp_path, mutation):
    data = bytearray((SMALL / "games.db").read_bytes())
    if mutation == "length":
        struct.pack_into("<i", data, 102, 4096)
    elif mutation == "page":
        struct.pack_into("<I", data, 110, 9999)
    else:
        data[101] = 2
        entry = struct.pack("<i", 4) + (b"Game" if mutation == "duplicate" else b"Else")
        data[114:126] = entry + struct.pack("<I", 2 if mutation == "alias" else 3)
    path = tmp_path / "optional.db"
    path.write_bytes(data)
    with pytest.raises(BackupValidationError):
        LiteDBReader(path).read_collection("Category", allow_missing=True)


def test_real_litedb_fixture():
    rows = LiteDBReader(FIXTURE).read_collection("Game")
    assert len(rows) == 147
    assert all(row["Name"] != "Deleted game" for row in rows)
    assert rows[0]["_id"] == "00112233-4455-6677-8899-000000000001"
    assert rows[0]["LastActivity"] == datetime(2025, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    assert rows[1]["Notes"] == "x" * 13000


def _bson(elements):
    body = (
        b"".join(
            bytes([kind]) + key.encode() + b"\0" + value
            for kind, key, value in elements
        )
        + b"\0"
    )
    return struct.pack("<i", len(body) + 4) + body


def test_bson_subset():
    guid = UUID("00112233-4455-6677-8899-aabbccddeeff")
    raw = _bson(
        [
            (1, "double", struct.pack("<d", 1.5)),
            (2, "str", struct.pack("<i", 3) + "é".encode() + b"\0"),
            (3, "doc", _bson([(16, "n", struct.pack("<i", -1))])),
            (4, "array", _bson([(18, "0", struct.pack("<q", 2**40))])),
            (5, "guid", struct.pack("<iB", 16, 4) + guid.bytes_le),
            (5, "bytes", struct.pack("<iB", 2, 0) + b"ab"),
            (8, "bool", b"\x01"),
            (9, "date", struct.pack("<q", 0)),
            (10, "null", b""),
            (255, "min", b""),
            (127, "max", b""),
        ]
    )
    assert decode_bson(raw) == {
        "double": 1.5,
        "str": "é",
        "doc": {"n": -1},
        "array": [2**40],
        "guid": str(guid),
        "bytes": b"ab",
        "bool": True,
        "date": datetime(1970, 1, 1, tzinfo=timezone.utc),
        "null": None,
        "min": None,
        "max": None,
    }


@pytest.mark.parametrize(
    "mutation",
    ["truncated", "signature", "version", "encrypted", "loop", "bounds", "oversized"],
)
def test_corrupt_input(tmp_path, mutation):
    data = bytearray(FIXTURE.read_bytes())
    if mutation == "truncated":
        data = data[:-1]
    elif mutation == "signature":
        data[25] = 0
    elif mutation == "version":
        data[52] = 8
    elif mutation == "encrypted":
        data[65] = 1
    else:
        for offset in range(0, len(data), 4096):
            if data[offset + 4] == 5:
                if mutation == "loop":
                    struct.pack_into("<I", data, offset + 9, offset // 4096)
                elif mutation == "bounds":
                    struct.pack_into("<I", data, offset + 9, len(data))
                else:
                    struct.pack_into("<i", data, offset + 25, MAX_DOCUMENT_SIZE + 1)
                break
    file = tmp_path / "games.db"
    file.write_bytes(data)
    with pytest.raises(BackupValidationError):
        LiteDBReader(file).read_collection("Game")


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        struct.pack("<i", MAX_DOCUMENT_SIZE + 1),
        _bson([(2, "bad", struct.pack("<i", -1))]),
    ],
)
def test_invalid_bson(raw):
    with pytest.raises(BackupValidationError):
        decode_bson(raw)
