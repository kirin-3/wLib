# pyright: reportMissingImports=false
# SPDX-License-Identifier: GPL-3.0-or-later
"""Folder-mode export never writes into the game folder (H19)."""

import os
from pathlib import Path

from core.renpy_tools import extract_archives
from tests.test_renpy_tools import build_rpa, make_game, never, no_progress, tree


def test_parent_of_game_as_output_does_not_write_into_game(tmp_path: Path) -> None:
    game = make_game(tmp_path)  # tmp_path / "Game"
    (game / "keep.txt").write_bytes(b"original")
    build_rpa(game / "game" / "Game.rpa", {"keep.txt": b"evil", "a.txt": b"a"})
    before = tree(game)

    result = extract_archives(str(game), "folder", str(tmp_path), no_progress, never)

    assert tree(game) == before
    assert tree(tmp_path / "Game_2") == {"keep.txt": b"evil", "a.txt": b"a"}
    assert result["files"] == 2


def test_export_folder_that_holds_the_game_is_not_used(tmp_path: Path) -> None:
    out = tmp_path / "out"
    game_parent = out / "images"
    game_parent.mkdir(parents=True)
    game = make_game(game_parent)  # out/images/Game
    build_rpa(game / "game" / "images.rpa", {"Game/game/x.txt": b"evil"})
    before = tree(game)

    _ = extract_archives(str(game), "folder", str(out), no_progress, never)

    assert tree(game) == before
    assert (out / "images_2" / "Game" / "game" / "x.txt").read_bytes() == b"evil"


def test_symlinked_export_folder_is_not_written_through(tmp_path: Path) -> None:
    game = make_game(tmp_path)
    build_rpa(game / "game" / "images.rpa", {"a.txt": b"a"})
    out = tmp_path / "rip"
    out.mkdir()
    os.symlink(game / "game", out / "images")
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, out / "images_2")
    before = tree(game)

    _ = extract_archives(str(game), "folder", str(out), no_progress, never)

    assert tree(game) == before
    assert list(outside.iterdir()) == []
    assert (out / "images_3" / "a.txt").read_bytes() == b"a"
