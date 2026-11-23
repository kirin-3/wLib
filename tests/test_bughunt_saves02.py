# SPDX-License-Identifier: GPL-3.0-or-later
"""Save editor bug hunt 02.

1. A slot replaced between Open and Save (same size, same mtime) must not
   get the stale edits: the load returns a revision of the bytes it decoded
   and the write refuses a different file.
2. An RGSS string keeps its encoding marker, so an edit must fit that
   encoding: a US-ASCII string takes only ASCII text, and a string with a
   named encoding is read-only.
"""

from __future__ import annotations

import copy
import hashlib
import os
import shutil
from pathlib import Path
from typing import cast

import pytest

from core import rgss_saves as gs
from core import rpgm_saves as rs
from core.api import Api
from core.database import init_db
from core.ruby_marshal import (
    BoolNode,
    IntNode,
    MarshalNode,
    StringNode,
    assign_ids,
    dump_all,
    load_all,
    project_dumps,
)
from tests.test_api_rpgm_saves import _rgss_game, mv_game

_PATCH: list[object] = [{"op": "set_switch", "id": 1, "value": True}]


@pytest.fixture
def backups(tmp_path_factory: pytest.TempPathFactory, monkeypatch) -> Path:
    db_dir = tmp_path_factory.mktemp("saves02-db")
    monkeypatch.setattr("core.database.DB_PATH", str(db_dir / "saves.db"))
    init_db()
    root = tmp_path_factory.mktemp("saves02-backups")
    monkeypatch.setattr(Api, "_save_backups_root", staticmethod(lambda: str(root)))
    return root


def _replace_keeping_stamp(path: Path, data: bytes) -> None:
    """Put `data` in `path` like a sync tool that keeps the old timestamp."""
    stat = path.stat()
    assert len(data) == stat.st_size
    with open(path, "r+b") as handle:
        _ = handle.write(data)
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert path.stat().st_mtime_ns == stat.st_mtime_ns


def _mv_same_size_variant(data: bytes) -> bytes:
    tree = rs.decode_save(data, "mv")
    party = cast(dict[str, object], tree["party"])
    gold = cast(int, party["_gold"])
    for value in range(gold + 1, gold + 1000):
        changed = copy.deepcopy(tree)
        cast(dict[str, object], changed["party"])["_gold"] = value
        encoded = rs.encode_save(changed, "mv")
        if len(encoded) == len(data) and encoded != data:
            return encoded
    raise AssertionError("no same-size variant found")


def _rgss_same_size_variant(data: bytes) -> bytes:
    dumps = load_all(data)
    nodes, _editable = assign_ids(dumps)
    for node in nodes.values():
        if isinstance(node, IntNode) and 1000 <= node.value < 10_000:
            node.set_value(node.value + 1)
            encoded = dump_all(dumps)
            assert len(encoded) == len(data) and encoded != data
            return encoded
    raise AssertionError("no int to change")


# ------------------------------------------------------- bug 1: stale slot


def test_load_returns_revision_of_the_decoded_bytes(tmp_path: Path, backups) -> None:
    folder, game_id = mv_game(tmp_path)
    payload = Api().load_rpgm_save(game_id, "file1.rpgsave")
    assert payload["success"], payload
    data = (folder / "www" / "save" / "file1.rpgsave").read_bytes()
    assert payload["revision"] == hashlib.sha256(data).hexdigest()


def test_mv_slot_replaced_after_open_is_not_overwritten(
    tmp_path: Path, backups: Path
) -> None:
    folder, game_id = mv_game(tmp_path)
    path = folder / "www" / "save" / "file1.rpgsave"
    api = Api()
    payload = api.load_rpgm_save(game_id, "file1.rpgsave")
    assert payload["success"], payload

    replacement = _mv_same_size_variant(path.read_bytes())
    _replace_keeping_stamp(path, replacement)

    result = api.write_rpgm_save(
        game_id,
        "file1.rpgsave",
        _PATCH,
        payload["size"],
        payload["mtime_ns"],
        payload["revision"],
    )
    assert result["success"] is False and result["error_code"] == "changed", result
    assert result["error"] == "The save changed since it was opened. Reopen the slot."
    assert path.read_bytes() == replacement
    assert list(backups.rglob("*.rpgsave")) == []  # refused before the backup


def test_rgss_slot_replaced_after_open_is_not_overwritten(
    tmp_path: Path, backups: Path
) -> None:
    folder, game_id = _rgss_game(tmp_path, "vxace", "Save01.rvdata2")
    path = folder / "Save01.rvdata2"
    api = Api()
    payload = api.load_rpgm_save(game_id, "Save01.rvdata2")
    assert payload["success"], payload
    gold_ref = payload["view"]["gold_ref"]

    replacement = _rgss_same_size_variant(path.read_bytes())
    _replace_keeping_stamp(path, replacement)

    result = api.write_rpgm_save(
        game_id,
        "Save01.rvdata2",
        [{"op": "set_node", "node": gold_ref, "value": 4321}],
        payload["size"],
        payload["mtime_ns"],
        payload["revision"],
    )
    assert result["success"] is False and result["error_code"] == "changed", result
    assert path.read_bytes() == replacement
    assert list(backups.rglob("*.rvdata2")) == []


def test_write_with_current_revision_still_works(tmp_path: Path, backups) -> None:
    folder, game_id = mv_game(tmp_path)
    api = Api()
    payload = api.load_rpgm_save(game_id, "file1.rpgsave")
    result = api.write_rpgm_save(
        game_id,
        "file1.rpgsave",
        _PATCH,
        payload["size"],
        payload["mtime_ns"],
        payload["revision"],
    )
    assert result["success"], result
    reloaded = api.load_rpgm_save(game_id, "file1.rpgsave")
    data = (folder / "www" / "save" / "file1.rpgsave").read_bytes()
    assert reloaded["revision"] == hashlib.sha256(data).hexdigest()
    assert reloaded["revision"] != payload["revision"]


@pytest.mark.parametrize("revision", [None, "", 5, "abc", "G" * 64, "A" * 64])
def test_write_without_a_valid_revision_is_refused(
    tmp_path: Path, backups: Path, revision: object
) -> None:
    folder, game_id = mv_game(tmp_path)
    path = folder / "www" / "save" / "file1.rpgsave"
    original = path.read_bytes()
    api = Api()
    payload = api.load_rpgm_save(game_id, "file1.rpgsave")
    result = api.write_rpgm_save(
        game_id,
        "file1.rpgsave",
        _PATCH,
        payload["size"],
        payload["mtime_ns"],
        revision,
    )
    assert result["success"] is False and result["error_code"] == "invalid_patch"
    assert path.read_bytes() == original
    assert list(backups.rglob("*.rpgsave")) == []


def test_core_writers_check_the_revision(tmp_path: Path) -> None:
    mv = tmp_path / "file1.rpgsave"
    shutil.copy(
        Path(__file__).parent / "fixtures" / "rpgm_saves" / "mv" / "file1.rpgsave", mv
    )
    rgss = tmp_path / "Save01.rvdata2"
    shutil.copy(
        Path(__file__).parent / "fixtures" / "rgss" / "vxace" / "Save01.rvdata2", rgss
    )
    called: list[str] = []
    wrong = "0" * 64
    mv_stat = mv.stat()
    with pytest.raises(rs.SaveChanged):
        _ = rs.write_save(
            str(mv),
            _PATCH,
            mv_stat.st_size,
            mv_stat.st_mtime_ns,
            {},
            lambda: called.append("mv") or "b",
            revision=wrong,
        )
    rgss_stat = rgss.stat()
    with pytest.raises(rs.SaveChanged):
        _ = gs.write_save(
            str(rgss),
            _PATCH,
            rgss_stat.st_size,
            rgss_stat.st_mtime_ns,
            lambda: called.append("rgss") or "b",
            revision=wrong,
        )
    assert called == []
    loaded = gs.load_save(str(rgss), {})
    assert loaded["revision"] == hashlib.sha256(rgss.read_bytes()).hexdigest()


# --------------------------------------------- bug 2: declared string encoding

# Made with Ruby 3.4 (only to make these bytes, not needed by the test):
#   header = { playtime_s: "00:00:01" }
#   contents = {
#     variables: Game_Variables(@data = [nil, "Hero".encode("US-ASCII"),
#                "abc".force_encoding("Shift_JIS"), "Old"]),
#     actors: Game_Actors(@data = [nil, Game_Actor(@name = "Hero" US-ASCII, @level = 1)]),
#   }
#   Marshal.dump(header) + Marshal.dump(contents)
_ENCODINGS_SAVE = (
    b'\x04\x08{\x06:\x0fplaytime_sI"\r00:00:01\x06:\x06ET'
    b"\x04\x08{\x07:\x0evariableso:\x13Game_Variables\x06:\n@data[\t0"
    b'I"\tHero\x06:\x06EF'
    b'I"\x08abc\x06:\rencoding"\x0eShift_JIS'
    b'I"\x08Old\x06;\x08T'
    b":\x0bactorso:\x10Game_Actors\x06;\x07[\x070o:\x0fGame_Actor\x07"
    b':\n@nameI"\tHero\x06;\x08F:\x0b@leveli\x06'
)


def _slot(tmp_path: Path) -> Path:
    path = tmp_path / "Save01.rvdata2"
    _ = path.write_bytes(_ENCODINGS_SAVE)
    return path


def _strings(data: bytes) -> tuple[dict[int, MarshalNode], set[int], list[int]]:
    nodes, editable = assign_ids(load_all(data))
    refs = [i for i, n in sorted(nodes.items()) if isinstance(n, StringNode)]
    return nodes, editable, refs


def _string_ids(data: bytes) -> dict[str, int]:
    """Node id per fixture string: header, ascii_var, sjis_var, utf8_var, ascii_name."""
    _nodes, _editable, refs = _strings(data)
    keys = ("header", "ascii_var", "sjis_var", "utf8_var", "ascii_name")
    assert len(refs) == len(keys)
    return dict(zip(keys, refs, strict=True))


def _write(path: Path, patches: list[object], backup_calls: list[int]) -> str:
    stat = path.stat()

    def backup() -> str:
        backup_calls.append(1)
        return "b"

    return gs.write_save(str(path), patches, stat.st_size, stat.st_mtime_ns, backup)


def _encoding_marker(node: MarshalNode) -> object:
    assert node.ivars is not None
    for key, value in node.ivars:
        if key.name == b"E":
            return isinstance(value, BoolNode) and value.value
        if key.name == b"encoding":
            return cast(StringNode, value).data
    return None


def test_us_ascii_string_takes_an_ascii_edit(tmp_path: Path) -> None:
    path = _slot(tmp_path)
    ids = _string_ids(_ENCODINGS_SAVE)
    calls: list[int] = []
    _ = _write(
        path,
        [
            {"op": "set_node", "node": ids["ascii_var"], "value": "Hera"},
            {"op": "set_actor", "id": 1, "field": "name", "value": "Alex"},
        ],
        calls,
    )
    assert calls == [1]
    nodes, _editable, _refs = _strings(path.read_bytes())
    var = cast(StringNode, nodes[ids["ascii_var"]])
    assert var.data == b"Hera" and _encoding_marker(var) is False
    name = cast(StringNode, nodes[ids["ascii_name"]])
    assert name.data == b"Alex" and _encoding_marker(name) is False


@pytest.mark.parametrize(
    "patch",
    [
        {"op": "set_node", "node": "ascii_var", "value": "Héro"},
        {"op": "set_variable", "id": 1, "value": "Héro"},
        {"op": "set_actor", "id": 1, "field": "name", "value": "Héro"},
    ],
)
def test_us_ascii_string_refuses_non_ascii_before_backup(
    tmp_path: Path, patch: dict[str, object]
) -> None:
    path = _slot(tmp_path)
    ids = _string_ids(_ENCODINGS_SAVE)
    if patch["op"] == "set_node":
        patch = {**patch, "node": ids[cast(str, patch["node"])]}
    calls: list[int] = []
    with pytest.raises(rs.InvalidPatch) as caught:
        _ = _write(path, [patch], calls)
    assert caught.value.code == "invalid_patch"
    assert calls == []
    assert path.read_bytes() == _ENCODINGS_SAVE


def test_named_encoding_string_is_read_only(tmp_path: Path) -> None:
    path = _slot(tmp_path)
    ids = _string_ids(_ENCODINGS_SAVE)
    _nodes, editable, _refs = _strings(_ENCODINGS_SAVE)
    assert ids["sjis_var"] not in editable
    assert ids["ascii_var"] in editable and ids["utf8_var"] in editable
    raw, _by_id, projected = project_dumps(load_all(_ENCODINGS_SAVE))
    assert ids["sjis_var"] not in projected
    assert raw  # the Raw view still shows it

    calls: list[int] = []
    for patch in (
        {"op": "set_node", "node": ids["sjis_var"], "value": "xyz"},
        {"op": "set_variable", "id": 2, "value": "xyz"},
    ):
        with pytest.raises(rs.InvalidPatch):
            _ = _write(path, [patch], calls)
    assert calls == []
    assert path.read_bytes() == _ENCODINGS_SAVE


def test_utf8_and_unmarked_strings_keep_utf8_edits(tmp_path: Path) -> None:
    path = _slot(tmp_path)
    ids = _string_ids(_ENCODINGS_SAVE)
    calls: list[int] = []
    _ = _write(
        path, [{"op": "set_node", "node": ids["utf8_var"], "value": "Ölð"}], calls
    )
    nodes, _editable, _refs = _strings(path.read_bytes())
    utf8 = cast(StringNode, nodes[ids["utf8_var"]])
    assert utf8.data == "Ölð".encode() and _encoding_marker(utf8) is True

    # XP/VX strings have no marker; they stay editable as UTF-8.
    unmarked = StringNode(b"Hero")
    assert unmarked.is_editable_utf8()
    unmarked.set_text("Héro")
    assert unmarked.data == "Héro".encode() and unmarked.ivars is None
