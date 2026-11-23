# SPDX-License-Identifier: GPL-3.0-or-later
"""Archive-install bug hunt, round 2.

1. A crash after saves were carried into a moved torrent download: the next
   cleanup takes the carried saves out again before it puts the download back.
2. RAR part names whose numbers differ only in zero padding (`part01` and
   `part1`) are refused, never swapped for each other.
"""

import json
import os
import time
from pathlib import Path

import pytest

import core.archive_install as ai
from core.archive_install import ArchiveInstallError, check_volume_set, volume_set
from core.downloads import torrents
from core.downloads.torrents import TorrentSupervisor

# --- 1. crash recovery of a moved download with carried saves ---------------


@pytest.fixture
def env(tmp_path, monkeypatch):
    import core.database as database

    monkeypatch.setenv("WLIB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "db.sqlite"))
    database.init_db()
    sup = TorrentSupervisor(
        command=lambda: (["true"], {}), free_space_fn=lambda p: 10**12, options=dict
    )
    monkeypatch.setattr(torrents, "supervisor", sup)
    return sup


def _old_game(tmp_path, override=False):
    import core.database as database

    old = tmp_path / "lib" / "Game" / "1.0"
    (old / "game" / "saves").mkdir(parents=True)
    (old / "Game.exe").write_text("old exe")
    (old / "slot1.sav").write_text("player save")
    (old / "game" / "saves" / "1-1.save").write_text("renpy save")
    game_id = database.add_game("Game", str(old / "Game.exe"), version="1.0")
    if override:
        (old / "www" / "save").mkdir(parents=True)
        (old / "www" / "save" / "file1.rpgsave").write_text("player rpg save")
        (old / "www" / "save" / "config.rpgsave").write_text("player config")
        database.update_game(game_id, {"save_path_override": str(old / "www" / "save")})
    return old, game_id


def _download(tmp_path, override=False):
    folder = tmp_path / "data" / "downloads" / "torrents" / ("ab" * 20)
    game = folder / "data" / "Game-2.0-pc"
    game.mkdir(parents=True)
    (game / "Game.exe").write_text("new exe")
    (game / "slot1.sav").write_text("download save")
    if override:
        (game / "www" / "save").mkdir(parents=True)
        (game / "www" / "save" / "config.rpgsave").write_text("download config")
    (folder / "state.json").write_text(
        json.dumps(
            {
                "job_id": "j1",
                "game_id": 1,
                "size": 10,
                "phase": "ended",
                "updated": time.time(),
            }
        )
    )
    return folder, game


def _files(root):
    return {
        str(p.relative_to(root)): p.read_text()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _dirs(root):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_dir())


class _Crash(BaseException):
    """Stands in for the app stopping: no except Exception handler runs."""


def _install(tmp_path, folder, game_id, **extra):
    return ai.install_archive(
        str(folder / "data"),
        library_folder=str(tmp_path / "lib"),
        pattern="{title}/{version}",
        title="Game",
        version="2.0",
        game_id=game_id,
        source_kind="folder",
        remove_old=False,
        **extra,
    )


def _crash_at_moving_into_place(stage):
    if stage == "moving into place":
        raise _Crash()


def _staging(tmp_path):
    lib = tmp_path / "lib"
    stagings = [
        p for p in lib.iterdir() if p.is_dir() and p.name.startswith(ai.STAGING_PREFIX)
    ]
    assert len(stagings) == 1
    return stagings[0]


@pytest.mark.parametrize("override", [False, True])
def test_crash_after_carrying_saves_cleanup_returns_the_download_unchanged(
    tmp_path, env, override
):
    old, game_id = _old_game(tmp_path, override=override)
    folder, game = _download(tmp_path, override=override)
    env.load()
    before = _files(game)
    before_dirs = _dirs(game)

    with pytest.raises(_Crash):
        _install(tmp_path, folder, game_id, progress_cb=_crash_at_moving_into_place)
    staging = _staging(tmp_path)
    # The crash left the carried saves in the moved download.
    assert (staging / "slot1.sav").read_text() == "player save"

    assert ai.cleanup_stale_staging(str(tmp_path / "lib")) == 0
    assert _files(game) == before
    assert _dirs(game) == before_dirs
    assert [p.name for p in game.iterdir() if p.name.startswith(".wlib")] == []
    assert not staging.exists()
    assert not Path(str(staging) + ai.MOVED_RECORD_SUFFIX).exists()
    assert (old / "slot1.sav").read_text() == "player save"
    assert not (tmp_path / "lib" / "Game" / "2.0").exists()


def test_crash_in_the_middle_of_carrying_saves_is_undone(tmp_path, env, monkeypatch):
    _old, game_id = _old_game(tmp_path)
    folder, game = _download(tmp_path)
    env.load()
    before = _files(game)
    real_copy = ai._SaveCarryUndo.copy  # pyright: ignore[reportPrivateUsage]
    calls = {"n": 0}

    def copy_then_crash(self, src, dst):
        calls["n"] += 1
        if calls["n"] == 2:
            # The original was moved aside, the copy never ran.
            self.before_write(
                os.path.join(dst, os.path.basename(src)) if os.path.isdir(dst) else dst
            )
            raise _Crash()
        return real_copy(self, src, dst)

    monkeypatch.setattr(ai._SaveCarryUndo, "copy", copy_then_crash)  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(_Crash):
        _install(tmp_path, folder, game_id)
    monkeypatch.undo()

    assert ai.cleanup_stale_staging(str(tmp_path / "lib")) == 0
    assert _files(game) == before
    assert [p.name for p in game.iterdir() if p.name.startswith(".wlib")] == []


def test_recovery_that_cannot_undo_the_saves_keeps_staging_and_retries(
    tmp_path, env, monkeypatch
):
    _old, game_id = _old_game(tmp_path)
    folder, game = _download(tmp_path)
    env.load()
    before = _files(game)

    with pytest.raises(_Crash):
        _install(tmp_path, folder, game_id, progress_cb=_crash_at_moving_into_place)
    staging = _staging(tmp_path)

    real_remove = os.remove

    def refuse_carried_save(path, *args, **kwargs):
        if os.path.basename(str(path)) == "1-1.save":
            raise PermissionError("busy")
        return real_remove(path, *args, **kwargs)

    monkeypatch.setattr(ai.os, "remove", refuse_carried_save)
    assert ai.cleanup_stale_staging(str(tmp_path / "lib")) == 0
    monkeypatch.undo()
    # Nothing went back half-done: the download waits in staging.
    assert not game.exists()
    assert staging.is_dir()
    assert Path(str(staging) + ai.MOVED_RECORD_SUFFIX).is_file()

    # The next cleanup finishes the job.
    assert ai.cleanup_stale_staging(str(tmp_path / "lib")) == 0
    assert _files(game) == before
    assert not staging.exists()


def test_successful_install_after_carrying_saves_keeps_them(tmp_path, env):
    _old, game_id = _old_game(tmp_path)
    folder, _game = _download(tmp_path)
    env.load()
    result = _install(tmp_path, folder, game_id)
    dest = tmp_path / "lib" / "Game" / "2.0"
    assert result["success"] is True
    assert (dest / "slot1.sav").read_text() == "player save"
    assert (dest / "game" / "saves" / "1-1.save").read_text() == "renpy save"
    assert [p.name for p in dest.iterdir() if p.name.startswith(".wlib")] == []
    assert ai.cleanup_stale_staging(str(tmp_path / "lib")) == 0
    assert (dest / "slot1.sav").read_text() == "player save"
    assert sorted(p.name for p in (tmp_path / "lib").iterdir()) == ["Game"]


def test_recorded_undo_entries_cannot_leave_the_staging_folder(tmp_path):
    lib = tmp_path / "lib"
    staging = Path(ai.create_staging_dir(str(lib)))
    (staging / "Game.exe").write_text("only copy")
    outside = tmp_path / "outside.txt"
    outside.write_text("keep")
    source = tmp_path / "source"
    ai.write_moved_record(str(staging), str(source))
    record_path = Path(str(staging) + ai.MOVED_RECORD_SUFFIX)
    record = json.loads(record_path.read_text())
    record["undo"] = {
        "folder": ".wlib-undo-1",
        "created": [os.path.join(os.pardir, os.pardir, "outside.txt")],
        "saved": [],
    }
    record_path.write_text(json.dumps(record))

    assert ai.cleanup_stale_staging(str(lib)) == 0
    assert outside.read_text() == "keep"
    # A record that cannot be trusted keeps the download where it is.
    assert (staging / "Game.exe").read_text() == "only copy"
    assert not source.exists()


# --- 2. RAR part numbers with different zero padding ------------------------


def _write(folder, names):
    for name in names:
        (folder / name).write_bytes(b"x")


@pytest.mark.parametrize("chosen", ["Game.part01.rar", "Game.part1.rar"])
def test_padding_variants_of_one_part_number_are_refused(tmp_path, chosen):
    _write(tmp_path, ["Game.part01.rar", "Game.part1.rar", "Game.part2.rar"])

    volumes = volume_set(str(tmp_path / chosen))

    assert volumes is not None
    assert not volumes.complete
    assert sorted(volumes.ambiguous) == sorted(
        str(tmp_path / n) for n in ("Game.part01.rar", "Game.part1.rar")
    )
    # Neither first part stands in for the other.
    assert str(tmp_path / "Game.part01.rar") not in volumes.parts
    assert str(tmp_path / "Game.part1.rar") not in volumes.parts
    with pytest.raises(ArchiveInstallError, match="zeros of the part number"):
        check_volume_set(volumes)


@pytest.mark.parametrize(
    ("names", "chosen"),
    [
        (["Game.part1.rar", "Game.part2.rar", "Game.part02.rar"], "Game.part1.rar"),
        (["Game.rar", "Game.r00", "Game.r000"], "Game.rar"),
        (["Game.zip", "Game.z01", "Game.z001"], "Game.zip"),
    ],
)
def test_padding_variants_of_any_part_are_refused(tmp_path, names, chosen):
    _write(tmp_path, names)

    volumes = volume_set(str(tmp_path / chosen))

    assert volumes is not None
    assert not volumes.complete
    assert volumes.ambiguous
    with pytest.raises(ArchiveInstallError, match="zeros of the part number"):
        check_volume_set(volumes)


def test_install_refuses_padding_variants_and_deletes_nothing(tmp_path):
    names = ["Game.part01.rar", "Game.part1.rar", "Game.part2.rar"]
    _write(tmp_path, names)
    library = tmp_path / "lib"
    library.mkdir()

    with pytest.raises(ArchiveInstallError, match="zeros of the part number"):
        ai.install_archive(
            str(tmp_path / "Game.part01.rar"),
            library_folder=str(library),
            pattern="{title}",
            title="Game",
            delete_archive=True,
        )

    assert all((tmp_path / name).exists() for name in names)
    assert list(library.iterdir()) == []


def test_padded_set_alone_still_forms_one_set(tmp_path):
    names = [f"Game.part{n:02d}.rar" for n in range(1, 12)]
    _write(tmp_path, names)

    volumes = volume_set(str(tmp_path / "Game.part05.rar"))

    assert volumes is not None
    assert volumes.complete
    assert volumes.first == str(tmp_path / "Game.part01.rar")
    assert sorted(volumes.parts) == sorted(str(tmp_path / n) for n in names)


def _cancel_after_carry_with_a_locked_save(tmp_path, folder, game_id, monkeypatch):
    """Cancel after the saves are carried in; one carried save stays locked."""
    import threading

    cancel = threading.Event()
    real_copy = ai.copy_infolder_saves
    real_remove = os.remove

    def copy_then_cancel(*args, **kwargs):
        result = real_copy(*args, **kwargs)
        cancel.set()
        return result

    def locked(path, *args, **kwargs):
        # A carried save held open (antivirus, Explorer) cannot be removed.
        if os.path.basename(str(path)) == "1-1.save":
            raise PermissionError("busy")
        return real_remove(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(ai, "copy_infolder_saves", copy_then_cancel)
        patch.setattr(ai.os, "remove", locked)
        with pytest.raises(ai.InstallCancelled):
            _install(tmp_path, folder, game_id, cancel=cancel)


def test_retry_after_a_failed_save_rollback_gets_the_download_back(
    tmp_path, env, monkeypatch
):
    old, game_id = _old_game(tmp_path)
    folder, game = _download(tmp_path)
    env.load()
    before = _files(game)
    _cancel_after_carry_with_a_locked_save(tmp_path, folder, game_id, monkeypatch)
    # The download waits in staging; its torrent folder is gone for now.
    assert not game.exists()
    _staging(tmp_path)

    # Retrying the same download puts it back first, then installs it.
    result = _install(tmp_path, folder, game_id)
    assert result["success"] is True
    dest = tmp_path / "lib" / "Game" / "2.0"
    assert (dest / "slot1.sav").read_text() == "player save"
    assert (old / "slot1.sav").read_text() == "player save"
    assert [p for p in (tmp_path / "lib").iterdir() if p.name.startswith(".wlib")] == []
    assert before  # the download had files of its own


def test_recover_moved_download_puts_back_only_its_own_download(
    tmp_path, env, monkeypatch
):
    _old, game_id = _old_game(tmp_path)
    folder, game = _download(tmp_path)
    env.load()
    before = _files(game)
    _cancel_after_carry_with_a_locked_save(tmp_path, folder, game_id, monkeypatch)
    staging = _staging(tmp_path)
    other = tmp_path / "data" / "downloads" / "torrents" / ("cd" * 20) / "data"

    # Another download's path finds nothing and touches nothing.
    assert ai.recover_moved_download(str(other), [str(tmp_path / "lib")]) is False
    assert staging.is_dir()

    assert ai.recover_moved_download(str(folder / "data"), [str(tmp_path / "lib")])
    assert _files(game) == before
    assert not staging.exists()
    # A download that is already back is left alone.
    assert not ai.recover_moved_download(str(folder / "data"), [str(tmp_path / "lib")])


def test_inspect_puts_a_stuck_download_back_only_when_no_install_runs(
    tmp_path, env, monkeypatch
):
    from core.api import Api

    _old, game_id = _old_game(tmp_path)
    folder, game = _download(tmp_path)
    env.load()
    before = _files(game)
    _cancel_after_carry_with_a_locked_save(tmp_path, folder, game_id, monkeypatch)
    api = Api()
    lib = str(tmp_path / "lib")

    api._archive_install_status["running"] = True
    assert (
        api.inspect_archive(str(folder / "data"), game_id, None, lib)["success"]
        is False
    )
    assert not game.exists()

    api._archive_install_status["running"] = False
    result = api.inspect_archive(str(folder / "data"), game_id, None, lib)
    assert result["success"] is True, result
    assert result["source_kind"] == "folder"
    assert _files(game) == before


def test_a_carried_save_folder_is_journalled_in_one_write(tmp_path):
    root = tmp_path / "staging"
    (root / "saves").mkdir(parents=True)
    (root / "saves" / "file0.sav").write_text("download save")
    source = tmp_path / "old" / "saves"
    source.mkdir(parents=True)
    for index in range(200):
        (source / f"file{index}.sav").write_text("player save")
    writes = []
    undo = ai._SaveCarryUndo(str(root), journal=writes.append)

    undo.before_tree(str(source), str(root / "saves"))

    # One record write names every file before any of them changes.
    assert len(writes) == 1
    assert writes[0]["saved"] == [os.path.join("saves", "file0.sav")]
    assert len(writes[0]["created"]) == 199
    # The copy step then needs no more journal writes.
    for index in range(200):
        _ = undo.copy(str(source / f"file{index}.sav"), str(root / "saves"))
    assert len(writes) == 1
    assert undo.rollback()
    assert sorted(p.name for p in (root / "saves").iterdir()) == ["file0.sav"]
    assert (root / "saves" / "file0.sav").read_text() == "download save"


def test_a_failed_journal_write_changes_nothing(tmp_path):
    root = tmp_path / "staging"
    (root / "saves").mkdir(parents=True)
    (root / "saves" / "slot.sav").write_text("download save")
    source = tmp_path / "old" / "saves"
    source.mkdir(parents=True)
    (source / "slot.sav").write_text("player save")

    def disk_full(_undo):
        raise OSError(28, "No space left on device")

    undo = ai._SaveCarryUndo(str(root), journal=disk_full)
    with pytest.raises(OSError):
        undo.before_tree(str(source), str(root / "saves"))
    assert (root / "saves" / "slot.sav").read_text() == "download save"
    assert [p.name for p in root.iterdir()] == ["saves"]
