# SPDX-License-Identifier: GPL-3.0-or-later
"""H7: uninstall and move must not take another game's saves or Wine prefix.

Game B lives outside Game A's folder, but B's save-folder override or custom
Wine prefix (or the shared Wine prefix) is inside it.
"""

import pytest

import core.game_files as gf
from core.archive_install import other_game_paths
from core.database import add_game, init_db, update_game, update_setting


def _hooks():
    return gf.GameHooks(
        running=lambda _gid: False,
        busy=lambda _gid: None,
        saves_folder=lambda _gid: "",
    )


@pytest.fixture
def lib(tmp_path, monkeypatch):
    init_db()
    library = tmp_path / "Library"
    library.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    ctx = gf.FilesContext(
        library_folders=[str(library)],
        downloads_folder=str(tmp_path / "Downloads"),
        pattern="{creator}/{title}/{version}",
    )
    update_setting("wine_prefix_path", "")
    return library, ctx


def _install(folder, title):
    exe = folder / "Game.exe"
    folder.mkdir(parents=True, exist_ok=True)
    exe.write_bytes(b"MZ")
    game_id = add_game(title=title, exe_path=str(exe), developer="Dev")
    assert game_id is not None
    return game_id


def _setup(lib, tmp_path):
    library, ctx = lib
    a_root = library / "A"
    a_id = _install(a_root, "A")
    b_id = _install(tmp_path / "Elsewhere" / "B", "B")
    inside = a_root / "shared"
    inside.mkdir()
    return ctx, a_id, b_id, a_root, inside


@pytest.mark.parametrize("field", ["save_path_override", "custom_prefix"])
def test_uninstall_refuses_folder_with_other_game_data(lib, tmp_path, field):
    ctx, a_id, b_id, a_root, inside = _setup(lib, tmp_path)
    _ = update_game(b_id, {field: str(inside)})
    entry = gf.plan_uninstall([a_id], {a_id: str(a_root)}, _hooks(), ctx)[0]
    assert entry["removable"] is False
    assert entry["reason"] == "The folder contains another library game."


@pytest.mark.parametrize("field", ["save_path_override", "custom_prefix"])
def test_move_refuses_folder_with_other_game_data(lib, tmp_path, field):
    ctx, a_id, b_id, a_root, inside = _setup(lib, tmp_path)
    _ = update_game(b_id, {field: str(inside)})
    dest = tmp_path / "Dest"
    dest.mkdir()
    entry = gf.plan_move([a_id], str(dest), {a_id: str(a_root)}, _hooks(), ctx, with_sizes=False)[0]
    assert entry["removable"] is False
    assert gf.entry_problem(entry) == "The folder contains another library game."


def test_uninstall_refuses_folder_with_shared_wine_prefix(lib, tmp_path):
    ctx, a_id, _b_id, a_root, inside = _setup(lib, tmp_path)
    update_setting("wine_prefix_path", str(inside))
    entry = gf.plan_uninstall([a_id], {a_id: str(a_root)}, _hooks(), ctx)[0]
    assert entry["removable"] is False


def test_other_game_data_outside_folder_allows_removal(lib, tmp_path):
    ctx, a_id, b_id, a_root, _inside = _setup(lib, tmp_path)
    outside = tmp_path / "Saves" / "B"
    outside.mkdir(parents=True)
    _ = update_game(b_id, {"save_path_override": str(outside), "custom_prefix": str(outside)})
    entry = gf.plan_uninstall([a_id], {a_id: str(a_root)}, _hooks(), ctx)[0]
    assert entry["removable"] is True, entry["reason"]


def test_own_save_override_does_not_block(lib, tmp_path):
    ctx, a_id, _b_id, a_root, inside = _setup(lib, tmp_path)
    _ = update_game(a_id, {"save_path_override": str(inside)})
    assert str(inside) not in other_game_paths(a_id)
    entry = gf.plan_uninstall([a_id], {a_id: str(a_root)}, _hooks(), ctx)[0]
    assert entry["removable"] is True, entry["reason"]
