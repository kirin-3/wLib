# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import os
import shutil
import stat
from typing import BinaryIO

from core import database


class AttachmentMissingError(FileNotFoundError):
    """Raised when an attachment file does not exist in the game folder."""


class NotAPdfError(ValueError):
    """Raised when a file is not an attachable PDF."""


def attachments_dir(game_id: int) -> str:
    return os.path.join(
        database.DATA_DIR, "attachments", str(int(game_id))
    )


def list_attachments(game_id: int) -> list[dict[str, object]]:
    folder = attachments_dir(game_id)
    try:
        entries = os.listdir(folder)
    except FileNotFoundError:
        return []
    results: list[dict[str, object]] = []
    for name in entries:
        if name.startswith("."):
            continue
        if not name.lower().endswith(".pdf"):
            continue
        full_path = os.path.join(folder, name)
        try:
            if not os.path.isfile(full_path):
                continue
            size = os.path.getsize(full_path)
        except OSError:
            continue
        results.append({"name": name, "size": int(size)})
    results.sort(key=lambda item: str(item["name"]).lower())
    return results


def remove_game_attachments(game_id: int) -> None:
    folder = attachments_dir(game_id)
    try:
        shutil.rmtree(folder)
    except FileNotFoundError:
        return
    except OSError:
        raise


def _check_attachment_name(name: object) -> str:
    text = str(name or "")
    if not text:
        raise ValueError("Attachment name is required")
    if "\x00" in text:
        raise ValueError("Invalid attachment name")
    if text in (".", ".."):
        raise ValueError("Invalid attachment name")
    if text.startswith("."):
        raise ValueError("Invalid attachment name")
    if "/" in text or "\\" in text:
        raise ValueError("Invalid attachment name")
    return text


def resolve_attachment(game_id: int, name: object) -> str:
    safe_name = _check_attachment_name(name)
    folder = attachments_dir(game_id)
    folder_real = os.path.realpath(folder)
    candidate = os.path.realpath(os.path.join(folder, safe_name))
    if os.path.dirname(candidate) != folder_real:
        raise ValueError("Invalid attachment name")
    if not os.path.isfile(candidate):
        raise AttachmentMissingError(f"Attachment not found: {safe_name}")
    return candidate


def remove_attachment(game_id: int, name: object) -> None:
    resolved = resolve_attachment(game_id, name)
    try:
        os.remove(resolved)
    except FileNotFoundError as exc:
        raise AttachmentMissingError(str(exc)) from exc


def _free_attachment_name(folder: str, base_name: str) -> str:
    stem = base_name[:-4] if base_name.lower().endswith(".pdf") else base_name
    candidate = base_name
    counter = 2
    while os.path.lexists(os.path.join(folder, candidate)) or os.path.lexists(
        os.path.join(folder, f".{candidate}.part")
    ):
        candidate = f"{stem} ({counter}).pdf"
        counter += 1
    return candidate


def _open_pdf_source(path: str) -> BinaryIO:
    """Open a regular file for reading; reject FIFOs/devices before touching them.

    A picked source (or a path from a hand-edited library file) can be a FIFO;
    opening one for reading blocks until a writer appears.
    """
    flags = (
        os.O_RDONLY
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_BINARY", 0)
    )
    fd = os.open(path, flags)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise NotAPdfError("Only PDF files can be attached")
    except BaseException:
        os.close(fd)
        raise
    return os.fdopen(fd, "rb")


def add_attachment(game_id: int, source_path: object) -> dict[str, object]:
    source = os.path.expanduser(str(source_path or "").strip())
    if not source.lower().endswith(".pdf"):
        raise NotAPdfError("Only PDF files can be attached")
    try:
        handle = _open_pdf_source(source)
    except NotAPdfError:
        raise
    except OSError as exc:
        raise OSError(str(exc) or "Could not read file") from exc

    with handle:
        signature = handle.read(5)
        if signature != b"%PDF-":
            raise NotAPdfError("Only PDF files can be attached")

        base_name = os.path.basename(source)
        if not base_name or base_name.startswith("."):
            raise NotAPdfError("Only PDF files can be attached")
        folder = attachments_dir(game_id)
        os.makedirs(folder, exist_ok=True)
        target_name = _free_attachment_name(folder, base_name)
        tmp_name = f".{target_name}.part"
        tmp_path = os.path.join(folder, tmp_name)
        final_path = os.path.join(folder, target_name)
        try:
            _ = handle.seek(0)
            with open(tmp_path, "wb") as out:
                _ = shutil.copyfileobj(handle, out)
            os.replace(tmp_path, final_path)
        except Exception:
            try:
                if os.path.lexists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass
            raise
    size = os.path.getsize(final_path)
    return {"name": target_name, "size": int(size)}


def prune_orphan_attachments(existing_ids: set[int] | list[int]) -> None:
    live = {str(int(item)) for item in existing_ids}
    base = os.path.join(database.DATA_DIR, "attachments")
    try:
        entries = os.listdir(base)
    except FileNotFoundError:
        return
    for entry in entries:
        if not entry.isdigit():
            continue
        if entry in live:
            continue
        full_path = os.path.join(base, entry)
        # Only remove directories; a stray file named "123" is left alone.
        try:
            if os.path.isdir(full_path) and not os.path.islink(full_path):
                shutil.rmtree(full_path)
        except OSError:
            continue
