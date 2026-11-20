# SPDX-License-Identifier: GPL-3.0-or-later
"""Folder import skips move/uninstall work folders and finds the right launcher."""

from __future__ import annotations

from pathlib import Path

import pytest

import core.game_files as game_files
import core.game_scan as game_scan


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()


@pytest.mark.parametrize("prefix", [game_files.MOVE_PREFIX, game_files.UNINSTALL_PREFIX])
def test_scan_skips_move_and_uninstall_work_folders(tmp_path, prefix):
    _touch(tmp_path / f"{prefix}123456" / "Game.exe")
    _touch(tmp_path / "Real Game" / "Real.exe")

    games = game_scan.scan_games_folder(str(tmp_path))

    assert [g["exe_path"] for g in games] == [str(tmp_path / "Real Game" / "Real.exe")]


def test_find_launcher_skips_a_move_folder_below_the_game(tmp_path):
    _touch(tmp_path / f"{game_files.MOVE_PREFIX}1" / "Copy.exe")
    _touch(tmp_path / "Inner" / "Inner.exe")

    assert game_scan.find_launcher(str(tmp_path)) == str(tmp_path / "Inner" / "Inner.exe")


def test_lone_32_bit_launcher_wins_over_a_web_page(tmp_path):
    folder = tmp_path / "Game"
    _touch(folder / "Game-32.exe")
    _touch(folder / "index.html")

    assert game_scan.find_launcher(str(folder)) == str(folder / "Game-32.exe")


def test_32_bit_twin_still_loses_to_its_64_bit_build(tmp_path):
    folder = tmp_path / "Game"
    _touch(folder / "Game-32.exe")
    _touch(folder / "Game.exe")
    _touch(folder / "index.html")

    assert game_scan.find_launcher(str(folder)) == str(folder / "Game.exe")


def test_launcher_found_when_the_search_uses_exactly_its_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(game_scan, "_MAX_SCANNED_DIRS", 4)
    # The root plus three subfolders: four directories, all of them read.
    for index in range(3):
        (tmp_path / f"d{index}").mkdir()
    _touch(tmp_path / "d2" / "Game.exe")

    assert game_scan.find_launcher(str(tmp_path)) == str(tmp_path / "d2" / "Game.exe")


def test_launcher_rejected_when_a_folder_was_not_read(tmp_path, monkeypatch):
    monkeypatch.setattr(game_scan, "_MAX_SCANNED_DIRS", 3)
    for index in range(3):
        (tmp_path / f"d{index}").mkdir()
    _touch(tmp_path / "d0" / "Game.exe")

    assert game_scan.find_launcher(str(tmp_path)) == ""
