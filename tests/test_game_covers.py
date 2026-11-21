# SPDX-License-Identifier: GPL-3.0-or-later
import os
import sqlite3
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


# A complete PNG ends with its IEND chunk; cut-off downloads are not cached.
PNG_END = b"\x00\x00\x00\x00IEND\xaeB`\x82"


@pytest.fixture(params=["png", "avif"])
def image(tmp_path, request):
    file = tmp_path / "image.txt"
    file.write_bytes(
        (Path(__file__).parent / "fixtures/playnite/avif/cover.png").read_bytes()
        if request.param == "avif"
        else b"\x89PNG\r\n\x1a\nfixture" + PNG_END
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

    game_id = database.add_game(
        "Cover", "/game.sh", "https://f95zone.to/threads/12345/"
    )
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
        conn.execute(
            "CREATE TRIGGER reject_delete BEFORE DELETE ON games BEGIN SELECT RAISE(ABORT, 'test failure'); END"
        )
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
        struct.pack(">I4sQ", 1, b"ftyp", 16 + len(body))
        if extended
        else struct.pack(">I4s", 8 + len(body), b"ftyp")
    ) + body


@pytest.mark.parametrize("brand", [b"avif", b"avis"])
@pytest.mark.parametrize("compatible", [False, True])
@pytest.mark.parametrize("extended", [False, True])
def test_avif_brands_and_box_sizes(tmp_path, brand, compatible, extended):
    data = (
        _ftyp(
            b"mif1" if compatible else brand,
            compatible=brand if compatible else b"mif1",
            extended=extended,
        )
        + b"image payload"
    )
    file = tmp_path / "misnamed.png"
    file.write_bytes(data)
    before = (file.stat().st_mtime_ns, file.read_bytes())
    assert game_covers.validate_cover_file(file) == "avif"
    name = game_covers.copy_cover_file(file)
    assert game_covers.COVER_NAME_RE.fullmatch(name)
    assert name.endswith(".avif")
    assert game_covers.cover_path(name).read_bytes() == data
    assert (file.stat().st_mtime_ns, file.read_bytes()) == before


@pytest.mark.parametrize(
    "data",
    [
        _ftyp(b"mif1", compatible=b"heic"),
        _ftyp(b"mp42", compatible=b"isom") + b"avif",
        _ftyp(b"mif1", minor=b"avif", compatible=b"heic"),
        _ftyp()[:-1],
        _ftyp(compatible=b"avifx"),
        struct.pack(">I4s", 0, b"ftyp") + b"avif\0\0\0\0",
        struct.pack(">I4s", 12, b"ftyp") + b"avif",
        struct.pack(">I4s", 0xFFFFFFFF, b"ftyp") + b"avif\0\0\0\0",
        struct.pack(">I4sQ", 1, b"ftyp", 20) + b"avif",
        struct.pack(">I4sQ", 1, b"ftyp", 2**63) + b"avif\0\0\0\0",
        struct.pack(">I4s", 1, b"ftyp") + b"avif",
        struct.pack(">I4s", 65540, b"ftyp") + b"avif\0\0\0\0" + b"mif1" * 16381,
    ],
)
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
    game_id = database.add_game(
        "Replace cover", "", "", cover_image="https://example.com/remote.png"
    )
    old = game_covers.set_cover(game_id, str(png))
    new = game_covers.set_cover(game_id, str(avif))
    assert new.endswith(".avif")
    assert game_covers.cover_path(new).read_bytes() == before[1]
    assert not game_covers.cover_path(old).exists()
    assert (
        database.get_game(game_id)["cover_image_path"]
        == "https://example.com/remote.png"
    )
    assert (avif.stat().st_mtime_ns, avif.read_bytes()) == before


@pytest.mark.parametrize(
    "data", [b"<html>challenge</html>", b"x" * (game_covers.MAX_COVER_SIZE + 1)]
)
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
        return b"\x89PNG\r\n\x1a\nimage" + PNG_END

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


def test_cache_retries_failed_covers_and_survives_database_errors(monkeypatch):
    from core import scraper

    url = "https://attachments.f95zone.to/flaky.png"
    game_id = database.add_game("Flaky", "", "", cover_image=url)
    attempts = []

    def fetch(url, limit):
        attempts.append(url)
        if len(attempts) == 1:
            raise ValueError("challenge")
        return b"\x89PNG\r\n\x1a\nimage" + PNG_END

    monkeypatch.setattr(scraper, "f95_get_bytes", fetch)
    now = [1000.0]
    monkeypatch.setattr(game_covers.time, "monotonic", lambda: now[0])
    service = game_covers.CoverCacheService()
    service._fill()
    service._fill()
    assert len(attempts) == 1
    assert service._next_retry_in() == game_covers.RETRY_BASE_SECONDS
    now[0] += game_covers.RETRY_BASE_SECONDS
    service._fill()
    assert len(attempts) == 2
    assert database.get_game(game_id)["cover_cache"]
    assert service._next_retry_in() is None

    # One failing pass must not end the worker.
    passes = []
    real_fill = service._fill

    def flaky_fill():
        passes.append(1)
        if len(passes) == 1:
            raise sqlite3.OperationalError("database is locked")
        real_fill()
        service._stop.set()

    monkeypatch.setattr(service, "_fill", flaky_fill)
    monkeypatch.setattr(game_covers, "ERROR_RETRY_SECONDS", 0.01)
    service._wake.set()
    service._run()
    assert len(passes) == 2


@pytest.mark.parametrize("action", ["purge", "change", "delete", "stop"])
def test_cache_inflight_download_is_discarded(monkeypatch, action):
    import threading
    from core import scraper

    game_id = database.add_game(
        "Race", "", "", cover_image="https://f95zone.to/one.png"
    )
    entered, release = threading.Event(), threading.Event()

    def fetch(*args):
        entered.set()
        assert release.wait(3)
        return b"\x89PNG\r\n\x1a\nimage" + PNG_END

    monkeypatch.setattr(scraper, "f95_get_bytes", fetch)
    service = game_covers.CoverCacheService()
    thread = threading.Thread(target=service._fill)
    thread.start()
    assert entered.wait(3)
    if action == "purge":
        service.purge()
    elif action == "change":
        database.update_game(
            game_id, {"cover_image_path": "https://f95zone.to/new.png"}
        )
    elif action == "delete":
        service.delete_game(game_id)
    else:
        service.stop()
    release.set()
    thread.join(3)
    assert not thread.is_alive()
    assert not game_covers.cache_covers_dir().exists() or not list(
        game_covers.cache_covers_dir().iterdir()
    )
    if action == "purge":
        service.wake()
        service._fill()
        assert not service._wake.is_set()


def test_cache_purge_preserves_local_covers_and_resets_api(image, monkeypatch):
    from core import scraper

    monkeypatch.setattr(scraper, "f95_get_bytes", lambda *args: image.read_bytes())
    api = Api()
    game_id = database.add_game(
        "Cache", "", "", cover_image="https://f95zone.to/image.png"
    )
    local = game_covers.set_cover(game_id, str(image))
    api.cover_cache._fill()
    status = api.get_cover_cache_status()
    assert status == {
        "success": True,
        "count": 1,
        "bytes": len(image.read_bytes()),
        "running": False,
    }
    assert api.purge_cover_cache()["count"] == 0
    assert database.get_game(game_id)["cover_cache"] is None
    assert game_covers.cover_path(local).is_file()
    api.cover_cache._fill()
    assert api.get_cover_cache_status()["bytes"] == 0


def test_cut_off_cover_is_not_cached():
    assert game_covers.image_looks_complete(b"\x89PNG\r\n\x1a\nimage" + PNG_END)
    assert not game_covers.image_looks_complete(b"\x89PNG\r\n\x1a\nimage")
    assert game_covers.image_looks_complete(b"\xff\xd8\xff\xe0data\xff\xd9")
    assert not game_covers.image_looks_complete(b"\xff\xd8\xff\xe0data")
    assert not game_covers.image_looks_complete(b"RIFF\x20\x00\x00\x00WEBPVP8 ")


def test_cache_drops_failures_for_covers_no_longer_wanted(monkeypatch):
    from core import scraper

    url = "https://attachments.f95zone.to/gone.png"
    game_id = database.add_game("Gone", "", "", cover_image=url)

    def fetch(url, limit):
        raise ValueError("challenge")

    monkeypatch.setattr(scraper, "f95_get_bytes", fetch)
    now = [1000.0]
    monkeypatch.setattr(game_covers.time, "monotonic", lambda: now[0])
    service = game_covers.CoverCacheService()
    service._fill()
    assert service._next_retry_in() == game_covers.RETRY_BASE_SECONDS
    database.update_game(game_id, {"cover_image_path": ""})
    now[0] += game_covers.RETRY_BASE_SECONDS * 2
    service._fill()
    # A stale failure whose retry time passed used to wake the loop every second.
    assert service._next_retry_in() is None


COVER_HELPERS = [
    (game_covers.cover_path, game_covers.covers_dir),
    (game_covers.cached_cover_path, game_covers.cache_covers_dir),
]


@pytest.mark.parametrize("helper,folder_of", COVER_HELPERS)
def test_strict_names_are_refused_without_file_checks(helper, folder_of, monkeypatch):
    def no_lstat(*_args, **_kwargs):
        raise AssertionError("a refused name must not touch the disk")

    monkeypatch.setattr(game_covers.os, "lstat", no_lstat)
    for name in ("../wlib.db", "a" * 32 + ".png/../x", "a" * 32 + ".svg", None, 5):
        assert helper(name) is None


@pytest.mark.parametrize("helper,folder_of", COVER_HELPERS)
def test_missing_file_gives_its_path(helper, folder_of):
    name = "b" * 32 + ".jpg"
    assert helper(name) == folder_of().resolve() / name


@pytest.mark.parametrize("helper,folder_of", COVER_HELPERS)
def test_links_inside_folder_pass_and_outside_fail(helper, folder_of):
    folder = folder_of()
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / ("c" * 32 + ".png")
    target.write_bytes(b"x")
    inside = "d" * 32 + ".png"
    (folder / inside).symlink_to(target)
    outside = "e" * 32 + ".png"
    (folder / outside).symlink_to(database.DB_PATH)
    assert helper(inside) == folder.resolve() / inside
    assert helper(outside) is None
    resolved = folder.resolve()
    assert helper(inside, folder=resolved) == helper(inside)
    assert helper(outside, folder=resolved) is None
    assert helper(target.name, folder=resolved) == helper(target.name)


def test_data_folder_reached_through_a_link(tmp_path, monkeypatch):
    real = tmp_path / "real"
    (real / "covers").mkdir(parents=True)
    link = tmp_path / "data-link"
    link.symlink_to(real)
    monkeypatch.setattr(database, "DATA_DIR", str(link))
    name = "f" * 32 + ".png"
    (real / "covers" / name).write_bytes(b"x")
    assert game_covers.cover_path(name) == real.resolve() / "covers" / name


@pytest.mark.parametrize(
    "helper,folder_of",
    [
        (game_covers.cover_file, game_covers.covers_dir),
        (game_covers.cached_cover_file, game_covers.cache_covers_dir),
    ],
)
def test_file_helpers_need_a_regular_file_inside(helper, folder_of):
    folder = folder_of()
    folder.mkdir(parents=True, exist_ok=True)
    real = folder / ("1" * 32 + ".png")
    real.write_bytes(b"xy")
    (folder / ("2" * 32 + ".png")).symlink_to(real)
    (folder / ("3" * 32 + ".png")).symlink_to(database.DB_PATH)
    (folder / ("4" * 32 + ".png")).symlink_to(folder / "missing")
    (folder / ("5" * 32 + ".png")).mkdir()
    found = helper(real.name)
    assert (
        found is not None
        and found[0] == folder.resolve() / real.name
        and found[1].st_size == 2
    )
    linked = helper("2" * 32 + ".png", folder=folder.resolve())
    assert linked is not None and linked[1].st_size == 2
    for name in ("3", "4", "5", "6"):
        assert helper(name * 32 + ".png") is None
    assert helper("../wlib.db") is None


class _LcBridge:
    """A connected extension that answers fetch_image like the real one."""

    def __init__(self, monkeypatch, answer, *, flag=True):
        import base64
        import json

        from core import extension_bridge as bridge

        bridge.reset_for_tests()
        monkeypatch.setattr(bridge, "LC_BACKGROUND_GAP_SECONDS", 0)
        self.requests = []

        def send(raw):
            message = json.loads(raw)
            self.requests.append(message["url"])
            status, content_type, body = answer(message["url"])
            bridge._handle_message(
                self.conn,
                {
                    "type": "fetch_image_chunk",
                    "id": message["id"],
                    "index": 0,
                    "ok": True,
                    "status": status,
                    "content_type": content_type,
                    "final_url": message["url"],
                    "data": base64.b64encode(body).decode(),
                    "last": True,
                },
            )

        self.conn = bridge.BridgeConnection(send)
        bridge._register_connection(self.conn)
        bridge._handle_message(
            self.conn,
            {
                "type": "hello",
                "protocol": 2,
                "can_fetch_lc": True,
                "can_fetch_lc_images": flag,
            },
        )


LC_COVER = "https://lewdcorner.com/attachments/cover-webp.891912/"
WEBP = b"RIFF\x08\x00\x00\x00WEBPVP8 "


def test_lc_cover_is_cached_through_the_extension_only(monkeypatch):
    from core import scraper

    f95 = "https://attachments.f95zone.to/f95.png"
    lc_game = database.add_game(
        "LC", "", lc_url="https://lewdcorner.com/threads/21/", cover_image=LC_COVER
    )
    f95_game = database.add_game("F95", "", "", cover_image=f95)
    order = []
    monkeypatch.setattr(
        scraper,
        "f95_get_bytes",
        lambda url, _limit: order.append(url) or b"\x89PNG\r\n\x1a\nx" + PNG_END,
    )
    lc = _LcBridge(
        monkeypatch, lambda url: order.append(url) or (200, "image/webp", WEBP)
    )
    game_covers.CoverCacheService()._fill()
    # F95 covers first; the LC URL never reaches the plain HTTP path.
    assert order == [f95, LC_COVER]
    assert lc.requests == [LC_COVER]
    name = database.get_game(lc_game)["cover_cache"]
    assert (
        name.endswith(".webp")
        and game_covers.cached_cover_path(name).read_bytes() == WEBP
    )
    assert database.get_game(f95_game)["cover_cache"]


def test_lc_cover_waits_without_an_extension_and_without_a_failure(monkeypatch):
    from core import extension_bridge as bridge, scraper

    game_id = database.add_game(
        "LC", "", lc_url="https://lewdcorner.com/threads/21/", cover_image=LC_COVER
    )
    monkeypatch.setattr(
        scraper, "f95_get_bytes", lambda *_args: pytest.fail("plain HTTP for an LC URL")
    )
    old = _LcBridge(
        monkeypatch, lambda _url: pytest.fail("older extension asked"), flag=False
    )
    service = game_covers.CoverCacheService()
    woken = []
    monkeypatch.setattr(service, "wake", lambda: woken.append(True))
    bridge.add_lc_images_listener(service.wake)
    service._fill()
    assert old.requests == []
    assert service._failed == {} and service._next_retry_in() is None
    assert database.get_game(game_id)["cover_cache"] is None
    bridge._handle_message(old.conn, {"type": "status", "can_fetch_lc_images": True})
    assert woken == [True]


def test_lc_covers_the_extension_refuses_are_never_fetched(monkeypatch):
    from core import scraper

    for thread, url in enumerate(
        (
            "https://lewdcorner.com/data/attachments/891/891912-abc.jpg?hash=x",
            "https://lewdcorner.com/proxy.php?image=https%3A%2F%2Fexample.org%2Fa.png",
            "https://lewdcorner.com/attachments/cover-webp.891912/extra",
        ),
        start=31,
    ):
        database.add_game(
            f"LC {thread}",
            "",
            lc_url=f"https://lewdcorner.com/threads/{thread}/",
            cover_image=url,
        )
    monkeypatch.setattr(
        scraper, "f95_get_bytes", lambda *_args: pytest.fail("plain HTTP for an LC URL")
    )
    lc = _LcBridge(
        monkeypatch, lambda _url: pytest.fail("extension asked for a URL it refuses")
    )
    service = game_covers.CoverCacheService()
    service._fill()
    # No request, no failure and no retry timer.
    assert (
        lc.requests == [] and service._failed == {} and service._next_retry_in() is None
    )


def test_lc_error_page_stores_nothing_and_retries_later(monkeypatch):
    game_id = database.add_game(
        "LC", "", lc_url="https://lewdcorner.com/threads/21/", cover_image=LC_COVER
    )
    answers = [
        (500, "text/html; charset=utf-8", b""),
        (200, "text/html", b"<html>login</html>"),
    ]
    lc = _LcBridge(
        monkeypatch,
        lambda _url: answers.pop(0) if answers else (200, "image/webp", WEBP),
    )
    service = game_covers.CoverCacheService()
    service._fill()
    assert database.get_game(game_id)["cover_cache"] is None
    assert not game_covers.cache_covers_dir().exists() or not any(
        game_covers.cache_covers_dir().iterdir()
    )
    first_due = service._failed[LC_COVER][1]
    service._fill()
    assert lc.requests == [LC_COVER]  # still waiting on its delay
    service._failed[LC_COVER] = (1, 0.0)
    service._fill()
    assert service._failed[LC_COVER][0] == 2
    assert service._failed[LC_COVER][1] - first_due > game_covers.RETRY_BASE_SECONDS / 2
    service._failed[LC_COVER] = (2, 0.0)
    service._fill()
    assert database.get_game(game_id)["cover_cache"]
    assert LC_COVER not in service._failed


def test_lc_hourly_limit_waits_for_the_hour_without_failures(monkeypatch):
    from core import extension_bridge as bridge

    urls = [f"https://lewdcorner.com/attachments/c{i}-webp.{i}/" for i in range(3)]
    for i, url in enumerate(urls):
        _ = database.add_game(
            f"LC {i}",
            "",
            lc_url=f"https://lewdcorner.com/threads/{i + 1}/",
            cover_image=url,
        )
    lc = _LcBridge(monkeypatch, lambda _url: (200, "image/webp", WEBP))
    monkeypatch.setattr(bridge, "LC_BACKGROUND_PER_HOUR", 1)
    monkeypatch.setattr(
        bridge, "_lc_background_fetches", __import__("collections").deque(maxlen=1)
    )
    service = game_covers.CoverCacheService()
    # An earlier failure of the last URL keeps its count while the limit holds.
    service._failed[urls[2]] = (3, 0.0)
    service._fill()
    assert lc.requests == [urls[0]]
    # The limit is not a failure of the URL that met it.
    assert set(service._failed) == {urls[2]} and service._failed[urls[2]][0] == 3
    assert 3500 < service._next_retry_in() <= 3600
    service._fill()
    assert lc.requests == [urls[0]]
    # The hour is over: the next pass goes on.
    bridge._lc_background_fetches.clear()
    service._lc_resume_at = 0.0
    service._fill()
    # One more fetch, then the limit of one per hour holds again.
    assert lc.requests == [urls[0], urls[1]]


def test_waiting_lc_failures_set_no_timer_without_an_extension(monkeypatch):
    from core import extension_bridge as bridge

    _ = database.add_game(
        "LC", "", lc_url="https://lewdcorner.com/threads/21/", cover_image=LC_COVER
    )
    lc = _LcBridge(
        monkeypatch, lambda _url: pytest.fail("older extension asked"), flag=False
    )
    service = game_covers.CoverCacheService()
    service._failed[LC_COVER] = (2, 0.0)
    service._fill()
    # Kept for the next connection, but no wake-up every second until then.
    assert service._failed[LC_COVER][0] == 2
    assert service._next_retry_in() is None
    bridge._handle_message(lc.conn, {"type": "status", "can_fetch_lc_images": True})
    assert service._next_retry_in() == 1.0


def test_lc_screenshot_copies_count_purge_and_cleanup(monkeypatch):
    kept_url = "https://lewdcorner.com/attachments/kept-png.1/"
    gone_url = "https://lewdcorner.com/attachments/gone-png.2/"
    png = b"\x89PNG\r\n\x1a\nx" + PNG_END
    kept = game_covers.store_lc_copy(game_covers.lc_image_key(kept_url), png)
    gone = game_covers.store_lc_copy(game_covers.lc_image_key(gone_url), png)
    assert kept.name == f"lc-{game_covers.lc_image_key(kept_url)}.png"
    assert (
        game_covers.lc_copy_file(game_covers.lc_image_key(kept_url)) == kept.resolve()
    )
    service = game_covers.CoverCacheService(lc_screenshot_urls=lambda: [kept_url])
    assert service.status()["bytes"] == 2 * len(png)
    service._sweep()
    assert kept.exists() and not gone.exists()
    service.purge()
    assert not kept.exists()
    assert service.status()["bytes"] == 0


def test_library_lc_screenshot_urls_cover_stored_and_catalog_lists(monkeypatch):
    from core import game_lookup

    stored = "https://lewdcorner.com/attachments/stored-png.1/"
    game_id = database.add_game(
        "LC", "", lc_url="https://lewdcorner.com/threads/21/", screenshots=[stored]
    )
    assert game_id
    monkeypatch.setattr(
        game_lookup,
        "lc_threads_screenshots",
        lambda ids: (
            {"21": ["https://lewdcorner.com/attachments/c-png.2/"]}
            if ids == ["21"]
            else {}
        ),
    )
    api = Api()
    assert api._library_lc_screenshot_urls() == [
        stored,
        "https://lewdcorner.com/attachments/c-png.2/",
    ]


def test_saved_lc_cover_asks_the_window_to_reload(monkeypatch):
    lc_game = database.add_game(
        "LC", "", lc_url="https://lewdcorner.com/threads/21/", cover_image=LC_COVER
    )
    answers = [(500, "text/html", b"")]
    _ = _LcBridge(
        monkeypatch,
        lambda _url: answers.pop(0) if answers else (200, "image/webp", WEBP),
    )
    reloads = []
    service = game_covers.CoverCacheService(on_lc_cover=lambda: reloads.append(True))
    service._fill()
    assert reloads == []  # nothing saved, nothing to show
    service._failed[LC_COVER] = (1, 0.0)
    service._fill()
    assert database.get_game(lc_game)["cover_cache"] and reloads == [True]


def test_lc_cover_reloads_are_grouped(monkeypatch):
    import threading

    api = Api()
    sent = []
    monkeypatch.setattr(api, "_notify_card_copies_ready", lambda: sent.append(True))
    timers = []

    class FakeTimer:
        def __init__(self, wait, run):
            self.wait, self.run, self.daemon = wait, run, False
            timers.append(self)

        def start(self):
            pass

    monkeypatch.setattr(threading, "Timer", FakeTimer)
    api._lc_cover_cached()
    api._lc_cover_cached()
    assert len(timers) == 1 and timers[0].wait == 0.0
    timers[0].run()
    assert sent == [True]
    api._lc_cover_cached()
    assert len(timers) == 2 and 14 < timers[1].wait <= api.LC_COVER_REFRESH_SECONDS
