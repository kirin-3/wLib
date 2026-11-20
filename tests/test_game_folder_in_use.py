"""A game still running from an earlier wLib window blocks move and uninstall."""

import os
import subprocess
import sys

import pytest

import core.game_files as gf
from core.database import add_game, init_db

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="the /proc check is Linux only"
)


def _hooks():
    return gf.GameHooks(running=lambda _gid: False, busy=lambda _gid: None)


@pytest.fixture
def lib(tmp_path, monkeypatch):
    init_db()
    library = tmp_path / "Library"
    library.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    ctx = gf.FilesContext(
        library_folders=[str(library)],
        downloads_folder=str(tmp_path / "Downloads"),
        pattern="{creator}/{title}/{version}",
    )
    return library, ctx


def _install(folder):
    exe = folder / "Game.exe"
    folder.mkdir(parents=True)
    exe.write_bytes(b"MZ")
    game_id = add_game(title="Game", exe_path=str(exe), developer="Dev")
    assert game_id is not None
    return game_id


def _fake_proc(tmp_path, *, exe="", cwd="", cmdline=()):
    proc = tmp_path / "proc"
    entry = proc / "4242"
    entry.mkdir(parents=True)
    if exe:
        os.symlink(exe, entry / "exe")
    if cwd:
        os.symlink(cwd, entry / "cwd")
    (entry / "cmdline").write_bytes(b"\0".join(w.encode() for w in cmdline) + b"\0")
    return str(proc)


def test_folder_in_use_reads_exe_cwd_and_wine_paths(tmp_path):
    game = tmp_path / "Game"
    (game / "sub").mkdir(parents=True)
    other = tmp_path / "Other"
    other.mkdir()

    assert gf.folder_in_use(str(game), _fake_proc(tmp_path / "a", exe=str(game / "sub" / "run (deleted)")))
    assert gf.folder_in_use(str(game), _fake_proc(tmp_path / "b", cwd=str(game)))
    wine_word = "Z:" + str(game / "Game.exe").replace("/", "\\")
    assert gf.folder_in_use(
        str(game), _fake_proc(tmp_path / "c", exe="/usr/bin/wine64-preloader", cmdline=("wine", wine_word))
    )
    assert not gf.folder_in_use(
        str(game), _fake_proc(tmp_path / "d", exe="/usr/bin/x", cwd=str(other), cmdline=(str(other / "a"),))
    )
    # A sibling folder whose name starts with the same text is not inside.
    assert not gf.folder_in_use(str(game), _fake_proc(tmp_path / "e", cwd=str(tmp_path / "Game2")))


def test_plans_block_a_folder_a_live_process_uses(lib):
    library, ctx = lib
    game_id = _install(library / "Game")
    # Stands in for a game the previous wLib window started: wLib has no
    # record of it, but it still runs inside the folder.
    proc = subprocess.Popen(["sleep", "30"], cwd=str(library / "Game"))
    try:
        uninstall = gf.plan_uninstall([game_id], {}, _hooks(), ctx)[0]
        move = gf.plan_move([game_id], str(library), {}, _hooks(), ctx, with_sizes=False)[0]
        assert uninstall["blocked_reason"] == gf.FOLDER_IN_USE
        assert move["blocked_reason"] == gf.FOLDER_IN_USE
    finally:
        proc.kill()
        _ = proc.wait()
    assert gf.plan_uninstall([game_id], {}, _hooks(), ctx)[0]["blocked_reason"] == ""
