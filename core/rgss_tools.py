# SPDX-License-Identifier: GPL-3.0-or-later
"""RPG Maker XP / VX / VX Ace (RGSSAD v1 & v3) archive tools.

Archive contents are untrusted. Every archive header, index, entry name,
data range, and destination path is validated before anything is written.
Everything takes explicit paths so it can be tested against a temp dir.
"""

from __future__ import annotations

from array import array
import os
import shutil
import stat
import struct
import sys
from dataclasses import dataclass, field
from typing import BinaryIO

from core.game_tools import (
    CHUNK_SIZE,
    CancelFn,
    GameToolError,
    ProgressFn,
    check_free_space,
    create_temp_file,
    export_root,
    resolve_destination,
    validate_output_folder,
)

ARCHIVE_EXTENSIONS = (".rgssad", ".rgss2a", ".rgss3a")
BACKUP_SUFFIX = ".wlib-backup"
RGSS_MAGIC = b"RGSSAD\x00"
HEADER_SIZE = 8
FORMAT_V1 = "RGSSAD v1"
FORMAT_V3 = "RGSSAD v3"
VERSION_FORMATS = {
    1: FORMAT_V1,
    3: FORMAT_V3,
}
V1_INITIAL_KEY = 0xDEADCAFE
MASK32 = 0xFFFFFFFF
MAX_NAME_LENGTH = 4096
MAX_ENTRIES = 200_000


class RgssUnsupportedError(Exception):
    """The archive is not an RGSSAD v1 or v3 archive wLib can read safely."""


@dataclass(frozen=True)
class RgssHeader:
    version: int
    format: str


@dataclass(frozen=True)
class RgssEntry:
    name: str
    offset: int
    size: int
    key: int


@dataclass
class RgssIndex:
    version: int
    format: str
    file_size: int
    entries: list[RgssEntry] = field(default_factory=list)
    skipped_invalid: int = 0


@dataclass(frozen=True)
class DiscoveredArchive:
    name: str
    filename: str
    path: str
    size: int
    backup: bool


def _advance_key(key: int) -> int:
    return (key * 7 + 3) & MASK32


def decode_entry_name(raw: bytes) -> str | None:
    """Decode an archive entry name as UTF-8, falling back to cp932 (Shift-JIS)."""
    for encoding in ("utf-8", "cp932"):
        try:
            return raw.decode(encoding).replace("\\", "/")
        except UnicodeDecodeError:
            continue
    return None


def parse_rgss_header(data: bytes) -> RgssHeader:
    if len(data) < HEADER_SIZE or data[: len(RGSS_MAGIC)] != RGSS_MAGIC:
        raise RgssUnsupportedError("Unrecognized RGSS archive header")
    version = data[7]
    fmt = VERSION_FORMATS.get(version)
    if fmt is None:
        raise RgssUnsupportedError(f"Unsupported RGSS archive version {version}")
    return RgssHeader(version=version, format=fmt)


def read_rgss_header(file: BinaryIO) -> RgssHeader:
    _ = file.seek(0)
    return parse_rgss_header(file.read(HEADER_SIZE))


def _read_v1_index(file: BinaryIO, file_size: int) -> RgssIndex:
    index = RgssIndex(version=1, format=FORMAT_V1, file_size=file_size)
    key = V1_INITIAL_KEY
    seen = 0
    while True:
        raw_len = file.read(4)
        if not raw_len:
            break
        if len(raw_len) < 4:
            raise RgssUnsupportedError("RGSSAD v1 entry header is truncated")
        name_len = struct.unpack("<I", raw_len)[0] ^ key
        key = _advance_key(key)
        if name_len == 0 or name_len > MAX_NAME_LENGTH:
            raise RgssUnsupportedError("Invalid RGSSAD v1 entry name length")
        enc_name = file.read(name_len)
        if len(enc_name) < name_len:
            raise RgssUnsupportedError("RGSSAD v1 entry name is truncated")
        name_buf = bytearray(name_len)
        for i, byte in enumerate(enc_name):
            name_buf[i] = byte ^ (key & 0xFF)
            key = _advance_key(key)
        raw_size = file.read(4)
        if len(raw_size) < 4:
            raise RgssUnsupportedError("RGSSAD v1 entry size is truncated")
        size = struct.unpack("<I", raw_size)[0] ^ key
        key = _advance_key(key)
        seen += 1
        if seen > MAX_ENTRIES:
            raise RgssUnsupportedError("RGSSAD v1 archive has too many entries")
        data_offset = file.tell()
        if data_offset + size > file_size:
            index.skipped_invalid += 1
            break
        data_key = key
        _ = file.seek(data_offset + size)
        name = decode_entry_name(bytes(name_buf))
        if name is None:
            index.skipped_invalid += 1
            continue
        index.entries.append(
            RgssEntry(name=name, offset=data_offset, size=size, key=data_key)
        )
    return index


def _read_v3_index(file: BinaryIO, file_size: int) -> RgssIndex:
    raw_seed = file.read(4)
    if len(raw_seed) < 4:
        raise RgssUnsupportedError("RGSSAD v3 header seed is truncated")
    seed = struct.unpack("<I", raw_seed)[0]
    key = (seed * 9 + 3) & MASK32
    key_bytes = key.to_bytes(4, "little")
    index = RgssIndex(version=3, format=FORMAT_V3, file_size=file_size)
    seen = 0
    while True:
        raw_offset = file.read(4)
        if len(raw_offset) < 4:
            raise RgssUnsupportedError("RGSSAD v3 index is truncated")
        offset = struct.unpack("<I", raw_offset)[0] ^ key
        if offset == 0:
            break
        raw_rest = file.read(12)
        if len(raw_rest) < 12:
            raise RgssUnsupportedError("RGSSAD v3 index is truncated")
        enc_size, enc_file_key, enc_name_len = struct.unpack("<III", raw_rest)
        size = enc_size ^ key
        file_key = enc_file_key ^ key
        name_len = enc_name_len ^ key
        if name_len == 0 or name_len > MAX_NAME_LENGTH:
            raise RgssUnsupportedError("Invalid RGSSAD v3 entry name length")
        enc_name = file.read(name_len)
        if len(enc_name) < name_len:
            raise RgssUnsupportedError("RGSSAD v3 index is truncated")
        seen += 1
        if seen > MAX_ENTRIES:
            raise RgssUnsupportedError("RGSSAD v3 archive has too many entries")
        raw_name = bytes(byte ^ key_bytes[i % 4] for i, byte in enumerate(enc_name))
        name = decode_entry_name(raw_name)
        if name is None or offset + size > file_size:
            index.skipped_invalid += 1
            continue
        index.entries.append(
            RgssEntry(name=name, offset=offset, size=size, key=file_key)
        )
    return index


def read_rgss_index(path: str) -> RgssIndex:
    """Read and validate the index of an RGSSAD v1 or v3 archive."""
    try:
        with open(path, "rb") as file:
            file_size = os.fstat(file.fileno()).st_size
            header = read_rgss_header(file)
            if header.version == 1:
                return _read_v1_index(file, file_size)
            return _read_v3_index(file, file_size)
    except OSError as exc:
        raise RgssUnsupportedError(str(exc)) from exc


# ── Streamed decryption ──


class _Cancelled(Exception):
    pass


def _remove_if_present(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def decrypt_chunk(chunk: bytes, key: int) -> tuple[bytes, int]:
    """XOR `chunk` with the RGSS keystream starting at `key`, returning (data, next_key)."""
    length = len(chunk)
    if length == 0:
        return b"", key
    word_count = (length + 3) // 4
    words = array("I")
    append = words.append
    for _ in range(word_count):
        append(key)
        key = (key * 7 + 3) & MASK32
    if sys.byteorder != "little":
        words.byteswap()
    keystream = words.tobytes()
    if len(keystream) != length:
        keystream = keystream[:length]
    plain = int.from_bytes(chunk, "little") ^ int.from_bytes(keystream, "little")
    return plain.to_bytes(length, "little"), key


def _read_exact(file: BinaryIO, count: int) -> bytes:
    chunk = file.read(count)
    if len(chunk) == count:
        return chunk
    if not chunk:
        raise OSError("Archive ended unexpectedly")
    buf = bytearray(chunk)
    while len(buf) < count:
        part = file.read(count - len(buf))
        if not part:
            raise OSError("Archive ended unexpectedly")
        buf += part
    return bytes(buf)


def write_entry(
    archive: BinaryIO, entry: RgssEntry, destination: str, cancelled: CancelFn
) -> None:
    """Decrypt and stream `entry` from `archive` into `destination` via a temp file."""
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    fd, temporary = create_temp_file(destination)
    try:
        with os.fdopen(fd, "wb") as out:
            _ = archive.seek(entry.offset)
            remaining = entry.size
            key = entry.key
            step = max(4, CHUNK_SIZE & ~3)
            while remaining > 0:
                if cancelled():
                    raise _Cancelled
                to_read = min(step, remaining)
                chunk = _read_exact(archive, to_read)
                decrypted, key = decrypt_chunk(chunk, key)
                _ = out.write(decrypted)
                remaining -= len(chunk)
        os.replace(temporary, destination)
    finally:
        _remove_if_present(temporary)


# ── Extraction ──


@dataclass
class _Task:
    archive: str
    entry: RgssEntry
    destination: str
    display: str


def extract_archives(
    folder: str,
    mode: str,
    output: str,
    progress: ProgressFn,
    cancelled: CancelFn,
) -> dict[str, object]:
    """Extract every listed RGSS archive; `mode` is "folder" or "in_place"."""
    if mode not in ("folder", "in_place"):
        raise GameToolError("Unknown extraction mode")
    output_real = validate_output_folder(folder, output) if mode == "folder" else ""
    archives = discover_archives(folder)
    if not archives:
        raise GameToolError("There are no archives to extract")
    folder_real = os.path.realpath(folder)

    unsupported: list[str] = []
    skipped_existing = skipped_unsafe = skipped_invalid = 0
    tasks: list[_Task] = []
    used_targets: set[str] = set()
    planned: set[str] = set()

    for archive in archives:
        try:
            index = read_rgss_index(archive.path)
        except RgssUnsupportedError:
            unsupported.append(archive.name)
            continue
        skipped_invalid += index.skipped_invalid
        if mode == "folder":
            stem, _ext = os.path.splitext(archive.name)
            root = export_root(output_real, folder_real, stem or "archive", used_targets)
            root_real = os.path.realpath(root)
        else:
            root = folder
            root_real = folder_real
        for entry in index.entries:
            destination = resolve_destination(root, root_real, entry.name)
            if destination is None:
                skipped_unsafe += 1
                continue
            if mode == "in_place":
                if os.path.lexists(destination) or destination in planned:
                    skipped_existing += 1
                    continue
                planned.add(destination)
            tasks.append(_Task(archive.path, entry, destination, entry.name))

    check_free_space(
        ((task.destination, task.entry.size) for task in tasks),
        disk_usage=shutil.disk_usage,
    )

    files = written_bytes = failed = 0
    was_cancelled = False
    total = len(tasks)
    progress(0, total, "")
    current_path = ""
    archive_file: BinaryIO | None = None
    try:
        for done, task in enumerate(tasks):
            if cancelled():
                was_cancelled = True
                break
            progress(done, total, task.display)
            if archive_file is None or task.archive != current_path:
                if archive_file is not None:
                    archive_file.close()
                    archive_file = None
                archive_file = open(task.archive, "rb")  # noqa: SIM115
                current_path = task.archive
            try:
                write_entry(archive_file, task.entry, task.destination, cancelled)
            except _Cancelled:
                was_cancelled = True
                break
            except PermissionError as exc:
                raise GameToolError(str(exc)) from exc
            except OSError:
                failed += 1
                continue
            files += 1
            written_bytes += task.entry.size
        if not was_cancelled:
            progress(total, total, "")
    except OSError as exc:
        raise GameToolError(str(exc)) from exc
    finally:
        if archive_file is not None:
            archive_file.close()

    return {
        "files": files,
        "bytes": written_bytes,
        "skipped": skipped_existing + skipped_unsafe + skipped_invalid,
        "skipped_existing": skipped_existing,
        "skipped_unsafe": skipped_unsafe,
        "skipped_invalid": skipped_invalid,
        "failed": failed,
        "unsupported": unsupported,
        "cancelled": was_cancelled,
    }


# ── Tab state and in-game switch ──


def _count_loose_files(
    folder: str, indexes: list[RgssIndex]
) -> tuple[int, int]:
    """Return (missing, differs) across unique safe entries of `indexes`."""
    folder_real = os.path.realpath(folder)
    seen: set[str] = set()
    missing = 0
    differs = 0
    for index in indexes:
        for entry in index.entries:
            destination = resolve_destination(folder, folder_real, entry.name)
            if destination is None or destination in seen:
                continue
            seen.add(destination)
            try:
                st = os.lstat(destination)
            except OSError:
                missing += 1
                continue
            if not stat.S_ISREG(st.st_mode):
                missing += 1
                continue
            if st.st_size != entry.size:
                differs += 1
    return missing, differs


def inspect_state(folder: str) -> dict[str, object]:
    """Return the Tools-tab RGSS state for `folder`."""
    discovered = discover_archives(folder)
    archives: list[dict[str, object]] = []
    supported_indexes: list[RgssIndex] = []
    for archive in discovered:
        try:
            index = read_rgss_index(archive.path)
        except RgssUnsupportedError:
            archives.append(
                {
                    "name": archive.name,
                    "size": archive.size,
                    "format": "",
                    "supported": False,
                    "entries": 0,
                }
            )
            continue
        supported_indexes.append(index)
        archives.append(
            {
                "name": archive.name,
                "size": archive.size,
                "format": index.format,
                "supported": True,
                "entries": len(index.entries),
            }
        )

    missing, differs = _count_loose_files(folder, supported_indexes)
    has_backup = any(archive.backup for archive in discovered)
    has_plain = any(not archive.backup for archive in discovered)
    return {
        "archives": archives,
        "missing": missing,
        "differs": differs,
        "use_extracted": has_backup and not has_plain,
        "backup": has_backup,
    }


def _rename_with_rollback(pairs: list[tuple[str, str]]) -> None:
    completed: list[tuple[str, str]] = []
    for src, dst in pairs:
        try:
            os.rename(src, dst)
        except OSError as exc:
            for prev_src, prev_dst in reversed(completed):
                try:
                    os.rename(prev_dst, prev_src)
                except OSError:
                    pass
            raise GameToolError(
                f"Could not rename {os.path.basename(src)}: {exc}"
            ) from exc
        completed.append((src, dst))


def set_use_extracted(folder: str, enabled: bool) -> bool:
    """Rename archives to/from `.wlib-backup`, returning the new switch state."""
    discovered = discover_archives(folder)
    if not discovered:
        raise GameToolError("There are no RGSS archives in this game folder")

    if enabled:
        pairs: list[tuple[str, str]] = []
        for archive in discovered:
            if not archive.backup:
                target = archive.path + BACKUP_SUFFIX
                if os.path.lexists(target):
                    raise GameToolError(
                        f"Cannot rename {archive.filename}: "
                        + f"{archive.filename}{BACKUP_SUFFIX} already exists"
                    )
                pairs.append((archive.path, target))

        indexes: list[RgssIndex] = []
        for archive in discovered:
            try:
                indexes.append(read_rgss_index(archive.path))
            except RgssUnsupportedError as exc:
                raise GameToolError(
                    f"Cannot use extracted files: {archive.name} is unsupported"
                ) from exc

        missing, _differs = _count_loose_files(folder, indexes)
        if missing:
            raise GameToolError(f"{missing} file(s) still need extracting first")

        _rename_with_rollback(pairs)
        return True

    pairs = []
    for archive in discovered:
        if archive.backup:
            target = os.path.join(folder, archive.name)
            if os.path.lexists(target):
                raise GameToolError(
                    f"Cannot restore {archive.filename}: {archive.name} already exists"
                )
            pairs.append((archive.path, target))

    _rename_with_rollback(pairs)
    return False


def detect_archive_format(path: str) -> str:
    """Return 'RGSSAD v1', 'RGSSAD v3', or '' if unsupported or unreadable."""
    try:
        with open(path, "rb") as file:
            return read_rgss_header(file).format
    except (OSError, RgssUnsupportedError):
        return ""


def strip_archive_name(filename: str) -> tuple[str, bool] | None:
    """Return (archive_name_without_backup, is_backup) if filename is an RGSS archive."""
    lowered = filename.lower()
    backup = False
    base = filename
    if lowered.endswith(BACKUP_SUFFIX):
        backup = True
        base = filename[: -len(BACKUP_SUFFIX)]
        lowered = base.lower()
    if not lowered.endswith(ARCHIVE_EXTENSIONS):
        return None
    return base, backup


def discover_archives(folder: str) -> list[DiscoveredArchive]:
    """Top-level RGSS archives in `folder`, with or without `.wlib-backup`."""
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return []
    found: list[DiscoveredArchive] = []
    for filename in names:
        parsed = strip_archive_name(filename)
        if parsed is None:
            continue
        base_name, is_backup = parsed
        path = os.path.join(folder, filename)
        if os.path.islink(path) or not os.path.isfile(path):
            continue
        try:
            size = os.path.getsize(path)
        except OSError:
            continue
        found.append(
            DiscoveredArchive(
                name=base_name,
                filename=filename,
                path=path,
                size=size,
                backup=is_backup,
            )
        )
    return found
