# SPDX-License-Identifier: GPL-3.0-or-later
"""RPG Maker MV/MZ tools: asset decryption and the in-game decrypted-files switch.

Decrypted copies are written next to the encrypted originals, which are kept.
Everything takes the data root (the folder holding `data/System.json`) so it can
be tested against a temp dir.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from collections.abc import Iterator
from dataclasses import dataclass
from typing import cast

from core.game_tools import (
    CHUNK_SIZE,
    PARTIAL_SUFFIX,
    CancelFn,
    GameToolError,
    ProgressFn,
)

ENCRYPTED_EXTENSIONS = {
    ".rpgmvp": ".png",
    ".png_": ".png",
    ".rpgmvo": ".ogg",
    ".ogg_": ".ogg",
    ".rpgmvm": ".m4a",
    ".m4a_": ".m4a",
}
IMAGE_EXTENSIONS = (".rpgmvp", ".png_")
FILE_HEADER_LENGTH = 16
ENCRYPTED_PREFIX_LENGTH = 16
MAGIC = b"RPGMV"
MIN_ENCRYPTED_SIZE = 32
PNG_HEADER = bytes.fromhex("89504e470d0a1a0a0000000d49484452")
KEY_PATTERN = re.compile(r"^[0-9a-fA-F]{32}$")
BACKUP_SUFFIX = ".wlib-backup"
KEY_SOURCE_SYSTEM = "system"
KEY_SOURCE_RECOVERED = "recovered"


@dataclass
class Asset:
    path: str
    destination: str
    image: bool


def system_json_path(root: str) -> str:
    return os.path.join(root, "data", "System.json")


def _load_system(root: str) -> dict[str, object]:
    try:
        with open(system_json_path(root), encoding="utf-8-sig") as file:
            data = cast(object, json.load(file))
    except (OSError, ValueError) as exc:
        raise GameToolError(f"Could not read System.json: {exc}") from exc
    if not isinstance(data, dict):
        raise GameToolError("System.json has an unexpected shape")
    return cast(dict[str, object], data)


def read_key(root: str) -> str | None:
    """The 32-hex-digit `encryptionKey`, or None when missing or malformed."""
    try:
        value = _load_system(root).get("encryptionKey")
    except GameToolError:
        return None
    if isinstance(value, str) and KEY_PATTERN.match(value):
        return value
    return None


def iter_assets(root: str) -> Iterator[Asset]:
    for current, dirs, files in os.walk(root, followlinks=False):
        dirs.sort()
        for name in sorted(files):
            stem, extension = os.path.splitext(name)
            lowered = extension.lower()
            target = ENCRYPTED_EXTENSIONS.get(lowered)
            if target is None:
                continue
            path = os.path.join(current, name)
            if os.path.islink(path) or not os.path.isfile(path):
                continue
            yield Asset(
                path, os.path.join(current, stem + target), lowered in IMAGE_EXTENSIONS
            )


def scan_assets(root: str) -> list[Asset]:
    return list(iter_assets(root))


def _read_head(path: str, length: int) -> bytes:
    try:
        with open(path, "rb") as file:
            return file.read(length)
    except OSError:
        return b""


def is_encrypted_file(path: str) -> bool:
    """Starts with the RPG Maker header and is long enough to hold the XOR block."""
    try:
        if os.path.getsize(path) < MIN_ENCRYPTED_SIZE:
            return False
    except OSError:
        return False
    return _read_head(path, len(MAGIC)) == MAGIC


def _xor(data: bytes, key: bytes) -> bytes:
    return bytes(a ^ b for a, b in zip(data, key, strict=False))


def recover_key(assets: list[Asset]) -> str | None:
    """Derive the key from encrypted PNG headers, confirmed on a second image."""
    candidates: list[bytes] = []
    for asset in assets:
        if not asset.image or not is_encrypted_file(asset.path):
            continue
        block = _read_head(asset.path, FILE_HEADER_LENGTH + ENCRYPTED_PREFIX_LENGTH)
        if len(block) < FILE_HEADER_LENGTH + ENCRYPTED_PREFIX_LENGTH:
            continue
        candidates.append(block[FILE_HEADER_LENGTH:])
        if len(candidates) == 2:
            break
    if not candidates:
        return None
    key = _xor(candidates[0], PNG_HEADER)
    if len(candidates) == 2 and _xor(candidates[1], key) != PNG_HEADER:
        return None
    return key.hex()


def resolve_key(root: str, assets: list[Asset]) -> tuple[str | None, str]:
    """(key, source) where source is "system", "recovered" or ""."""
    key = read_key(root)
    if key is not None:
        return key, KEY_SOURCE_SYSTEM
    recovered = recover_key(assets)
    if recovered is not None:
        return recovered, KEY_SOURCE_RECOVERED
    return None, ""


def missing_copies(assets: list[Asset]) -> list[Asset]:
    """Really encrypted assets that have no decrypted copy yet."""
    return [
        asset
        for asset in assets
        if not os.path.lexists(asset.destination) and is_encrypted_file(asset.path)
    ]


class _Cancelled(Exception):
    pass


def _decrypt_file(asset: Asset, key: bytes, cancelled: CancelFn) -> None:
    temporary = asset.destination + PARTIAL_SUFFIX
    try:
        with open(asset.path, "rb") as source, open(temporary, "wb") as out:
            _ = source.seek(FILE_HEADER_LENGTH)
            block = source.read(ENCRYPTED_PREFIX_LENGTH)
            _ = out.write(_xor(block, key) + block[len(key) :])
            while True:
                if cancelled():
                    raise _Cancelled
                chunk = source.read(CHUNK_SIZE)
                if not chunk:
                    break
                _ = out.write(chunk)
        os.replace(temporary, asset.destination)
    finally:
        try:
            os.remove(temporary)
        except FileNotFoundError:
            pass


def decrypt_assets(
    root: str, progress: ProgressFn, cancelled: CancelFn
) -> dict[str, object]:
    assets = scan_assets(root)
    if not assets:
        raise GameToolError("This game's assets are not encrypted")
    key_text, source = resolve_key(root, assets)
    if key_text is None:
        raise GameToolError("Could not find the encryption key")
    key = bytes.fromhex(key_text)

    decrypted = already = skipped = 0
    was_cancelled = False
    total = len(assets)
    progress(0, total, "")
    for done, asset in enumerate(assets):
        if cancelled():
            was_cancelled = True
            break
        progress(done, total, os.path.relpath(asset.path, root).replace(os.sep, "/"))
        if os.path.lexists(asset.destination):
            already += 1
            continue
        if not is_encrypted_file(asset.path):
            skipped += 1
            continue
        try:
            _decrypt_file(asset, key, cancelled)
        except _Cancelled:
            was_cancelled = True
            break
        except PermissionError as exc:
            raise GameToolError(str(exc)) from exc
        except OSError:
            skipped += 1
            continue
        decrypted += 1
    if not was_cancelled:
        progress(total, total, "")
    return {
        "decrypted": decrypted,
        "already": already,
        "skipped": skipped,
        "cancelled": was_cancelled,
        "key_source": source,
    }


# ── In-game switch ──


def read_use_decrypted(root: str) -> bool:
    """On exactly when both encryption flags in System.json are false."""
    try:
        data = _load_system(root)
    except GameToolError:
        return False
    return (
        data.get("hasEncryptedImages") is False
        and data.get("hasEncryptedAudio") is False
    )


def has_backup(root: str) -> bool:
    return os.path.isfile(system_json_path(root) + BACKUP_SUFFIX)


def _original_flags(backup: str) -> dict[str, bool]:
    """Encryption flags from the backup, so turning the switch off restores them.

    A game may encrypt only its images; forcing audio back to true would make it
    look for encrypted audio files that never existed. Without a readable backup
    both flags go back to true.
    """
    flags = {"hasEncryptedImages": True, "hasEncryptedAudio": True}
    try:
        with open(backup, encoding="utf-8-sig") as file:
            data = cast(object, json.load(file))
    except (OSError, ValueError):
        return flags
    if isinstance(data, dict):
        for name in flags:
            value = cast(dict[str, object], data).get(name)
            if isinstance(value, bool):
                flags[name] = value
    return flags


def set_use_decrypted(root: str, enabled: bool) -> None:
    path = system_json_path(root)
    if enabled:
        pending = len(missing_copies(scan_assets(root)))
        if pending:
            raise GameToolError(
                f"{pending} encrypted asset(s) still need decrypting first"
            )
    data = _load_system(root)
    temporary = path + PARTIAL_SUFFIX
    try:
        backup = path + BACKUP_SUFFIX
        if enabled and not os.path.exists(backup):
            _ = shutil.copy2(path, backup)
        if enabled:
            data["hasEncryptedImages"] = False
            data["hasEncryptedAudio"] = False
        else:
            data.update(_original_flags(backup))
        with open(temporary, "w", encoding="utf-8", newline="") as file:
            _ = file.write(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
        os.replace(temporary, path)
    except OSError as exc:
        raise GameToolError(str(exc)) from exc
    finally:
        try:
            os.remove(temporary)
        except FileNotFoundError:
            pass
