# SPDX-License-Identifier: GPL-3.0-or-later
import os
import struct
from pathlib import Path
from uuid import uuid4

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QBuffer, QByteArray, QIODevice
from PyQt6.QtGui import QImageReader

from core import cover_cards, database
from core.api import Api


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "wlib.db"))
    monkeypatch.setattr(database, "DATA_DIR", str(tmp_path))
    database.init_db()


def _lzw_literal(pixels: list[int]) -> bytes:
    """GIF LZW data for a 2-bit palette that clears the table every 2 codes."""
    bits = 0
    nbits = 0
    out = bytearray()

    def emit(code: int) -> None:
        nonlocal bits, nbits
        bits |= code << nbits
        nbits += 3
        while nbits >= 8:
            out.append(bits & 0xFF)
            bits >>= 8
            nbits -= 8

    emit(4)  # clear
    for i, pixel in enumerate(pixels):
        emit(pixel)
        if i % 2 == 1:
            emit(4)
    emit(5)  # end of information
    if nbits:
        out.append(bits & 0xFF)
    blocks = bytearray()
    for i in range(0, len(out), 255):
        part = out[i : i + 255]
        blocks.append(len(part))
        blocks += part
    return bytes(blocks) + b"\x00"


def make_gif(width=40, height=10, frames=3, delay_cs=5) -> bytes:
    palette = bytes([255, 0, 0, 0, 255, 0, 0, 0, 255, 255, 255, 255])
    data = bytearray(b"GIF89a" + struct.pack("<HHBBB", width, height, 0xF1, 0, 0) + palette)
    data += b"\x21\xff\x0bNETSCAPE2.0\x03\x01\x00\x00\x00"
    for i in range(frames):
        data += b"\x21\xf9\x04\x00" + struct.pack("<H", delay_cs) + b"\x00\x00"
        data += b"\x2c" + struct.pack("<HHHHB", 0, 0, width, height, 0)
        data += b"\x02" + _lzw_literal([i % 4] * (width * height))
    data += b"\x3b"
    return bytes(data)


def read_webp(path: Path):
    ba = QByteArray(path.read_bytes())
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buf, b"webp")
    out = []
    for _ in range(reader.imageCount()):
        image = reader.read()
        out.append((image.width(), image.height(), reader.nextImageDelay()))
    return reader.loopCount(), out


def put_cover(tmp_path: Path, data: bytes, ext="gif", cache=False) -> Path:
    folder = tmp_path / "covers" / ("cache" if cache else "")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{uuid4().hex}.{ext}"
    path.write_bytes(data)
    return path


def test_card_paths_validation():
    name = "a" * 32
    assert cover_cards.card_path(f"{name}.still.webp") is not None
    assert cover_cards.card_path(f"{name}.anim.webp") is not None
    for bad in (
        f"../{name}.still.webp",
        "cards/../../wlib.db",
        f"{name}.still.png",
        f"{name}.gif",
        "x.still.webp",
        None,
        5,
    ):
        assert cover_cards.card_path(bad) is None
    assert cover_cards.card_paths("../x.gif") is None
    still, anim = cover_cards.card_paths(f"{name}.gif")
    assert still.name == f"{name}.still.webp" and anim.name == f"{name}.anim.webp"


def test_wide_gif_makes_scaled_copies(tmp_path):
    source = put_cover(tmp_path, make_gif(1600, 400, frames=3, delay_cs=5))
    assert cover_cards.make_card_copies(source) == (True, True)
    still, anim = cover_cards.card_paths(source.name)
    assert [(w, h) for w, h, _ in read_webp(still)[1]] == [(640, 160)]
    loops, frames = read_webp(anim)
    assert loops == -1
    assert [(w, h) for w, h, _ in frames] == [(640, 160)] * 3
    assert [d for *_, d in frames] == [50, 50, 50]


def test_small_gif_not_upscaled(tmp_path):
    source = put_cover(tmp_path, make_gif(30, 17))
    assert cover_cards.make_card_copies(source) == (True, True)
    still, anim = cover_cards.card_paths(source.name)
    assert read_webp(still)[1][0][:2] == (30, 17)
    assert read_webp(anim)[1][0][:2] == (30, 17)


def test_static_images_make_nothing(tmp_path):
    static_gif = put_cover(tmp_path, make_gif(frames=1))
    png = put_cover(tmp_path, b"\x89PNG\r\n\x1a\n", ext="png")
    for source in (static_gif, png):
        assert cover_cards.make_card_copies(source) == (False, False)
        assert not cover_cards.card_paths(source.name)[0].exists()


def test_over_limit_gif_keeps_only_still(tmp_path, monkeypatch):
    monkeypatch.setattr(cover_cards, "MAX_FRAMES", 2)
    source = put_cover(tmp_path, make_gif(frames=3))
    assert cover_cards.make_card_copies(source) == (True, False)
    still, anim = cover_cards.card_paths(source.name)
    assert still.exists() and not anim.exists()


def test_output_size_limit_keeps_only_still(tmp_path, monkeypatch):
    source = put_cover(tmp_path, make_gif(frames=3))
    monkeypatch.setattr(cover_cards, "MAX_OUTPUT_BYTES", 10)
    assert cover_cards.make_card_copies(source) == (True, False)


def test_corrupt_gif_makes_nothing(tmp_path):
    source = put_cover(tmp_path, b"GIF89a" + b"\x00" * 20)
    assert cover_cards.make_card_copies(source) == (False, False)
    assert not list((tmp_path / "covers" / "cards").glob("*")) if (tmp_path / "covers" / "cards").exists() else True


def test_sweep_deletes_only_orphans(tmp_path):
    source = put_cover(tmp_path, make_gif())
    cover_cards.make_card_copies(source)
    orphan = "b" * 32
    cards = cover_cards.cards_dir()
    (cards / f"{orphan}.still.webp").write_bytes(b"x")
    (cards / f"{orphan}.anim.webp").write_bytes(b"x")
    (cards / ".card-stale").write_bytes(b"x")
    os.utime(cards / ".card-stale", (0, 0))
    (cards / ".card-inflight").write_bytes(b"x")
    outside = tmp_path / "covers" / f"{orphan}.still.webp"
    outside.write_bytes(b"x")
    (tmp_path / "wlib-keep.txt").write_bytes(b"x")
    cover_cards.sweep_orphans()
    assert sorted(p.name for p in cards.iterdir()) == sorted(
        [f"{source.stem}.still.webp", f"{source.stem}.anim.webp", ".card-inflight"]
    )
    assert outside.exists() and (tmp_path / "wlib-keep.txt").exists()


def test_sweep_keeps_cached_source_copies(tmp_path):
    source = put_cover(tmp_path, make_gif(), cache=True)
    cover_cards.make_card_copies(source)
    cover_cards.sweep_orphans()
    assert cover_cards.card_paths(source.name)[0].exists()
    source.unlink()
    cover_cards.sweep_orphans()
    assert not cover_cards.card_paths(source.name)[0].exists()


def test_pending_sources_lists_referenced_without_copies(tmp_path):
    game_id = database.add_game("G", "/g/game.sh") if hasattr(database, "add_game") else None
    assert game_id is not None
    local = put_cover(tmp_path, make_gif())
    cached = put_cover(tmp_path, make_gif(), cache=True)
    png = put_cover(tmp_path, b"\x89PNG\r\n\x1a\n", ext="png")
    database.set_game_local_cover(game_id, local.name)
    with database.get_connection() as conn:
        conn.execute("UPDATE games SET cover_cache = ? WHERE id = ?", (cached.name, game_id))
        conn.commit()
    assert {p.name for p in cover_cards.pending_sources()} == {local.name, cached.name}
    cover_cards.make_card_copies(local)
    assert {p.name for p in cover_cards.pending_sources()} == {cached.name}
    assert png.name not in {p.name for p in cover_cards.pending_sources()}


def test_service_processes_once_and_skips_failures(tmp_path):
    a, b = tmp_path / ("a" * 32 + ".gif"), tmp_path / ("b" * 32 + ".gif")
    calls: list[str] = []

    def make(path):
        calls.append(path.name)
        return (path is a, False)

    notices: list[int] = []
    service = cover_cards.CoverCardService(
        on_copies_ready=lambda: notices.append(1),
        pending=lambda: [a, b],
        make=make,
    )
    service.process_pending()
    service.process_pending()
    assert calls == [a.name, b.name]
    assert notices


def test_service_disabled_without_webp_writer(monkeypatch):
    monkeypatch.setattr(cover_cards, "webp_writer_available", lambda: False)
    service = cover_cards.CoverCardService()
    service.start()
    assert service.disabled and service._thread is None
    service.wake()
    assert not service._wake.is_set()


def test_notices_are_debounced(tmp_path):
    now = [0.0]
    sources = [tmp_path / f"{i:032x}.gif" for i in range(40)]
    notices: list[int] = []

    def make(_path):
        now[0] += 0.5
        return True, True

    service = cover_cards.CoverCardService(
        on_copies_ready=lambda: notices.append(1),
        pending=lambda: sources,
        make=make,
        clock=lambda: now[0],
    )
    service.process_pending()
    # 40 copies over 20 s: at most one per 5 s plus the final drain notice.
    assert 2 <= len(notices) <= 6


# --- API integration -------------------------------------------------------


def _api():
    api = Api()
    api.library_sync.server_available = True
    return api


def _game(tmp_path, local=None, cached=None, url="https://x/c.gif"):
    game_id = database.add_game("G", "/g/game.sh")
    with database.get_connection() as conn:
        conn.execute("UPDATE games SET cover_image_path = ? WHERE id = ?", (url, game_id))
        if cached:
            conn.execute(
                "UPDATE games SET cover_cache = ?, cover_cache_url = ? WHERE id = ?",
                (cached.name, url, game_id),
            )
        conn.commit()
    if local:
        database.set_game_local_cover(game_id, local.name)
    return game_id


def test_get_games_reports_card_urls_for_shown_source(tmp_path):
    local = put_cover(tmp_path, make_gif())
    cached = put_cover(tmp_path, make_gif(), cache=True)
    _game(tmp_path, local=local, cached=cached)
    api = _api()
    game = api.get_games()[0]
    assert "cover_card_still_url" not in game  # copies not made yet
    cover_cards.make_card_copies(cached)
    game = api.get_games()[0]
    assert "cover_card_still_url" not in game  # local cover wins, no copy for it
    cover_cards.make_card_copies(local)
    game = api.get_games()[0]
    base = "http://127.0.0.1:8183/covers/cards/"
    assert game["cover_card_still_url"] == f"{base}{local.stem}.still.webp"
    assert game["cover_card_anim_url"] == f"{base}{local.stem}.anim.webp"


def test_anim_url_absent_when_only_still_exists(tmp_path, monkeypatch):
    cached = put_cover(tmp_path, make_gif(frames=3), cache=True)
    monkeypatch.setattr(cover_cards, "MAX_FRAMES", 2)
    cover_cards.make_card_copies(cached)
    _game(tmp_path, cached=cached)
    game = _api().get_games()[0]
    assert "cover_card_still_url" in game and "cover_card_anim_url" not in game


def test_stale_cache_name_gets_no_card_urls(tmp_path):
    cached = put_cover(tmp_path, make_gif(), cache=True)
    cover_cards.make_card_copies(cached)
    _game(tmp_path, cached=cached, url="https://x/c.gif")
    with database.get_connection() as conn:
        conn.execute("UPDATE games SET cover_image_path = 'https://x/other.gif'")
        conn.commit()
    game = _api().get_games()[0]
    assert "cover_card_still_url" not in game


def test_replacing_local_cover_removes_old_copies(tmp_path):
    old = put_cover(tmp_path, make_gif())
    game_id = _game(tmp_path, local=old)
    cover_cards.make_card_copies(old)
    new = tmp_path / "new.png"
    new.write_bytes(b"\x89PNG\r\n\x1a\nx" + b"\x00\x00\x00\x00IEND\xaeB`\x82")
    api = _api()
    assert api.set_game_cover_file(game_id, str(new))["success"]
    assert not cover_cards.card_paths(old.name)[0].exists()
    assert not cover_cards.card_paths(old.name)[1].exists()


def test_delete_game_and_clear_cover_remove_copies(tmp_path):
    a = put_cover(tmp_path, make_gif())
    game_a = _game(tmp_path, local=a)
    cover_cards.make_card_copies(a)
    api = _api()
    assert api.clear_game_cover(game_a)["success"]
    assert not cover_cards.card_paths(a.name)[0].exists()
    b = put_cover(tmp_path, make_gif())
    game_b = database.add_game("B", "/g/b.sh")
    database.set_game_local_cover(game_b, b.name)
    cover_cards.make_card_copies(b)
    assert api.delete_game(game_b)["success"]
    assert not cover_cards.card_paths(b.name)[0].exists()


def test_purge_removes_cached_copies_and_keeps_local(tmp_path):
    local = put_cover(tmp_path, make_gif())
    cached = put_cover(tmp_path, make_gif(), cache=True)
    _game(tmp_path, local=local, cached=cached)
    cover_cards.make_card_copies(local)
    cover_cards.make_card_copies(cached)
    api = _api()
    assert api.purge_cover_cache()["success"]
    assert cover_cards.card_paths(local.name)[0].exists()
    assert not cover_cards.card_paths(cached.name)[0].exists()


def test_copies_stay_out_of_export_and_dirty_tracking(tmp_path):
    import json

    from core.library_backup import build_library_backup, build_sync_backup

    local = put_cover(tmp_path, make_gif())
    _game(tmp_path, local=local)
    before_sync = json.dumps(build_sync_backup("t", "d")["games"], sort_keys=True, default=str)
    before_export = json.dumps(build_library_backup(), sort_keys=True, default=str)
    cover_cards.make_card_copies(local)
    cover_cards.sweep_orphans()
    after_sync = json.dumps(build_sync_backup("t", "d")["games"], sort_keys=True, default=str)
    after_export = json.dumps(build_library_backup(), sort_keys=True, default=str)
    assert "cards" not in after_export and "still.webp" not in after_export
    assert before_sync == after_sync
    assert json.loads(before_export).get("games") == json.loads(after_export).get("games")


def test_is_animated_reads_gif_webp_and_avif_headers(tmp_path):
    assert cover_cards.is_animated(put_cover(tmp_path, make_gif(frames=3)))
    assert not cover_cards.is_animated(put_cover(tmp_path, make_gif(frames=1)))
    vp8x = b"RIFF\x00\x00\x00\x00WEBPVP8X\x0a\x00\x00\x00"
    assert cover_cards.is_animated(put_cover(tmp_path, vp8x + b"\x02" + b"\x00" * 9, "webp"))
    assert not cover_cards.is_animated(put_cover(tmp_path, vp8x + b"\x00" + b"\x00" * 9, "webp"))
    assert not cover_cards.is_animated(put_cover(tmp_path, b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 20, "webp"))
    def ftyp(brands):
        return struct.pack(">I", 16 + 4 * len(brands)) + b"ftypavif\x00\x00\x00\x00" + b"".join(brands)
    assert cover_cards.is_animated(put_cover(tmp_path, ftyp([b"avif", b"avis"]), "avif"))
    assert not cover_cards.is_animated(put_cover(tmp_path, ftyp([b"avif", b"mif1"]), "avif"))
    assert not cover_cards.is_animated(put_cover(tmp_path, b"\x89PNG", "png"))
    assert not cover_cards.is_animated(tmp_path / "missing.gif")


def test_get_games_flags_an_animated_cover(tmp_path):
    local = put_cover(tmp_path, make_gif())
    _game(tmp_path, local=local)
    api = _api()
    assert api.get_games()[0].get("cover_animated") is True
    cover_cards.make_card_copies(local)
    game = api.get_games()[0]
    assert "cover_card_still_url" in game and game.get("cover_animated") is True
    static = put_cover(tmp_path, make_gif(frames=1))
    _game(tmp_path, local=static)
    assert [g.get("cover_animated", False) for g in api.get_games()] == [True, False]
