# SPDX-License-Identifier: GPL-3.0-or-later
import os
import struct
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


@pytest.fixture(params=["png", "avif"])
def image(tmp_path, request):
    file = tmp_path / "image.txt"
    file.write_bytes(
        (Path(__file__).parent / "fixtures/playnite/avif/cover.png").read_bytes()
        if request.param == "avif" else b"\x89PNG\r\n\x1a\nfixture"
    )
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
@pytest.mark.parametrize("extension", ["png", "avif"])
def test_fifo_source_is_rejected_without_blocking(tmp_path, extension):
    import threading

    fifo = tmp_path / f"cover.{extension}"
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


@pytest.mark.parametrize("extension", ["png", "avif"])
def test_symlink_outside_covers(tmp_path, extension):
    game_covers.covers_dir().mkdir()
    name = "a" * 32 + "." + extension
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
    def picker(*a, **k):
        assert "*.avif" in k["file_types"][0]
        return str(image)

    monkeypatch.setattr(api, "_browse_linux_dialog", picker)
    monkeypatch.setattr(api, "_browse_qt_dialog", picker)
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


def _ftyp(major=b"avif", minor=b"\0" * 4, compatible=b"mif1", *, extended=False):
    body = major + minor + compatible
    return (
        struct.pack(">I4sQ", 1, b"ftyp", 16 + len(body)) if extended
        else struct.pack(">I4s", 8 + len(body), b"ftyp")
    ) + body


@pytest.mark.parametrize("brand", [b"avif", b"avis"])
@pytest.mark.parametrize("compatible", [False, True])
@pytest.mark.parametrize("extended", [False, True])
def test_avif_brands_and_box_sizes(tmp_path, brand, compatible, extended):
    data = _ftyp(
        b"mif1" if compatible else brand,
        compatible=brand if compatible else b"mif1", extended=extended,
    ) + b"image payload"
    file = tmp_path / "misnamed.png"
    file.write_bytes(data)
    before = (file.stat().st_mtime_ns, file.read_bytes())
    assert game_covers.validate_cover_file(file) == "avif"
    name = game_covers.copy_cover_file(file)
    assert game_covers.COVER_NAME_RE.fullmatch(name)
    assert name.endswith(".avif")
    assert game_covers.cover_path(name).read_bytes() == data
    assert (file.stat().st_mtime_ns, file.read_bytes()) == before


@pytest.mark.parametrize("data", [
    _ftyp(b"mif1", compatible=b"heic"),
    _ftyp(b"mp42", compatible=b"isom") + b"avif",
    _ftyp(b"mif1", minor=b"avif", compatible=b"heic"),
    _ftyp()[:-1],
    _ftyp(compatible=b"avifx"),
    struct.pack(">I4s", 0, b"ftyp") + b"avif\0\0\0\0",
    struct.pack(">I4s", 12, b"ftyp") + b"avif",
    struct.pack(">I4s", 0xffffffff, b"ftyp") + b"avif\0\0\0\0",
    struct.pack(">I4sQ", 1, b"ftyp", 20) + b"avif",
    struct.pack(">I4sQ", 1, b"ftyp", 2**63) + b"avif\0\0\0\0",
    struct.pack(">I4s", 1, b"ftyp") + b"avif",
    struct.pack(">I4s", 65540, b"ftyp") + b"avif\0\0\0\0" + b"mif1" * 16381,
])
def test_invalid_avif_does_not_replace_existing_cover(tmp_path, image, data):
    game_id = database.add_game("Keep cover", "", "")
    old = game_covers.set_cover(game_id, str(image))
    file = tmp_path / "candidate.avif"
    file.write_bytes(data)
    with pytest.raises(ValueError, match="supported image.*AVIF"):
        game_covers.validate_cover_file(file)
    with pytest.raises(ValueError, match="supported image.*AVIF"):
        game_covers.set_cover(game_id, str(file))
    assert database.get_game(game_id)["cover_local"] == old
    assert [p.name for p in game_covers.covers_dir().iterdir()] == [old]


def test_avif_inspection_budget(tmp_path, monkeypatch):
    file = tmp_path / "oversized-ftyp.avif"
    data = struct.pack(">I4s", 65540, b"ftyp") + b"avif\0\0\0\0" + b"mif1" * 16381
    file.write_bytes(data)
    original = game_covers._open_image_source
    reads = []

    class TrackedSource:
        def __enter__(self):
            self.stream = original(file)
            return self

        def __exit__(self, *args):
            self.stream.close()

        def fileno(self):
            return self.stream.fileno()

        def read(self, size):
            reads.append(size)
            return self.stream.read(size)

    monkeypatch.setattr(game_covers, "_open_image_source", lambda _: TrackedSource())
    with pytest.raises(ValueError, match="supported image"):
        game_covers.validate_cover_file(file)
    assert sum(reads) <= game_covers.MAX_TYPE_BOX_SIZE


def test_size_limit_and_growth_cleanup(image, monkeypatch):
    with image.open("r+b") as stream:
        stream.truncate(game_covers.MAX_COVER_SIZE + 1)
    for action in (game_covers.validate_cover_file, game_covers.copy_cover_file):
        with pytest.raises(ValueError, match="20 MiB"):
            action(image)
    with image.open("r+b") as stream:
        stream.truncate(1024)
    original = game_covers._inspect_image

    def grow_after_inspection(stream):
        result = original(stream)
        with image.open("r+b") as writer:
            writer.truncate(game_covers.MAX_COVER_SIZE + 1)
        return result

    monkeypatch.setattr(game_covers, "_inspect_image", grow_after_inspection)
    with pytest.raises(ValueError, match="20 MiB"):
        game_covers.copy_cover_file(image)
    assert not list(game_covers.covers_dir().iterdir())


def test_cover_database_failure_cleans_copy(image, monkeypatch):
    def fail(*a):
        raise RuntimeError("database write failed")

    monkeypatch.setattr(database, "set_game_local_cover", fail)
    with pytest.raises(RuntimeError, match="database write failed"):
        game_covers.set_cover(1, str(image))
    assert not list(game_covers.covers_dir().iterdir())


def test_avif_replaces_png_without_changing_source_or_remote(tmp_path):
    png = tmp_path / "old.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\nfixture")
    avif = Path(__file__).parent / "fixtures/playnite/avif/cover.png"
    before = (avif.stat().st_mtime_ns, avif.read_bytes())
    game_id = database.add_game("Replace cover", "", "", cover_image="https://example.com/remote.png")
    old = game_covers.set_cover(game_id, str(png))
    new = game_covers.set_cover(game_id, str(avif))
    assert new.endswith(".avif")
    assert game_covers.cover_path(new).read_bytes() == before[1]
    assert not game_covers.cover_path(old).exists()
    assert database.get_game(game_id)["cover_image_path"] == "https://example.com/remote.png"
    assert (avif.stat().st_mtime_ns, avif.read_bytes()) == before


@pytest.mark.parametrize("data", [b"<html>challenge</html>", b"x" * (game_covers.MAX_COVER_SIZE + 1)])
def test_cached_cover_rejects_invalid_data(data):
    with pytest.raises(ValueError):
        game_covers.store_cached_cover(data)
    assert not list(game_covers.cache_covers_dir().iterdir())


def test_cached_avif_and_path_isolation():
    data = _ftyp() + b"image"
    name = game_covers.store_cached_cover(data)
    assert name.endswith(".avif")
    assert game_covers.cached_cover_path(name).read_bytes() == data
    assert game_covers.cover_path("cache/" + name) is None
    for invalid in ("../" + name, "/" + name, "cache/" + name, "a" * 64 + ".png"):
        assert game_covers.cached_cover_path(invalid) is None


def test_cache_fill_replace_remove_failure_and_sweep(monkeypatch):
    from core import scraper

    url = "https://attachments.f95zone.to/first.png"
    game_id = database.add_game("Cache", "", "", cover_image=url)
    calls = []
    def fetch(url, limit):
        calls.append(url)
        if "fail" in url:
            raise ValueError("challenge")
        return b"\x89PNG\r\n\x1a\nimage"
    monkeypatch.setattr(scraper, "f95_get_bytes", fetch)
    service = game_covers.CoverCacheService()
    orphan = game_covers.store_cached_cover(fetch(url, 1))
    service._sweep()
    assert not game_covers.cached_cover_path(orphan).exists()
    service._fill()
    first = database.get_game(game_id)["cover_cache"]
    assert game_covers.cached_cover_path(first).is_file()
    calls.clear()
    service._fill()
    assert not calls
    game_covers.delete_cached_cover(first)
    service._fill()
    first = database.get_game(game_id)["cover_cache"]
    database.update_game(game_id, {"cover_image_path": url + "2"})
    service._fill()
    assert not game_covers.cached_cover_path(first).exists()
    second = database.get_game(game_id)["cover_cache"]
    database.update_game(game_id, {"cover_image_path": ""})
    service._fill()
    assert not game_covers.cached_cover_path(second).exists()
    assert database.get_game(game_id)["cover_cache"] is None
    database.update_game(game_id, {"cover_image_path": url + "fail"})
    calls.clear()
    service._fill()
    service._fill()
    assert calls == [url + "fail"]


@pytest.mark.parametrize("action", ["purge", "change", "delete", "stop"])
def test_cache_inflight_download_is_discarded(monkeypatch, action):
    import threading
    from core import scraper

    game_id = database.add_game("Race", "", "", cover_image="https://f95zone.to/one.png")
    entered, release = threading.Event(), threading.Event()
    def fetch(*args):
        entered.set()
        assert release.wait(3)
        return b"\x89PNG\r\n\x1a\nimage"
    monkeypatch.setattr(scraper, "f95_get_bytes", fetch)
    service = game_covers.CoverCacheService()
    thread = threading.Thread(target=service._fill)
    thread.start()
    assert entered.wait(3)
    if action == "purge":
        service.purge()
    elif action == "change":
        database.update_game(game_id, {"cover_image_path": "https://f95zone.to/new.png"})
    elif action == "delete":
        service.delete_game(game_id)
    else:
        service.stop()
    release.set()
    thread.join(3)
    assert not thread.is_alive()
    assert not game_covers.cache_covers_dir().exists() or not list(game_covers.cache_covers_dir().iterdir())
    if action == "purge":
        service.wake()
        service._fill()
        assert not service._wake.is_set()


def test_cache_purge_preserves_local_covers_and_resets_api(image, monkeypatch):
    from core import scraper

    monkeypatch.setattr(scraper, "f95_get_bytes", lambda *args: image.read_bytes())
    api = Api()
    game_id = database.add_game("Cache", "", "", cover_image="https://f95zone.to/image.png")
    local = game_covers.set_cover(game_id, str(image))
    api.cover_cache._fill()
    status = api.get_cover_cache_status()
    assert status == {"success": True, "count": 1, "bytes": len(image.read_bytes()), "running": False}
    assert api.purge_cover_cache()["count"] == 0
    assert database.get_game(game_id)["cover_cache"] is None
    assert game_covers.cover_path(local).is_file()
    api.cover_cache._fill()
    assert api.get_cover_cache_status()["bytes"] == 0
