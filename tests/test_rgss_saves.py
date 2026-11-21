# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import shutil
from pathlib import Path
from typing import cast

import pytest

from core import rgss_saves
from core.rgss_saves import (
    FORMAT_VX,
    FORMAT_VXACE,
    FORMAT_XP,
    apply_patches,
    build_view,
    list_slots,
    load_rgss_names,
    load_save,
    resolve_slot,
    save_folders,
    write_save,
)
from core.rpgm_saves import (
    BackupFailed,
    InvalidPatch,
    JsonDict,
    RpgmSaveError,
    SaveChanged,
    UnsupportedSaveFormat,
)
from core.ruby_marshal import (
    ArrayNode,
    BoolNode,
    FloatNode,
    HashNode,
    IntNode,
    LinkNode,
    NilNode,
    ObjectNode,
    StringNode,
    SymbolNode,
    UserDefNode,
    assign_ids,
    dump_all,
    load_all,
    trees_equal,
)
from tests.test_rgss_tools import build_rgss_v3

FIXTURES_ROOT = Path(__file__).parent / "fixtures" / "rgss"


def test_list_slots_engines_playtime_and_filtering(tmp_path: Path) -> None:
    # VX Ace save next to Game.exe
    vxace_dir = tmp_path / "vxace"
    vxace_dir.mkdir()
    shutil.copy(
        FIXTURES_ROOT / "vxace" / "Save01.rvdata2", vxace_dir / "Save01.rvdata2"
    )
    slots = list_slots(str(vxace_dir), "")
    assert len(slots) == 1
    assert slots[0]["file"] == "Save01.rvdata2"
    assert slots[0]["slot"] == 1
    assert slots[0]["format"] == FORMAT_VXACE
    assert slots[0]["playtime"] == "00:00:23"
    assert slots[0]["relative"] == ""

    # XP game with S_data.rxdata and Save2.rxdata next to Game.exe -> S_data.rxdata not listed
    xp_dir = tmp_path / "xp"
    xp_dir.mkdir()
    shutil.copy(FIXTURES_ROOT / "xp" / "Save1.rxdata", xp_dir / "Save2.rxdata")
    (xp_dir / "S_data.rxdata").write_bytes(b"\x04\x080")
    (xp_dir / "System.rxdata").write_bytes(b"\x04\x080")
    slots = list_slots(str(xp_dir), "")
    assert [s["file"] for s in slots] == ["Save2.rxdata"]
    assert slots[0]["slot"] == 2
    assert slots[0]["format"] == FORMAT_XP
    # 144000 frames at 40 fps = 01:00:00
    assert slots[0]["playtime"] == "01:00:00"

    # VX game playtime (3660 frames at 60 fps = 00:01:01)
    vx_dir = tmp_path / "vx"
    vx_dir.mkdir()
    shutil.copy(FIXTURES_ROOT / "vx" / "Save1.rvdata", vx_dir / "Save1.rvdata")
    slots = list_slots(str(vx_dir), "")
    assert len(slots) == 1
    assert slots[0]["format"] == FORMAT_VX
    assert slots[0]["playtime"] == "00:01:01"

    # Subfolder savedata/Save03.rvdata2
    sub_dir = tmp_path / "sub"
    (sub_dir / "savedata").mkdir(parents=True)
    shutil.copy(
        FIXTURES_ROOT / "vxace" / "Save01.rvdata2",
        sub_dir / "savedata" / "Save03.rvdata2",
    )
    slots = list_slots(str(sub_dir), "")
    assert len(slots) == 1
    assert slots[0]["slot"] == 3
    assert slots[0]["relative"] == "savedata"

    # No saves yet
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    assert list_slots(str(empty_dir), "") == []


def test_list_slots_duplicate_priority_dedupe_and_unreadable_playtime(
    tmp_path: Path,
) -> None:
    exe_dir = tmp_path / "game"
    exe_dir.mkdir()
    eff_dir = tmp_path / "effective"
    eff_dir.mkdir()
    (exe_dir / "Save").mkdir()
    (exe_dir / "Saves").mkdir()

    # Same file name in exe folder and effective folder -> exe folder wins
    shutil.copy(FIXTURES_ROOT / "vx" / "Save1.rvdata", exe_dir / "Save1.rvdata")
    (eff_dir / "Save1.rvdata").write_bytes(b"corrupt-other")
    # Same file in Save and Saves -> Save wins
    (exe_dir / "Save" / "Save2.rvdata").write_bytes(b"corrupt-save")
    shutil.copy(
        FIXTURES_ROOT / "vx" / "Save1.rvdata", exe_dir / "Saves" / "Save2.rvdata"
    )
    # Effective-only slot with unreadable playtime -> still listed with ""
    (eff_dir / "save3.rvdata2").write_bytes(b"not-marshal-at-all")

    slots = list_slots(str(exe_dir), str(eff_dir))
    assert [s["file"] for s in slots] == [
        "Save1.rvdata",
        "Save2.rvdata",
        "save3.rvdata2",
    ]
    assert slots[0]["folder"] == str(exe_dir)
    assert slots[0]["relative"] == ""
    assert slots[0]["playtime"] == "00:01:01"
    assert slots[1]["folder"] == str(exe_dir / "Save")
    assert slots[1]["relative"] == "Save"
    assert slots[1]["playtime"] == ""
    assert slots[2]["folder"] == str(eff_dir)
    assert slots[2]["relative"] is None
    assert slots[2]["playtime"] == ""

    # Folder resolving to same path is read once
    symlink_eff = tmp_path / "eff_link"
    symlink_eff.symlink_to(exe_dir / "Saves")
    folders = save_folders(str(exe_dir), str(symlink_eff))
    reals = [Path(f).resolve() for f, _ in folders]
    assert len(reals) == len(set(reals))
    assert all(rel is not None for _, rel in folders)


def test_resolve_slot_by_name_only(tmp_path: Path) -> None:
    game = tmp_path / "game"
    (game / "Data").mkdir(parents=True)
    (game / "Data" / "System.rvdata2").write_bytes(b"\x04\x080")
    shutil.copy(FIXTURES_ROOT / "vxace" / "Save01.rvdata2", game / "Save01.rvdata2")

    path, folder, relative = resolve_slot(str(game), "", "Save01.rvdata2")
    assert path == str(game / "Save01.rvdata2")
    assert folder == str(game)
    assert relative == ""

    for bad in (
        "../Data/System.rvdata2",
        "Data/System.rvdata2",
        "Data\\Save01.rvdata2",
        ".Save01.rvdata2",
        "S_data.rxdata",
        "Save99.rvdata2",
        "",
        None,
        123,
    ):
        with pytest.raises(RpgmSaveError) as exc_info:
            _ = resolve_slot(str(game), "", bad)
        assert exc_info.value.code == "not_found"


def test_load_rgss_names_all_engines() -> None:
    # XP
    xp_names = load_rgss_names(str(FIXTURES_ROOT / "xp"), FORMAT_XP)
    xp_items = cast(list[JsonDict], xp_names["items"])
    # Escape codes stripped and blank item omitted
    assert [(e["id"], e["name"], e["kind"]) for e in xp_items] == [
        (1, "Herb", "item"),
        (2, "Elixir", "item"),
    ]
    assert xp_names["currency"] == "Gold"
    assert xp_names["mp_label"] == "SP"
    assert xp_names["params"] == ["HP", "SP", "STR", "DEX", "AGI", "INT"]
    assert cast(list[str], xp_names["switches"])[3] == "Boss Defeated"

    # VX
    vx_names = load_rgss_names(str(FIXTURES_ROOT / "vx"), FORMAT_VX)
    vx_items = cast(list[JsonDict], vx_names["items"])
    assert [(e["id"], e["name"]) for e in vx_items] == [(1, "Potion"), (2, "Ether")]
    assert vx_names["currency"] == "G"
    assert vx_names["mp_label"] == "MP"
    assert vx_names["params"] == ["HP", "MP", "ATK", "DEF", "SPI", "AGI"]
    assert cast(dict[str, str], vx_names["actors"])["1"] == "Sona"

    # VX Ace (key item + hidden item + currency + 8 params)
    ace_names = load_rgss_names(str(FIXTURES_ROOT / "vxace"), FORMAT_VXACE)
    ace_items = cast(list[JsonDict], ace_names["items"])
    assert [(e["id"], e["name"], e["kind"]) for e in ace_items] == [
        (1, "Magic Water", "item"),
        (2, "Old Key", "key"),
        (3, "Hidden Token", "hidden"),
    ]
    assert ace_names["currency"] == "GP"
    assert ace_names["mp_label"] == "MP"
    assert len(cast(list[str], ace_names["params"])) == 8


def test_load_rgss_names_from_archive_and_unreadable(tmp_path: Path) -> None:
    # Data only inside Game.rgss3a (no loose Data/ folder)
    game = tmp_path / "archived"
    game.mkdir()
    src_data = FIXTURES_ROOT / "vxace" / "Data"
    build_rgss_v3(
        game / "Game.rgss3a",
        [
            ("Data/Items.rvdata2", (src_data / "Items.rvdata2").read_bytes()),
            ("Data/System.rvdata2", (src_data / "System.rvdata2").read_bytes()),
        ],
    )
    names = load_rgss_names(str(game), FORMAT_VXACE)
    items = cast(list[JsonDict], names["items"])
    assert items[0]["name"] == "Magic Water"
    assert items[1] == {
        "id": 2,
        "name": "Old Key",
        "kind": "key",
        "description": "Opens the tower gate.",
    }
    assert names["currency"] == "GP"

    # Unreadable/corrupt loose files still succeed with defaults & empty lists
    broken = tmp_path / "broken"
    (broken / "Data").mkdir(parents=True)
    (broken / "Data" / "Items.rxdata").write_bytes(b"corrupt")
    (broken / "Data" / "System.rxdata").write_bytes(b"\x04\x070")
    fallback = load_rgss_names(str(broken), FORMAT_XP)
    assert fallback["items"] == []
    assert fallback["switches"] == []
    assert fallback["currency"] == "G"
    assert fallback["mp_label"] == "SP"
    assert fallback["params"] == ["Max HP", "Max SP", "STR", "DEX", "AGI", "INT"]


def test_load_save_all_three_engines() -> None:
    # XP
    xp_folder = FIXTURES_ROOT / "xp"
    xp = load_save(
        str(xp_folder / "Save1.rxdata"), load_rgss_names(str(xp_folder), FORMAT_XP)
    )
    assert xp["format"] == FORMAT_XP
    xp_view = cast(JsonDict, xp["view"])
    assert xp_view["missing"] == []
    assert xp_view["gold"] == 750
    assert isinstance(xp_view["gold_ref"], int)
    xp_inv = cast(dict[str, list[JsonDict]], xp_view["inventory"])
    assert [(r["id"], r["count"], r["name"]) for r in xp_inv["item"]] == [
        (1, 4, "Herb"),
        (2, 1, "Elixir"),
    ]
    assert isinstance(xp_inv["item"][0]["ref"], int)
    xp_actors = cast(list[JsonDict], xp_view["actors"])
    assert len(xp_actors) == 2
    assert xp_actors[0]["name"] == "Aluxes"
    assert xp_actors[0]["level"] == 5
    assert xp_actors[0]["exp"] == 1000
    assert xp_actors[0]["has_curve"] is True
    assert xp_actors[0]["hp"] == 420
    assert xp_actors[0]["mp"] == 110
    assert xp_actors[0]["param_plus"] == [20, 10, 5, 3, -2, 4]
    assert isinstance(xp_actors[0]["mp_ref"], int)
    assert len(cast(list[object], xp_actors[0]["param_plus_refs"])) == 6

    # VX
    vx_folder = FIXTURES_ROOT / "vx"
    vx = load_save(
        str(vx_folder / "Save1.rvdata"), load_rgss_names(str(vx_folder), FORMAT_VX)
    )
    assert vx["format"] == FORMAT_VX
    vx_view = cast(JsonDict, vx["view"])
    assert vx_view["missing"] == []
    assert vx_view["gold"] == 120
    vx_actors = cast(list[JsonDict], vx_view["actors"])
    assert vx_actors[0]["name"] == "Sona"
    assert vx_actors[0]["has_curve"] is True
    assert vx_actors[0]["param_plus"] == [15, 5, 3, 2, 1, -1]
    vx_vars = cast(list[JsonDict], vx_view["variables"])
    assert vx_vars[0]["value"] == 99 and vx_vars[0]["type"] == "number"
    assert vx_vars[1]["value"] == "Silver" and vx_vars[1]["type"] == "string"
    assert vx_vars[2]["value"] == 2.25 and vx_vars[2]["type"] == "number"
    assert vx_vars[3]["value"] is None and vx_vars[3]["type"] == "other"

    # VX Ace
    ace_folder = FIXTURES_ROOT / "vxace"
    ace = load_save(
        str(ace_folder / "Save01.rvdata2"),
        load_rgss_names(str(ace_folder), FORMAT_VXACE),
    )
    assert ace["format"] == FORMAT_VXACE
    ace_view = cast(JsonDict, ace["view"])
    assert ace_view["missing"] == []
    assert ace_view["gold"] == 2500
    ace_weapons = cast(dict[str, list[JsonDict]], ace_view["inventory"])["weapon"]
    assert [(r["id"], r["name"], r["known"]) for r in ace_weapons] == [
        (1, "Steel Blade", True),
        (42, "Unknown weapon #42", False),
    ]
    ace_actors = cast(list[JsonDict], ace_view["actors"])
    assert ace_actors[0]["name"] == "Eric"
    assert ace_actors[0]["exp"] == 1250
    assert ace_actors[0]["has_curve"] is False
    assert ace_actors[0]["param_plus"] == [25, 10, 4, 2, 0, 0, -3, 5]
    # Named switch 3 ("Secret Ending") is listed even though save only stored up to 2
    ace_switches = cast(list[JsonDict], ace_view["switches"])
    assert len(ace_switches) == 3
    assert ace_switches[2]["name"] == "Secret Ending"
    assert ace_switches[2]["value"] is False
    assert ace_switches[2]["ref"] is None
    # Raw variable 4 holds Tone -> read-only ("other")
    ace_vars = cast(list[JsonDict], ace_view["variables"])
    assert ace_vars[3]["type"] == "other" and ace_vars[3]["ref"] is None


def test_load_save_unexpected_layout_and_encrypted(tmp_path: Path) -> None:
    # Remove :party from VX Ace contents hash -> opens with missing parts + Raw view intact
    dumps = load_all((FIXTURES_ROOT / "vxace" / "Save01.rvdata2").read_bytes())
    contents = dumps[1]
    assert isinstance(contents, HashNode)
    contents.pairs = [
        (k, v)
        for k, v in contents.pairs
        if not (isinstance(k, SymbolNode) and k.name == b"party")
    ]
    no_party = tmp_path / "Save01.rvdata2"
    no_party.write_bytes(dump_all(dumps))

    payload = load_save(
        str(no_party), load_rgss_names(str(FIXTURES_ROOT / "vxace"), FORMAT_VXACE)
    )
    view = cast(JsonDict, payload["view"])
    assert view["gold"] is None
    assert view["gold_ref"] is None
    assert set(cast(list[str], view["missing"])) == {
        "gold",
        "items",
        "weapons",
        "armors",
    }
    assert len(cast(list[JsonDict], view["actors"])) == 2
    assert len(cast(list[object], payload["raw"])) == 2

    # Script-encrypted / compressed save -> UnsupportedSaveFormat
    encrypted = tmp_path / "Save02.rvdata2"
    encrypted.write_bytes(b"ENC\x00\x01\x02\x03not-marshal")
    with pytest.raises(UnsupportedSaveFormat):
        _ = load_save(str(encrypted), load_rgss_names(str(tmp_path), FORMAT_VXACE))


def _load_fixture_tree(engine: str, filename: str):
    raw = (FIXTURES_ROOT / engine / filename).read_bytes()
    dumps = load_all(raw)
    nodes_by_id, editable_ids = assign_ids(dumps)
    names = load_rgss_names(str(FIXTURES_ROOT / engine), engine)
    return dumps, nodes_by_id, editable_ids, names


def test_patch_gold_and_inventory() -> None:
    # VX Ace: change gold to 50000 and remove item 1 (count=0)
    dumps, nodes, editable, names = _load_fixture_tree(FORMAT_VXACE, "Save01.rvdata2")
    view = build_view(dumps, FORMAT_VXACE, names, editable)
    gold_ref = view["gold_ref"]
    assert isinstance(gold_ref, int)

    apply_patches(
        dumps,
        FORMAT_VXACE,
        [
            {"op": "set_node", "node": gold_ref, "value": 50000},
            {"op": "set_count", "kind": "item", "id": 1, "count": 0},
        ],
        nodes,
        editable,
    )
    reloaded = load_all(dump_all(dumps))
    _, edit2 = assign_ids(reloaded)
    view2 = build_view(reloaded, FORMAT_VXACE, names, edit2)
    assert view2["gold"] == 50000
    items2 = cast(dict[str, list[JsonDict]], view2["inventory"])["item"]
    assert [r["id"] for r in items2] == [2]

    # Gold out of range rejected
    with pytest.raises(InvalidPatch):
        apply_patches(
            dumps,
            FORMAT_VXACE,
            [{"op": "set_node", "node": gold_ref, "value": 10_000_000}],
            nodes,
            editable,
        )

    # XP: add weapon 2 ("Iron Sword") with count 1
    xp_dumps, xp_nodes, xp_edit, xp_names = _load_fixture_tree(
        FORMAT_XP, "Save1.rxdata"
    )
    apply_patches(
        xp_dumps,
        FORMAT_XP,
        [{"op": "set_count", "kind": "weapon", "id": 2, "count": 1}],
        xp_nodes,
        xp_edit,
    )
    xp_view = build_view(xp_dumps, FORMAT_XP, xp_names, xp_edit)
    weapons = cast(dict[str, list[JsonDict]], xp_view["inventory"])["weapon"]
    assert [(r["id"], r["count"], r["name"]) for r in weapons] == [
        (1, 2, "Bronze Sword"),
        (2, 1, "Iron Sword"),
    ]


def test_patch_actors_levels_and_strings() -> None:
    # XP: set actor 1 SP to 200 and bonus 5 (@int_plus) to -10
    xp_dumps, xp_nodes, xp_edit, xp_names = _load_fixture_tree(
        FORMAT_XP, "Save1.rxdata"
    )
    apply_patches(
        xp_dumps,
        FORMAT_XP,
        [
            {"op": "set_actor", "id": 1, "field": "mp", "value": 200},
            {
                "op": "set_actor",
                "id": 1,
                "field": "paramPlus",
                "index": 5,
                "value": -10,
            },
        ],
        xp_nodes,
        xp_edit,
    )
    xp_actor = cast(
        list[JsonDict], build_view(xp_dumps, FORMAT_XP, xp_names, xp_edit)["actors"]
    )[0]
    assert xp_actor["mp"] == 200
    assert cast(list[int], xp_actor["param_plus"])[5] == -10

    # VX: raise level to 8 -> exp becomes 1863 from @exp_list; rename without encoding marker
    vx_dumps, vx_nodes, vx_edit, vx_names = _load_fixture_tree(
        FORMAT_VX, "Save1.rvdata"
    )
    apply_patches(
        vx_dumps,
        FORMAT_VX,
        [
            {"op": "set_actor", "id": 1, "field": "level", "value": 8},
            {"op": "set_actor", "id": 1, "field": "name", "value": "Clärís"},
        ],
        vx_nodes,
        vx_edit,
    )
    vx_actors_obj = vx_dumps[9]
    assert isinstance(vx_actors_obj, ObjectNode)
    vx_actor_arr = vx_actors_obj.attributes[0][1]
    assert isinstance(vx_actor_arr, ArrayNode)
    vx_actor_node = vx_actor_arr.items[1]
    assert isinstance(vx_actor_node, ObjectNode)
    attrs = {k.name: v for k, v in vx_actor_node.attributes}
    assert isinstance(attrs[b"@level"], IntNode) and attrs[b"@level"].value == 8
    assert isinstance(attrs[b"@exp"], IntNode) and attrs[b"@exp"].value == 1863
    # VX name had no encoding marker -> still no ivars, UTF-8 bytes
    assert isinstance(attrs[b"@name"], StringNode)
    assert attrs[b"@name"].data == "Clärís".encode()
    assert attrs[b"@name"].ivars is None

    # Explicit exp edit after level edit wins
    apply_patches(
        vx_dumps,
        FORMAT_VX,
        [
            {"op": "set_actor", "id": 1, "field": "level", "value": 6},
            {"op": "set_actor", "id": 1, "field": "exp", "value": 9999},
        ],
        vx_nodes,
        vx_edit,
    )
    assert isinstance(attrs[b"@level"], IntNode) and attrs[b"@level"].value == 6
    assert isinstance(attrs[b"@exp"], IntNode) and attrs[b"@exp"].value == 9999

    # VX level beyond @exp_list leaves exp unchanged
    apply_patches(
        vx_dumps,
        FORMAT_VX,
        [{"op": "set_actor", "id": 1, "field": "level", "value": 50}],
        vx_nodes,
        vx_edit,
    )
    assert isinstance(attrs[b"@level"], IntNode) and attrs[b"@level"].value == 50
    assert isinstance(attrs[b"@exp"], IntNode) and attrs[b"@exp"].value == 9999

    # VX Ace: level raised -> exp stays unchanged; bonus 2 on actor 2 -> 50; rename keeps :E => true
    ace_dumps, ace_nodes, ace_edit, ace_names = _load_fixture_tree(
        FORMAT_VXACE, "Save01.rvdata2"
    )
    apply_patches(
        ace_dumps,
        FORMAT_VXACE,
        [
            {"op": "set_actor", "id": 1, "field": "level", "value": 10},
            {"op": "set_actor", "id": 1, "field": "name", "value": "Clärís"},
            {"op": "set_actor", "id": 2, "field": "paramPlus", "index": 2, "value": 50},
        ],
        ace_nodes,
        ace_edit,
    )
    ace_view = build_view(ace_dumps, FORMAT_VXACE, ace_names, ace_edit)
    ace_actors = cast(list[JsonDict], ace_view["actors"])
    assert ace_actors[0]["level"] == 10
    assert ace_actors[0]["exp"] == 1250  # unchanged!
    assert ace_actors[0]["name"] == "Clärís"
    assert cast(list[int], ace_actors[1]["param_plus"])[2] == 50
    # Verify :E => true encoding marker preserved on VX Ace name
    name_ref = cast(int, ace_actors[0]["name_ref"])
    name_node = ace_nodes[name_ref]
    assert isinstance(name_node, StringNode)
    assert name_node.data == "Clärís".encode()
    assert name_node.ivars is not None
    assert name_node.ivars[0][0].name == b"E"
    assert (
        isinstance(name_node.ivars[0][1], BoolNode)
        and name_node.ivars[0][1].value is True
    )


def test_patch_switches_and_variables() -> None:
    ace_dumps, ace_nodes, ace_edit, _ace_names = _load_fixture_tree(
        FORMAT_VXACE, "Save01.rvdata2"
    )
    # Turn switch 12 on and set variable 25 to 3 (pads 11..24 with nil)
    apply_patches(
        ace_dumps,
        FORMAT_VXACE,
        [
            {"op": "set_switch", "id": 12, "value": True},
            {"op": "set_variable", "id": 25, "value": 3},
            {"op": "set_variable", "id": 2, "value": "Alicia"},
            {"op": "set_variable", "id": 3, "value": 4.75},
        ],
        ace_nodes,
        ace_edit,
    )
    contents = ace_dumps[1]
    assert isinstance(contents, HashNode)
    by_sym = {k.name: v for k, v in contents.pairs if isinstance(k, SymbolNode)}
    sw_obj = by_sym[b"switches"]
    assert isinstance(sw_obj, ObjectNode)
    sw_arr = sw_obj.attributes[0][1]
    assert isinstance(sw_arr, ArrayNode)
    assert isinstance(sw_arr.items[12], BoolNode) and sw_arr.items[12].value is True
    assert all(isinstance(sw_arr.items[i], NilNode) for i in range(3, 12))

    var_obj = by_sym[b"variables"]
    assert isinstance(var_obj, ObjectNode)
    var_arr = var_obj.attributes[0][1]
    assert isinstance(var_arr, ArrayNode)
    assert isinstance(var_arr.items[25], IntNode) and var_arr.items[25].value == 3
    assert all(isinstance(var_arr.items[i], NilNode) for i in range(5, 25))
    assert (
        isinstance(var_arr.items[2], StringNode)
        and var_arr.items[2].decode_utf8() == "Alicia"
    )
    assert isinstance(var_arr.items[3], FloatNode) and var_arr.items[3].value == 4.75

    # Read-only variable (Tone at id 4) rejected
    with pytest.raises(InvalidPatch):
        apply_patches(
            ace_dumps,
            FORMAT_VXACE,
            [{"op": "set_variable", "id": 4, "value": 1}],
            ace_nodes,
            ace_edit,
        )


def test_patch_whole_number_variable_stays_whole() -> None:
    dumps, nodes, editable, _names = _load_fixture_tree(FORMAT_VXACE, "Save01.rvdata2")
    contents = dumps[1]
    assert isinstance(contents, HashNode)
    by_sym = {k.name: v for k, v in contents.pairs if isinstance(k, SymbolNode)}
    var_obj = by_sym[b"variables"]
    assert isinstance(var_obj, ObjectNode)
    var_arr = var_obj.attributes[0][1]
    assert isinstance(var_arr, ArrayNode)
    ident = next(
        i for i, node in enumerate(var_arr.items) if i and isinstance(node, IntNode)
    )
    before = var_arr.items[ident]
    assert isinstance(before, IntNode)
    old_value = before.value

    with pytest.raises(InvalidPatch, match="whole number"):
        apply_patches(
            dumps,
            FORMAT_VXACE,
            [{"op": "set_variable", "id": ident, "value": 2.5}],
            nodes,
            editable,
        )
    assert before.value == old_value

    apply_patches(
        dumps,
        FORMAT_VXACE,
        [{"op": "set_variable", "id": ident, "value": 7.0}],
        nodes,
        editable,
    )
    after = var_arr.items[ident]
    assert isinstance(after, IntNode) and after.value == 7


def test_patch_raw_set_node_and_refusals() -> None:
    ace_dumps, ace_nodes, ace_edit, _names = _load_fixture_tree(
        FORMAT_VXACE, "Save01.rvdata2"
    )
    # Find @text_history[0] ("Welcome to the tower."), a LinkNode, a UserDefNode,
    # a non-UTF-8 StringNode (Shift-JIS / binary), a SymbolNode, and an IntNode.
    text_id = next(
        nid
        for nid, n in ace_nodes.items()
        if isinstance(n, StringNode) and n.data == b"Welcome to the tower."
    )
    int_id = next(
        nid
        for nid, n in ace_nodes.items()
        if isinstance(n, IntNode) and nid in ace_edit
    )
    link_id = next(nid for nid, n in ace_nodes.items() if isinstance(n, LinkNode))
    udef_id = next(nid for nid, n in ace_nodes.items() if isinstance(n, UserDefNode))
    sym_id = next(nid for nid, n in ace_nodes.items() if isinstance(n, SymbolNode))
    bad_str_id = next(
        nid
        for nid, n in ace_nodes.items()
        if isinstance(n, StringNode) and not n.is_editable_utf8()
    )

    # Valid edit of @text_history[0]
    apply_patches(
        ace_dumps,
        FORMAT_VXACE,
        [{"op": "set_node", "node": text_id, "value": "New history line"}],
        ace_nodes,
        ace_edit,
    )
    edited = ace_nodes[text_id]
    assert isinstance(edited, StringNode)
    assert edited.decode_utf8() == "New history line"
    assert edited.ivars is not None  # keeps :E => true

    # Type mismatch rejected ("abc" for whole number)
    with pytest.raises(InvalidPatch):
        apply_patches(
            ace_dumps,
            FORMAT_VXACE,
            [{"op": "set_node", "node": int_id, "value": "abc"}],
            ace_nodes,
            ace_edit,
        )
    # Float rejected for int
    with pytest.raises(InvalidPatch):
        apply_patches(
            ace_dumps,
            FORMAT_VXACE,
            [{"op": "set_node", "node": int_id, "value": 1.5}],
            ace_nodes,
            ace_edit,
        )
    # Read-only nodes refused: link, userdef, symbol, non-UTF-8 string
    for ro_id in (link_id, udef_id, sym_id, bad_str_id):
        with pytest.raises(InvalidPatch):
            apply_patches(
                ace_dumps,
                FORMAT_VXACE,
                [{"op": "set_node", "node": ro_id, "value": "nope"}],
                ace_nodes,
                ace_edit,
            )


def _no_temp_files(folder: Path) -> bool:
    return not any(p.name.endswith(".wlib-tmp") for p in folder.iterdir())


def test_write_save_unedited_byte_identical_and_gold_only_diff(tmp_path: Path) -> None:
    slot = tmp_path / "Save01.rvdata2"
    original = (FIXTURES_ROOT / "vxace" / "Save01.rvdata2").read_bytes()
    slot.write_bytes(original)
    st = slot.stat()

    # Unedited write -> byte-identical
    name = write_save(str(slot), [], st.st_size, st.st_mtime_ns, lambda: "b1")
    assert name == "b1"
    assert slot.read_bytes() == original
    assert _no_temp_files(tmp_path)

    # Gold-only edit -> tree identical except gold
    loaded = load_save(
        str(slot), load_rgss_names(str(FIXTURES_ROOT / "vxace"), FORMAT_VXACE)
    )
    gold_ref = cast(int, cast(JsonDict, loaded["view"])["gold_ref"])
    st2 = slot.stat()
    _ = write_save(
        str(slot),
        [{"op": "set_node", "node": gold_ref, "value": 50000}],
        st2.st_size,
        st2.st_mtime_ns,
        lambda: "b2",
    )
    assert _no_temp_files(tmp_path)

    expected = load_all(original)
    exp_nodes, exp_edit = assign_ids(expected)
    apply_patches(
        expected,
        FORMAT_VXACE,
        [{"op": "set_node", "node": gold_ref, "value": 50000}],
        exp_nodes,
        exp_edit,
    )
    actual = load_all(slot.read_bytes())
    assert trees_equal(actual, expected)


def test_write_save_safety_refusals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slot = tmp_path / "Save1.rvdata"
    original = (FIXTURES_ROOT / "vx" / "Save1.rvdata").read_bytes()
    slot.write_bytes(original)
    st = slot.stat()

    # Changed size/mtime refused before backup
    backup_called = False

    def should_not_backup() -> str:
        nonlocal backup_called
        backup_called = True
        return "b"

    with pytest.raises(SaveChanged):
        _ = write_save(str(slot), [], st.st_size, st.st_mtime_ns + 1, should_not_backup)
    assert not backup_called
    assert slot.read_bytes() == original

    # Failed backup leaves file unchanged
    def fail_backup() -> str:
        raise BackupFailed("disk full")

    with pytest.raises(BackupFailed):
        _ = write_save(
            str(slot),
            [{"op": "set_switch", "id": 1, "value": True}],
            st.st_size,
            st.st_mtime_ns,
            fail_backup,
        )
    assert slot.read_bytes() == original
    assert _no_temp_files(tmp_path)

    # File modified during backup -> recheck catches it, no overwrite, no temp file
    modified_bytes = original + b"\x04\x080"

    def racing_backup() -> str:
        slot.write_bytes(modified_bytes)
        return "b-race"

    with pytest.raises(SaveChanged):
        _ = write_save(
            str(slot),
            [{"op": "set_switch", "id": 1, "value": True}],
            st.st_size,
            st.st_mtime_ns,
            racing_backup,
        )
    assert slot.read_bytes() == modified_bytes
    assert _no_temp_files(tmp_path)

    # Restore original for failed encode-check test
    slot.write_bytes(original)
    st3 = slot.stat()
    real_decode = rgss_saves.decode_save
    calls = {"n": 0}

    def flaky_decode(data: bytes):
        calls["n"] += 1
        if calls["n"] == 2:
            return [NilNode()]
        return real_decode(data)

    monkeypatch.setattr(rgss_saves, "decode_save", flaky_decode)
    with pytest.raises(RpgmSaveError, match="failed its check"):
        _ = write_save(
            str(slot),
            [{"op": "set_switch", "id": 1, "value": True}],
            st3.st_size,
            st3.st_mtime_ns,
            lambda: "b-check",
        )
    assert slot.read_bytes() == original
    assert _no_temp_files(tmp_path)
