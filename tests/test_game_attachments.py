# SPDX-License-Identifier: GPL-3.0-or-later
import os

import pytest

from core import database
from core.game_attachments import (
    AttachmentMissingError,
    NotAPdfError,
    add_attachment,
    attachments_dir,
    list_attachments,
    prune_orphan_attachments,
    remove_attachment,
    remove_game_attachments,
    resolve_attachment,
)


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    target = tmp_path / "data"
    target.mkdir()
    monkeypatch.setenv("WLIB_DATA_DIR", str(target))
    monkeypatch.setattr(database, "DATA_DIR", str(target))
    return target


def _write_pdf(path, body=b"hello"):
    from pathlib import Path

    Path(path).write_bytes(b"%PDF-1.4\n" + body)


def test_attachments_dir_uses_data_dir(data_dir):
    folder = attachments_dir(42)
    assert folder == os.path.join(str(data_dir), "attachments", "42")
    assert folder.startswith(database.DATA_DIR)


def test_list_attachments_filters_and_sorts(data_dir):
    folder = attachments_dir(7)
    os.makedirs(folder, exist_ok=True)
    _write_pdf(os.path.join(folder, "b.pdf"), b"b" * 10)
    _write_pdf(os.path.join(folder, "A.pdf"), b"a" * 5)
    with open(os.path.join(folder, "notes.txt"), "w", encoding="utf-8") as handle:
        handle.write("not a pdf")
    with open(os.path.join(folder, ".hidden.pdf"), "wb") as handle:
        handle.write(b"%PDF-1.4 hidden")
    os.makedirs(os.path.join(folder, "folder.pdf"), exist_ok=True)

    listed = list_attachments(7)
    assert [item["name"] for item in listed] == ["A.pdf", "b.pdf"]
    assert all(set(item.keys()) == {"name", "size"} for item in listed)
    assert listed[0]["size"] == 5 + len(b"%PDF-1.4\n")

    assert list_attachments(9999) == []


def test_remove_game_attachments(data_dir):
    folder = attachments_dir(11)
    os.makedirs(folder, exist_ok=True)
    _write_pdf(os.path.join(folder, "guide.pdf"))
    remove_game_attachments(11)
    assert not os.path.exists(folder)
    # Missing folder is a no-op.
    remove_game_attachments(11)


def test_add_attachment_happy_path_and_original_untouched(tmp_path, data_dir):
    source = tmp_path / "guide.pdf"
    _write_pdf(source)
    result = add_attachment(3, str(source))
    assert result["name"] == "guide.pdf"
    stored = os.path.join(attachments_dir(3), "guide.pdf")
    assert os.path.isfile(stored)
    assert source.read_bytes().startswith(b"%PDF-")
    assert [item["name"] for item in list_attachments(3)] == ["guide.pdf"]
    assert not os.path.exists(os.path.join(attachments_dir(3), ".guide.pdf.part"))


def test_add_attachment_rejects_extension_and_signature(tmp_path, data_dir):
    notes = tmp_path / "notes.txt"
    notes.write_bytes(b"%PDF-1.4\ncontent")
    with pytest.raises(NotAPdfError):
        add_attachment(4, str(notes))
    fake = tmp_path / "fake.pdf"
    fake.write_bytes(b"not a pdf")
    with pytest.raises(NotAPdfError):
        add_attachment(4, str(fake))
    assert list_attachments(4) == []


def test_add_attachment_collision_picks_free_name(tmp_path, data_dir):
    first = tmp_path / "guide.pdf"
    _write_pdf(first, b"first")
    assert add_attachment(5, str(first))["name"] == "guide.pdf"
    second_dir = tmp_path / "other"
    second_dir.mkdir()
    second = second_dir / "guide.pdf"
    _write_pdf(second, b"second")
    result = add_attachment(5, str(second))
    assert result["name"] == "guide (2).pdf"
    assert sorted(item["name"] for item in list_attachments(5)) == [
        "guide (2).pdf",
        "guide.pdf",
    ]


def test_add_attachment_unreadable_source_leaves_nothing(tmp_path, data_dir):
    missing = tmp_path / "missing.pdf"
    with pytest.raises(OSError):
        add_attachment(6, str(missing))
    assert list_attachments(6) == []
    assert not os.path.exists(os.path.join(attachments_dir(6), ".missing.pdf.part"))


def test_resolve_and_remove_guards(data_dir):
    folder = attachments_dir(8)
    os.makedirs(folder, exist_ok=True)
    _write_pdf(os.path.join(folder, "ok.pdf"))
    assert resolve_attachment(8, "ok.pdf").endswith(os.path.join("8", "ok.pdf"))
    with pytest.raises(ValueError):
        resolve_attachment(8, "../wlib.db")
    with pytest.raises(ValueError):
        resolve_attachment(8, "a/b.pdf")
    with pytest.raises(ValueError):
        resolve_attachment(8, ".hidden.pdf")
    with pytest.raises(ValueError):
        resolve_attachment(8, "..")
    with pytest.raises(ValueError):
        resolve_attachment(8, "bad\x00.pdf")
    with pytest.raises(AttachmentMissingError):
        resolve_attachment(8, "missing.pdf")
    # Successful remove deletes the file.
    remove_attachment(8, "ok.pdf")
    assert list_attachments(8) == []
    with pytest.raises(AttachmentMissingError):
        remove_attachment(8, "ok.pdf")


def test_prune_orphan_attachments(data_dir):
    base = os.path.join(str(data_dir), "attachments")
    os.makedirs(os.path.join(base, "1"), exist_ok=True)
    os.makedirs(os.path.join(base, "2"), exist_ok=True)
    os.makedirs(os.path.join(base, "notes"), exist_ok=True)
    with open(os.path.join(base, "loose.txt"), "w", encoding="utf-8") as handle:
        handle.write("keep")
    prune_orphan_attachments({1})
    assert os.path.isdir(os.path.join(base, "1"))
    assert not os.path.exists(os.path.join(base, "2"))
    assert os.path.isdir(os.path.join(base, "notes"))
    assert os.path.isfile(os.path.join(base, "loose.txt"))
