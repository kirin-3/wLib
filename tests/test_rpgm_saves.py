# SPDX-License-Identifier: GPL-3.0-or-later
import copy
import json
import os
import time
from pathlib import Path
from typing import cast

import pytest

from core import rpgm_saves as rs

FIXTURES = Path(__file__).parent / "fixtures" / "rpgm_saves"
MV_FILE = FIXTURES / "mv" / "file1.rpgsave"
MZ_FILE = FIXTURES / "mz" / "file0.rmmzsave"


def _names(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "items": [{"id": 7, "name": "Magic Water", "kind": "item", "description": ""}],
        "weapons": [],
        "armors": [],
        "actors": {"1": "Harold"},
        "classes": {"1": {"name": "Hero", "expParams": [30.0, 20.0, 30.0, 30.0]}},
        "switches": ["", "Intro"],
        "variables": ["", "Gold seen"],
        "currency": "G",
        "params": list(rs._DEFAULT_PARAMS),
    }
    base.update(overrides)
    return base


def _tree(wrapped: bool = False) -> dict[str, object]:
    def arr(values: list[object]) -> object:
        return {"@c": 5, "@a": values} if wrapped else values

    return {
        "switches": {"@": "Game_Switches", "_data": arr([None, True, None])},
        "variables": {"@": "Game_Variables", "_data": arr([None, 4, "txt", {"x": 1}])},
        "actors": {
            "@": "Game_Actors",
            "_data": arr(
                [
                    None,
                    {
                        "@": "Game_Actor",
                        "_name": "Harold",
                        "_level": 5,
                        "_classId": 1,
                        "_exp": {"1": 100},
                        "_hp": 50,
                        "_mp": 10,
                        "_paramPlus": arr([0] * 8),
                    },
                ]
            ),
        },
        "party": {
            "@": "Game_Party",
            "_gold": 10,
            "_items": {"@c": 9, "7": 5, "8": 1},
            "_weapons": {},
            "_armors": {},
            "_quest": {"points": 3, "title": "abc", "on": True},
        },
    }


# ------------------------------------------------------------- codec


@pytest.mark.parametrize(("path", "fmt"), [(MV_FILE, "mv"), (MZ_FILE, "mz")])
def test_codec_fixtures_decode_and_round_trip(path: Path, fmt: str) -> None:
    tree = rs.decode_save(path.read_bytes(), fmt)
    assert "party" in tree
    assert rs.decode_save(rs.encode_save(tree, fmt), fmt) == tree


@pytest.mark.parametrize("fmt", ["mv", "mz"])
@pytest.mark.parametrize(
    "data", [b"", b"garbage!!", b"\xff\xfe\x00", b"AAAA", b"[1,2]"]
)
def test_codec_garbage_is_unsupported(fmt: str, data: bytes) -> None:
    with pytest.raises(rs.UnsupportedSaveFormat):
        rs.decode_save(data, fmt)


def test_codec_json_array_and_plugin_text_are_unsupported() -> None:
    wrapped = rs.encode_save({"a": 1}, "mv").decode()
    assert rs.decode_save(wrapped.encode(), "mv") == {"a": 1}
    with pytest.raises(rs.UnsupportedSaveFormat):
        rs.decode_save(b"plugin-encrypted-blob-9f8e7d", "mv")


def test_codec_large_mv_save_is_fast() -> None:
    tree = {"data": [{"id": i, "name": f"Name {i}", "v": i * 3} for i in range(60_000)]}
    encoded = rs.encode_save(tree, "mv")
    assert len(json.dumps(tree)) > 2_000_000
    started = time.perf_counter()
    assert rs.decode_save(encoded, "mv") == tree
    assert time.perf_counter() - started < 3


# ------------------------------------------------------------- names


def _data_root(tmp_path: Path, **files: object) -> Path:
    data = tmp_path / "data"
    data.mkdir()
    for stem, content in files.items():
        (data / f"{stem}.json").write_text(
            content if isinstance(content, str) else json.dumps(content),
            encoding="utf-8",
        )
    return tmp_path


def test_strip_escapes() -> None:
    assert rs.strip_escapes("\\C[2]Elixir\\C[0]") == "Elixir"
    assert rs.strip_escapes("\\I[5]Sword\\{big\\}") == "Swordbig"


def test_load_names_items_kinds_and_blanks(tmp_path: Path) -> None:
    root = _data_root(
        tmp_path,
        Items=[
            None,
            {"name": "\\C[2]Elixir\\C[0]", "itypeId": 1, "description": "Heals"},
            {"name": "", "itypeId": 1},
            {"name": "Crown", "itypeId": 2},
            {"name": "Secret", "itypeId": 3},
        ],
        Actors=[None, {"name": "Harold"}],
        Classes=[None, {"name": "Hero", "expParams": [30, 20, 30, 30]}],
        System={
            "switches": ["", "Met \\C[1]King"],
            "variables": ["", "Gold"],
            "currencyUnit": "G",
            "terms": {"params": ["HP", "MP"]},
        },
    )
    names = rs.load_names(str(root))
    items = cast(list[dict[str, object]], names["items"])
    assert [(e["id"], e["name"], e["kind"]) for e in items] == [
        (1, "Elixir", "item"),
        (3, "Crown", "key"),
        (4, "Secret", "hidden"),
    ]
    assert names["switches"] == ["", "Met King"]
    assert names["actors"] == {"1": "Harold"}
    assert names["params"][:3] == ["HP", "MP", "ATK"]  # type: ignore[index]
    assert names["currency"] == "G"


def test_load_names_missing_and_corrupt_files(tmp_path: Path) -> None:
    root = _data_root(tmp_path, Items="{not json")
    names = rs.load_names(str(root))
    assert names["items"] == []
    assert names["weapons"] == []
    assert names["params"] == list(rs._DEFAULT_PARAMS)


# ---------------------------------------------------------- view model


@pytest.mark.parametrize("wrapped", [False, True])
def test_view_model_handles_wrappers(wrapped: bool) -> None:
    names = _names(
        switches=["", "Intro", "", "", "Late"],
        variables=["", "Gold seen", "", "", "", "Z"],
    )
    view = rs.build_view(_tree(wrapped), names)
    assert view["gold"] == 10
    inventory = cast(dict[str, list[dict[str, object]]], view["inventory"])
    assert [(r["id"], r["count"], r["name"]) for r in inventory["item"]] == [
        (7, 5, "Magic Water"),
        (8, 1, "Unknown item #8"),
    ]
    actors = cast(list[dict[str, object]], view["actors"])
    assert actors[0]["level"] == 5
    assert actors[0]["exp"] == 100
    assert actors[0]["has_curve"] is True
    switches = cast(list[dict[str, object]], view["switches"])
    assert len(switches) == 4 and switches[0]["value"] is True
    variables = cast(list[dict[str, object]], view["variables"])
    assert len(variables) == 5
    assert [v["type"] for v in variables[:4]] == ["number", "string", "other", "number"]
    assert variables[4]["name"] == "Z" and variables[4]["value"] == 0
    assert view["missing"] == []


def test_view_model_reports_missing_sections() -> None:
    view = rs.build_view({"party": {}}, _names())
    assert {"gold", "actors", "switches", "variables"} <= set(
        cast(list[str], view["missing"])
    )


@pytest.mark.parametrize(("path", "fmt"), [(MV_FILE, "mv"), (MZ_FILE, "mz")])
def test_view_model_on_real_fixtures(path: Path, fmt: str) -> None:
    tree = rs.decode_save(path.read_bytes(), fmt)
    view = rs.build_view(tree, _names())
    assert view["missing"] == []
    actors = cast(list[dict[str, object]], view["actors"])
    assert actors and isinstance(actors[0]["level"], int)
    assert isinstance(view["gold"], int)


def test_exp_for_level() -> None:
    curve = [30, 20, 30, 30]
    assert rs.exp_for_level(curve, 1) == 0
    assert rs.exp_for_level(curve, 2) == 50
    assert rs.exp_for_level(curve, 3) > rs.exp_for_level(curve, 2)
    assert rs.exp_for_level(curve, 99) > 1_000_000
    # acc_b of 0 divides by zero in JavaScript, leaving only the extra term
    assert rs.exp_for_level([30, 20, 30, 0], 5) == 80


@pytest.mark.parametrize("fmt", ["mv", "mz"])
def test_codec_round_trips_lone_surrogates(fmt: str) -> None:
    tree = json.loads('{"party": {"_name": "a\\ud800b"}, "x": "\\ud83d\\ude00"}')
    assert rs.decode_save(rs.encode_save(tree, fmt), fmt) == tree


# ------------------------------------------------------------ patches


def _apply(tree: dict[str, object], *patches: dict[str, object]) -> None:
    rs.apply_patches(tree, list(patches), _names())


def test_set_op_keeps_type_and_rejects_markers() -> None:
    tree = _tree()
    _apply(tree, {"op": "set", "path": ["party", "_quest", "points"], "value": 10})
    _apply(tree, {"op": "set", "path": ["party", "_quest", "title"], "value": "x"})
    _apply(tree, {"op": "set", "path": ["party", "_quest", "on"], "value": False})
    party = cast(dict[str, dict[str, object]], tree)["party"]
    assert party["_quest"] == {"points": 10, "title": "x", "on": False}
    for bad in (
        {"op": "set", "path": ["party", "_quest", "points"], "value": "abc"},
        {"op": "set", "path": ["party", "_quest", "points"], "value": 1.5},
        {"op": "set", "path": ["party", "_quest", "points"], "value": True},
        {"op": "set", "path": ["party", "@"], "value": "x"},
        {"op": "set", "path": ["party", "_items", "@c"], "value": 1},
        {"op": "set", "path": ["party", "_quest"], "value": 1},
        {"op": "set", "path": ["party", "_quest", "new"], "value": 1},
        {"op": "set", "path": [], "value": 1},
    ):
        with pytest.raises(rs.InvalidPatch):
            _apply(tree, bad)


def test_set_op_goes_through_array_wrappers() -> None:
    tree = _tree(wrapped=True)
    _apply(tree, {"op": "set", "path": ["actors", "_data", 1, "_level"], "value": 9})
    _apply(
        tree, {"op": "set", "path": ["actors", "_data", 1, "_paramPlus", 2], "value": 3}
    )
    actor = cast(
        list[dict[str, object]],
        cast(dict[str, dict[str, object]], tree)["actors"]["_data"]["@a"],
    )[1]  # type: ignore[index]
    assert actor["_level"] == 9
    assert cast(dict[str, list[int]], actor["_paramPlus"])["@a"][2] == 3


def test_set_count_creates_updates_and_deletes() -> None:
    tree = _tree()
    _apply(tree, {"op": "set_count", "kind": "item", "id": 9, "count": 3})
    _apply(tree, {"op": "set_count", "kind": "weapon", "id": 2, "count": 1})
    _apply(tree, {"op": "set_count", "kind": "item", "id": 7, "count": 0})
    party = cast(dict[str, dict[str, object]], tree)["party"]
    assert party["_items"] == {"@c": 9, "8": 1, "9": 3}
    assert party["_weapons"] == {"2": 1}
    for count in (-3, 2.5, 10_000, "5", None):
        with pytest.raises(rs.InvalidPatch):
            _apply(tree, {"op": "set_count", "kind": "item", "id": 1, "count": count})
    with pytest.raises(rs.InvalidPatch):
        _apply(tree, {"op": "set_count", "kind": "gem", "id": 1, "count": 1})
    with pytest.raises(rs.InvalidPatch):
        _apply(tree, {"op": "set_count", "kind": "item", "id": 0, "count": 1})


def test_switch_and_variable_padding() -> None:
    tree = _tree()
    _apply(tree, {"op": "set_switch", "id": 6, "value": True})
    _apply(tree, {"op": "set_variable", "id": 8, "value": 3})
    _apply(tree, {"op": "set_variable", "id": 2, "value": "new"})
    switches = cast(dict[str, dict[str, list[object]]], tree)["switches"]["_data"]
    variables = cast(dict[str, dict[str, list[object]]], tree)["variables"]["_data"]
    assert switches == [None, True, None, None, None, None, True]  # type: ignore[comparison-overlap]
    assert variables[8] == 3 and variables[5] is None  # type: ignore[index]
    assert variables[2] == "new"  # type: ignore[index]
    for bad in (
        {"op": "set_switch", "id": 1, "value": 1},
        {"op": "set_variable", "id": 3, "value": 1},
        {"op": "set_variable", "id": 2, "value": 5},
        {"op": "set_variable", "id": 1, "value": "text"},
        {"op": "set_variable", "id": 1, "value": float("nan")},
        {"op": "set_variable", "id": 1, "value": None},
    ):
        with pytest.raises(rs.InvalidPatch):
            _apply(tree, bad)


def test_set_actor_fields_and_level_updates_exp() -> None:
    tree = _tree(wrapped=True)
    _apply(tree, {"op": "set_actor", "id": 1, "field": "name", "value": "Hero"})
    _apply(tree, {"op": "set_actor", "id": 1, "field": "level", "value": 20})
    _apply(tree, {"op": "set_actor", "id": 1, "field": "hp", "value": 999})
    _apply(
        tree,
        {"op": "set_actor", "id": 1, "field": "paramPlus", "index": 2, "value": -5},
    )
    actors = cast(dict[str, dict[str, list[dict[str, object]]]], tree)["actors"]
    actor = actors["_data"]["@a"][1]  # type: ignore[index]
    assert actor["_name"] == "Hero" and actor["_level"] == 20 and actor["_hp"] == 999
    assert actor["_exp"] == {"1": rs.exp_for_level([30, 20, 30, 30], 20)}
    assert cast(dict[str, list[int]], actor["_paramPlus"])["@a"][2] == -5
    _apply(tree, {"op": "set_actor", "id": 1, "field": "exp", "value": 7})
    assert actor["_exp"] == {"1": 7}
    for bad in (
        {"op": "set_actor", "id": 1, "field": "level", "value": 0},
        {"op": "set_actor", "id": 1, "field": "hp", "value": -1},
        {"op": "set_actor", "id": 1, "field": "name", "value": ""},
        {"op": "set_actor", "id": 2, "field": "hp", "value": 1},
        {"op": "set_actor", "id": 1, "field": "paramPlus", "index": 8, "value": 1},
        {"op": "set_actor", "id": 1, "field": "mystery", "value": 1},
    ):
        with pytest.raises(rs.InvalidPatch):
            _apply(tree, bad)


def test_level_without_class_curve_leaves_exp() -> None:
    tree = _tree()
    rs.apply_patches(
        tree,
        [{"op": "set_actor", "id": 1, "field": "level", "value": 12}],
        _names(classes={}),
    )
    actors = cast(dict[str, dict[str, list[dict[str, object]]]], tree)["actors"]
    assert actors["_data"][1]["_exp"] == {"1": 100}  # type: ignore[index]


def test_patches_leave_everything_else_and_key_order() -> None:
    tree = _tree()
    before = copy.deepcopy(tree)
    _apply(tree, {"op": "set_count", "kind": "item", "id": 7, "count": 6})
    party = cast(dict[str, dict[str, object]], tree)["party"]
    before_party = cast(dict[str, dict[str, object]], before)["party"]
    assert list(party) == list(before_party)
    cast(dict[str, object], party["_items"])["7"] = 5
    assert tree == before


def test_unknown_edit_is_rejected() -> None:
    for bad in ([{"op": "delete"}], ["x"], [{"op": "set_switch"}]):
        with pytest.raises(rs.InvalidPatch):
            rs.apply_patches(_tree(), bad, _names())


# ------------------------------------------------------------- slots


def _mv_layout(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "game" / "www"
    (root / "data").mkdir(parents=True)
    save = root / "save"
    save.mkdir()
    return root, save


def test_list_slots_mv_and_global_details(tmp_path: Path) -> None:
    root, save = _mv_layout(tmp_path)
    (save / "file1.rpgsave").write_bytes(rs.encode_save(_tree(), "mv"))
    (save / "file3.rpgsave").write_bytes(rs.encode_save(_tree(), "mv"))
    (save / "config.rpgsave").write_bytes(b"x")
    (save / "notes.txt").write_bytes(b"x")
    glob = [None, {"playtime": "01:23:45", "title": "Chapter 1"}]
    (save / "global.rpgsave").write_bytes(rs.encode_save({}, "mv"))
    (save / "global.rpgsave").write_bytes(
        rs.compress_to_base64(json.dumps(glob)).encode()
    )
    slots = rs.list_slots(str(root), "")
    assert [s["file"] for s in slots] == ["file1.rpgsave", "file3.rpgsave"]
    assert slots[0]["playtime"] == "01:23:45" and slots[0]["title"] == "Chapter 1"
    assert slots[1]["playtime"] == ""


def test_list_slots_mz_override_and_dedupe(tmp_path: Path) -> None:
    root = tmp_path / "game"
    (root / "data").mkdir(parents=True)
    (root / "save").mkdir()
    (root / "save" / "file2.rmmzsave").write_bytes(rs.encode_save(_tree(), "mz"))
    other = tmp_path / "elsewhere"
    other.mkdir()
    (other / "file5.rmmzsave").write_bytes(rs.encode_save(_tree(), "mz"))
    (other / "global.rmmzsave").write_bytes(b"unreadable")
    slots = rs.list_slots(str(root), str(other))
    assert [s["slot"] for s in slots] == [2, 5]
    assert all(s["playtime"] == "" for s in slots)
    assert len(rs.list_slots(str(root), str(root / "save"))) == 1
    # A same-named file elsewhere is hidden: names must address one file
    (other / "file2.rmmzsave").write_bytes(rs.encode_save(_tree(), "mz"))
    slots = rs.list_slots(str(root), str(other))
    assert [s["file"] for s in slots] == ["file2.rmmzsave", "file5.rmmzsave"]
    assert slots[0]["folder"] == str(root / "save")


def test_resolve_slot_rejects_bad_names(tmp_path: Path) -> None:
    root, save = _mv_layout(tmp_path)
    (save / "file1.rpgsave").write_bytes(rs.encode_save(_tree(), "mv"))
    path, folder = rs.resolve_slot(str(root), "", "file1.rpgsave")
    assert path == str(save / "file1.rpgsave") and folder == str(save)
    for bad in (
        "../data/System.json",
        "../save/file1.rpgsave",
        "file1.rpgsave/",
        ".file1.rpgsave",
        "global.rpgsave",
        "file9.rpgsave",
        "",
        None,
        5,
    ):
        with pytest.raises(rs.RpgmSaveError):
            rs.resolve_slot(str(root), "", bad)


# -------------------------------------------------------- safe write


def _slot_file(tmp_path: Path, fmt: str = "mv") -> Path:
    path = tmp_path / ("file1.rpgsave" if fmt == "mv" else "file1.rmmzsave")
    path.write_bytes(rs.encode_save(_tree(), fmt))
    return path


def _stat(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns


@pytest.mark.parametrize("fmt", ["mv", "mz"])
def test_write_save_applies_patches_and_backs_up(tmp_path: Path, fmt: str) -> None:
    path = _slot_file(tmp_path, fmt)
    original = path.read_bytes()
    size, mtime = _stat(path)
    snapshots: list[bytes] = []

    def backup() -> str:
        snapshots.append(path.read_bytes())
        return "backup-1"

    name = rs.write_save(
        str(path),
        [{"op": "set_count", "kind": "item", "id": 7, "count": 9}],
        size,
        mtime,
        _names(),
        backup,
    )
    assert name == "backup-1" and snapshots == [original]
    tree = rs.decode_save(path.read_bytes(), fmt)
    assert cast(dict[str, dict[str, object]], tree)["party"]["_items"] == {
        "@c": 9,
        "7": 9,
        "8": 1,
    }
    assert not list(tmp_path.glob("*.wlib-tmp"))


def test_write_save_refuses_changed_file(tmp_path: Path) -> None:
    path = _slot_file(tmp_path)
    size, mtime = _stat(path)
    os.utime(path, ns=(mtime + 5_000_000_000, mtime + 5_000_000_000))
    called: list[int] = []
    with pytest.raises(rs.SaveChanged):
        rs.write_save(
            str(path), [], size, mtime, _names(), lambda: called.append(1) or ""
        )
    assert not called


def test_write_save_failed_backup_leaves_file(tmp_path: Path) -> None:
    path = _slot_file(tmp_path)
    original = path.read_bytes()
    size, mtime = _stat(path)

    def backup() -> str:
        raise rs.BackupFailed("disk full")

    with pytest.raises(rs.BackupFailed):
        rs.write_save(
            str(path),
            [{"op": "set_switch", "id": 1, "value": False}],
            size,
            mtime,
            _names(),
            backup,
        )
    assert path.read_bytes() == original


def test_write_save_invalid_patch_makes_no_backup(tmp_path: Path) -> None:
    path = _slot_file(tmp_path)
    size, mtime = _stat(path)
    called: list[int] = []
    with pytest.raises(rs.InvalidPatch):
        rs.write_save(
            str(path),
            [{"op": "nope"}],
            size,
            mtime,
            _names(),
            lambda: called.append(1) or "",
        )
    assert not called


def test_write_save_failed_verify_leaves_file_and_no_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _slot_file(tmp_path)
    original = path.read_bytes()
    size, mtime = _stat(path)
    real_decode = rs.decode_save
    calls: list[int] = []

    def flaky(data: bytes, fmt: str) -> dict[str, object]:
        calls.append(1)
        tree = real_decode(data, fmt)
        if len(calls) >= 2:
            tree["tampered"] = True
        return tree

    monkeypatch.setattr(rs, "decode_save", flaky)
    with pytest.raises(rs.RpgmSaveError):
        rs.write_save(str(path), [], size, mtime, _names(), lambda: "b")
    assert path.read_bytes() == original
    assert not list(tmp_path.glob("*.wlib-tmp"))


def test_write_save_without_edits_round_trips(tmp_path: Path) -> None:
    path = _slot_file(tmp_path)
    before = rs.decode_save(path.read_bytes(), "mv")
    size, mtime = _stat(path)
    _ = rs.write_save(str(path), [], size, mtime, _names(), lambda: "b")
    assert rs.decode_save(path.read_bytes(), "mv") == before


def test_load_save_payload(tmp_path: Path) -> None:
    path = _slot_file(tmp_path)
    payload = rs.load_save(str(path), _names())
    assert payload["format"] == "mv" and payload["size"] == path.stat().st_size
    assert "party" in cast(dict[str, object], payload["raw"])


def test_set_gold_path_is_range_checked() -> None:
    tree = _tree()
    _apply(tree, {"op": "set", "path": ["party", "_gold"], "value": 50000})
    for bad in (-1, 10_000_000):
        with pytest.raises(rs.InvalidPatch):
            _apply(tree, {"op": "set", "path": ["party", "_gold"], "value": bad})
