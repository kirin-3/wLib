# SPDX-License-Identifier: GPL-3.0-or-later
"""RPG Maker MV/MZ plugin list: read, check, switch and restore `js/plugins.js`.

The list is read with a strict grammar (one `$plugins` assignment of a JSON
array, with only comments and white space around it). A switch replaces the
one `status` token of one entry and keeps every other byte. Everything takes
the data root (the folder holding `data/System.json`) so it can be tested
against a temp dir. Nothing is stored: each load reads the files again.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from core.game_tools import (
    GameToolError,
    create_temp_file,
    is_inside,
    safe_relative_parts,
)

LIST_LIMIT = 4 * 1024 * 1024
SCRIPT_LIMIT = 8 * 1024 * 1024
BACKUP_SUFFIX = ".wlib-backup"

ENGINE_MV = "mv"
ENGINE_MZ = "mz"
ENGINE_UNKNOWN = "unknown"

BACKUP_NONE = "none"
BACKUP_INVALID = "invalid"
BACKUP_SAME = "same"
BACKUP_TOGGLES = "toggles"
BACKUP_CHANGED = "changed"

_MZ_FOLDER_RULE = b"extractFileName(plugin.name)"
_READ_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_NONBLOCK", 0)
    | getattr(os, "O_BINARY", 0)
)
# JavaScript white space that can sit around the assignment, BOM included.
_JS_SPACE = frozenset(" \t\n\r\v\f ﻿  ")
_JS_LINE_END = re.compile("[\n\r  ]")
_JSON_SPACE = frozenset(" \t\n\r")
_IDENTIFIER_CHAR = re.compile(r"[\w$]")
_TAG = re.compile(r"^[ \t]*\*?[ \t]*@(base|orderAfter|orderBefore)[ \t]+(.*)$", re.M)
_SURROGATE = re.compile("[\ud800-\udfff]")


class PluginListError(GameToolError):
    """The plugin list or its backup cannot be used; the message is the reason."""


class StalePluginList(GameToolError):
    """The file changed after the list that a request refers to was loaded."""


@dataclass(frozen=True)
class PluginEntry:
    index: int
    name: str
    description: str
    enabled: bool
    status_start: int
    status_end: int


@dataclass(frozen=True)
class PluginList:
    text: str
    entries: tuple[PluginEntry, ...]


# ── Strict reader ──


def _reject_constant(value: str) -> object:
    raise PluginListError(f"The $plugins array uses {value}, which is not JSON")


_DECODER = json.JSONDecoder(parse_constant=_reject_constant)


def _skip_js_space(text: str, pos: int) -> int:
    length = len(text)
    while pos < length:
        char = text[pos]
        if char in _JS_SPACE:
            pos += 1
        elif text.startswith("//", pos):
            match = _JS_LINE_END.search(text, pos + 2)
            pos = match.start() if match else length
        elif text.startswith("/*", pos):
            end = text.find("*/", pos + 2)
            if end < 0:
                raise PluginListError("A comment in the plugin list is not closed")
            pos = end + 2
        else:
            break
    return pos


def _skip_json_space(text: str, pos: int) -> int:
    while pos < len(text) and text[pos] in _JSON_SPACE:
        pos += 1
    return pos


def _decode(text: str, pos: int) -> tuple[object, int]:
    try:
        value, end = cast(tuple[object, int], _DECODER.raw_decode(text, pos))
    except json.JSONDecodeError as exc:
        raise PluginListError(f"The $plugins array is not valid JSON: {exc}") from exc
    return value, end


def _invalid(text: str, pos: int, expected: str) -> PluginListError:
    line = text.count("\n", 0, pos) + 1
    return PluginListError(
        f"The $plugins array is not valid JSON: expected {expected} (line {line})"
    )


def _parse_entry(text: str, pos: int, index: int) -> tuple[PluginEntry, int]:
    label = f"Entry {index + 1}"
    pos = _skip_json_space(text, pos + 1)
    names: list[object] = []
    statuses: list[tuple[object, int, int]] = []
    description: object = ""
    if pos < len(text) and text[pos] == "}":
        pos += 1
    else:
        while True:
            if pos >= len(text) or text[pos] != '"':
                raise _invalid(text, pos, "a key")
            key, pos = _decode(text, pos)
            pos = _skip_json_space(text, pos)
            if pos >= len(text) or text[pos] != ":":
                raise _invalid(text, pos, "':'")
            start = _skip_json_space(text, pos + 1)
            value, pos = _decode(text, start)
            if key == "status":
                statuses.append((value, start, pos))
            elif key == "name":
                names.append(value)
            elif key == "description":
                description = value
            pos = _skip_json_space(text, pos)
            if pos < len(text) and text[pos] == ",":
                pos = _skip_json_space(text, pos + 1)
                continue
            if pos < len(text) and text[pos] == "}":
                pos += 1
                break
            raise _invalid(text, pos, "',' or '}'")
    if len(names) != 1 or not isinstance(names[0], str):
        raise PluginListError(f"{label} has no single text name")
    if not statuses:
        raise PluginListError(f"{label} has no true/false status")
    if len(statuses) > 1:
        raise PluginListError(f"{label} has more than one status")
    status, start, end = statuses[0]
    if type(status) is not bool:
        raise PluginListError(f"{label} has a status that is not true or false")
    return (
        PluginEntry(
            index,
            names[0],
            description if isinstance(description, str) else "",
            status,
            start,
            end,
        ),
        pos,
    )


def _parse_array(text: str, pos: int) -> tuple[list[PluginEntry], int]:
    entries: list[PluginEntry] = []
    pos = _skip_json_space(text, pos + 1)
    if pos < len(text) and text[pos] == "]":
        return entries, pos + 1
    while True:
        if pos >= len(text) or text[pos] != "{":
            _value, _end = _decode(text, pos)
            raise PluginListError(f"Entry {len(entries) + 1} is not an object")
        entry, pos = _parse_entry(text, pos, len(entries))
        entries.append(entry)
        pos = _skip_json_space(text, pos)
        if pos < len(text) and text[pos] == ",":
            pos = _skip_json_space(text, pos + 1)
            continue
        if pos < len(text) and text[pos] == "]":
            return entries, pos + 1
        raise _invalid(text, pos, "',' or ']'")


def parse_text(text: str) -> PluginList:
    """Parse the decoded text of `plugins.js`; raises PluginListError."""
    pos = _skip_js_space(text, 0)
    for keyword in ("var", "let", "const"):
        if text.startswith(keyword, pos):
            after = _skip_js_space(text, pos + len(keyword))
            if after > pos + len(keyword):
                pos = after
            break
    if not text.startswith("$plugins", pos) or (
        pos + 8 < len(text) and _IDENTIFIER_CHAR.match(text, pos + 8)
    ):
        raise PluginListError(
            "The plugin list does not start with a $plugins assignment"
        )
    pos = _skip_js_space(text, pos + 8)
    if not text.startswith("=", pos) or text.startswith("==", pos):
        raise PluginListError(
            "The plugin list does not start with a $plugins assignment"
        )
    pos = _skip_js_space(text, pos + 1)
    if not text.startswith("[", pos):
        raise PluginListError("$plugins is not set to an array")
    try:
        entries, pos = _parse_array(text, pos)
    except RecursionError as exc:
        raise PluginListError("The $plugins array is nested too deeply") from exc
    pos = _skip_js_space(text, pos)
    if text.startswith(";", pos):
        pos = _skip_js_space(text, pos + 1)
    if pos != len(text):
        raise PluginListError("The plugin list has code after the $plugins array")
    return PluginList(text, tuple(entries))


def parse_bytes(data: bytes) -> PluginList:
    if len(data) > LIST_LIMIT:
        raise PluginListError("The plugin list is larger than 4 MiB")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PluginListError("The plugin list is not UTF-8 text") from exc
    return parse_text(text)


def _masked(plugins: PluginList) -> str:
    parts: list[str] = []
    last = 0
    for entry in plugins.entries:
        parts.append(plugins.text[last : entry.status_start])
        parts.append("\0")
        last = entry.status_end
    parts.append(plugins.text[last:])
    return "".join(parts)


def switched_text(plugins: PluginList, index: int, enabled: bool) -> str:
    entry = plugins.entries[index]
    token = "true" if enabled else "false"
    return plugins.text[: entry.status_start] + token + plugins.text[entry.status_end :]


# ── Safe file access ──


def _read_regular(path: str, limit: int, label: str) -> bytes | None:
    """The bytes of a regular, non-symlink file, or None when it is missing."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise PluginListError(f"{label} cannot be read: {exc.strerror}") from exc
    if stat.S_ISLNK(info.st_mode):
        raise PluginListError(f"{label} is a symbolic link")
    if not stat.S_ISREG(info.st_mode):
        raise PluginListError(f"{label} is not a regular file")
    if info.st_size > limit:
        raise PluginListError(f"{label} is larger than {limit // (1024 * 1024)} MiB")
    try:
        fd = os.open(path, _READ_FLAGS)
        with os.fdopen(fd, "rb") as file:
            if not stat.S_ISREG(os.fstat(file.fileno()).st_mode):
                raise PluginListError(f"{label} is not a regular file")
            data = file.read(limit + 1)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise PluginListError(f"{label} cannot be read: {exc.strerror}") from exc
    if len(data) > limit:
        raise PluginListError(f"{label} is larger than {limit // (1024 * 1024)} MiB")
    return data


def _read_quiet(path: str, limit: int) -> bytes | None:
    try:
        return _read_regular(path, limit, "File")
    except PluginListError:
        return None


def js_folder(root: str) -> str:
    """`<root>/js`, refused when it resolves outside the data root."""
    js = os.path.join(root, "js")
    if not is_inside(os.path.realpath(root), os.path.realpath(js)):
        raise PluginListError("The js folder is outside the game's data folder")
    if not os.path.isdir(js):
        raise PluginListError("The js folder was not found")
    return js


def list_path(js: str) -> str:
    return os.path.join(js, "plugins.js")


def backup_path(js: str) -> str:
    return os.path.join(js, "plugins.js" + BACKUP_SUFFIX)


def revision_of(data: bytes | None) -> str:
    return "" if data is None else hashlib.sha256(data).hexdigest()


def _read_list(js: str) -> bytes | None:
    return _read_regular(list_path(js), LIST_LIMIT, "The plugin list")


# ── Engine rules and checks ──


def detect_plugin_engine(js: str) -> tuple[str, bool]:
    """(engine, whether the MZ loader removes folders from plugin names)."""
    managers = _read_quiet(os.path.join(js, "rmmz_managers.js"), SCRIPT_LIMIT)
    if managers is not None:
        return ENGINE_MZ, _MZ_FOLDER_RULE in managers
    if _read_quiet(os.path.join(js, "rpg_managers.js"), SCRIPT_LIMIT) is not None:
        return ENGINE_MV, False
    return ENGINE_UNKNOWN, False


def load_key(name: str, strip_folders: bool) -> str:
    return name.rsplit("/", 1)[-1] if strip_folders else name


FILE_OK = "ok"
FILE_CASE = "case"
FILE_MISSING = "missing"
FILE_UNSAFE = "unsafe"


class _FileLookup:
    """Finds `js/plugins/<name>.js` per path part, with one listing per folder."""

    def __init__(self, root: str, js: str) -> None:
        self._root_real: str = os.path.realpath(root)
        self._js: str = js
        self._listings: dict[str, list[str] | None] = {}

    def _listing(self, folder: str) -> list[str] | None:
        if folder not in self._listings:
            names: list[str] | None = None
            if is_inside(self._root_real, os.path.realpath(folder)):
                try:
                    names = sorted(os.listdir(folder))
                except OSError:
                    names = None
            self._listings[folder] = names
        return self._listings[folder]

    def find(self, name: str) -> tuple[str, str | None]:
        if not name or "\\" in name or "\0" in name:
            return FILE_UNSAFE, None
        parts = safe_relative_parts(name)
        if parts is None:
            return FILE_UNSAFE, None
        current = os.path.join(self._js, "plugins")
        if not is_inside(self._root_real, os.path.realpath(current)):
            return FILE_UNSAFE, None
        current = self._js
        case = False
        for part in ["plugins", *parts[:-1], parts[-1] + ".js"]:
            names = self._listing(current)
            if names is None:
                return FILE_MISSING, None
            if part not in names:
                folded = part.casefold()
                matches = [listed for listed in names if listed.casefold() == folded]
                if not matches:
                    return FILE_MISSING, None
                part = matches[0]
                case = True
            current = os.path.join(current, part)
        if not is_inside(self._root_real, os.path.realpath(current)):
            return FILE_UNSAFE, None
        if not os.path.isfile(current):
            return FILE_MISSING, None
        return (FILE_CASE if case else FILE_OK), current


def parse_header_tags(text: str) -> dict[str, list[str]]:
    """`@base`, `@orderAfter` and `@orderBefore` values from all `/*:` blocks."""
    tags: dict[str, list[str]] = {"base": [], "orderAfter": [], "orderBefore": []}
    pos = 0
    while True:
        start = text.find("/*", pos)
        if start < 0:
            break
        end = text.find("*/", start + 2)
        if end < 0:
            end = len(text)
        if text.startswith("/*:", start):
            for match in _TAG.finditer(text, start + 3, end):
                value = match.group(2).strip()
                if value.endswith("*/"):
                    value = value[:-2].rstrip()
                if value:
                    tags[match.group(1)].append(value)
        pos = end + 2
    return tags


def _read_header(path: str) -> dict[str, list[str]]:
    data = _read_quiet(path, SCRIPT_LIMIT)
    if data is None:
        return {"base": [], "orderAfter": [], "orderBefore": []}
    return parse_header_tags(data.decode("utf-8", errors="replace"))


def safe_text(value: str) -> str:
    """Replace unpaired surrogates so the text can cross the bridge as JSON."""
    return _SURROGATE.sub("�", value)


def _unique_by_key(values: list[str], strip: bool, own: str) -> list[tuple[str, str]]:
    seen: set[str] = set()
    result: list[tuple[str, str]] = []
    for value in values:
        key = load_key(value, strip)
        if key == own or key in seen:
            continue
        seen.add(key)
        result.append((value, key))
    return result


def analyze(
    root: str,
    js: str,
    entries: tuple[PluginEntry, ...],
    engine: str,
    strip: bool,
    headers: bool = True,
) -> list[dict[str, object]]:
    """The entry payloads with load state, file checks and MZ header checks."""
    count = len(entries)
    keys = [load_key(entry.name, strip) for entry in entries]
    loaded = [False] * count
    duplicate = [False] * count
    first: dict[str, int] = {}
    for entry in entries:
        if not entry.enabled:
            continue
        if keys[entry.index] in first:
            duplicate[entry.index] = True
        else:
            first[keys[entry.index]] = entry.index
            loaded[entry.index] = True
    taken_over = {keys[i] for i in range(count) if duplicate[i]}

    lookup = _FileLookup(root, js)
    files: list[tuple[str, str | None]] = [
        (FILE_OK, None) if duplicate[i] else lookup.find(entry.name)
        for i, entry in enumerate(entries)
    ]

    tags: dict[int, dict[str, list[str]]] = {}
    if headers and engine == ENGINE_MZ:
        for i in range(count):
            path = files[i][1]
            if loaded[i] and path is not None:
                tags[i] = _read_header(path)

    needed_by: list[list[str]] = [[] for _ in range(count)]
    for i, declared in tags.items():
        for _value, key in _unique_by_key(declared["base"], strip, keys[i]):
            target = first.get(key)
            if target is not None and key not in taken_over:
                needed_by[target].append(safe_text(entries[i].name))

    payloads: list[dict[str, object]] = []
    for i, entry in enumerate(entries):
        issues: list[dict[str, object]] = []
        status = files[i][0]
        if duplicate[i]:
            issues.append({"code": "duplicate", "level": "note", "names": []})
        else:
            if status == FILE_UNSAFE:
                issues.append({"code": "unsafe_name", "level": "warning", "names": []})
            elif status == FILE_MISSING:
                issues.append(
                    {
                        "code": "missing_file",
                        "level": "warning" if loaded[i] else "note",
                        "names": [],
                    }
                )
            elif status == FILE_CASE:
                issues.append({"code": "case_mismatch", "level": "note", "names": []})
            declared = tags.get(i)
            if declared is not None:
                missing = [
                    safe_text(value)
                    for value, key in _unique_by_key(declared["base"], strip, keys[i])
                    if key not in first
                ]
                if missing:
                    issues.append(
                        {
                            "code": "missing_dependency",
                            "level": "warning",
                            "names": missing,
                        }
                    )
                for tag, direction in (
                    ("orderAfter", "after"),
                    ("orderBefore", "before"),
                ):
                    broken = [
                        safe_text(value)
                        for value, key in _unique_by_key(declared[tag], strip, keys[i])
                        if key in first
                        and (first[key] > i if direction == "after" else first[key] < i)
                    ]
                    if broken:
                        issues.append(
                            {
                                "code": "wrong_order",
                                "level": "warning",
                                "direction": direction,
                                "names": broken,
                            }
                        )
        payloads.append(
            {
                "index": entry.index,
                "name": safe_text(entry.name),
                "description": safe_text(entry.description),
                "enabled": entry.enabled,
                "loaded": loaded[i],
                "can_enable": status not in (FILE_UNSAFE, FILE_MISSING),
                "needed_by": needed_by[i],
                "issues": issues,
            }
        )
    return payloads


# ── Backup state ──


def backup_state(js: str, current: bytes | None) -> dict[str, str]:
    """`{state, reason}` from a compare of the backup with the current bytes."""
    try:
        backup = _read_regular(backup_path(js), LIST_LIMIT, "The backup")
    except PluginListError as exc:
        return {"state": BACKUP_INVALID, "reason": str(exc)}
    if backup is None:
        return {"state": BACKUP_NONE, "reason": ""}
    try:
        saved = parse_bytes(backup)
    except PluginListError as exc:
        return {
            "state": BACKUP_INVALID,
            "reason": f"The backup is not a readable plugin list: {exc}",
        }
    if current is not None and current == backup:
        return {"state": BACKUP_SAME, "reason": ""}
    if current is not None:
        try:
            if _masked(parse_bytes(current)) == _masked(saved):
                return {"state": BACKUP_TOGGLES, "reason": ""}
        except PluginListError:
            pass
    return {"state": BACKUP_CHANGED, "reason": ""}


# ── Load ──


def load_plugins(root: str) -> dict[str, object]:
    """The card state: engine, readable, reason, revision, entries and backup."""
    try:
        js = js_folder(root)
    except PluginListError as exc:
        return {
            "engine": ENGINE_UNKNOWN,
            "readable": False,
            "reason": str(exc),
            "revision": "",
            "entries": [],
            "backup": {"state": BACKUP_NONE, "reason": ""},
        }
    engine, strip = detect_plugin_engine(js)
    state: dict[str, object] = {
        "engine": engine,
        "readable": False,
        "reason": "",
        "revision": "",
        "entries": [],
    }
    try:
        data = _read_list(js)
    except PluginListError as exc:
        state["reason"] = str(exc)
        # A symlink or special file at the list path: no restore over it.
        state["backup"] = {"state": BACKUP_NONE, "reason": ""}
        return state
    state["revision"] = revision_of(data)
    state["backup"] = backup_state(js, data)
    if data is None:
        state["reason"] = "js/plugins.js was not found"
        return state
    try:
        plugins = parse_bytes(data)
    except PluginListError as exc:
        state["reason"] = str(exc)
        return state
    state["readable"] = True
    state["entries"] = analyze(root, js, plugins.entries, engine, strip)
    return state


# ── Writes ──


def _write_atomic(
    path: str, data: bytes, mode_source: str, before_replace: Callable[[], None]
) -> None:
    """Write `data` to `path` through a temporary file and `os.replace`."""
    fd, temporary = create_temp_file(path)
    try:
        with os.fdopen(fd, "wb") as file:
            _ = file.write(data)
            file.flush()
            os.fsync(file.fileno())
        if mode_source:
            shutil.copymode(mode_source, temporary)
        before_replace()
        os.replace(temporary, path)
        temporary = ""
    finally:
        if temporary:
            try:
                os.remove(temporary)
            except FileNotFoundError:
                pass


def _check_revision(js: str, revision: str) -> bytes | None:
    data = _read_list(js)
    if revision_of(data) != revision:
        raise StalePluginList(
            "The plugin list changed after it was loaded. Showing the current list."
        )
    return data


def set_plugin_enabled(
    root: str, index: int, enabled: bool, revision: str
) -> dict[str, object]:
    """Switch one entry and return the new state; raises GameToolError."""
    js = js_folder(root)
    path = list_path(js)
    data = _check_revision(js, revision)
    if data is None:
        raise PluginListError("js/plugins.js was not found")
    plugins = parse_bytes(data)
    if not 0 <= index < len(plugins.entries):
        raise PluginListError("The plugin entry does not exist")
    entry = plugins.entries[index]
    if entry.enabled == enabled:
        return load_plugins(root)
    if enabled:
        engine, strip = detect_plugin_engine(js)
        payload = analyze(root, js, plugins.entries, engine, strip, headers=False)
        if not payload[index]["can_enable"]:
            raise PluginListError(
                "This plugin cannot be switched on: its file is missing or its name is unsafe"
            )
    backup = backup_path(js)
    try:
        backup_info = os.lstat(backup)
    except FileNotFoundError:
        backup_info = None
    if backup_info is not None and (
        stat.S_ISLNK(backup_info.st_mode) or not stat.S_ISREG(backup_info.st_mode)
    ):
        raise PluginListError(
            "plugins.js.wlib-backup is not a regular file, so no backup can be made"
        )

    text = switched_text(plugins, index, enabled)
    new_data = text.encode("utf-8")
    check = parse_bytes(new_data)
    if (
        len(check.entries) != len(plugins.entries)
        or _masked(check) != _masked(plugins)
        or any(
            new.enabled != (enabled if new.index == index else old.enabled)
            for new, old in zip(check.entries, plugins.entries, strict=True)
        )
    ):
        raise PluginListError("The switch would change more than one value")

    def still_current() -> None:
        _ = _check_revision(js, revision)

    if backup_info is None:
        _write_atomic(backup, data, path, still_current)
    _write_atomic(path, new_data, path, still_current)
    return load_plugins(root)


def restore_plugins(
    root: str, revision: str, replace_changed: bool
) -> dict[str, object]:
    """Put the backup back and return the new state; raises GameToolError."""
    js = js_folder(root)
    path = list_path(js)
    current = _check_revision(js, revision)
    state = backup_state(js, current)
    kind = state["state"]
    if kind == BACKUP_NONE:
        raise PluginListError("There is no plugin list backup")
    if kind == BACKUP_INVALID:
        raise PluginListError(state["reason"])
    if kind == BACKUP_SAME:
        raise PluginListError("The plugin list already equals its backup")
    if kind == BACKUP_CHANGED and not replace_changed:
        raise PluginListError(
            "The plugin list has other changes than on/off values. "
            + "Confirm that they will be lost."
        )
    backup = backup_path(js)
    data = _read_regular(backup, LIST_LIMIT, "The backup")
    if data is None:
        raise PluginListError("There is no plugin list backup")

    def still_current() -> None:
        _ = _check_revision(js, revision)

    _write_atomic(path, data, path if current is not None else backup, still_current)
    return load_plugins(root)
