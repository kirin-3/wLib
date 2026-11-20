"""Multi-part sets whose names differ only in letter case stay apart."""

import os

import pytest

from core import archive_volumes
from core.archive_install import ArchiveInstallError, check_volume_set, volume_set


def _case_sensitive(folder) -> bool:
    (folder / "probe").write_bytes(b"")
    try:
        return not (folder / "PROBE").exists()
    finally:
        (folder / "probe").unlink()


def _write(folder, names):
    for name in names:
        (folder / name).write_bytes(b"x")


@pytest.fixture
def folder(tmp_path):
    if not _case_sensitive(tmp_path):
        pytest.skip("needs a case-sensitive filesystem")
    return tmp_path


@pytest.mark.parametrize("lower_last", [True, False])
@pytest.mark.parametrize(
    ("upper", "lower", "chosen"),
    [
        (["Game.part1.rar", "Game.part2.rar"], ["game.part1.rar", "game.part2.rar"], "Game.part2.rar"),
        (["Game.7z.001", "Game.7z.002"], ["game.7z.001", "game.7z.002"], "Game.7z.001"),
        (["Game.rar", "Game.r00"], ["game.rar", "game.r00"], "Game.r00"),
        (["Game.zip", "Game.z01"], ["game.zip", "game.z01"], "Game.zip"),
    ],
)
def test_chosen_case_variant_set_is_kept(folder, monkeypatch, upper, lower, chosen, lower_last):
    _write(folder, upper + lower)
    order = upper + lower if lower_last else lower + upper
    real_listdir = os.listdir
    monkeypatch.setattr(
        archive_volumes.os,
        "listdir",
        lambda path: order if os.path.abspath(path) == str(folder) else real_listdir(path),
    )

    volumes = volume_set(str(folder / chosen))

    assert volumes is not None
    assert volumes.complete
    assert sorted(volumes.parts) == sorted(str(folder / name) for name in upper)
    assert os.path.basename(volumes.first) in upper
    check_volume_set(volumes)


def test_parts_that_only_differ_in_suffix_case_are_ambiguous(folder):
    _write(folder, ["Game.part1.rar", "Game.part2.rar", "Game.PART2.RAR"])

    volumes = volume_set(str(folder / "Game.part1.rar"))

    assert volumes is not None
    assert not volumes.complete
    assert sorted(volumes.ambiguous) == sorted(str(folder / n) for n in ("Game.part2.rar", "Game.PART2.RAR"))
    assert volumes.parts == (str(folder / "Game.part1.rar"),)
    with pytest.raises(ArchiveInstallError, match="letter case"):
        check_volume_set(volumes)


def test_chosen_file_wins_its_own_part_number(folder):
    _write(folder, ["Game.part1.rar", "Game.PART1.RAR", "Game.part2.rar"])

    volumes = volume_set(str(folder / "Game.PART1.RAR"))

    assert volumes is not None
    assert volumes.complete
    assert volumes.first == str(folder / "Game.PART1.RAR")
    assert volumes.parts == (str(folder / "Game.PART1.RAR"), str(folder / "Game.part2.rar"))


def test_mixed_case_parts_without_a_collision_still_form_one_set(folder):
    _write(folder, ["Game.part1.rar", "GAME.PART2.RAR"])

    volumes = volume_set(str(folder / "GAME.PART2.RAR"))

    assert volumes is not None
    assert volumes.complete
    assert volumes.first == str(folder / "Game.part1.rar")


def test_install_refuses_an_ambiguous_set_and_deletes_nothing(folder):
    from core.archive_install import install_archive

    names = ["Game.part1.rar", "Game.part2.rar", "Game.PART2.RAR"]
    _write(folder, names)
    library = folder / "lib"
    library.mkdir()

    with pytest.raises(ArchiveInstallError, match="letter case"):
        install_archive(
            str(folder / "Game.part1.rar"),
            library_folder=str(library),
            pattern="{title}",
            title="Game",
            delete_archive=True,
        )

    assert all((folder / name).exists() for name in names)
