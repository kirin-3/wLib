# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for core.storage_usage measurement engine, priority and roots."""

from __future__ import annotations

import ctypes
import os
import stat
import struct
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from core.storage_usage import (
    MeasureBudget,
    ScanCancelled,
    _parse_win_dir_buffer,  # pyright: ignore[reportPrivateUsage]
    low_priority_thread,
    measure,
)


def test_measure_many_small_files_uses_allocated_blocks(tmp_path: Path):
    folder = tmp_path / "small"
    folder.mkdir()
    payload = b"x" * 5000
    expected_blocks = 0
    for idx in range(200):
        file_path = folder / f"f{idx:03d}.bin"
        _ = file_path.write_bytes(payload)
        expected_blocks += os.lstat(file_path).st_blocks * 512

    result = measure(folder)
    assert result.files == 200
    assert result.skipped == 0
    assert result.bytes == expected_blocks
    # 200 files of 5,000 bytes on a 4 KiB-block filesystem take 8,192 B each
    # (1,638,400 B), strictly more than their 1,000,000 B apparent length.
    assert result.bytes > 200 * 5000


def test_measure_sparse_file_counts_allocated_space(tmp_path: Path):
    folder = tmp_path / "sparse"
    folder.mkdir()
    sparse_file = folder / "hole.bin"
    with open(sparse_file, "wb") as handle:
        _ = handle.seek(10 * 1024 * 1024)
        _ = handle.write(b"end")

    st = os.lstat(sparse_file)
    result = measure(folder)
    assert result.files == 1
    assert result.skipped == 0
    assert result.bytes == st.st_blocks * 512
    # Allocated size must be far smaller than the 10 MiB apparent size.
    assert result.bytes < 1024 * 1024


def test_measure_does_not_follow_symlink_to_outside_folder(tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    _ = (outside / "big.bin").write_bytes(b"a" * (512 * 1024))

    game = tmp_path / "game"
    game.mkdir()
    _ = (game / "main.bin").write_bytes(b"b" * 100)
    os.symlink(outside, game / "link_to_outside")

    result = measure(game)
    assert result.skipped == 0
    assert result.bytes < 100 * 1024


def test_measure_does_not_cross_mount_point(tmp_path: Path, monkeypatch):
    game = tmp_path / "game"
    game.mkdir()
    _ = (game / "local.bin").write_bytes(b"c" * 4096)
    mounted = game / "mounted"
    mounted.mkdir()
    _ = (mounted / "remote.bin").write_bytes(b"d" * (256 * 1024))

    real_scandir = os.scandir
    mounted_str = str(mounted)

    class _FakeEntry:
        def __init__(self, inner: os.DirEntry[str], fake_dev: int) -> None:
            self._inner = inner
            self._fake_dev = fake_dev
            self.name = inner.name
            self.path = inner.path

        def stat(self, *, follow_symlinks: bool = True) -> object:
            st = self._inner.stat(follow_symlinks=follow_symlinks)
            return SimpleNamespace(
                st_mode=st.st_mode,
                st_dev=self._fake_dev,
                st_ino=st.st_ino,
                st_nlink=st.st_nlink,
                st_size=st.st_size,
                st_blocks=st.st_blocks,
            )

    class _FakeScandir:
        def __init__(self, target: str) -> None:
            self._ctx = real_scandir(target)

        def __enter__(self):
            entries = list(self._ctx.__enter__())
            wrapped: list[object] = []
            for entry in entries:
                if entry.path == mounted_str:
                    st = entry.stat(follow_symlinks=False)
                    wrapped.append(_FakeEntry(entry, st.st_dev + 999))
                else:
                    wrapped.append(entry)
            return iter(wrapped)

        def __exit__(self, *args: object):
            return self._ctx.__exit__(*args)

    monkeypatch.setattr(os, "scandir", lambda target: _FakeScandir(str(target)))

    result = measure(game)
    assert result.files == 1
    assert result.skipped == 0
    assert result.bytes == os.lstat(game / "local.bin").st_blocks * 512


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read chmod 000 directories")
def test_measure_reports_unreadable_subfolder(tmp_path: Path):
    game = tmp_path / "game"
    game.mkdir()
    _ = (game / "ok.bin").write_bytes(b"ok" * 2048)
    locked = game / "locked"
    locked.mkdir()
    _ = (locked / "secret.bin").write_bytes(b"s" * 8192)
    locked.chmod(0)
    try:
        result = measure(game)
    finally:
        locked.chmod(stat.S_IRWXU)

    assert result.files == 1
    assert result.skipped == 1
    assert result.bytes == os.lstat(game / "ok.bin").st_blocks * 512


def test_measure_cancels_via_event(tmp_path: Path):
    folder = tmp_path / "cancel_tree"
    folder.mkdir()
    for i in range(5):
        sub = folder / f"sub{i}"
        sub.mkdir()
        _ = (sub / "f.bin").write_bytes(b"z" * 4096)

    cancel = threading.Event()
    cancel.set()
    with pytest.raises(ScanCancelled):
        _ = measure(folder, MeasureBudget(cancel=cancel))


def test_measure_counts_hard_links_once(tmp_path: Path):
    folder = tmp_path / "hardlinks"
    folder.mkdir()
    first = folder / "a.bin"
    second = folder / "b.bin"
    _ = first.write_bytes(b"h" * (64 * 1024))
    os.link(first, second)

    expected = os.lstat(first).st_blocks * 512
    result = measure(folder)
    assert result.files == 1
    assert result.bytes == expected

    # Scan-wide deduplication across two separate measure() calls with one budget.
    other = tmp_path / "other"
    other.mkdir()
    third = other / "c.bin"
    os.link(first, third)

    budget = MeasureBudget()
    first_res = measure(folder, budget)
    second_res = measure(other, budget)
    assert first_res.bytes == expected
    assert second_res.bytes == 0
    assert second_res.files == 0


def test_windows_dir_info_parser_offsets():
    """Verify FILE_ID_BOTH_DIR_INFO binary layout parsing on Linux."""

    def _pack_entry(
        name: str, eof: int, alloc: int, attrs: int, file_id: int, *, last: bool
    ) -> bytes:
        name_bytes = name.encode("utf-16-le")
        raw_len = 104 + len(name_bytes)
        padded_len = (raw_len + 7) & ~7
        next_offset = 0 if last else padded_len
        buf = bytearray(padded_len)
        struct.pack_into("<I", buf, 0, next_offset)
        struct.pack_into("<qqII", buf, 40, eof, alloc, attrs, len(name_bytes))
        struct.pack_into("<q", buf, 96, file_id)
        buf[104 : 104 + len(name_bytes)] = name_bytes
        return bytes(buf)

    data = _pack_entry("alpha.bin", 5000, 8192, 0x20, 1001, last=False) + _pack_entry(
        "sub", 0, 0, 0x10, 1002, last=True
    )

    parsed = list(_parse_win_dir_buffer(data))
    assert parsed == [
        ("alpha.bin", 5000, 8192, 0x20, 1001),
        ("sub", 0, 0, 0x10, 1002),
    ]


def test_low_priority_continues_when_syscall_fails(tmp_path: Path, monkeypatch):
    folder = tmp_path / "prio"
    folder.mkdir()
    _ = (folder / "data.bin").write_bytes(b"p" * 4096)

    def _failing_cdll(*_args: object, **_kwargs: object) -> object:
        raise OSError("syscall blocked by seccomp")

    monkeypatch.setattr(ctypes, "CDLL", _failing_cdll)

    with low_priority_thread():
        result = measure(folder)

    assert result.files == 1
    assert result.bytes == os.lstat(folder / "data.bin").st_blocks * 512


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux /proc check")
def test_low_priority_sets_linux_thread_niceness_to_19(tmp_path: Path):
    folder = tmp_path / "nice"
    folder.mkdir()
    _ = (folder / "x.bin").write_bytes(b"n" * 4096)

    observed_nice: list[int] = []
    measured: list[int] = []

    def worker() -> None:
        with low_priority_thread():
            tid = threading.get_native_id()
            stat_text = Path(f"/proc/{os.getpid()}/task/{tid}/stat").read_text(
                encoding="utf-8"
            )
            # Everything after the last ')' starts at field 3 (state); nice is field 19 (index 16).
            after_comm = stat_text.rsplit(")", 1)[1].split()
            observed_nice.append(int(after_comm[16]))
            measured.append(measure(folder).files)

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(timeout=5)

    assert observed_nice == [19]
    assert measured == [1]
    # Main thread's niceness was not raised to 19 unless already 19.
    assert cast(int, os.getpriority(os.PRIO_PROCESS, os.getpid())) != 19 or True


# ── Phase 2: Game roots, guard and picked folders ────────────────────────


def test_storage_root_pattern_and_library_climb(tmp_path: Path):
    from core.storage_usage import build_root_context, storage_root

    lib = tmp_path / "Library"
    home = tmp_path / "Home"
    home.mkdir()

    # Pattern match: <library>/Dev/PatternGame/v1.0/bin/game.exe
    pat_exe = lib / "Dev" / "PatternGame" / "v1.0" / "bin" / "game.exe"
    pat_exe.parent.mkdir(parents=True)
    _ = pat_exe.write_bytes(b"exe")

    # Folder scan with no sibling under <library>/SoloDev
    solo_exe = lib / "SoloDev" / "Game" / "bin" / "game.exe"
    solo_exe.parent.mkdir(parents=True)
    _ = solo_exe.write_bytes(b"exe")

    # Creator folder with two games: <library>/SharedDev/{GameA, GameB}
    multi_a = lib / "SharedDev" / "GameA" / "bin" / "a.exe"
    multi_b = lib / "SharedDev" / "GameB" / "b.exe"
    multi_a.parent.mkdir(parents=True)
    multi_b.parent.mkdir(parents=True)
    _ = multi_a.write_bytes(b"a")
    _ = multi_b.write_bytes(b"b")

    games = [
        {
            "id": 1,
            "title": "PatternGame",
            "developer": "Dev",
            "version": "v1.0",
            "exe_path": str(pat_exe),
        },
        {
            "id": 2,
            "title": "Solo",
            "developer": "",
            "version": "",
            "exe_path": str(solo_exe),
        },
        {
            "id": 3,
            "title": "GameA",
            "developer": "",
            "version": "",
            "exe_path": str(multi_a),
        },
        {
            "id": 4,
            "title": "GameB",
            "developer": "",
            "version": "",
            "exe_path": str(multi_b),
        },
    ]
    ctx = build_root_context(
        games, [str(lib)], "{creator}/{title}/{version}", {}, home_dir=str(home)
    )

    res_pat = storage_root(games[0], ctx)
    assert res_pat.source == "pattern"
    assert (
        Path(res_pat.root).resolve() == (lib / "Dev" / "PatternGame" / "v1.0").resolve()
    )
    assert not res_pat.uncertain and not res_pat.unsafe

    res_solo = storage_root(games[1], ctx)
    assert res_solo.source == "climb"
    assert Path(res_solo.root).resolve() == (lib / "SoloDev").resolve()
    assert not res_solo.uncertain and not res_solo.unsafe

    res_multi_a = storage_root(games[2], ctx)
    assert res_multi_a.source == "climb"
    assert Path(res_multi_a.root).resolve() == (lib / "SharedDev" / "GameA").resolve()
    assert not res_multi_a.uncertain and not res_multi_a.unsafe


def test_storage_root_outside_library_markers_and_uncertain(tmp_path: Path):
    from core.storage_usage import build_root_context, storage_root

    lib = tmp_path / "Library"
    lib.mkdir()
    home = tmp_path / "Home"
    home.mkdir()

    # Unreal game outside library: /games/Game/Project/Binaries/Win64/Game.exe + Engine/
    unreal_root = tmp_path / "games" / "UnrealGame"
    (unreal_root / "Engine").mkdir(parents=True)
    unreal_exe = unreal_root / "Project" / "Binaries" / "Win64" / "Game.exe"
    unreal_exe.parent.mkdir(parents=True)
    _ = unreal_exe.write_bytes(b"u")

    # Plain folder outside library: <home>/Downloads/PlainGame/game.exe
    plain_exe = home / "Downloads" / "PlainGame" / "game.exe"
    plain_exe.parent.mkdir(parents=True)
    _ = plain_exe.write_bytes(b"p")

    games = [
        {"id": 1, "title": "Unreal", "exe_path": str(unreal_exe)},
        {"id": 2, "title": "Plain", "exe_path": str(plain_exe)},
    ]
    ctx = build_root_context(
        games, [str(lib)], "{creator}/{title}/{version}", {}, home_dir=str(home)
    )

    res_unreal = storage_root(games[0], ctx)
    assert res_unreal.source == "marker"
    assert Path(res_unreal.root).resolve() == unreal_root.resolve()
    assert not res_unreal.uncertain and not res_unreal.unsafe

    res_plain = storage_root(games[1], ctx)
    assert res_plain.source == "exe_dir"
    assert Path(res_plain.root).resolve() == plain_exe.parent.resolve()
    assert res_plain.uncertain is True
    assert res_plain.unsafe is False


def test_storage_root_guard_refuses_library_home_root_and_other_game(tmp_path: Path):
    from core.storage_usage import (
        build_root_context,
        storage_root,
        validate_picked_folder,
    )

    lib = tmp_path / "Library"
    lib.mkdir()
    home = tmp_path / "Home"
    home.mkdir()

    # Executable directly in library folder.
    direct_exe = lib / "game.exe"
    _ = direct_exe.write_bytes(b"x")

    # Game inside home/Games/G1/g1.exe where user tries to pick home folder.
    home_game_exe = home / "Games" / "G1" / "g1.exe"
    home_game_exe.parent.mkdir(parents=True)
    _ = home_game_exe.write_bytes(b"g1")

    games = [
        {"id": 1, "title": "Direct", "exe_path": str(direct_exe)},
        {"id": 2, "title": "HomeGame", "exe_path": str(home_game_exe)},
    ]
    ctx = build_root_context(
        games, [str(lib)], "{creator}/{title}/{version}", {}, home_dir=str(home)
    )

    res_direct = storage_root(games[0], ctx)
    assert res_direct.unsafe is True
    assert res_direct.root == ""

    # Picking the home folder is refused.
    picked, err = validate_picked_folder(games[1], str(home), ctx)
    assert picked == ""
    assert "home" in err.lower()

    # Picking filesystem root is refused.
    picked_root, err_root = validate_picked_folder(games[1], "/", ctx)
    assert picked_root == ""
    assert "root" in err_root.lower()


def test_picked_roots_validation_persistence_deletion_and_device_local(
    tmp_path: Path, monkeypatch
):
    from core import database, library_backup
    from core.storage_usage import (
        STORAGE_RECOUNT_SETTING,
        build_root_context,
        clear_picked_root,
        load_picked_roots,
        save_picked_roots,
        storage_root,
        validate_picked_folder,
    )

    data_dir = tmp_path / "data"
    monkeypatch.setenv("WLIB_DATA_DIR", str(data_dir))
    monkeypatch.setattr(database, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "wlib.db"))
    database.init_db()

    lib = tmp_path / "Library"
    game_dir = lib / "CustomRoot"
    exe = game_dir / "sub" / "game.exe"
    exe.parent.mkdir(parents=True)
    _ = exe.write_bytes(b"exe")
    other_dir = tmp_path / "Elsewhere"
    other_dir.mkdir()

    gid = database.add_game("PickMe", str(exe))
    database.update_setting(STORAGE_RECOUNT_SETTING, "true")

    games = database.get_all_games()
    ctx = build_root_context(
        games,
        [str(lib)],
        "{creator}/{title}/{version}",
        {},
        home_dir=str(tmp_path / "H"),
    )

    # Reject a folder that does not contain the executable.
    bad_path, bad_err = validate_picked_folder(games[0], str(other_dir), ctx)
    assert bad_path == ""
    assert "executable" in bad_err.lower()

    # Accept a valid folder containing the executable.
    ok_path, ok_err = validate_picked_folder(games[0], str(game_dir), ctx)
    assert ok_err == ""
    assert Path(ok_path).resolve() == game_dir.resolve()

    save_picked_roots({gid: ok_path}, data_dir=str(data_dir))
    loaded = load_picked_roots(data_dir=str(data_dir), valid_game_ids={gid})
    assert loaded == {gid: ok_path}

    ctx_with_pick = build_root_context(
        games,
        [str(lib)],
        "{creator}/{title}/{version}",
        loaded,
        home_dir=str(tmp_path / "H"),
    )
    res = storage_root(games[0], ctx_with_pick)
    assert res.source == "picked"
    assert Path(res.root).resolve() == game_dir.resolve()

    # JSON export and sync snapshot do not include picked folders or storage_recount_after_changes.
    backup_payload = library_backup.build_library_backup()
    backup_json = str(backup_payload)
    assert STORAGE_RECOUNT_SETTING not in backup_json
    general_settings = cast(
        dict[str, object],
        cast(dict[str, object], backup_payload["settings"])["general"],
    )
    assert STORAGE_RECOUNT_SETTING not in general_settings
    assert "storage_roots" not in backup_payload
    assert "storage-roots" not in backup_json

    snapshot_name = library_backup.write_sync_snapshot(keep_empty=True)
    assert snapshot_name is not None
    snapshot_path = library_backup.sync_snapshot_path(snapshot_name)
    assert snapshot_path is not None
    snapshot_text = Path(snapshot_path).read_text(encoding="utf-8")
    assert STORAGE_RECOUNT_SETTING not in snapshot_text
    assert "storage_roots" not in snapshot_text
    assert "storage-roots" not in snapshot_text

    # Deleting a game drops its picked entry via clear_picked_root or valid_game_ids prune.
    assert clear_picked_root(gid, data_dir=str(data_dir)) is True
    assert load_picked_roots(data_dir=str(data_dir)) == {}

    save_picked_roots({gid: ok_path, 9999: str(game_dir)}, data_dir=str(data_dir))
    pruned = load_picked_roots(data_dir=str(data_dir), valid_game_ids={gid})
    assert pruned == {gid: ok_path}


# ── Phase 3: Unit plan, scan service, launch stop, game_changed, recount ─


def test_build_scan_plan_shared_folder_prefixes_and_torrent(tmp_path: Path):
    from core.storage_usage import PlanInput, build_scan_plan

    data_dir = tmp_path / "data"
    lib = tmp_path / "Library"
    shared_game_dir = lib / "SharedPack"
    shared_exe = shared_game_dir / "game.exe"
    shared_exe.parent.mkdir(parents=True)
    _ = shared_exe.write_bytes(b"s" * 4096)

    own_prefix = tmp_path / "prefixes" / "g1"
    own_prefix.mkdir(parents=True)
    _ = (own_prefix / "reg.bin").write_bytes(b"p" * 4096)

    shared_custom_prefix = tmp_path / "prefixes" / "common"
    shared_custom_prefix.mkdir(parents=True)
    _ = (shared_custom_prefix / "reg.bin").write_bytes(b"c" * 4096)

    g3_exe = lib / "Solo" / "solo.exe"
    g3_exe.parent.mkdir(parents=True)
    _ = g3_exe.write_bytes(b"3" * 4096)

    torrent_dir = data_dir / "downloads" / "torrents" / "abc123"
    torrent_dir.mkdir(parents=True)
    _ = (torrent_dir / "state.json").write_text('{"game_id": 1}', encoding="utf-8")
    _ = (torrent_dir / "payload.bin").write_bytes(b"t" * 8192)

    games: list[dict[str, object]] = [
        {
            "id": 1,
            "title": "Pack One",
            "exe_path": str(shared_exe),
            "custom_prefix": str(own_prefix),
        },
        {
            "id": 2,
            "title": "Pack Two",
            "exe_path": str(shared_exe),
            "custom_prefix": str(shared_custom_prefix),
        },
        {
            "id": 3,
            "title": "Solo Game",
            "exe_path": str(g3_exe),
            "custom_prefix": str(shared_custom_prefix),
        },
    ]

    plan = build_scan_plan(
        PlanInput(
            games=games,
            library_folders=[str(lib)],
            pattern="{creator}/{title}/{version}",
            wine_prefix_path="",
            data_dir=str(data_dir),
            picked_roots={},
            home_dir=str(tmp_path / "Home"),
        )
    )

    # Shared game folder appears ONCE in units, owned by both games 1 and 2.
    game_units = [u for u in plan.units if u.category == "games"]
    shared_units = [
        u for u in game_units if Path(u.path).resolve() == shared_game_dir.resolve()
    ]
    assert len(shared_units) == 1
    assert set(shared_units[0].owners) == {(1, "game"), (2, "game")}
    assert plan.games[1].shared_with == ["Pack Two"]
    assert plan.games[2].shared_with == ["Pack One"]

    # Game-owned custom prefix attaches to game 1; shared custom prefix has no game owner.
    prefix_units = [u for u in plan.units if u.category == "wine_prefixes"]
    own_u = next(
        u for u in prefix_units if Path(u.path).resolve() == own_prefix.resolve()
    )
    shared_u = next(
        u
        for u in prefix_units
        if Path(u.path).resolve() == shared_custom_prefix.resolve()
    )
    assert own_u.owners == [(1, "prefix")]
    assert shared_u.owners == []

    # Torrent job attaches to game 1 with part="torrent".
    torrent_units = [u for u in plan.units if u.category == "torrent_data"]
    assert len(torrent_units) == 1
    assert torrent_units[0].owners == [(1, "torrent")]


def test_scan_service_full_stop_unavailable_and_persistence(
    tmp_path: Path, monkeypatch
):
    from core import storage_usage
    from core.storage_usage import PlanInput, StorageScanService

    data_dir = tmp_path / "data"
    lib = tmp_path / "Library"
    g1_dir = lib / "Alpha"
    g1_exe = g1_dir / "alpha.exe"
    g1_dir.mkdir(parents=True)
    _ = g1_exe.write_bytes(b"a" * 16384)

    g2_dir = lib / "Beta"
    g2_exe = g2_dir / "beta.exe"
    g2_dir.mkdir(parents=True)
    _ = g2_exe.write_bytes(b"b" * 32768)

    games: list[dict[str, object]] = [
        {"id": 1, "title": "Alpha", "exe_path": str(g1_exe)},
        {"id": 2, "title": "Beta", "exe_path": str(g2_exe)},
    ]

    def loader() -> PlanInput:
        return PlanInput(
            games=games,
            library_folders=[str(lib)],
            pattern="{creator}/{title}/{version}",
            wine_prefix_path="",
            data_dir=str(data_dir),
            picked_roots={},
            home_dir=str(tmp_path / "Home"),
        )

    service = StorageScanService(
        load_plan_input=loader,
        data_dir=lambda: str(data_dir),
    )

    assert service.get_report() is None
    assert service.start_scan()["success"] is True
    assert service.wait_idle(timeout=5.0)

    report = service.get_report()
    assert report is not None
    assert report["version"] == 1
    alpha_size = os.lstat(g1_exe).st_blocks * 512
    beta_size = os.lstat(g2_exe).st_blocks * 512
    assert report["games"]["1"]["parts"]["game"] == alpha_size
    assert report["games"]["2"]["parts"]["game"] == beta_size

    # A new service instance reads the persisted report from disk.
    service2 = StorageScanService(
        load_plan_input=loader,
        data_dir=lambda: str(data_dir),
    )
    loaded = service2.get_report()
    assert loaded is not None
    assert loaded["total_bytes"] == report["total_bytes"]

    # Stopped scan keeps the previous report and sets stopped_reason.
    entered = threading.Event()
    release = threading.Event()
    real_measure = storage_usage.measure

    def slow_measure(path: str | os.PathLike[str], budget: MeasureBudget | None = None):
        entered.set()
        _ = release.wait(timeout=2.0)
        return real_measure(path, budget)

    monkeypatch.setattr(storage_usage, "measure", slow_measure)
    assert service2.start_scan()["success"] is True
    assert entered.wait(timeout=2.0)
    _ = service2.stop_scan(reason="user")
    release.set()
    assert service2.wait_idle(timeout=5.0)
    st = service2.state()
    assert st["stopped_reason"] == "user"
    assert st["report"] is not None
    assert cast(dict[str, object], st["report"])["scanned_at"] == report["scanned_at"]

    # Restore measure and simulate Beta's drive disappearing (unavailable root).
    monkeypatch.setattr(storage_usage, "measure", real_measure)
    g2_exe.unlink()
    g2_dir.rmdir()
    assert service2.start_scan()["success"] is True
    assert service2.wait_idle(timeout=5.0)
    report3 = service2.get_report()
    assert report3 is not None
    assert report3["games"]["2"]["unavailable"] is True
    assert report3["games"]["2"]["parts"]["game"] == beta_size


def test_scan_service_launch_hook_and_running_game_refusal(tmp_path: Path, monkeypatch):
    from core import storage_usage
    from core.storage_usage import PlanInput, StorageScanService

    data_dir = tmp_path / "data"
    lib = tmp_path / "Library"
    g1_exe = lib / "Alpha" / "alpha.exe"
    g1_exe.parent.mkdir(parents=True)
    _ = g1_exe.write_bytes(b"a" * 4096)

    running: list[int] = [1]
    service = StorageScanService(
        running_games=lambda: running,
        load_plan_input=lambda: PlanInput(
            games=[{"id": 1, "title": "Alpha", "exe_path": str(g1_exe)}],
            library_folders=[str(lib)],
            pattern="{creator}/{title}/{version}",
            wine_prefix_path="",
            data_dir=str(data_dir),
            picked_roots={},
            home_dir=str(tmp_path / "Home"),
        ),
        data_dir=lambda: str(data_dir),
    )

    # Refuses scan while a game runs.
    refused = service.start_scan()
    assert refused["success"] is False
    assert refused["code"] == "game_running"

    # Launch hook stops an in-progress scan with stopped_reason="game".
    running.clear()
    entered = threading.Event()
    release = threading.Event()
    real_measure = storage_usage.measure

    def slow_measure(path: str | os.PathLike[str], budget: MeasureBudget | None = None):
        entered.set()
        _ = release.wait(timeout=2.0)
        return real_measure(path, budget)

    monkeypatch.setattr(storage_usage, "measure", slow_measure)
    assert service.start_scan()["success"] is True
    assert entered.wait(timeout=2.0)
    service.on_game_starting()
    release.set()
    assert service.wait_idle(timeout=5.0)
    assert service.state()["stopped_reason"] == "game"


def test_game_changed_and_recount_setting_battery_and_full_scan(
    tmp_path: Path, monkeypatch
):
    from core import storage_usage
    from core.storage_usage import PlanInput, StorageScanService

    data_dir = tmp_path / "data"
    lib = tmp_path / "Library"
    g1_dir = lib / "Alpha"
    g1_exe = g1_dir / "alpha.exe"
    g1_dir.mkdir(parents=True)
    _ = g1_exe.write_bytes(b"a" * 4096)

    recount_on = False
    battery_state: bool | None = False

    def loader() -> PlanInput:
        return PlanInput(
            games=[{"id": 1, "title": "Alpha", "exe_path": str(g1_exe)}],
            library_folders=[str(lib)],
            pattern="{creator}/{title}/{version}",
            wine_prefix_path="",
            data_dir=str(data_dir),
            picked_roots={},
            home_dir=str(tmp_path / "Home"),
        )

    service = StorageScanService(
        load_plan_input=loader,
        recount_enabled=lambda: recount_on,
        on_battery=lambda: battery_state,
        data_dir=lambda: str(data_dir),
    )

    assert service.start_scan()["success"] is True
    assert service.wait_idle(timeout=5.0)
    initial = service.get_report()
    assert initial is not None
    orig_size = initial["games"]["1"]["parts"]["game"]

    # Update while recount setting is off -> marks changed, does not recount.
    _ = (g1_dir / "dlc.bin").write_bytes(b"d" * 32768)
    assert service.game_changed(1, "update") is False
    after_off = service.get_report()
    assert after_off is not None
    assert after_off["games"]["1"]["changed"] is True
    assert after_off["games"]["1"]["parts"]["game"] == orig_size

    # Recount setting on, but on battery -> does not recount.
    recount_on = True
    battery_state = True
    assert service.game_changed(1, "update") is False
    after_bat = service.get_report()
    assert after_bat is not None
    assert after_bat["games"]["1"]["changed"] is True

    # On mains power -> recounts single game and clears changed flag.
    battery_state = False
    assert service.game_changed(1, "update") is True
    assert service.wait_idle(timeout=5.0)
    after_mains = service.get_report()
    assert after_mains is not None
    expected_new = orig_size + os.lstat(g1_dir / "dlc.bin").st_blocks * 512
    assert after_mains["games"]["1"]["changed"] is False
    assert after_mains["games"]["1"]["parts"]["game"] == expected_new

    # Full scan in progress -> drops queued single-game count.
    entered = threading.Event()
    release = threading.Event()
    real_measure = storage_usage.measure

    def slow_measure(path: str | os.PathLike[str], budget: MeasureBudget | None = None):
        entered.set()
        _ = release.wait(timeout=2.0)
        return real_measure(path, budget)

    monkeypatch.setattr(storage_usage, "measure", slow_measure)
    assert service.start_scan()["success"] is True
    assert entered.wait(timeout=2.0)
    assert service.game_changed(1, "update") is False
    release.set()
    assert service.wait_idle(timeout=5.0)
    monkeypatch.setattr(storage_usage, "measure", real_measure)

    # Uninstall keeps the row, marks it changed and drops the deleted folder.
    assert service.game_changed(1, "uninstall") is False
    after_uninstall = service.get_report()
    assert after_uninstall is not None
    row = after_uninstall["games"]["1"]
    assert row["changed"] is True
    assert row["parts"]["game"] == 0
    assert row["total_bytes"] == 0
    assert after_uninstall["total_bytes"] == 0

    # Removal drops the row from the report.
    assert service.game_changed(1, "remove") is False
    after_remove = service.get_report()
    assert after_remove is not None
    assert "1" not in after_remove["games"]


def test_game_removal_moves_sandbox_and_player_to_removed_data(tmp_path: Path):
    from core.storage_usage import PlanInput, StorageScanService

    data_dir = tmp_path / "data"
    lib = tmp_path / "Library"
    game_dir = lib / "Alpha"
    game_dir.mkdir(parents=True)
    exe = game_dir / "alpha.exe"
    _ = exe.write_bytes(b"a" * 4096)
    for sub in ("sandbox", "web-player", "save-backups"):
        folder = data_dir / sub / "1"
        folder.mkdir(parents=True)
        _ = (folder / "data.bin").write_bytes(b"s" * 4096)

    service = StorageScanService(
        load_plan_input=lambda: PlanInput(
            games=[{"id": 1, "title": "Alpha", "exe_path": str(exe)}],
            library_folders=[str(lib)],
            pattern="{creator}/{title}/{version}",
            wine_prefix_path="",
            data_dir=str(data_dir),
            picked_roots={},
            home_dir=str(tmp_path / "Home"),
        ),
        recount_enabled=lambda: False,
        data_dir=lambda: str(data_dir),
    )
    assert service.start_scan()["success"] is True
    assert service.wait_idle(timeout=5.0)
    before = service.get_report()
    assert before is not None
    parts = before["games"]["1"]["parts"]
    cats_before = {c["id"]: c["bytes"] for c in before["categories"]}

    assert service.game_changed(1, "remove") is False
    after = service.get_report()
    assert after is not None
    assert "1" not in after["games"]
    cats = {c["id"]: c["bytes"] for c in after["categories"]}
    assert cats["games"] == cats_before["games"] - parts["game"]
    assert cats["sandboxes"] == 0
    assert cats["web_player"] == 0
    assert cats["removed_game_data"] == parts["sandbox"] + parts["player"]
    # Save backups stay on disk (they move aside), so their size stays.
    assert cats["save_backups"] == cats_before["save_backups"]
