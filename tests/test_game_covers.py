# SPDX-License-Identifier: GPL-3.0-or-later
import os
from pathlib import Path

import pytest

from core import database, game_covers
from core.api import Api
from core.library_backup import (
    build_library_backup,
    apply_library_backup,
    build_sync_backup,
)


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "wlib.db"))
    monkeypatch.setattr(database, "DATA_DIR", str(tmp_path))
    database.init_db()


@pytest.fixture
def image(tmp_path):
    file = tmp_path / "image.txt"
    file.write_bytes(b"\x89PNG\r\n\x1a\nfixture")
    return file


@pytest.mark.parametrize(
    "header,extension",
    [
        (b"\xff\xd8\xff", "jpg"),
        (b"\x89PNG\r\n\x1a\n", "png"),
        (b"GIF87a", "gif"),
        (b"GIF89a", "gif"),
        (b"RIFF1234WEBP", "webp"),
    ],
)
def test_magic_bytes(tmp_path, header, extension):
    source = tmp_path / "source.wrong"
    source.write_bytes(header)
    name = game_covers.copy_cover_file(source)
    assert name.endswith("." + extension)
    assert game_covers.cover_path(name).read_bytes() == header


def test_invalid_and_oversized(tmp_path):
    file = tmp_path / "fake.jpg"
    file.write_text("not an image")
    with pytest.raises(ValueError, match="supported image"):
        game_covers.copy_cover_file(file)
    with file.open("wb") as stream:
        stream.write(b"\x89PNG\r\n\x1a\n")
        stream.truncate(game_covers.MAX_COVER_SIZE + 1)
    with pytest.raises(ValueError, match="20 MiB"):
        game_covers.copy_cover_file(file)
    assert not list(game_covers.covers_dir().iterdir())


@pytest.mark.parametrize(
    "name",
    ["../wlib.db", "/tmp/test.png", "a.png", "a" * 32 + ".svg", "a" * 32 + ".png/../x"],
)
def test_delete_rejects_traversal(name):
    assert game_covers.cover_path(name) is None
    game_covers.delete_cover_file(name)
    assert Path(database.DB_PATH).is_file()


@pytest.mark.skipif(os.name == "nt", reason="POSIX FIFOs need Linux")
def test_fifo_source_is_rejected_without_blocking(tmp_path):
    import threading

    fifo = tmp_path / "cover.png"
    os.mkfifo(str(fifo))
    errors: list[str] = []

    def run() -> None:
        try:
            game_covers.copy_cover_file(fifo)
        except ValueError as exc:
            errors.append(str(exc))

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(2)
    assert not thread.is_alive()
    assert errors and "supported image" in errors[0]


def test_symlink_outside_covers(tmp_path):
    game_covers.covers_dir().mkdir()
    name = "a" * 32 + ".png"
    (game_covers.covers_dir() / name).symlink_to(database.DB_PATH)
    assert game_covers.cover_path(name) is None
    game_covers.delete_cover_file(name)
    assert Path(database.DB_PATH).exists()


def test_replacement_clear_and_sync(image):
    game_id = database.add_game(
        "Cover", "/game.sh", "", cover_image="https://example.com/remote.jpg"
    )
    before = database.get_setting("sync_local_modified_at")
    first = game_covers.set_cover(game_id, str(image))
    assert database.get_setting("sync_local_modified_at") == before
    second = game_covers.set_cover(game_id, str(image))
    assert not (game_covers.covers_dir() / first).exists()
    assert (game_covers.covers_dir() / second).exists()
    assert (
        database.get_game(game_id)["cover_image_path"]
        == "https://example.com/remote.jpg"
    )
    game_covers.clear_cover(game_id)
    assert not (game_covers.covers_dir() / second).exists()
    assert database.get_setting("sync_local_modified_at") == before


def test_api_choose_set_payload_clear_delete(image, monkeypatch):
    api = Api()
    game_id = database.add_game("Cover", "/game.sh", "")
    monkeypatch.setattr(api, "_browse_linux_dialog", lambda *a, **k: str(image))
    monkeypatch.setattr(api, "_browse_qt_dialog", lambda *a, **k: str(image))
    api.library_sync.server_available = False
    result = api.choose_game_cover(game_id)
    assert result["success"]
    assert "cover_local_url" not in result["game"]
    assert "cover_local_url" not in api.get_games()[0]
    api.library_sync.server_available = True
    assert api.get_games()[0]["cover_local_url"].startswith(
        "http://127.0.0.1:8183/covers/"
    )
    result = api.set_game_cover_file(game_id, str(image))
    assert result["game"]["cover_local_url"]
    assert api.clear_game_cover(game_id)["success"]
    assert not list(game_covers.covers_dir().iterdir())
    api.set_game_cover_file(game_id, str(image))
    assert api.delete_game(game_id)["success"]
    assert not list(game_covers.covers_dir().iterdir())


def test_api_invalid_and_cancel(image, monkeypatch):
    api = Api()
    assert not api.set_game_cover_file(9999, str(image))["success"]
    game_id = database.add_game("Cover", "/game.sh", "")
    monkeypatch.setattr(api, "_browse_linux_dialog", lambda *a, **k: "")
    monkeypatch.setattr(api, "_browse_qt_dialog", lambda *a, **k: "")
    assert api.choose_game_cover(game_id)["cancelled"]
    assert not api.set_game_cover_file(game_id, str(image.parent / "missing"))[
        "success"
    ]


def test_exports_backup_sync_and_metadata_keep_cover(image):
    game_id = database.add_game(
        "Cover", "/game.sh", "https://f95zone.to/threads/12345/"
    )
    name = game_covers.set_cover(game_id, str(image))
    for backup in (build_library_backup(), build_sync_backup("now", "test")):
        assert "cover_local" not in str(backup)
        assert name not in str(backup)
        apply_library_backup(backup, {"sections": ["metadata"]})
        assert database.get_game(game_id)["cover_local"] == name
    database.update_game(
        game_id,
        {"cover_image_path": "https://example.com/new.jpg", "cover_local": None},
    )
    assert database.get_game(game_id)["cover_local"] == name


def test_old_schema_migration():
    with database.get_connection() as conn:
        conn.execute("ALTER TABLE games DROP COLUMN cover_local")
    database.init_db()
    with database.get_connection() as conn:
        assert "cover_local" in {
            row[1] for row in conn.execute("PRAGMA table_info(games)")
        }


def test_regular_backup_cannot_import_cover_files(image):
    from core.library_backup import _available_backup_sections

    game_id = database.add_game("Cover", "/game.sh", "https://f95zone.to/threads/12345/")
    name = game_covers.set_cover(game_id, str(image))
    backup = build_library_backup()
    backup["games"][0]["cover_file"] = str(image)
    backup["games"][0]["metadata"]["cover_local"] = "bad.png"
    backup["selected_sections"].append("covers")
    assert "covers" not in _available_backup_sections(backup)
    apply_library_backup(backup)
    assert database.get_game(game_id)["cover_local"] == name
    assert [p.name for p in game_covers.covers_dir().iterdir()] == [name]


def test_sync_deletion_removes_cover_after_commit_and_preserves_it_on_rollback(image):
    game_id = database.add_game("Cover", "/game.sh", "")
    name = game_covers.set_cover(game_id, str(image))
    backup = build_library_backup()
    backup["games"] = []
    # A failing trigger rolls back a mirror deletion; the old file stays intact.
    with database.get_connection() as conn:
        conn.execute("CREATE TRIGGER reject_delete BEFORE DELETE ON games BEGIN SELECT RAISE(ABORT, 'test failure'); END")
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError, match="test failure"):
        apply_library_backup(backup, mirror=True)
    assert database.get_game(game_id)["cover_local"] == name
    assert (game_covers.covers_dir() / name).exists()
    with database.get_connection() as conn:
        conn.execute("DROP TRIGGER reject_delete")
    apply_library_backup(backup, mirror=True)
    assert database.get_game(game_id) is None
    assert not (game_covers.covers_dir() / name).exists()
