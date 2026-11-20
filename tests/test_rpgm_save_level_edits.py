# SPDX-License-Identifier: GPL-3.0-or-later
"""RPG Maker save editor: level caps, learned skills and linked slots."""

import json
import os
from pathlib import Path
from typing import cast

import pytest

from core import rpgm_saves as rs


def _data_root(tmp_path: Path) -> Path:
    data = tmp_path / "game" / "data"
    data.mkdir(parents=True)
    files: dict[str, object] = {
        "Actors": [None, {"name": "Harold", "maxLevel": 50}, {"name": "Free"}],
        "Classes": [
            None,
            {
                "name": "Hero",
                "expParams": [30, 20, 30, 30],
                # Stat tables cover levels 0..60 (shortest row wins).
                "params": [[1] * 61] * 7 + [[1] * 62],
                "learnings": [
                    {"level": 5, "skillId": 10, "note": ""},
                    {"level": 6, "skillId": 3, "note": ""},
                    {"level": 9, "skillId": 20, "note": ""},
                    {"level": 2, "skillId": 1, "note": ""},
                ],
            },
        ],
        "System": {},
    }
    for stem, content in files.items():
        (data / f"{stem}.json").write_text(json.dumps(content), encoding="utf-8")
    return tmp_path / "game"


def _tree(actor_id: int = 1, level: int = 4) -> dict[str, object]:
    actor: dict[str, object] = {
        "@": "Game_Actor",
        "_actorId": actor_id,
        "_name": "Harold",
        "_level": level,
        "_classId": 1,
        "_exp": {"1": 100},
        "_skills": {"@a": [1, 7]},
    }
    rows: list[object] = [None, None, None]
    rows[actor_id] = actor
    return {"actors": {"@": "Game_Actors", "_data": {"@a": rows}}}


def _actor(tree: dict[str, object], actor_id: int = 1) -> dict[str, object]:
    actors = cast(dict[str, dict[str, list[dict[str, object]]]], tree["actors"])
    return actors["_data"]["@a"][actor_id]


def _level(
    tree: dict[str, object], names: dict[str, object], value: int, actor_id: int = 1
) -> None:
    rs.apply_patches(
        tree,
        [{"op": "set_actor", "id": actor_id, "field": "level", "value": value}],
        names,
    )


def test_load_names_reads_level_tables_and_learnings(tmp_path: Path) -> None:
    names = rs.load_names(str(_data_root(tmp_path)))
    hero = cast(dict[str, dict[str, object]], names["classes"])["1"]
    assert hero["maxLevel"] == 60
    assert [5, 10] in cast(list[list[int]], hero["learnings"])
    assert names["actorMaxLevels"] == {"1": 50}


def test_level_above_actor_cap_is_refused(tmp_path: Path) -> None:
    names = rs.load_names(str(_data_root(tmp_path)))
    tree = _tree()
    with pytest.raises(rs.InvalidPatch):
        _level(tree, names, 51)
    _level(tree, names, 50)
    assert _actor(tree)["_level"] == 50


def test_level_above_class_table_is_refused(tmp_path: Path) -> None:
    # Actor 2 has no maxLevel, so the class tables (0..60) set the cap.
    names = rs.load_names(str(_data_root(tmp_path)))
    tree = _tree(actor_id=2)
    with pytest.raises(rs.InvalidPatch):
        _level(tree, names, 61, actor_id=2)
    _level(tree, names, 60, actor_id=2)
    assert _actor(tree, 2)["_level"] == 60


def test_actor_already_past_cap_keeps_editing(tmp_path: Path) -> None:
    names = rs.load_names(str(_data_root(tmp_path)))
    tree = _tree(level=120)
    _level(tree, names, 150)
    assert _actor(tree)["_level"] == 150


def test_raising_level_learns_passed_skills_sorted(tmp_path: Path) -> None:
    names = rs.load_names(str(_data_root(tmp_path)))
    tree = _tree(level=4)
    _level(tree, names, 6)
    assert _actor(tree)["_skills"] == {"@a": [1, 3, 7, 10]}
    assert _actor(tree)["_exp"] == {"1": rs.exp_for_level([30, 20, 30, 30], 6)}


def test_lowering_level_forgets_no_skills(tmp_path: Path) -> None:
    names = rs.load_names(str(_data_root(tmp_path)))
    tree = _tree(level=10)
    _level(tree, names, 3)
    assert _actor(tree)["_skills"] == {"@a": [1, 7]}


def test_write_refuses_symlinked_slot(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    shared.mkdir()
    target = shared / "file1.rpgsave"
    target.write_bytes(rs.encode_save(_tree(), "mv"))
    saves = tmp_path / "save"
    saves.mkdir()
    link = saves / "file1.rpgsave"
    os.symlink(target, link)
    original = target.read_bytes()
    stat = os.stat(link)
    called: list[int] = []
    with pytest.raises(rs.RpgmSaveError) as info:
        rs.write_save(
            str(link),
            [{"op": "set_actor", "id": 1, "field": "name", "value": "New"}],
            stat.st_size,
            stat.st_mtime_ns,
            {"classes": {}},
            lambda: called.append(1) or "b",
        )
    assert info.value.code == "write_failed"
    assert not called
    assert link.is_symlink() and target.read_bytes() == original
