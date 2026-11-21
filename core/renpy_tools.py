# SPDX-License-Identifier: GPL-3.0-or-later
"""Ren'Py mod tools: developer console file and `.rpa` archive extraction.

Archive contents are untrusted. The index pickle is read with an unpickler that
only allows the two callables pickle protocol 2 uses to store bytes, each
restricted, and every entry path is checked against the target folder before
anything is written. Everything takes explicit paths so it can be tested
against a temp dir.
"""

from __future__ import annotations

import codecs
import io
import os
import pickle
import shutil
import zlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import BinaryIO, cast, override

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

URM_ARCHIVE_NAME = "0x52_urm.rpa"
CONSOLE_RPY = "zzz_wlib_console.rpy"
CONSOLE_RPYC = "zzz_wlib_console.rpyc"
DEVELOPER_LINE = "    config.developer = True"
SCRIPT_EXTENSIONS = (".rpy", ".rpyc", ".rpym", ".rpymc")
MAX_INDEX_BYTES = 64 * 1024 * 1024
HEADER_PEEK = 128
SUPPORTED_MAGICS = ("RPA-2.0", "RPA-3.0", "RPA-3.2")


# ── Console ──


def _game_dir(game_folder: str) -> str:
    return os.path.join(game_folder, "game")


def console_paths(game_folder: str) -> tuple[str, str]:
    base = _game_dir(game_folder)
    return os.path.join(base, CONSOLE_RPY), os.path.join(base, CONSOLE_RPYC)


def console_script(developer: bool) -> str:
    lines = [
        "# Written by wLib. Turn the console off in wLib to remove this file.",
        "init 999 python:",
        "    config.console = True",
    ]
    if developer:
        lines.append(DEVELOPER_LINE)
    return "\n".join(lines) + "\n"


def console_state(game_folder: str) -> str:
    """"off", "on" or "developer" from the files in `game/`."""
    rpy, rpyc = console_paths(game_folder)
    if os.path.isfile(rpy):
        try:
            with open(rpy, encoding="utf-8", errors="replace") as file:
                text = file.read()
        except OSError:
            return "on"
        return "developer" if DEVELOPER_LINE.strip() in text else "on"
    if os.path.isfile(rpyc):
        return "on"
    return "off"


def _remove_if_present(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def set_console(game_folder: str, enabled: bool, developer: bool = False) -> str:
    """Write or remove the console script and return the new state."""
    rpy, rpyc = console_paths(game_folder)
    try:
        if not enabled:
            _remove_if_present(rpy)
            _remove_if_present(rpyc)
            return "off"
        content = console_script(developer)
        current = ""
        if os.path.isfile(rpy):
            with open(rpy, encoding="utf-8", errors="replace") as file:
                current = file.read()
        if current != content:
            fd, temporary = create_temp_file(rpy)
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as file:
                    _ = file.write(content)
                os.replace(temporary, rpy)
            finally:
                _remove_if_present(temporary)
            _remove_if_present(rpyc)
    except OSError as exc:
        raise GameToolError(str(exc)) from exc
    return "developer" if developer else "on"


# ── RPA reading ──


class RpaUnsupportedError(Exception):
    """The archive is not a format wLib can read safely."""


@dataclass
class RpaHeader:
    magic: str
    offset: int
    key: int


# (offset, length, prefix): data is the prefix followed by `length` bytes read
# from `offset`. In Ren'Py's own reader (renpy/loader.py) the prefix is kept as
# a separate entry and `dlen` counts only bytes read from the archive, so the
# prefix is not part of `length`. Archives Ren'Py builds use empty prefixes.
Segment = tuple[int, int, bytes]


@dataclass
class RpaEntry:
    name: str
    segments: list[Segment]

    @property
    def size(self) -> int:
        return sum(len(prefix) + length for _offset, length, prefix in self.segments)


@dataclass
class RpaIndex:
    magic: str
    file_size: int
    entries: list[RpaEntry] = field(default_factory=list)
    skipped_invalid: int = 0


def parse_rpa_header(line: bytes) -> RpaHeader:
    try:
        parts = line.decode("ascii").split()
        magic = parts[0]
        if magic not in SUPPORTED_MAGICS:
            raise RpaUnsupportedError(f"Unsupported archive format {magic}")
        offset = int(parts[1], 16)
        key = 0
        if magic == "RPA-3.0":
            key_fields = parts[2:]
        elif magic == "RPA-3.2":
            key_fields = parts[3:]
        else:
            key_fields = []
        for value in key_fields:
            key ^= int(value, 16)
    except (UnicodeDecodeError, IndexError, ValueError) as exc:
        raise RpaUnsupportedError("Unreadable archive header") from exc
    return RpaHeader(magic, offset, key)


def read_rpa_header(file: BinaryIO) -> RpaHeader:
    _ = file.seek(0)
    head = file.read(HEADER_PEEK)
    line = head.split(b"\n", 1)[0]
    return parse_rpa_header(line)


def inflate_capped(file: BinaryIO, cap: int = MAX_INDEX_BYTES) -> bytes:
    """Inflate the zlib stream at the current position, refusing more than `cap`."""
    decompressor = zlib.decompressobj()
    output = bytearray()
    try:
        while not decompressor.eof:
            chunk = file.read(64 * 1024)
            if not chunk:
                raise RpaUnsupportedError("Archive index is truncated")
            output += decompressor.decompress(chunk, cap - len(output) + 1)
            if len(output) > cap:
                raise RpaUnsupportedError("Archive index is too large")
    except zlib.error as exc:
        raise RpaUnsupportedError("Archive index does not decompress") from exc
    return bytes(output)


def _latin1_encode(text: object, encoding: object = "latin-1") -> bytes:
    # Protocol 2 stores non-empty bytes as _codecs.encode(text, "latin1").
    if not isinstance(text, str) or str(encoding).replace("-", "").lower() != "latin1":
        raise pickle.UnpicklingError("Unexpected encode call in archive index")
    return codecs.encode(text, "latin-1")


def _empty_bytes(*args: object) -> bytes:
    # Protocol 2 stores b"" as bytes() (rpatool writes such prefixes). Any
    # argument could ask for a huge allocation, so only the empty form is allowed.
    if args:
        raise pickle.UnpicklingError("Unexpected bytes call in archive index")
    return b""


class _IndexUnpickler(pickle.Unpickler):
    """Only the two callables protocol 2 uses to store bytes, both restricted."""

    @override
    def find_class(self, module: str, name: str) -> object:
        if (module, name) == ("_codecs", "encode"):
            return cast(object, _latin1_encode)
        if module in ("__builtin__", "builtins") and name == "bytes":
            return cast(object, _empty_bytes)
        raise pickle.UnpicklingError(f"Blocked {module}.{name} in archive index")


def load_index(data: bytes) -> object:
    try:
        return cast(object, _IndexUnpickler(io.BytesIO(data), encoding="bytes").load())
    except Exception as exc:
        raise RpaUnsupportedError("Archive index cannot be read safely") from exc


def _as_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _entry_name(raw: object) -> str | None:
    if isinstance(raw, bytes):
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if isinstance(raw, str):
        return raw
    return None


def _segment(raw: object, key: int, file_size: int) -> Segment | None:
    if not isinstance(raw, (tuple, list)):
        return None
    items = cast(Sequence[object], raw)
    if len(items) not in (2, 3):
        return None
    offset, length = _as_int(items[0]), _as_int(items[1])
    if offset is None or length is None:
        return None
    offset ^= key
    length ^= key
    prefix = b""
    if len(items) == 3:
        extra = items[2]
        if isinstance(extra, bytes):
            prefix = extra
        elif isinstance(extra, str):
            try:
                prefix = extra.encode("latin-1")
            except UnicodeEncodeError:
                return None
        else:
            return None
    if offset < 0 or length < 0 or offset + length > file_size:
        return None
    return offset, length, prefix


def normalize_index(raw: object, header: RpaHeader, file_size: int) -> RpaIndex:
    if not isinstance(raw, dict):
        raise RpaUnsupportedError("Archive index has an unexpected shape")
    index = RpaIndex(header.magic, file_size)
    for raw_name, raw_segments in cast(Mapping[object, object], raw).items():
        name = _entry_name(raw_name)
        if name is None or not isinstance(raw_segments, (list, tuple)):
            index.skipped_invalid += 1
            continue
        segments: list[Segment] = []
        for raw_segment in cast(Sequence[object], raw_segments):
            segment = _segment(raw_segment, header.key, file_size)
            if segment is None:
                segments = []
                break
            segments.append(segment)
        if not segments:
            index.skipped_invalid += 1
            continue
        index.entries.append(RpaEntry(name, segments))
    return index


def read_rpa_index(path: str) -> RpaIndex:
    try:
        with open(path, "rb") as file:
            file_size = os.fstat(file.fileno()).st_size
            header = read_rpa_header(file)
            if header.offset >= file_size:
                raise RpaUnsupportedError("Archive index lies outside the file")
            _ = file.seek(header.offset)
            data = inflate_capped(file)
    except OSError as exc:
        raise RpaUnsupportedError(str(exc)) from exc
    return normalize_index(load_index(data), header, file_size)


# ── Archive listing ──


def _rpa_magic(path: str) -> str:
    try:
        with open(path, "rb") as file:
            return read_rpa_header(file).magic
    except (OSError, RpaUnsupportedError):
        return ""


def list_archives(game_folder: str) -> list[dict[str, object]]:
    """Every `.rpa` under `game/` except URM's, with size and header format."""
    base = _game_dir(game_folder)
    found: list[dict[str, object]] = []
    for current, dirs, files in os.walk(base, followlinks=False):
        dirs.sort()
        for name in sorted(files):
            lowered = name.lower()
            if not lowered.endswith(".rpa") or lowered == URM_ARCHIVE_NAME:
                continue
            path = os.path.join(current, name)
            if os.path.islink(path) or not os.path.isfile(path):
                continue
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            found.append(
                {
                    "name": os.path.relpath(path, base).replace(os.sep, "/"),
                    "path": path,
                    "size": size,
                    "format": _rpa_magic(path),
                }
            )
    return found


# ── Extraction ──


class _Cancelled(Exception):
    pass


def _write_entry(
    archive: BinaryIO, entry: RpaEntry, destination: str, cancelled: CancelFn
) -> None:
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    fd, temporary = create_temp_file(destination)
    try:
        with os.fdopen(fd, "wb") as out:
            for offset, length, prefix in entry.segments:
                if prefix:
                    _ = out.write(prefix)
                _ = archive.seek(offset)
                remaining = length
                while remaining > 0:
                    if cancelled():
                        raise _Cancelled
                    chunk = archive.read(min(CHUNK_SIZE, remaining))
                    if not chunk:
                        raise OSError("Archive ended unexpectedly")
                    _ = out.write(chunk)
                    remaining -= len(chunk)
        os.replace(temporary, destination)
    finally:
        _remove_if_present(temporary)


@dataclass
class _Task:
    archive: str
    entry: RpaEntry
    destination: str
    display: str


def extract_archives(
    game_folder: str,
    mode: str,
    output: str,
    progress: ProgressFn,
    cancelled: CancelFn,
) -> dict[str, object]:
    """Extract every listed archive; `mode` is "folder" or "in_place"."""
    if mode not in ("folder", "in_place"):
        raise GameToolError("Unknown extraction mode")
    output_real = validate_output_folder(game_folder, output) if mode == "folder" else ""
    archives = list_archives(game_folder)
    if not archives:
        raise GameToolError("There are no archives to extract")
    game_root = _game_dir(game_folder)
    game_real = os.path.realpath(game_folder)

    unsupported: list[str] = []
    skipped_scripts = skipped_existing = skipped_unsafe = skipped_invalid = 0
    tasks: list[_Task] = []
    used_targets: set[str] = set()
    # In place, two archives can hold the same file. Ren'Py sorts archives by
    # their path under game/ without the extension, in reverse, and takes a
    # file from the first archive that has it (patch.rpa beats archive.rpa).
    # Loose files beat every archive, so the copy written here must be the
    # one Ren'Py used, or it would shadow the patch from now on.
    planned: set[str] = set()
    if mode == "in_place":
        archives = sorted(
            archives,
            key=lambda archive: str(archive["name"])[: -len(".rpa")],
            reverse=True,
        )
    for archive in archives:
        path = str(archive["path"])
        name = str(archive["name"])
        try:
            index = read_rpa_index(path)
        except RpaUnsupportedError:
            unsupported.append(name)
            continue
        skipped_invalid += index.skipped_invalid
        if mode == "folder":
            stem = os.path.basename(name)[: -len(".rpa")] or "archive"
            root = export_root(output_real, game_real, stem, used_targets)
        else:
            # Ren'Py resolves archive entries against `game/`, wherever under
            # it the archive sits (`game/patch/x.rpa` holds `images/a.png`
            # for `game/images/a.png`).
            root = game_root
        root_real = os.path.realpath(root)
        for entry in index.entries:
            destination = resolve_destination(root, root_real, entry.name)
            if destination is None:
                skipped_unsafe += 1
                continue
            if mode == "in_place":
                if entry.name.lower().endswith(SCRIPT_EXTENSIONS):
                    skipped_scripts += 1
                    continue
                if os.path.lexists(destination) or destination in planned:
                    skipped_existing += 1
                    continue
                planned.add(destination)
            tasks.append(_Task(path, entry, destination, entry.name))

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
                _write_entry(archive_file, task.entry, task.destination, cancelled)
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
        "skipped": skipped_scripts + skipped_existing + skipped_unsafe + skipped_invalid,
        "skipped_scripts": skipped_scripts,
        "skipped_existing": skipped_existing,
        "skipped_unsafe": skipped_unsafe,
        "skipped_invalid": skipped_invalid,
        "failed": failed,
        "unsupported": unsupported,
        "cancelled": was_cancelled,
    }
