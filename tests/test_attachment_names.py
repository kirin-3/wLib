# SPDX-License-Identifier: GPL-3.0-or-later
import os

import pytest

from core import database
from core.game_attachments import (
    add_attachment,
    attachments_dir,
    list_attachments,
    remove_attachment,
    resolve_attachment,
)


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    target = tmp_path / "data"
    target.mkdir()
    monkeypatch.setenv("WLIB_DATA_DIR", str(target))
    monkeypatch.setattr(database, "DATA_DIR", str(target))
    return target


@pytest.mark.skipif(os.name == "nt", reason="needs POSIX symlinks")
def test_removing_symlinked_attachment_keeps_its_target(data_dir):
    folder = attachments_dir(1)
    os.makedirs(folder)
    target = os.path.join(folder, "guide.txt")
    with open(target, "w", encoding="utf-8") as handle:
        handle.write("keep me")
    link = os.path.join(folder, "shortcut.txt")
    os.symlink("guide.txt", link)
    assert [item["name"] for item in list_attachments(1)] == [
        "guide.txt",
        "shortcut.txt",
    ]

    remove_attachment(1, "shortcut.txt")

    assert not os.path.lexists(link)
    assert os.path.isfile(target)
    assert [item["name"] for item in list_attachments(1)] == ["guide.txt"]


@pytest.mark.skipif(os.name == "nt", reason="backslash is a separator on Windows")
def test_backslash_name_can_be_added_opened_and_removed(tmp_path, data_dir):
    source = tmp_path / "patch\\notes.txt"
    source.write_text("notes", encoding="utf-8")
    result = add_attachment(2, str(source))
    assert result["name"] == "patch\\notes.txt"

    resolved = resolve_attachment(2, "patch\\notes.txt")
    assert os.path.basename(resolved) == "patch\\notes.txt"
    remove_attachment(2, "patch\\notes.txt")
    assert list_attachments(2) == []


def test_slash_name_is_still_rejected(data_dir):
    os.makedirs(attachments_dir(3))
    with pytest.raises(ValueError):
        resolve_attachment(3, "../guide.txt")
    with pytest.raises(ValueError):
        remove_attachment(3, "sub/guide.txt")


def test_long_attachment_name_is_staged_and_numbered_within_limit(tmp_path, data_dir):
    name = "a" * 246 + ".txt"  # 250 bytes, fits one name component
    first_dir = tmp_path / "one"
    first_dir.mkdir()
    (first_dir / name).write_text("first", encoding="utf-8")
    assert add_attachment(4, str(first_dir / name))["name"] == name

    long_name = "b" * 251 + ".txt"  # 255 bytes, the component limit
    for index in range(2):
        source_dir = tmp_path / f"long{index}"
        source_dir.mkdir()
        (source_dir / long_name).write_text(str(index), encoding="utf-8")
        added = add_attachment(4, str(source_dir / long_name))
        assert len(os.fsencode(str(added["name"]))) <= 255
        assert str(added["name"]).endswith(".txt")
    names = [str(item["name"]) for item in list_attachments(4)]
    assert len(names) == 3
    assert long_name in names
    assert any(item.endswith(" (2).txt") for item in names)
    assert not [
        entry for entry in os.listdir(attachments_dir(4)) if entry.startswith(".")
    ]
