# SPDX-License-Identifier: GPL-3.0-or-later
import os

from core.database import (
    add_game,
    add_game_launch_target,
    clear_game_install,
    create_collection,
    get_game,
    init_db,
    list_game_launch_targets,
    repoint_game_paths,
    update_game,
    update_playtime,
)


def test_repoint_game_paths_moves_inside_paths_only(tmp_path):
    init_db()
    old = str(tmp_path / "old" / "Game")
    new = str(tmp_path / "new" / "Game")
    outside = str(tmp_path / "prefixes" / "game")
    game_id = add_game(title="Game", exe_path=os.path.join(old, "bin", "Game.exe"))
    assert game_id is not None
    _ = update_game(
        game_id,
        {
            "cover_image_path": os.path.join(old, "cover.png"),
            "custom_prefix": outside,
            # Exact root: the saves override is the game folder itself.
            "save_path_override": old,
        },
    )
    inside = add_game_launch_target(game_id, "Patch", os.path.join(old, "patch.exe"))
    # A sibling that only shares the name prefix is outside the root.
    sibling = add_game_launch_target(game_id, "Other", old + "-mods/mod.exe")

    result = repoint_game_paths(game_id, old, new)

    row = get_game(game_id)
    assert row is not None
    assert row["exe_path"] == os.path.join(new, "bin", "Game.exe")
    assert row["cover_image_path"] == os.path.join(new, "cover.png")
    assert row["custom_prefix"] == outside
    assert row["save_path_override"] == new
    targets = {t["id"]: t["exe_path"] for t in list_game_launch_targets(game_id)}
    assert targets[inside["id"]] == os.path.join(new, "patch.exe")
    assert targets[sibling["id"]] == old + "-mods/mod.exe"
    assert result["targets"] == 1


def test_repoint_game_paths_keeps_urls_and_prefix_inside(tmp_path):
    init_db()
    old = str(tmp_path / "old")
    new = str(tmp_path / "new")
    game_id = add_game(title="Game", exe_path=os.path.join(old, "Game.exe"))
    assert game_id is not None
    _ = update_game(
        game_id,
        {
            "cover_image_path": "https://attachments.f95zone.to/cover.png",
            "custom_prefix": os.path.join(old, "prefix"),
        },
    )
    _ = repoint_game_paths(game_id, old, new)
    row = get_game(game_id)
    assert row is not None
    assert row["cover_image_path"] == "https://attachments.f95zone.to/cover.png"
    assert row["custom_prefix"] == os.path.join(new, "prefix")


def test_clear_game_install_keeps_library_data(tmp_path):
    init_db()
    game_id = add_game(title="Game", exe_path=str(tmp_path / "Game.exe"))
    assert game_id is not None
    _ = update_game(game_id, {"progress": "Chapter 3", "play_status": "Playing"})
    update_playtime(game_id, 600)
    _ = create_collection("Favorites", game_id)
    _ = add_game_launch_target(game_id, "Patch", str(tmp_path / "patch.exe"))

    clear_game_install(game_id)

    row = get_game(game_id)
    assert row is not None
    assert row["exe_path"] == ""
    assert row["progress"] == "Chapter 3"
    assert row["play_status"] == "Playing"
    assert row["playtime_seconds"] == 600
    assert "Favorites" in str(row["collections"])
    assert list_game_launch_targets(game_id) == []
