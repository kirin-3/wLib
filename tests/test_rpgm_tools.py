# pyright: reportMissingImports=false
# SPDX-License-Identifier: GPL-3.0-or-later
import json
import os
from pathlib import Path

import pytest

from core import rpgm_tools
from core.game_tools import GameToolError

KEY = "00112233445566778899aabbccddeeff"
PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452") + b"image-body" * 20
HEADER = b"RPGMV\x00\x00\x00\x00\x03\x01" + b"\x00" * 5
assert len(HEADER) == 16


def encrypt(data: bytes, key: str = KEY) -> bytes:
    raw = bytes.fromhex(key)
    first = bytes(a ^ b for a, b in zip(data[:16], raw, strict=True))
    return HEADER + first + data[16:]


def system_json(root: Path, **extra: object) -> None:
    data: dict[str, object] = {
        "gameTitle": "T",
        "hasEncryptedImages": True,
        "hasEncryptedAudio": True,
        "encryptionKey": KEY,
        **extra,
    }
    (root / "data").mkdir(parents=True, exist_ok=True)
    (root / "data" / "System.json").write_text(json.dumps(data), encoding="utf-8")


def no_progress(_done: int, _total: int, _current: str) -> None:
    return None


def never() -> bool:
    return False


def make_mv(tmp_path: Path) -> Path:
    root = tmp_path / "www"
    system_json(root)
    for rel, body in {
        "img/faces/Actor1.rpgmvp": PNG,
        "img/pictures/Title.png_": PNG,
        "audio/bgm/Theme.rpgmvo": b"OggS" + b"o" * 60,
        "audio/se/Hit.ogg_": b"OggS" + b"s" * 60,
        "audio/bgm/Song.rpgmvm": b"m4a-" * 20,
        "audio/me/Fan.m4a_": b"fan-" * 20,
    }.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(encrypt(body))
    return root


# ── Key ──


def test_read_key_valid_bom_and_malformed(tmp_path):
    system_json(tmp_path)
    assert rpgm_tools.read_key(str(tmp_path)) == KEY

    path = tmp_path / "data" / "System.json"
    path.write_text(json.dumps({"encryptionKey": KEY}), encoding="utf-8-sig")
    assert path.read_bytes().startswith(b"\xef\xbb\xbf")
    assert rpgm_tools.read_key(str(tmp_path)) == KEY

    for bad in ("short", KEY + "00", "z" * 32, None, 5):
        path.write_text(json.dumps({"encryptionKey": bad}), encoding="utf-8")
        assert rpgm_tools.read_key(str(tmp_path)) is None


def test_recover_key_and_second_image_confirmation(tmp_path):
    root = make_mv(tmp_path)
    system_json(root, encryptionKey="")
    assets = rpgm_tools.scan_assets(str(root))
    assert rpgm_tools.resolve_key(str(root), assets) == (KEY, "recovered")

    other = "ffeeddccbbaa99887766554433221100"
    (root / "img" / "pictures" / "Title.png_").write_bytes(encrypt(PNG, other))
    assert rpgm_tools.recover_key(rpgm_tools.scan_assets(str(root))) is None


def test_audio_only_has_no_recoverable_key(tmp_path):
    root = tmp_path / "www"
    system_json(root, encryptionKey="")
    audio = root / "audio" / "bgm" / "Theme.rpgmvo"
    audio.parent.mkdir(parents=True)
    audio.write_bytes(encrypt(b"OggS" + b"o" * 60))
    assets = rpgm_tools.scan_assets(str(root))
    assert rpgm_tools.resolve_key(str(root), assets) == (None, "")
    with pytest.raises(GameToolError, match="encryption key"):
        _ = rpgm_tools.decrypt_assets(str(root), no_progress, never)
    assert not (audio.parent / "Theme.ogg").exists()


# ── Decrypt ──


def test_decrypt_mv_and_mz_files_byte_identical(tmp_path):
    root = make_mv(tmp_path)
    result = rpgm_tools.decrypt_assets(str(root), no_progress, never)
    assert result["decrypted"] == 6
    assert result["key_source"] == "system"
    assert (root / "img/faces/Actor1.png").read_bytes() == PNG
    assert (root / "img/pictures/Title.png").read_bytes() == PNG
    assert (root / "audio/bgm/Theme.ogg").read_bytes() == b"OggS" + b"o" * 60
    assert (root / "audio/se/Hit.ogg").read_bytes() == b"OggS" + b"s" * 60
    assert (root / "audio/bgm/Song.m4a").read_bytes() == b"m4a-" * 20
    assert (root / "audio/me/Fan.m4a").read_bytes() == b"fan-" * 20
    assert (root / "img/faces/Actor1.rpgmvp").exists()
    assert not [p for p in root.rglob("*") if p.name.endswith(".wlib-partial")]


def test_decrypt_uses_recovered_key_for_audio(tmp_path):
    root = make_mv(tmp_path)
    system_json(root, encryptionKey="")
    result = rpgm_tools.decrypt_assets(str(root), no_progress, never)
    assert result["key_source"] == "recovered"
    assert (root / "audio/bgm/Theme.ogg").read_bytes() == b"OggS" + b"o" * 60


def test_decrypt_skips_fake_header_and_keeps_existing_copy(tmp_path):
    root = make_mv(tmp_path)
    (root / "img" / "faces" / "Fake.rpgmvp").write_bytes(b"X" * 100)
    (root / "img" / "faces" / "Short.rpgmvp").write_bytes(b"RPGMV")
    (root / "img" / "faces" / "Actor1.png").write_bytes(b"mine")

    result = rpgm_tools.decrypt_assets(str(root), no_progress, never)

    assert result["already"] == 1
    assert result["skipped"] == 2
    assert result["decrypted"] == 5
    assert (root / "img/faces/Actor1.png").read_bytes() == b"mine"
    assert not (root / "img/faces/Fake.png").exists()
    assert not (root / "img/faces/Short.png").exists()


def test_decrypt_cancel_leaves_no_partial(tmp_path, monkeypatch):
    root = make_mv(tmp_path)
    seen: list[str] = []

    def progress(_d: int, _t: int, current: str) -> None:
        if current:
            seen.append(current)

    result = rpgm_tools.decrypt_assets(str(root), progress, lambda: len(seen) > 2)
    assert result["cancelled"] is True
    assert result["decrypted"] == 2
    assert not [p for p in root.rglob("*") if p.name.endswith(".wlib-partial")]

    # Cancel inside a file removes its partial copy too.
    monkeypatch.setattr(rpgm_tools, "CHUNK_SIZE", 8)
    for path in [p for p in root.rglob("*") if p.suffix in {".png", ".ogg", ".m4a"}]:
        path.unlink()
    calls = {"n": 0}

    def cancelled() -> bool:
        calls["n"] += 1
        return calls["n"] > 3

    result = rpgm_tools.decrypt_assets(str(root), no_progress, cancelled)
    assert result["cancelled"] is True
    assert not [p for p in root.rglob("*") if p.name.endswith(".wlib-partial")]


def test_nothing_to_decrypt(tmp_path):
    system_json(tmp_path)
    with pytest.raises(GameToolError, match="not encrypted"):
        _ = rpgm_tools.decrypt_assets(str(tmp_path), no_progress, never)


# ── Use decrypted ──


def test_use_decrypted_on_off_backup_and_key_order(tmp_path):
    root = make_mv(tmp_path)
    path = root / "data" / "System.json"
    original = path.read_bytes()
    before = json.loads(original)
    _ = rpgm_tools.decrypt_assets(str(root), no_progress, never)

    rpgm_tools.set_use_decrypted(str(root), True)

    backup = Path(str(path) + ".wlib-backup")
    assert backup.read_bytes() == original
    after = json.loads(path.read_text(encoding="utf-8"))
    assert after["hasEncryptedImages"] is False
    assert after["hasEncryptedAudio"] is False
    assert list(after) == list(before)
    assert {k: v for k, v in after.items() if not k.startswith("hasEncrypted")} == {
        k: v for k, v in before.items() if not k.startswith("hasEncrypted")
    }
    assert " " not in path.read_text(encoding="utf-8")
    assert rpgm_tools.read_use_decrypted(str(root)) is True
    assert rpgm_tools.has_backup(str(root)) is True

    # A second "on" does not replace the backup with the changed file.
    rpgm_tools.set_use_decrypted(str(root), True)
    assert backup.read_bytes() == original

    rpgm_tools.set_use_decrypted(str(root), False)
    restored = json.loads(path.read_text(encoding="utf-8"))
    assert restored["hasEncryptedImages"] is True
    assert restored["hasEncryptedAudio"] is True
    assert rpgm_tools.read_use_decrypted(str(root)) is False
    assert backup.exists()


def test_use_decrypted_off_restores_original_flags(tmp_path):
    # Only images encrypted: turning the switch off must not claim audio is
    # encrypted, or the game looks for .rpgmvo files that never existed.
    root = tmp_path / "www"
    system_json(root, hasEncryptedAudio=False)
    face = root / "img" / "faces" / "Actor1.rpgmvp"
    face.parent.mkdir(parents=True)
    face.write_bytes(encrypt(PNG))
    _ = rpgm_tools.decrypt_assets(str(root), no_progress, never)

    rpgm_tools.set_use_decrypted(str(root), True)
    rpgm_tools.set_use_decrypted(str(root), False)

    after = json.loads((root / "data" / "System.json").read_text(encoding="utf-8"))
    assert after["hasEncryptedImages"] is True
    assert after["hasEncryptedAudio"] is False


def test_use_decrypted_off_without_backup_sets_both_flags(tmp_path):
    root = tmp_path / "www"
    system_json(root, hasEncryptedImages=False, hasEncryptedAudio=False)
    rpgm_tools.set_use_decrypted(str(root), False)
    after = json.loads((root / "data" / "System.json").read_text(encoding="utf-8"))
    assert after["hasEncryptedImages"] is True
    assert after["hasEncryptedAudio"] is True


def test_use_decrypted_refused_while_copies_missing(tmp_path):
    root = make_mv(tmp_path)
    path = root / "data" / "System.json"
    original = path.read_bytes()
    _ = rpgm_tools.decrypt_assets(str(root), no_progress, never)
    (root / "img/faces/Actor1.png").unlink()
    (root / "audio/bgm/Theme.ogg").unlink()

    with pytest.raises(GameToolError, match="2 encrypted asset"):
        rpgm_tools.set_use_decrypted(str(root), True)

    assert path.read_bytes() == original
    assert not rpgm_tools.has_backup(str(root))


def test_use_decrypted_ignores_fake_header_files(tmp_path):
    root = make_mv(tmp_path)
    (root / "img" / "faces" / "Fake.rpgmvp").write_bytes(b"X" * 100)
    _ = rpgm_tools.decrypt_assets(str(root), no_progress, never)
    rpgm_tools.set_use_decrypted(str(root), True)
    assert rpgm_tools.read_use_decrypted(str(root)) is True


def test_use_decrypted_state_requires_both_flags(tmp_path):
    system_json(tmp_path, hasEncryptedImages=False)
    assert rpgm_tools.read_use_decrypted(str(tmp_path)) is False
    system_json(tmp_path, hasEncryptedImages=False, hasEncryptedAudio=False)
    assert rpgm_tools.read_use_decrypted(str(tmp_path)) is True


def test_symlinked_assets_are_not_followed(tmp_path):
    root = make_mv(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "Evil.rpgmvp").write_bytes(encrypt(PNG))
    os.symlink(outside, root / "img" / "linked")
    names = [os.path.basename(a.path) for a in rpgm_tools.scan_assets(str(root))]
    assert "Evil.rpgmvp" not in names
