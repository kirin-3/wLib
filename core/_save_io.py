# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared save-editor error types, fingerprinting, and atomic temp-file replacement."""

from __future__ import annotations

import hashlib
import os
import shutil
from typing import ClassVar

from core.game_tools import create_temp_file

TEMP_SUFFIX = ".wlib-tmp"
CHANGED_MESSAGE = "The save changed since it was opened. Reopen the slot."

Fingerprint = tuple[int, int, int, int, str]


class RpgmSaveError(Exception):
    """A save editor action failed; the message is safe to show the user."""

    default_code: ClassVar[str] = "error"

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.code: str = code if code is not None else self.default_code


class UnsupportedSaveFormat(RpgmSaveError):
    default_code: ClassVar[str] = "unsupported_format"


class InvalidPatch(RpgmSaveError):
    default_code: ClassVar[str] = "invalid_patch"


class SaveChanged(RpgmSaveError):
    default_code: ClassVar[str] = "changed"


class BackupFailed(RpgmSaveError):
    default_code: ClassVar[str] = "backup_failed"


def reject_symlink(path: str) -> None:
    if os.path.islink(path):
        # The backup skips links and the replace would turn the link into a
        # plain file, so the edit would have no backup of the real slot.
        raise RpgmSaveError(
            "This save file is a link to another file. Linked saves cannot be edited.",
            "write_failed",
        )


def read_fingerprint(
    path: str, *, max_bytes: int | None = None
) -> tuple[bytes, Fingerprint]:
    """Read `path` and return its bytes and a fingerprint of those bytes.

    The fingerprint holds the size, mtime_ns, device and inode from the open
    handle, and a SHA-256 of the bytes read.
    """
    try:
        with open(path, "rb") as handle:
            stat = os.fstat(handle.fileno())
            if max_bytes is not None and stat.st_size > max_bytes:
                raise UnsupportedSaveFormat("This save file is too large to open")
            data = handle.read() if max_bytes is None else handle.read(max_bytes + 1)
    except UnsupportedSaveFormat:
        raise
    except FileNotFoundError as exc:
        raise SaveChanged("The save file is gone. Reopen the slot.") from exc
    except OSError as exc:
        raise RpgmSaveError("The save file could not be read", "read_failed") from exc
    if max_bytes is not None and len(data) > max_bytes:
        raise UnsupportedSaveFormat("This save file is too large to open")
    digest = hashlib.sha256(data).hexdigest()
    return data, (len(data), stat.st_mtime_ns, stat.st_dev, stat.st_ino, digest)


def atomic_replace_checked(
    path: str, encoded: bytes, fingerprint: Fingerprint
) -> None:
    """Write `encoded` to a temp file, recheck `fingerprint`, and replace `path`."""
    temp = ""
    try:
        fd, temp = create_temp_file(path, TEMP_SUFFIX)
        with os.fdopen(fd, "wb") as handle:
            _ = handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            shutil.copymode(path, temp)
        except OSError:
            pass
        _, current = read_fingerprint(path)
        if current != fingerprint:
            raise SaveChanged(CHANGED_MESSAGE)
        os.replace(temp, path)
    except OSError as exc:
        raise RpgmSaveError(
            f"The save could not be written: {exc.strerror or exc}", "write_failed"
        ) from exc
    finally:
        if temp and os.path.lexists(temp):
            try:
                os.remove(temp)
            except OSError:
                pass
