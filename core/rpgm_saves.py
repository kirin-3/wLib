# SPDX-License-Identifier: GPL-3.0-or-later
"""RPG Maker MV/MZ save files: codecs, names, view model, patches and safe write.

Everything here takes explicit paths so it can be tested against a temp dir.
`core.api` resolves the game's folders from the stored row and calls in.

Save formats:
- MV `.rpgsave`: `LZString.compressToBase64` text holding JSON.
- MZ `.rmmzsave`: zlib-compressed JSON whose bytes were written as a JS
  "binary string" and so stored as UTF-8 (each byte becomes one code point).
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import zlib
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import ClassVar, cast

from core.lzstring import LZStringError, compress_to_base64, decompress_from_base64

FORMAT_MV = "mv"
FORMAT_MZ = "mz"
_EXTENSIONS = {FORMAT_MV: ".rpgsave", FORMAT_MZ: ".rmmzsave"}
SLOT_NAME = re.compile(r"^file(\d+)\.(rpgsave|rmmzsave)$")
TEMP_SUFFIX = ".wlib-tmp"

MAX_GOLD = 9_999_999
MAX_COUNT = 9_999
MAX_ID = 99_999
MAX_STAT = 99_999_999
MAX_LEVEL = 9_999
MAX_BONUS = 9_999_999
MAX_EXP = 2**53 - 1
MAX_VARIABLE = 2**53 - 1
MAX_NAME = 200
MAX_STRING = 100_000

_DEFAULT_PARAMS = ("Max HP", "Max MP", "ATK", "DEF", "MAT", "MDF", "AGI", "LUK")
_COUNT_KEYS = {"item": "_items", "weapon": "_weapons", "armor": "_armors"}
_CATEGORY_FILES = {"item": "Items", "weapon": "Weapons", "armor": "Armors"}

JsonDict = dict[str, object]


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


# ---------------------------------------------------------------- codecs


def format_for_name(name: str) -> str:
    return FORMAT_MZ if name.endswith(".rmmzsave") else FORMAT_MV


def decode_save(data: bytes, fmt: str) -> JsonDict:
    """Decode a save file's bytes to its JSON object."""
    try:
        text = data.decode("utf-8")
        if fmt == FORMAT_MZ:
            raw = zlib.decompress(text.encode("latin-1"))
            tree = cast(object, json.loads(raw.decode("utf-8")))
        elif fmt == FORMAT_MV:
            decoded = decompress_from_base64(text.strip())
            tree = cast(object, json.loads(decoded))
        else:
            raise UnsupportedSaveFormat("Unknown save format")
    except UnsupportedSaveFormat:
        raise
    except (
        UnicodeError,
        zlib.error,
        LZStringError,
        ValueError,
        RecursionError,
    ) as exc:
        raise UnsupportedSaveFormat(
            "This save format is not supported (it may be encrypted by a plugin)"
        ) from exc
    if not isinstance(tree, dict):
        raise UnsupportedSaveFormat("This save does not hold a JSON object")
    return cast(JsonDict, tree)


def encode_save(tree: Mapping[str, object], fmt: str) -> bytes:
    text = json.dumps(tree, ensure_ascii=False, separators=(",", ":"))
    if fmt == FORMAT_MZ:
        try:
            payload = text.encode("utf-8")
        except UnicodeEncodeError:
            # A lone surrogate (from a `\udXXX` escape) has no UTF-8 form;
            # ASCII-escaped JSON keeps it as an escape the game parses back.
            payload = json.dumps(tree, separators=(",", ":")).encode("ascii")
        packed = zlib.compress(payload, 1)
        return packed.decode("latin-1").encode("utf-8")
    if fmt == FORMAT_MV:
        return compress_to_base64(text).encode("utf-8")
    raise UnsupportedSaveFormat("Unknown save format")


# ----------------------------------------------------------------- names

_ESCAPE_RE = re.compile(r"\\[A-Za-z]+\[\d*\]|\\[{}$.|!<>^]|\\[A-Za-z](?![A-Za-z])")


def strip_escapes(text: str) -> str:
    """Remove RPG Maker text escape codes such as `\\C[2]`."""
    return _ESCAPE_RE.sub("", text).strip()


def _read_json_list(data_dir: str, stem: str) -> list[object]:
    try:
        with open(os.path.join(data_dir, f"{stem}.json"), "rb") as handle:
            loaded = cast(object, json.loads(handle.read().decode("utf-8-sig")))
    except (OSError, ValueError, RecursionError):
        return []
    return cast(list[object], loaded) if isinstance(loaded, list) else []


def _read_json_dict(data_dir: str, stem: str) -> JsonDict:
    try:
        with open(os.path.join(data_dir, f"{stem}.json"), "rb") as handle:
            loaded = cast(object, json.loads(handle.read().decode("utf-8-sig")))
    except (OSError, ValueError, RecursionError):
        return {}
    return cast(JsonDict, loaded) if isinstance(loaded, dict) else {}


def _entries(rows: list[object], category: str) -> list[JsonDict]:
    entries: list[JsonDict] = []
    for index, row in enumerate(rows):
        if index == 0 or not isinstance(row, dict):
            continue
        record = cast(JsonDict, row)
        name = strip_escapes(str(record.get("name") or ""))
        if not name:
            continue
        kind = "item"
        if category == "item":
            itype = record.get("itypeId")
            if itype == 2:
                kind = "key"
            elif itype in (3, 4):
                kind = "hidden"
        entries.append(
            {
                "id": index,
                "name": name,
                "kind": kind,
                "description": strip_escapes(str(record.get("description") or "")),
            }
        )
    return entries


def _name_list(rows: object) -> list[str]:
    if not isinstance(rows, list):
        return []
    return [
        strip_escapes(entry) if isinstance(entry, str) else ""
        for entry in cast(list[object], rows)
    ]


def load_names(data_root: str) -> JsonDict:
    """Names and class curves from `<data root>/data/`; empty parts on failure."""
    data_dir = os.path.join(data_root, "data")
    actors: dict[str, str] = {}
    for index, row in enumerate(_read_json_list(data_dir, "Actors")):
        if index and isinstance(row, dict):
            actors[str(index)] = strip_escapes(
                str(cast(JsonDict, row).get("name") or "")
            )
    classes: dict[str, JsonDict] = {}
    for index, row in enumerate(_read_json_list(data_dir, "Classes")):
        if not index or not isinstance(row, dict):
            continue
        record = cast(JsonDict, row)
        params = record.get("expParams")
        curve: list[float] | None = None
        if (
            isinstance(params, list)
            and len(cast(list[object], params)) >= 4
            and all(
                isinstance(v, (int, float)) and not isinstance(v, bool)
                for v in cast(list[object], params)[:4]
            )
        ):
            curve = [float(v) for v in cast(list[float], params)[:4]]
        classes[str(index)] = {
            "name": strip_escapes(str(record.get("name") or "")),
            "expParams": curve,
        }
    system = _read_json_dict(data_dir, "System")
    terms = system.get("terms")
    params = (
        _name_list(cast(JsonDict, terms).get("params"))
        if isinstance(terms, dict)
        else []
    )
    labels = [
        params[i] if i < len(params) and params[i] else _DEFAULT_PARAMS[i]
        for i in range(8)
    ]
    return {
        "items": _entries(_read_json_list(data_dir, "Items"), "item"),
        "weapons": _entries(_read_json_list(data_dir, "Weapons"), "weapon"),
        "armors": _entries(_read_json_list(data_dir, "Armors"), "armor"),
        "actors": actors,
        "classes": classes,
        "switches": _name_list(system.get("switches")),
        "variables": _name_list(system.get("variables")),
        "currency": strip_escapes(str(system.get("currencyUnit") or "")),
        "params": labels,
    }


def exp_for_level(exp_params: Sequence[float], level: int) -> int:
    """Minimum experience for `level` on RPG Maker's standard class curve."""
    basis, extra, acc_a, acc_b = (float(v) for v in exp_params[:4])
    if level <= 1:
        return 0
    # JavaScript divides by zero to Infinity, which zeroes the first term.
    denominator = 6 + math.pow(level, 2) / 50 / acc_b if acc_b else math.inf
    value = (
        basis * math.pow(level - 1, 0.9 + acc_a / 250) * level * (level + 1) / denominator
        + (level - 1) * extra
    )
    return int(math.floor(value + 0.5))


# --------------------------------------------------------- tree helpers


def _unwrap(node: object) -> object:
    """Step through a JsonEx `{"@a": [...]}` array wrapper."""
    if isinstance(node, dict) and "@a" in node:
        return cast(JsonDict, node)["@a"]
    return cast(object, node)


def _child(node: object, key: str) -> object | None:
    node = _unwrap(node)
    if isinstance(node, dict):
        return cast(JsonDict, node).get(key)
    return None


def _array(node: object) -> list[object] | None:
    node = _unwrap(node)
    return cast(list[object], node) if isinstance(node, list) else None


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _plain_int(value: object) -> bool:
    if isinstance(value, bool):
        return False
    return isinstance(value, int) or (isinstance(value, float) and value.is_integer())


def _counts(party: object, key: str) -> dict[int, int] | None:
    held = _child(party, key)
    if not isinstance(held, dict):
        return None
    result: dict[int, int] = {}
    for name, count in cast(JsonDict, held).items():
        if name.startswith("@") or not name.isdigit() or not _plain_int(count):
            continue
        result[int(name)] = int(cast(int, count))
    return result


# ------------------------------------------------------------ view model


def _lookup_name(names: Mapping[str, object], category: str, ident: int) -> str | None:
    key = {"item": "items", "weapon": "weapons", "armor": "armors"}[category]
    for entry in cast(list[JsonDict], names.get(key) or []):
        if entry.get("id") == ident:
            return str(entry.get("name") or "")
    return None


def _actor_view(index: int, actor: JsonDict, names: Mapping[str, object]) -> JsonDict:
    class_id = actor.get("_classId")
    exp: object = None
    exp_map = actor.get("_exp")
    if isinstance(exp_map, dict) and class_id is not None:
        exp = cast(JsonDict, exp_map).get(str(class_id))
    plus = _array(actor.get("_paramPlus")) or []
    classes = cast(dict[str, JsonDict], names.get("classes") or {})
    class_info = classes.get(str(class_id)) or {}
    actor_names = cast(dict[str, str], names.get("actors") or {})
    return {
        "id": index,
        "name": str(actor.get("_name") or ""),
        "database_name": actor_names.get(str(index), ""),
        "level": actor.get("_level"),
        "exp": exp,
        "class_id": class_id,
        "class_name": str(class_info.get("name") or ""),
        "has_curve": class_info.get("expParams") is not None,
        "hp": actor.get("_hp"),
        "mp": actor.get("_mp"),
        "param_plus": [
            plus[i] if i < len(plus) and _is_number(plus[i]) else 0 for i in range(8)
        ],
    }


def _value_rows(
    values: list[object] | None, named: list[str], kind: str
) -> list[JsonDict]:
    stored = max(len(values) - 1, 0) if values is not None else 0
    last = max(stored, len(named) - 1)
    rows: list[JsonDict] = []
    for ident in range(1, last + 1):
        value = values[ident] if values is not None and ident < len(values) else None
        name = named[ident] if ident < len(named) else ""
        if kind == "switch":
            rows.append(
                {"id": ident, "name": name, "value": bool(value), "type": "bool"}
            )
            continue
        if value is None:
            rows.append({"id": ident, "name": name, "value": 0, "type": "number"})
        elif _is_number(value):
            rows.append({"id": ident, "name": name, "value": value, "type": "number"})
        elif isinstance(value, str):
            rows.append({"id": ident, "name": name, "value": value, "type": "string"})
        else:
            rows.append({"id": ident, "name": name, "value": None, "type": "other"})
    return rows


def build_view(tree: Mapping[str, object], names: Mapping[str, object]) -> JsonDict:
    """Curated sections of a decoded save, each with a `missing` flag if absent."""
    missing: list[str] = []
    party = tree.get("party")
    gold = _child(party, "_gold")
    if not _plain_int(gold):
        gold = None
        missing.append("gold")
    inventory: dict[str, list[JsonDict]] = {}
    for category, key in _COUNT_KEYS.items():
        held = _counts(party, key)
        if held is None:
            missing.append(key.strip("_"))
            inventory[category] = []
            continue
        rows: list[JsonDict] = []
        for ident in sorted(held):
            known = _lookup_name(names, category, ident)
            rows.append(
                {
                    "id": ident,
                    "count": held[ident],
                    "name": known if known else f"Unknown {category} #{ident}",
                    "known": known is not None,
                }
            )
        inventory[category] = rows
    actors: list[JsonDict] = []
    actor_rows = _array(_child(tree.get("actors"), "_data"))
    if actor_rows is None:
        missing.append("actors")
    else:
        for index, actor in enumerate(actor_rows):
            if index and isinstance(actor, dict):
                actors.append(_actor_view(index, cast(JsonDict, actor), names))
    switch_values = _array(_child(tree.get("switches"), "_data"))
    variable_values = _array(_child(tree.get("variables"), "_data"))
    if switch_values is None:
        missing.append("switches")
    if variable_values is None:
        missing.append("variables")
    return {
        "gold": gold,
        "inventory": inventory,
        "actors": actors,
        "switches": _value_rows(
            switch_values, cast(list[str], names.get("switches") or []), "switch"
        ),
        "variables": _value_rows(
            variable_values, cast(list[str], names.get("variables") or []), "variable"
        ),
        "missing": missing,
    }


# --------------------------------------------------------------- patches


def _check_int(value: object, low: int, high: int, label: str) -> int:
    if not _plain_int(value):
        raise InvalidPatch(f"{label} must be a whole number")
    number = int(cast(int, value))
    if number < low or number > high:
        raise InvalidPatch(f"{label} must be from {low} to {high}")
    return number


def _check_id(value: object, label: str = "Id") -> int:
    return _check_int(value, 1, MAX_ID, label)


def _walk_to_parent(root: object, path: Sequence[object]) -> tuple[object, object]:
    if not path:
        raise InvalidPatch("A path is required")
    node = root
    for step in path[:-1]:
        node = _step(node, step)
    last = path[-1]
    if isinstance(last, int) and not isinstance(last, bool):
        node = _unwrap(node)
    return node, last


def _step(node: object, step: object) -> object:
    if isinstance(step, int) and not isinstance(step, bool):
        node = _unwrap(node)
    if isinstance(step, str):
        if step.startswith("@"):
            raise InvalidPatch("Keys starting with @ cannot be edited")
        if not isinstance(node, dict) or step not in cast(JsonDict, node):
            raise InvalidPatch("That value does not exist in the save")
        return cast(JsonDict, node)[step]
    if isinstance(step, int) and not isinstance(step, bool):
        if (
            not isinstance(node, list)
            or step < 0
            or step >= len(cast(list[object], node))
        ):
            raise InvalidPatch("That value does not exist in the save")
        return cast(list[object], node)[step]
    raise InvalidPatch("A path holds only keys and indexes")


def _same_type(old: object, new: object) -> bool:
    if isinstance(old, bool) or isinstance(new, bool):
        return isinstance(old, bool) and isinstance(new, bool)
    if _is_number(old):
        return _is_number(new)
    return isinstance(old, str) and isinstance(new, str)


def _apply_set(tree: JsonDict, patch: Mapping[str, object]) -> None:
    raw_path = patch.get("path")
    if not isinstance(raw_path, list):
        raise InvalidPatch("A path is required")
    parent, last = _walk_to_parent(tree, cast(list[object], raw_path))
    value = patch.get("value")
    if isinstance(last, str):
        if last.startswith("@"):
            raise InvalidPatch("Keys starting with @ cannot be edited")
        if not isinstance(parent, dict) or last not in cast(JsonDict, parent):
            raise InvalidPatch("That value does not exist in the save")
        container: JsonDict | list[object] = cast(JsonDict, parent)
        old = cast(JsonDict, parent)[last]
        slot: str | int = last
    elif isinstance(last, int) and not isinstance(last, bool):
        if not isinstance(parent, list) or not 0 <= last < len(
            cast(list[object], parent)
        ):
            raise InvalidPatch("That value does not exist in the save")
        container = cast(list[object], parent)
        old = cast(list[object], parent)[last]
        slot = last
    else:
        raise InvalidPatch("A path holds only keys and indexes")
    if old is None or isinstance(old, (dict, list)):
        raise InvalidPatch("Only text, number and on/off values can be edited")
    if cast(list[object], raw_path) == ["party", "_gold"]:
        _ = _check_int(value, 0, MAX_GOLD, "Gold")
    if not _same_type(old, value):
        raise InvalidPatch("The new value must keep the type of the old one")
    if _is_number(value):
        number = cast(float, value)
        if isinstance(number, float) and not math.isfinite(number):
            raise InvalidPatch("The number is not valid")
        if isinstance(old, int) and not _plain_int(number):
            raise InvalidPatch("This value must be a whole number")
        if abs(number) > MAX_VARIABLE:
            raise InvalidPatch("The number is too large")
        value = int(number) if isinstance(old, int) else number
    elif isinstance(value, str) and len(value) > MAX_STRING:
        raise InvalidPatch("The text is too long")
    if isinstance(container, dict):
        container[cast(str, slot)] = value
    else:
        container[cast(int, slot)] = value


def _apply_set_count(tree: JsonDict, patch: Mapping[str, object]) -> None:
    kind = patch.get("kind")
    if not isinstance(kind, str) or kind not in _COUNT_KEYS:
        raise InvalidPatch("Unknown inventory kind")
    ident = _check_id(patch.get("id"))
    count = _check_int(patch.get("count"), 0, MAX_COUNT, "Count")
    party = tree.get("party")
    if not isinstance(party, dict):
        raise InvalidPatch("This save has no party")
    party_dict = cast(JsonDict, party)
    held = party_dict.get(_COUNT_KEYS[kind])
    if held is None:
        held = {}
        party_dict[_COUNT_KEYS[kind]] = held
    if not isinstance(held, dict):
        raise InvalidPatch("This save's inventory has an unexpected layout")
    if count == 0:
        _ = cast(JsonDict, held).pop(str(ident), None)
    else:
        cast(JsonDict, held)[str(ident)] = count


def _data_array(tree: JsonDict, section: str) -> list[object]:
    values = _array(_child(tree.get(section), "_data"))
    if values is None:
        raise InvalidPatch(f"This save has no {section} data")
    return values


def _pad(values: list[object], ident: int) -> None:
    while len(values) <= ident:
        values.append(None)


def _apply_set_switch(tree: JsonDict, patch: Mapping[str, object]) -> None:
    ident = _check_id(patch.get("id"))
    value = patch.get("value")
    if not isinstance(value, bool):
        raise InvalidPatch("A switch is on or off")
    values = _data_array(tree, "switches")
    _pad(values, ident)
    values[ident] = value


def _apply_set_variable(tree: JsonDict, patch: Mapping[str, object]) -> None:
    ident = _check_id(patch.get("id"))
    value = patch.get("value")
    values = _data_array(tree, "variables")
    old = values[ident] if ident < len(values) else None
    if old is not None and not _is_number(old) and not isinstance(old, str):
        raise InvalidPatch("This variable holds a value that cannot be edited")
    if isinstance(value, str):
        if old is not None and not isinstance(old, str):
            raise InvalidPatch("This variable holds a number")
        if len(value) > MAX_STRING:
            raise InvalidPatch("The text is too long")
    elif _is_number(value):
        if isinstance(old, str):
            raise InvalidPatch("This variable holds text")
        number = cast(float, value)
        if isinstance(number, float) and not math.isfinite(number):
            raise InvalidPatch("The number is not valid")
        if abs(number) > MAX_VARIABLE:
            raise InvalidPatch("The number is too large")
        if _plain_int(number):
            value = int(number)
    else:
        raise InvalidPatch("A variable holds a number or text")
    _pad(values, ident)
    values[ident] = value


def _apply_set_actor(
    tree: JsonDict, patch: Mapping[str, object], names: Mapping[str, object]
) -> None:
    ident = _check_id(patch.get("id"), "Actor")
    rows = _data_array(tree, "actors")
    actor = rows[ident] if ident < len(rows) else None
    if not isinstance(actor, dict):
        raise InvalidPatch("That actor is not in the save")
    record = cast(JsonDict, actor)
    field = patch.get("field")
    value = patch.get("value")
    class_id = record.get("_classId")
    if field == "name":
        if not isinstance(value, str) or not value.strip() or len(value) > MAX_NAME:
            raise InvalidPatch("The name must be text of 1 to 200 characters")
        record["_name"] = value
    elif field == "level":
        level = _check_int(value, 1, MAX_LEVEL, "Level")
        record["_level"] = level
        classes = cast(dict[str, JsonDict], names.get("classes") or {})
        curve = cast(
            "list[float] | None", (classes.get(str(class_id)) or {}).get("expParams")
        )
        exp_map = record.get("_exp")
        if curve is not None and isinstance(exp_map, dict):
            cast(JsonDict, exp_map)[str(class_id)] = exp_for_level(curve, level)
    elif field == "exp":
        exp = _check_int(value, 0, MAX_EXP, "Experience")
        exp_map = record.get("_exp")
        if class_id is None or not isinstance(exp_map, dict):
            raise InvalidPatch("This actor has no experience data")
        cast(JsonDict, exp_map)[str(class_id)] = exp
    elif field in ("hp", "mp"):
        record[f"_{field}"] = _check_int(value, 0, MAX_STAT, str(field).upper())
    elif field == "paramPlus":
        index = _check_int(patch.get("index"), 0, 7, "Parameter index")
        plus = _array(record.get("_paramPlus"))
        if plus is None:
            raise InvalidPatch("This actor has no parameter bonuses")
        _pad(plus, index)
        plus[index] = _check_int(value, -MAX_BONUS, MAX_BONUS, "Bonus")
        for i, entry in enumerate(plus):
            if entry is None:
                plus[i] = 0
    else:
        raise InvalidPatch("Unknown actor field")


def apply_patches(
    tree: JsonDict, patches: Sequence[object], names: Mapping[str, object]
) -> None:
    """Validate and apply `patches` in order, in place. Raises InvalidPatch."""
    if len(patches) > 20_000:
        raise InvalidPatch("Too many edits at once")
    for raw in patches:
        if not isinstance(raw, dict):
            raise InvalidPatch("An edit is not valid")
        patch = cast(JsonDict, raw)
        op = patch.get("op")
        if op == "set":
            _apply_set(tree, patch)
        elif op == "set_count":
            _apply_set_count(tree, patch)
        elif op == "set_switch":
            _apply_set_switch(tree, patch)
        elif op == "set_variable":
            _apply_set_variable(tree, patch)
        elif op == "set_actor":
            _apply_set_actor(tree, patch, names)
        else:
            raise InvalidPatch("Unknown edit")


# ----------------------------------------------------------------- slots


def save_folders(data_root: str, effective: str) -> list[str]:
    """`<data root>/save` plus the effective folder, deduplicated by real path."""
    folders: list[str] = []
    seen: set[str] = set()
    for candidate in (os.path.join(data_root, "save"), effective):
        if not candidate or not os.path.isdir(candidate):
            continue
        real = os.path.realpath(candidate)
        if real not in seen:
            seen.add(real)
            folders.append(candidate)
    return folders


def _global_info(folder: str, fmt: str) -> dict[int, JsonDict]:
    path = os.path.join(folder, "global" + _EXTENSIONS[fmt])
    try:
        with open(path, "rb") as handle:
            data = handle.read(8 * 1024 * 1024)
        text = data.decode("utf-8")
        if fmt == FORMAT_MZ:
            loaded = cast(object, json.loads(zlib.decompress(text.encode("latin-1"))))
        else:
            loaded = cast(object, json.loads(decompress_from_base64(text.strip())))
    except (
        OSError,
        UnicodeError,
        zlib.error,
        LZStringError,
        ValueError,
        RecursionError,
    ):
        return {}
    if not isinstance(loaded, list):
        return {}
    return {
        index: cast(JsonDict, entry)
        for index, entry in enumerate(cast(list[object], loaded))
        if isinstance(entry, dict)
    }


def list_slots(data_root: str, effective: str) -> list[dict[str, object]]:
    """Slot files with details; each has an internal `folder` the API strips."""
    slots: list[dict[str, object]] = []
    seen: set[str] = set()
    # Files are addressed by name, so a name found in an earlier folder hides
    # a same-named file in a later one (the game's own save folder wins).
    names_seen: set[str] = set()
    for folder in save_folders(data_root, effective):
        try:
            entries = sorted(os.listdir(folder))
        except OSError:
            continue
        infos: dict[str, dict[int, JsonDict]] = {}
        for entry in entries:
            match = SLOT_NAME.match(entry)
            path = os.path.join(folder, entry)
            if match is None or not os.path.isfile(path):
                continue
            real = os.path.realpath(path)
            if real in seen or entry in names_seen:
                continue
            seen.add(real)
            names_seen.add(entry)
            try:
                stat = os.stat(path)
            except OSError:
                continue
            fmt = format_for_name(entry)
            if fmt not in infos:
                infos[fmt] = _global_info(folder, fmt)
            info = infos[fmt].get(int(match.group(1))) or {}
            playtime = info.get("playtime")
            title = info.get("title")
            slots.append(
                {
                    "file": entry,
                    "slot": int(match.group(1)),
                    "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(
                        timespec="seconds"
                    ),
                    "size": stat.st_size,
                    "playtime": playtime if isinstance(playtime, str) else "",
                    "title": title if isinstance(title, str) else "",
                    "folder": folder,
                }
            )
    slots.sort(key=lambda slot: (cast(int, slot["slot"]), str(slot["file"])))
    return slots


def resolve_slot(data_root: str, effective: str, name: object) -> tuple[str, str]:
    """(path, folder) of a listed slot file, addressed by name only."""
    if (
        not isinstance(name, str)
        or not name
        or name.startswith(".")
        or "/" in name
        or "\\" in name
        or os.sep in name
        or SLOT_NAME.match(name) is None
    ):
        raise RpgmSaveError("That save file is not available", "not_found")
    for slot in list_slots(data_root, effective):
        if slot["file"] == name:
            folder = cast(str, slot["folder"])
            return os.path.join(folder, name), folder
    raise RpgmSaveError("That save file is not available", "not_found")


# ------------------------------------------------------------ load/write


def load_save(path: str, names: Mapping[str, object]) -> JsonDict:
    """Decode a slot file into the payload the editor shows."""
    try:
        stat = os.stat(path)
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError as exc:
        raise RpgmSaveError("The save file could not be read", "read_failed") from exc
    fmt = format_for_name(os.path.basename(path))
    tree = decode_save(data, fmt)
    return {
        "format": fmt,
        "size": stat.st_size,
        "mtime_ns": str(stat.st_mtime_ns),  # string: a JS number would lose digits
        "view": build_view(tree, names),
        "names": dict(names),
        "raw": tree,
    }


def write_save(
    path: str,
    patches: Sequence[object],
    size: int,
    mtime_ns: int,
    names: Mapping[str, object],
    backup: Callable[[], str],
) -> str:
    """Apply `patches` to the slot file safely and return the backup name.

    Order: size/mtime check, decode and apply, backup, encode, verify, then an
    atomic replace. Any failure leaves the original file untouched.
    """
    try:
        stat = os.stat(path)
    except OSError as exc:
        raise SaveChanged("The save file is gone. Reopen the slot.") from exc
    if stat.st_size != size or stat.st_mtime_ns != mtime_ns:
        raise SaveChanged("The save changed since it was opened. Reopen the slot.")
    fmt = format_for_name(os.path.basename(path))
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError as exc:
        raise RpgmSaveError("The save file could not be read", "read_failed") from exc
    if len(data) != size:
        raise SaveChanged("The save changed since it was opened. Reopen the slot.")
    tree = decode_save(data, fmt)
    apply_patches(tree, patches, names)
    backup_name = backup()
    encoded = encode_save(tree, fmt)
    if decode_save(encoded, fmt) != tree:
        raise RpgmSaveError("The edited save failed its check, nothing was written")
    folder, name = os.path.split(path)
    temp = os.path.join(folder, f".{name}{TEMP_SUFFIX}")
    try:
        with open(temp, "wb") as handle:
            _ = handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            shutil.copymode(path, temp)
        except OSError:
            pass
        os.replace(temp, path)
    except OSError as exc:
        raise RpgmSaveError(
            f"The save could not be written: {exc.strerror or exc}", "write_failed"
        ) from exc
    finally:
        if os.path.lexists(temp):
            try:
                os.remove(temp)
            except OSError:
                pass
    return backup_name
