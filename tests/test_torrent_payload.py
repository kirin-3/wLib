# SPDX-License-Identifier: GPL-3.0-or-later
"""What a finished torrent holds (archive or game folder) and which torrents are refused."""
import io
import sys
from pathlib import Path

from core.downloads import torrent_helper
from core.downloads.torrents import find_payload


def _files(root: Path, *names: str) -> Path:
    for name in names:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_bytes(b"x")
    return root


def test_single_archive_with_readme_is_an_archive(tmp_path: Path) -> None:
    data = _files(tmp_path, "Game-1.0/Game-1.0-pc.zip", "Game-1.0/readme.txt")
    assert find_payload(data) == ("archive", str(data / "Game-1.0" / "Game-1.0-pc.zip"))


def test_multi_part_rar_installs_from_its_first_part(tmp_path: Path) -> None:
    data = _files(tmp_path, "Game.part1.rar", "Game.part2.rar", "Game.part3.rar")
    kind, path = find_payload(data)
    assert kind == "archive" and path.endswith("Game.part1.rar")


def test_split_7z_installs_from_its_first_part(tmp_path: Path) -> None:
    data = _files(tmp_path, "Game.7z.001", "Game.7z.002")
    kind, path = find_payload(data)
    assert kind == "archive" and path.endswith("Game.7z.001")


def test_game_folder_with_its_own_zip_stays_a_folder(tmp_path: Path) -> None:
    data = _files(tmp_path, "Game-1.0/Game.exe", "Game-1.0/lib/python.zip", "Game-1.0/game/script.rpy")
    assert find_payload(data) == ("folder", str(data / "Game-1.0"))


def test_two_separate_archives_are_a_folder(tmp_path: Path) -> None:
    data = _files(tmp_path, "Game-pc.zip", "Game-mac.zip")
    assert find_payload(data) == ("folder", str(data))


def test_symlink_entries_are_refused() -> None:
    files: list[object] = [
        {b"path": [b"game.exe"], b"length": 1},
        {b"path": [b"link"], b"length": 0, b"attr": b"l", b"symlink path": [b"..", b"..", b"etc"]},
    ]
    assert torrent_helper.unsafe_paths({b"name": b"Game", b"files": files})


def test_v2_file_tree_traversal_is_refused() -> None:
    tree: dict[bytes, object] = {b"..": {b"evil": {b"": {b"length": 1}}}}
    assert torrent_helper.unsafe_paths({b"name": b"Game", b"file tree": tree})


def test_v2_file_tree_symlink_is_refused() -> None:
    tree: dict[bytes, object] = {b"link": {b"": {b"length": 0, b"attr": b"l"}}}
    assert torrent_helper.unsafe_paths({b"name": b"Game", b"file tree": tree})


def test_plain_v2_file_tree_is_accepted() -> None:
    tree: dict[bytes, object] = {b"Game": {b"game.exe": {b"": {b"length": 5}}}}
    assert not torrent_helper.unsafe_paths({b"name": b"Game", b"file tree": tree})


def test_events_bypass_the_app_log_stdout_copy(monkeypatch) -> None:
    pipe_in, pipe_out = io.StringIO('{"cmd":"shutdown"}\n'), io.StringIO()
    tee = io.StringIO()
    monkeypatch.setattr(sys, "__stdin__", pipe_in)
    monkeypatch.setattr(sys, "__stdout__", pipe_out)
    monkeypatch.setattr(sys, "stdout", tee)
    assert torrent_helper.run() == 0
    assert '"bye"' in pipe_out.getvalue()
    # What the app log copies (sys.stdout) never sees protocol lines.
    assert tee.getvalue() == ""
