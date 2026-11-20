# SPDX-License-Identifier: GPL-3.0-or-later
"""H5: removing a game folder must not go into a mount inside it.

Real bind mounts need root, so the mount table is faked.
"""

import os
import sys

import pytest

import core.archive_install as ai
import core.game_files as gf
from core.database import add_game, init_db


def _fake_mounts(monkeypatch, points):
    monkeypatch.setattr(ai, "_linux_mount_points", lambda: [*points])


def _fake_ismount(monkeypatch, mounted):
    real_ismount = os.path.ismount
    wanted = {os.path.realpath(p) for p in mounted}
    monkeypatch.setattr(
        ai.os.path,
        "ismount",
        lambda p: os.path.realpath(p) in wanted or real_ismount(p),
    )


def _game_tree(tmp_path):
    root = tmp_path / "Library" / "Game"
    shared = root / "shared"
    shared.mkdir(parents=True)
    (root / "Game.exe").write_bytes(b"MZ")
    (shared / "keep.txt").write_text("not the game's")
    return root, shared


def test_mountinfo_is_parsed_with_escapes(tmp_path, monkeypatch):
    if not sys.platform.startswith("linux"):
        pytest.skip("Linux mount table")
    table = tmp_path / "mountinfo"
    _ = table.write_text(
        "22 1 0:21 / / rw - ext4 /dev/sda1 rw\n"
        + "40 22 0:21 /data /home/u/My\\040Games/G/shared rw - ext4 /dev/sda1 rw\n"
    )
    monkeypatch.setattr(ai, "_MOUNTINFO", str(table))
    assert ai._linux_mount_points() == ["/", "/home/u/My Games/G/shared"]


def test_linux_table_finds_nested_mount(tmp_path, monkeypatch):
    root, shared = _game_tree(tmp_path)
    _fake_mounts(monkeypatch, ["/", str(tmp_path), os.path.realpath(shared)])
    assert ai.mount_points_within(str(root)) == [os.path.realpath(shared)]
    # A mount beside the folder, or above it, does not count.
    assert ai.mount_points_within(str(root / "Game.exe")) == []


def test_walk_fallback_finds_nested_mount(tmp_path, monkeypatch):
    if os.name == "nt":
        pytest.skip("Windows rmtree does not enter mounted volumes")
    root, shared = _game_tree(tmp_path)
    monkeypatch.setattr(ai, "_linux_mount_points", lambda: None)
    _fake_ismount(monkeypatch, [str(shared)])
    assert ai.mount_points_within(str(root)) == [str(shared)]


def test_guard_refuses_folder_with_nested_mount(tmp_path, monkeypatch):
    root, shared = _game_tree(tmp_path)
    _fake_mounts(monkeypatch, [os.path.realpath(shared)])
    guard = ai.can_remove_old_folder(
        str(root), "", [str(tmp_path / "Library")], "", [], str(root / "Game.exe")
    )
    assert guard["removable"] is False
    assert "mounted folder" in str(guard["reason"])


def test_guard_allows_folder_without_mounts(tmp_path, monkeypatch):
    root, _shared = _game_tree(tmp_path)
    _fake_mounts(monkeypatch, ["/"])
    guard = ai.can_remove_old_folder(
        str(root), "", [str(tmp_path / "Library")], "", [], str(root / "Game.exe")
    )
    assert guard == {"removable": True, "reason": ""}


def test_remove_tree_collect_keeps_tree_with_nested_mount(tmp_path, monkeypatch):
    root, shared = _game_tree(tmp_path)
    _fake_mounts(monkeypatch, [os.path.realpath(shared)])
    errors = ai.remove_tree_collect(str(root))
    assert errors and "mounted folder" in errors[0]
    assert (shared / "keep.txt").read_text() == "not the game's"
    assert (root / "Game.exe").exists()


def test_remove_tree_collect_still_removes_plain_tree(tmp_path, monkeypatch):
    root, _shared = _game_tree(tmp_path)
    _fake_mounts(monkeypatch, ["/"])
    assert ai.remove_tree_collect(str(root)) == []
    assert not root.exists()


def test_uninstall_refuses_game_with_nested_mount(tmp_path, monkeypatch):
    init_db()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    root, shared = _game_tree(tmp_path)
    game_id = add_game(title="Game", exe_path=str(root / "Game.exe"), developer="Dev")
    assert game_id is not None
    _fake_mounts(monkeypatch, [os.path.realpath(shared)])
    ctx = gf.FilesContext(
        library_folders=[str(tmp_path / "Library")],
        downloads_folder=str(tmp_path / "Downloads"),
        pattern="{title}",
    )
    hooks = gf.UninstallHooks(
        running=lambda _gid: False,
        busy=lambda _gid: None,
        saves_folder=lambda _gid: "",
    )
    results = gf.run_uninstall(
        [game_id],
        {game_id: str(root)},
        backup=False,
        remove_from_library=False,
        hooks=hooks,
        ctx=ctx,
    )
    assert results[0]["success"] is False
    assert "mounted folder" in str(results[0]["error"])
    assert (shared / "keep.txt").read_text() == "not the game's"
