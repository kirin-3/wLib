"""H20: mod-tool writers must not reuse a fixed temporary file name.

A file or symlink that already sits at `<name>.wlib-partial` (or the save
editor's `.<name>.wlib-tmp`) must not be truncated, followed or removed.
"""

import os
from pathlib import Path

from test_renpy_tools import build_rpa, make_game, never, no_progress
from test_rpgm_saves import _names, _slot_file, _stat
from test_rpgm_tools import make_mv

from core import renpy_tools, rpgm_saves, rpgm_tools
from core.game_tools import create_temp_file


def _plant(path: Path, target: Path) -> None:
    """Put a symlink to `target` at `path`."""
    os.symlink(target, path)


def test_create_temp_file_is_new_unique_and_keeps_the_fixed_name(tmp_path: Path):
    fixed = tmp_path / "a.png.wlib-partial"
    _ = fixed.write_bytes(b"keep")

    fd1, first = create_temp_file(str(tmp_path / "a.png"))
    fd2, second = create_temp_file(str(tmp_path / "a.png"))
    os.close(fd1)
    os.close(fd2)

    assert first != second
    assert str(fixed) not in (first, second)
    assert os.path.dirname(first) == str(tmp_path)
    assert first.endswith(".wlib-partial")
    assert fixed.read_bytes() == b"keep"


def test_console_keeps_files_at_the_old_temporary_name(tmp_path: Path):
    game = make_game(tmp_path)
    rpy, _rpyc = renpy_tools.console_paths(str(game))
    victim = tmp_path / "victim.txt"
    _ = victim.write_bytes(b"outside")
    _plant(Path(rpy + ".wlib-partial"), victim)

    assert renpy_tools.set_console(str(game), True) == "on"

    assert victim.read_bytes() == b"outside"
    assert os.path.islink(rpy + ".wlib-partial")
    assert "config.console" in Path(rpy).read_text(encoding="utf-8")


def test_in_place_extract_keeps_an_existing_partial_name_file(tmp_path: Path):
    game = make_game(tmp_path)
    build_rpa(game / "game" / "images.rpa", {"image.png": b"new"})
    sibling = game / "game" / "image.png.wlib-partial"
    _ = sibling.write_bytes(b"user file")

    result = renpy_tools.extract_archives(str(game), "in_place", "", no_progress, never)

    assert result["files"] == 1
    assert (game / "game" / "image.png").read_bytes() == b"new"
    assert sibling.read_bytes() == b"user file"


def test_extract_supports_long_multibyte_asset_names(tmp_path: Path):
    game = make_game(tmp_path)
    name = "絵" * 79 + ".png"
    build_rpa(game / "game" / "images.rpa", {name: b"image"})

    result = renpy_tools.extract_archives(str(game), "in_place", "", no_progress, never)

    assert result["files"] == 1
    assert (game / "game" / name).read_bytes() == b"image"


def test_extract_does_not_write_through_a_partial_name_symlink(tmp_path: Path):
    game = make_game(tmp_path)
    build_rpa(game / "game" / "images.rpa", {"image.png": b"new"})
    victim = tmp_path / "victim.txt"
    _ = victim.write_bytes(b"outside")
    _plant(game / "game" / "image.png.wlib-partial", victim)

    _ = renpy_tools.extract_archives(str(game), "in_place", "", no_progress, never)

    assert victim.read_bytes() == b"outside"
    assert (game / "game" / "image.png").read_bytes() == b"new"


def test_decrypt_keeps_an_existing_partial_name_file(tmp_path: Path):
    root = make_mv(tmp_path)
    sibling = root / "img" / "faces" / "Actor1.png.wlib-partial"
    _ = sibling.write_bytes(b"user file")
    victim = tmp_path / "victim.txt"
    _ = victim.write_bytes(b"outside")
    _plant(root / "audio" / "bgm" / "Theme.ogg.wlib-partial", victim)

    result = rpgm_tools.decrypt_assets(str(root), no_progress, never)

    assert result["decrypted"] == 6
    assert sibling.read_bytes() == b"user file"
    assert victim.read_bytes() == b"outside"
    leftovers = [
        p
        for p in root.rglob("*.wlib-partial")
        if p not in (sibling, root / "audio" / "bgm" / "Theme.ogg.wlib-partial")
    ]
    assert leftovers == []


def test_use_decrypted_does_not_write_through_a_partial_name_symlink(tmp_path: Path):
    root = make_mv(tmp_path)
    _ = rpgm_tools.decrypt_assets(str(root), no_progress, never)
    path = root / "data" / "System.json"
    victim = tmp_path / "victim.txt"
    _ = victim.write_bytes(b"outside")
    _plant(Path(str(path) + ".wlib-partial"), victim)

    rpgm_tools.set_use_decrypted(str(root), True)

    assert victim.read_bytes() == b"outside"
    assert rpgm_tools.read_use_decrypted(str(root)) is True


def test_write_save_does_not_write_through_the_old_temp_name(tmp_path: Path):
    path = _slot_file(tmp_path)
    size, mtime = _stat(path)
    victim = tmp_path / "victim.txt"
    _ = victim.write_bytes(b"outside")
    old_temp = tmp_path / f".{path.name}.wlib-tmp"
    _plant(old_temp, victim)

    _ = rpgm_saves.write_save(str(path), [], size, mtime, _names(), lambda: "b")

    assert victim.read_bytes() == b"outside"
    assert old_temp.is_symlink()
    assert [p for p in tmp_path.glob("*.wlib-tmp") if p != old_temp] == []
