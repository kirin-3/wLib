# SPDX-License-Identifier: GPL-3.0-or-later
"""RPG Maker XP, VX and VX Ace (RGSS) save files: slots, names, view, patches and safe write.

Everything here takes explicit paths so it can be tested against a temp dir.
`core.api_mixins.saves` resolves the game's folders from the stored row and calls in.
"""

from __future__ import annotations

import math
import os
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import cast

from core import rgss_tools
from core._save_io import (
    CHANGED_MESSAGE,
    InvalidPatch,
    RpgmSaveError,
    SaveChanged,
    UnsupportedSaveFormat,
    atomic_replace_checked,
    read_fingerprint,
    reject_symlink,
)
from core.rpgm_saves import (
    MAX_BONUS,
    MAX_COUNT,
    MAX_EXP,
    MAX_GOLD,
    MAX_ID,
    MAX_LEVEL,
    MAX_NAME,
    MAX_STAT,
    MAX_STRING,
    MAX_VARIABLE,
    JsonDict,
    strip_escapes,
)
from core.ruby_marshal import (
    MAX_BYTES,
    MAX_SAFE_JS_INT,
    ArrayNode,
    BoolNode,
    FloatNode,
    HashNode,
    IntNode,
    LinkNode,
    MarshalNode,
    MarshalUnsupported,
    NilNode,
    ObjectNode,
    StringNode,
    SymbolNode,
    assign_ids,
    dump_all,
    load_all,
    load_prefix,
    project_dumps,
    trees_equal,
)

FORMAT_XP = "xp"
FORMAT_VX = "vx"
FORMAT_VXACE = "vxace"

EXT_TO_FORMAT = {
    ".rxdata": FORMAT_XP,
    ".rvdata": FORMAT_VX,
    ".rvdata2": FORMAT_VXACE,
}
FORMAT_TO_EXT = {fmt: ext for ext, fmt in EXT_TO_FORMAT.items()}

SLOT_NAME = re.compile(r"^save(\d+)\.(rxdata|rvdata|rvdata2)$", re.IGNORECASE)
ALLOWED_RELATIVES = ("", "Save", "Saves", "savedata")
_SUBFOLDERS = ("Save", "Saves", "savedata")


def format_for_name(name: str) -> str:
    lower = name.lower()
    for ext, fmt in EXT_TO_FORMAT.items():
        if lower.endswith(ext):
            return fmt
    raise RpgmSaveError("Unknown RGSS save format", "unsupported_format")


# ----------------------------------------------------------------------- slots


def save_folders(exe_folder: str, effective: str) -> list[tuple[str, str | None]]:
    """Listed save folders in priority order, deduplicated by real path.

    Each item is `(folder_path, relative)` where `relative` is `""` for the
    executable folder, `"Save"`, `"Saves"`, or `"savedata"` for its direct
    subfolders, or `None` for the game's effective saves folder.
    """
    candidates: list[tuple[str, str | None]] = []
    if exe_folder:
        candidates.append((exe_folder, ""))
        for sub in _SUBFOLDERS:
            candidates.append((os.path.join(exe_folder, sub), sub))
    if effective:
        candidates.append((effective, None))

    folders: list[tuple[str, str | None]] = []
    seen: set[str] = set()
    for path, relative in candidates:
        if not path or not os.path.isdir(path):
            continue
        real = os.path.realpath(path)
        if real in seen:
            continue
        seen.add(real)
        folders.append((path, relative))
    return folders


def _hash_get_sym(node: MarshalNode, sym_name: bytes) -> MarshalNode | None:
    if not isinstance(node, HashNode):
        return None
    for k, v in node.pairs:
        if isinstance(k, SymbolNode) and k.name == sym_name:
            return v
    return None


def _format_frames(frames: int, fps: int) -> str:
    if frames < 0 or fps <= 0:
        return ""
    total_seconds = frames // fps
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _read_playtime(path: str, fmt: str) -> str:
    """Best-effort playtime string for a listed slot; returns '' when unreadable."""
    try:
        if os.path.getsize(path) > MAX_BYTES:
            return ""
        with open(path, "rb") as handle:
            data = handle.read(MAX_BYTES + 1)
    except OSError:
        return ""
    if len(data) > MAX_BYTES:
        return ""
    need = 1 if fmt == FORMAT_VXACE else 2
    try:
        dumps = load_prefix(data, need)
    except MarshalUnsupported:
        return ""
    if len(dumps) < need:
        return ""
    if fmt == FORMAT_VXACE:
        playtime_node = _hash_get_sym(dumps[0], b"playtime_s")
        if isinstance(playtime_node, StringNode):
            text = playtime_node.decode_utf8()
            return text if text is not None else ""
        return ""
    frames_node = dumps[1]
    if isinstance(frames_node, IntNode) and frames_node.value >= 0:
        fps = 40 if fmt == FORMAT_XP else 60
        return _format_frames(frames_node.value, fps)
    return ""


def list_slots(exe_folder: str, effective: str) -> list[dict[str, object]]:
    """Listed RGSS slot files with internal `folder` and `relative` fields."""
    slots: list[dict[str, object]] = []
    seen_real: set[str] = set()
    seen_names: set[str] = set()
    for folder, relative in save_folders(exe_folder, effective):
        try:
            entries = sorted(os.listdir(folder))
        except OSError:
            continue
        for entry in entries:
            match = SLOT_NAME.match(entry)
            if match is None:
                continue
            path = os.path.join(folder, entry)
            if not os.path.isfile(path):
                continue
            real = os.path.realpath(path)
            if real in seen_real or entry in seen_names:
                continue
            seen_real.add(real)
            seen_names.add(entry)
            try:
                st = os.stat(path)
            except OSError:
                continue
            fmt = format_for_name(entry)
            slots.append(
                {
                    "file": entry,
                    "slot": int(match.group(1)),
                    "format": fmt,
                    "modified": datetime.fromtimestamp(st.st_mtime).isoformat(
                        timespec="seconds"
                    ),
                    "size": st.st_size,
                    "playtime": _read_playtime(path, fmt),
                    "title": "",
                    "folder": folder,
                    "relative": relative,
                }
            )
    slots.sort(key=lambda slot: (cast(int, slot["slot"]), str(slot["file"])))
    return slots


def resolve_slot(
    exe_folder: str, effective: str, name: object
) -> tuple[str, str, str | None]:
    """Return `(path, folder, relative)` of a listed RGSS slot, addressed by name only."""
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
    for slot in list_slots(exe_folder, effective):
        if slot["file"] == name:
            folder = cast(str, slot["folder"])
            relative = cast("str | None", slot["relative"])
            return os.path.join(folder, name), folder, relative
    raise RpgmSaveError("That save file is not available", "not_found")


# ----------------------------------------------------------------------- names

_XP_DEFAULT_PARAMS = ("Max HP", "Max SP", "STR", "DEX", "AGI", "INT")
_XP_PARAM_IVARS = (b"@hp", b"@sp", b"@str", b"@dex", b"@agi", b"@int")

_VX_DEFAULT_PARAMS = ("Max HP", "Max MP", "ATK", "DEF", "SPI", "AGI")
_VX_PARAM_IVARS = (b"@hp", b"@mp", b"@atk", b"@def", b"@spi", b"@agi")

_VXACE_DEFAULT_PARAMS = ("Max HP", "Max MP", "ATK", "DEF", "MAT", "MDF", "AGI", "LUK")


def _unwrap_link(node: MarshalNode | None) -> MarshalNode | None:
    current = node
    for _ in range(8):
        if not isinstance(current, LinkNode):
            return current
        current = current.target
    return current


def _ivar(node: MarshalNode | None, name: bytes) -> MarshalNode | None:
    obj = _unwrap_link(node)
    if not isinstance(obj, ObjectNode):
        return None
    for sym, val in obj.attributes:
        if sym.name == name:
            return _unwrap_link(val)
    return None


def _node_text(node: MarshalNode | None) -> str:
    leaf = _unwrap_link(node)
    if not isinstance(leaf, StringNode):
        return ""
    for encoding in ("utf-8", "cp932"):
        try:
            return leaf.data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return leaf.data.decode("utf-8", errors="replace")


def _find_loose_data_file(folder: str, filename: str) -> str | None:
    target_lower = filename.lower()
    try:
        top_entries = sorted(os.listdir(folder))
    except OSError:
        return None
    for entry in top_entries:
        if entry.lower() != "data":
            continue
        data_dir = os.path.join(folder, entry)
        if not os.path.isdir(data_dir):
            continue
        try:
            sub_entries = sorted(os.listdir(data_dir))
        except OSError:
            continue
        for sub in sub_entries:
            if sub.lower() == target_lower:
                candidate = os.path.join(data_dir, sub)
                if not os.path.islink(candidate) and os.path.isfile(candidate):
                    return candidate
    return None


def _read_data_root(folder: str, stem: str, ext: str) -> MarshalNode | None:
    filename = f"{stem}{ext}"
    raw: bytes | None = None
    loose = _find_loose_data_file(folder, filename)
    if loose is not None:
        try:
            if os.path.getsize(loose) <= MAX_BYTES:
                with open(loose, "rb") as handle:
                    raw = handle.read(MAX_BYTES + 1)
                if len(raw) > MAX_BYTES:
                    raw = None
        except OSError:
            raw = None
    else:
        raw = rgss_tools.read_entry(folder, f"Data/{filename}", MAX_BYTES)
    if not raw:
        return None
    try:
        dumps = load_all(raw)
    except MarshalUnsupported:
        return None
    return dumps[0] if dumps else None


def _db_entries(root: MarshalNode | None, category: str, fmt: str) -> list[JsonDict]:
    arr = _unwrap_link(root)
    if not isinstance(arr, ArrayNode):
        return []
    entries: list[JsonDict] = []
    for index, raw_row in enumerate(arr.items):
        if index == 0:
            continue
        row = _unwrap_link(raw_row)
        if not isinstance(row, ObjectNode):
            continue
        name = strip_escapes(_node_text(_ivar(row, b"@name")))
        if not name:
            continue
        kind = "item"
        if category == "item" and fmt == FORMAT_VXACE:
            itype = _ivar(row, b"@itype_id")
            if isinstance(itype, IntNode):
                if itype.value == 2:
                    kind = "key"
                elif itype.value in (3, 4):
                    kind = "hidden"
        entries.append(
            {
                "id": index,
                "name": name,
                "kind": kind,
                "description": strip_escapes(_node_text(_ivar(row, b"@description"))),
            }
        )
    return entries


def _name_list(node: MarshalNode | None) -> list[str]:
    arr = _unwrap_link(node)
    if not isinstance(arr, ArrayNode):
        return []
    return [strip_escapes(_node_text(entry)) for entry in arr.items]


def load_rgss_names(folder: str, fmt: str) -> JsonDict:
    """Load database names from `<folder>/Data/*.<ext>` or the RGSS archive."""
    ext = FORMAT_TO_EXT.get(fmt)
    if ext is None:
        raise RpgmSaveError("Unknown RGSS save format", "unsupported_format")

    items = _db_entries(_read_data_root(folder, "Items", ext), "item", fmt)
    weapons = _db_entries(_read_data_root(folder, "Weapons", ext), "weapon", fmt)
    armors = _db_entries(_read_data_root(folder, "Armors", ext), "armor", fmt)

    actors: dict[str, str] = {}
    actors_root = _unwrap_link(_read_data_root(folder, "Actors", ext))
    if isinstance(actors_root, ArrayNode):
        for index, raw_row in enumerate(actors_root.items):
            if index == 0:
                continue
            row = _unwrap_link(raw_row)
            if isinstance(row, ObjectNode):
                actors[str(index)] = strip_escapes(_node_text(_ivar(row, b"@name")))

    system = _unwrap_link(_read_data_root(folder, "System", ext))
    switches = _name_list(_ivar(system, b"@switches"))
    variables = _name_list(_ivar(system, b"@variables"))

    currency = ""
    params: list[str]
    mp_label: str

    if fmt == FORMAT_XP:
        words = _ivar(system, b"@words")
        currency = strip_escapes(_node_text(_ivar(words, b"@gold"))) or "G"
        params = [
            strip_escapes(_node_text(_ivar(words, ivar_name))) or default
            for ivar_name, default in zip(
                _XP_PARAM_IVARS, _XP_DEFAULT_PARAMS, strict=True
            )
        ]
        mp_label = strip_escapes(_node_text(_ivar(words, b"@sp"))) or "SP"
    elif fmt == FORMAT_VX:
        terms = _ivar(system, b"@terms")
        currency = strip_escapes(_node_text(_ivar(terms, b"@gold"))) or "G"
        params = [
            strip_escapes(_node_text(_ivar(terms, ivar_name))) or default
            for ivar_name, default in zip(
                _VX_PARAM_IVARS, _VX_DEFAULT_PARAMS, strict=True
            )
        ]
        mp_label = strip_escapes(_node_text(_ivar(terms, b"@mp"))) or "MP"
    else:
        currency = strip_escapes(_node_text(_ivar(system, b"@currency_unit"))) or "G"
        terms = _ivar(system, b"@terms")
        raw_params = _name_list(_ivar(terms, b"@params"))
        params = [
            raw_params[i]
            if i < len(raw_params) and raw_params[i]
            else _VXACE_DEFAULT_PARAMS[i]
            for i in range(8)
        ]
        mp_label = "MP"

    return {
        "items": items,
        "weapons": weapons,
        "armors": armors,
        "actors": actors,
        "classes": {},
        "switches": switches,
        "variables": variables,
        "currency": currency,
        "params": params,
        "mp_label": mp_label,
    }


load_names = load_rgss_names


# ----------------------------------------------------------- layout & view

_XP_BONUS_IVARS = (
    b"@maxhp_plus",
    b"@maxsp_plus",
    b"@str_plus",
    b"@dex_plus",
    b"@agi_plus",
    b"@int_plus",
)
_VX_BONUS_IVARS = (
    b"@maxhp_plus",
    b"@maxmp_plus",
    b"@atk_plus",
    b"@def_plus",
    b"@spi_plus",
    b"@agi_plus",
)
_INVENTORY_IVARS = (
    ("item", b"@items", "items"),
    ("weapon", b"@weapons", "weapons"),
    ("armor", b"@armors", "armors"),
)


@dataclass
class _Sections:
    party: ObjectNode | None
    actors: ObjectNode | None
    switches: ObjectNode | None
    variables: ObjectNode | None


def _raw_ivar(node: MarshalNode | None, name: bytes) -> MarshalNode | None:
    obj = _unwrap_link(node)
    if not isinstance(obj, ObjectNode):
        return None
    for sym, val in obj.attributes:
        if sym.name == name:
            return val
    return None


def _expect_class(node: MarshalNode | None, expected: str) -> ObjectNode | None:
    obj = _unwrap_link(node)
    if isinstance(obj, ObjectNode) and obj.class_name.text == expected:
        return obj
    return None


def _extract_sections(dumps: list[MarshalNode], fmt: str) -> _Sections:
    if fmt == FORMAT_XP:
        return _Sections(
            party=_expect_class(dumps[8] if len(dumps) > 8 else None, "Game_Party"),
            actors=_expect_class(dumps[7] if len(dumps) > 7 else None, "Game_Actors"),
            switches=_expect_class(
                dumps[3] if len(dumps) > 3 else None, "Game_Switches"
            ),
            variables=_expect_class(
                dumps[4] if len(dumps) > 4 else None, "Game_Variables"
            ),
        )
    if fmt == FORMAT_VX:
        return _Sections(
            party=_expect_class(dumps[10] if len(dumps) > 10 else None, "Game_Party"),
            actors=_expect_class(dumps[9] if len(dumps) > 9 else None, "Game_Actors"),
            switches=_expect_class(
                dumps[6] if len(dumps) > 6 else None, "Game_Switches"
            ),
            variables=_expect_class(
                dumps[7] if len(dumps) > 7 else None, "Game_Variables"
            ),
        )
    if fmt == FORMAT_VXACE:
        contents = _unwrap_link(dumps[1]) if len(dumps) > 1 else None
        if isinstance(contents, HashNode):
            return _Sections(
                party=_expect_class(_hash_get_sym(contents, b"party"), "Game_Party"),
                actors=_expect_class(_hash_get_sym(contents, b"actors"), "Game_Actors"),
                switches=_expect_class(
                    _hash_get_sym(contents, b"switches"), "Game_Switches"
                ),
                variables=_expect_class(
                    _hash_get_sym(contents, b"variables"), "Game_Variables"
                ),
            )
    return _Sections(party=None, actors=None, switches=None, variables=None)


def _node_ref(node: MarshalNode | None, editable_ids: set[int]) -> int | None:
    if node is None or node.node_id is None:
        return None
    return node.node_id if node.node_id in editable_ids else None


def _lookup_db_name(
    names: Mapping[str, object], category: str, ident: int
) -> str | None:
    key = {"item": "items", "weapon": "weapons", "armor": "armors"}[category]
    for entry in cast(list[JsonDict], names.get(key) or []):
        if entry.get("id") == ident:
            return str(entry.get("name") or "")
    return None


def _actor_view(
    index: int,
    actor: ObjectNode,
    fmt: str,
    names: Mapping[str, object],
    editable_ids: set[int],
) -> JsonDict:
    name_node = _raw_ivar(actor, b"@name")
    if isinstance(name_node, StringNode):
        utf8 = name_node.decode_utf8()
        name_val = utf8 if utf8 is not None else name_node.display_text()
        name_ref = _node_ref(name_node, editable_ids)
    else:
        name_val = ""
        name_ref = None

    level_node = _raw_ivar(actor, b"@level")
    level_val = level_node.value if isinstance(level_node, IntNode) else None
    level_ref = (
        _node_ref(level_node, editable_ids) if isinstance(level_node, IntNode) else None
    )

    class_node = _raw_ivar(actor, b"@class_id")
    class_id = class_node.value if isinstance(class_node, IntNode) else None

    hp_node = _raw_ivar(actor, b"@hp")
    hp_val = hp_node.value if isinstance(hp_node, IntNode) else None
    hp_ref = _node_ref(hp_node, editable_ids) if isinstance(hp_node, IntNode) else None

    mp_ivar = b"@sp" if fmt == FORMAT_XP else b"@mp"
    mp_node = _raw_ivar(actor, mp_ivar)
    mp_val = mp_node.value if isinstance(mp_node, IntNode) else None
    mp_ref = _node_ref(mp_node, editable_ids) if isinstance(mp_node, IntNode) else None

    exp_val: int | None = None
    exp_ref: int | None = None
    has_curve = False

    if fmt in (FORMAT_XP, FORMAT_VX):
        exp_node = _raw_ivar(actor, b"@exp")
        if isinstance(exp_node, IntNode):
            exp_val = exp_node.value
            exp_ref = _node_ref(exp_node, editable_ids)
        exp_list = _unwrap_link(_raw_ivar(actor, b"@exp_list"))
        if isinstance(exp_node, IntNode) and isinstance(exp_list, ArrayNode):
            has_curve = any(isinstance(entry, IntNode) for entry in exp_list.items[1:])
    else:
        exp_map = _unwrap_link(_raw_ivar(actor, b"@exp"))
        if isinstance(exp_map, HashNode) and class_id is not None:
            for k, v in exp_map.pairs:
                if (
                    isinstance(k, IntNode)
                    and k.value == class_id
                    and isinstance(v, IntNode)
                ):
                    exp_val = v.value
                    exp_ref = _node_ref(v, editable_ids)
                    break

    param_plus: list[int] = []
    param_plus_refs: list[int | None] = []
    if fmt == FORMAT_XP:
        for ivar_name in _XP_BONUS_IVARS:
            node = _raw_ivar(actor, ivar_name)
            param_plus.append(node.value if isinstance(node, IntNode) else 0)
            param_plus_refs.append(
                _node_ref(node, editable_ids) if isinstance(node, IntNode) else None
            )
    elif fmt == FORMAT_VX:
        for ivar_name in _VX_BONUS_IVARS:
            node = _raw_ivar(actor, ivar_name)
            param_plus.append(node.value if isinstance(node, IntNode) else 0)
            param_plus_refs.append(
                _node_ref(node, editable_ids) if isinstance(node, IntNode) else None
            )
    else:
        plus_arr = _unwrap_link(_raw_ivar(actor, b"@param_plus"))
        items = plus_arr.items if isinstance(plus_arr, ArrayNode) else []
        for i in range(8):
            node = items[i] if i < len(items) else None
            param_plus.append(node.value if isinstance(node, IntNode) else 0)
            param_plus_refs.append(
                _node_ref(node, editable_ids) if isinstance(node, IntNode) else None
            )

    actor_names = cast(dict[str, str], names.get("actors") or {})
    return {
        "id": index,
        "name": name_val,
        "database_name": actor_names.get(str(index), ""),
        "level": level_val,
        "exp": exp_val,
        "class_id": class_id,
        "class_name": "",
        "has_curve": has_curve,
        "hp": hp_val,
        "mp": mp_val,
        "param_plus": param_plus,
        "name_ref": name_ref,
        "level_ref": level_ref,
        "exp_ref": exp_ref,
        "hp_ref": hp_ref,
        "mp_ref": mp_ref,
        "param_plus_refs": param_plus_refs,
    }


def _switch_rows(
    arr: ArrayNode | None, named: list[str], editable_ids: set[int]
) -> list[JsonDict]:
    stored = max(len(arr.items) - 1, 0) if arr is not None else 0
    last = max(stored, len(named) - 1)
    rows: list[JsonDict] = []
    for ident in range(1, last + 1):
        node = arr.items[ident] if arr is not None and ident < len(arr.items) else None
        name = named[ident] if ident < len(named) else ""
        if isinstance(node, BoolNode):
            rows.append(
                {
                    "id": ident,
                    "name": name,
                    "value": node.value,
                    "type": "bool",
                    "ref": _node_ref(node, editable_ids),
                }
            )
        elif node is None or isinstance(node, NilNode):
            rows.append(
                {
                    "id": ident,
                    "name": name,
                    "value": False,
                    "type": "bool",
                    "ref": None,
                }
            )
        else:
            rows.append(
                {
                    "id": ident,
                    "name": name,
                    "value": True,
                    "type": "bool",
                    "ref": None,
                }
            )
    return rows


def _variable_rows(
    arr: ArrayNode | None, named: list[str], editable_ids: set[int]
) -> list[JsonDict]:
    stored = max(len(arr.items) - 1, 0) if arr is not None else 0
    last = max(stored, len(named) - 1)
    rows: list[JsonDict] = []
    for ident in range(1, last + 1):
        node = arr.items[ident] if arr is not None and ident < len(arr.items) else None
        name = named[ident] if ident < len(named) else ""
        if node is None or isinstance(node, NilNode):
            rows.append(
                {"id": ident, "name": name, "value": 0, "type": "number", "ref": None}
            )
        elif isinstance(node, IntNode) and abs(node.value) <= MAX_SAFE_JS_INT:
            rows.append(
                {
                    "id": ident,
                    "name": name,
                    "value": node.value,
                    "type": "number",
                    "ref": _node_ref(node, editable_ids),
                }
            )
        elif isinstance(node, FloatNode) and math.isfinite(node.value):
            rows.append(
                {
                    "id": ident,
                    "name": name,
                    "value": node.value,
                    "type": "number",
                    "ref": _node_ref(node, editable_ids),
                }
            )
        elif isinstance(node, StringNode) and node.is_editable_utf8():
            rows.append(
                {
                    "id": ident,
                    "name": name,
                    "value": node.decode_utf8(),
                    "type": "string",
                    "ref": _node_ref(node, editable_ids),
                }
            )
        else:
            rows.append(
                {"id": ident, "name": name, "value": None, "type": "other", "ref": None}
            )
    return rows


def build_view(
    dumps: list[MarshalNode],
    fmt: str,
    names: Mapping[str, object],
    editable_ids: set[int],
) -> JsonDict:
    """Curated sections of a decoded RGSS save, with `ref` on every leaf."""
    sections = _extract_sections(dumps, fmt)
    missing: list[str] = []

    gold_node = _raw_ivar(sections.party, b"@gold")
    if isinstance(gold_node, IntNode) and abs(gold_node.value) <= MAX_SAFE_JS_INT:
        gold: int | None = gold_node.value
        gold_ref = _node_ref(gold_node, editable_ids)
    else:
        gold = None
        gold_ref = None
        missing.append("gold")

    inventory: dict[str, list[JsonDict]] = {}
    for category, ivar_name, missing_key in _INVENTORY_IVARS:
        held = _unwrap_link(_raw_ivar(sections.party, ivar_name))
        if not isinstance(held, HashNode):
            missing.append(missing_key)
            inventory[category] = []
            continue
        entries: dict[int, IntNode] = {}
        for k, v in held.pairs:
            if isinstance(k, IntNode) and isinstance(v, IntNode):
                entries[k.value] = v
        rows: list[JsonDict] = []
        for ident in sorted(entries):
            count_node = entries[ident]
            known = _lookup_db_name(names, category, ident)
            rows.append(
                {
                    "id": ident,
                    "count": count_node.value,
                    "name": known if known else f"Unknown {category} #{ident}",
                    "known": known is not None,
                    "ref": _node_ref(count_node, editable_ids),
                }
            )
        inventory[category] = rows

    actors: list[JsonDict] = []
    actor_arr = _unwrap_link(_raw_ivar(sections.actors, b"@data"))
    if not isinstance(actor_arr, ArrayNode):
        missing.append("actors")
    else:
        for index, raw_actor in enumerate(actor_arr.items):
            if index == 0:
                continue
            actor = _expect_class(raw_actor, "Game_Actor")
            if actor is not None:
                actors.append(_actor_view(index, actor, fmt, names, editable_ids))

    switch_arr = _unwrap_link(_raw_ivar(sections.switches, b"@data"))
    variable_arr = _unwrap_link(_raw_ivar(sections.variables, b"@data"))
    if not isinstance(switch_arr, ArrayNode):
        missing.append("switches")
        switch_arr = None
    if not isinstance(variable_arr, ArrayNode):
        missing.append("variables")
        variable_arr = None

    return {
        "gold": gold,
        "gold_ref": gold_ref,
        "inventory": inventory,
        "actors": actors,
        "switches": _switch_rows(
            switch_arr, cast(list[str], names.get("switches") or []), editable_ids
        ),
        "variables": _variable_rows(
            variable_arr, cast(list[str], names.get("variables") or []), editable_ids
        ),
        "missing": missing,
    }


def decode_save(data: bytes) -> list[MarshalNode]:
    try:
        return load_all(data)
    except MarshalUnsupported as exc:
        raise UnsupportedSaveFormat(
            "This save format is not supported (it may be encrypted or compressed by a script)"
        ) from exc


def load_save(path: str, names: Mapping[str, object]) -> JsonDict:
    """Decode an RGSS slot file into the editor payload."""
    try:
        st = os.stat(path)
        if st.st_size > MAX_BYTES:
            raise UnsupportedSaveFormat("This save file is too large to open")
        with open(path, "rb") as handle:
            data = handle.read(MAX_BYTES + 1)
    except UnsupportedSaveFormat:
        raise
    except OSError as exc:
        raise RpgmSaveError("The save file could not be read", "read_failed") from exc
    if len(data) > MAX_BYTES:
        raise UnsupportedSaveFormat("This save file is too large to open")
    fmt = format_for_name(os.path.basename(path))
    dumps = decode_save(data)
    raw, _nodes_by_id, editable_ids = project_dumps(dumps)
    return {
        "format": fmt,
        "size": st.st_size,
        "mtime_ns": str(st.st_mtime_ns),
        "view": build_view(dumps, fmt, names, editable_ids),
        "names": dict(names),
        "raw": raw,
    }


# --------------------------------------------------------------------- patches


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _plain_int(value: object) -> bool:
    if isinstance(value, bool):
        return False
    return isinstance(value, int) or (isinstance(value, float) and value.is_integer())


def _check_int(value: object, low: int, high: int, label: str) -> int:
    if not _plain_int(value):
        raise InvalidPatch(f"{label} must be a whole number")
    number = int(cast(int, value))
    if number < low or number > high:
        raise InvalidPatch(f"{label} must be from {low} to {high}")
    return number


def _check_id(value: object, label: str = "Id") -> int:
    return _check_int(value, 1, MAX_ID, label)


def _utf8_ivars() -> list[tuple[SymbolNode, MarshalNode]]:
    return [(SymbolNode(b"E"), BoolNode(True))]


def _set_int_ivar(obj: ObjectNode, name: bytes, value: int) -> None:
    for idx, (sym, existing) in enumerate(obj.attributes):
        if sym.name == name:
            if isinstance(existing, IntNode):
                existing.set_value(value)
            else:
                obj.attributes[idx] = (sym, IntNode(value))
            return
    raise InvalidPatch("That value does not exist in the save")


def _pad_nil(items: list[MarshalNode], ident: int) -> None:
    while len(items) <= ident:
        items.append(NilNode())


def _apply_set_node(
    patch: Mapping[str, object],
    nodes_by_id: Mapping[int, MarshalNode],
    editable_ids: set[int],
    gold_node: MarshalNode | None,
) -> None:
    raw_id = patch.get("node")
    if not isinstance(raw_id, int) or isinstance(raw_id, bool):
        raise InvalidPatch("A node id is required")
    target = nodes_by_id.get(raw_id)
    if target is None:
        raise InvalidPatch("That value does not exist in the save")
    if raw_id not in editable_ids:
        raise InvalidPatch("This value is read-only")
    value = patch.get("value")
    if isinstance(target, BoolNode):
        if not isinstance(value, bool):
            raise InvalidPatch("The new value must keep the type of the old one")
        target.value = value
        return
    if isinstance(target, IntNode):
        if target is gold_node:
            number = _check_int(value, 0, MAX_GOLD, "Gold")
        else:
            if not _plain_int(value):
                raise InvalidPatch("This value must be a whole number")
            number = int(cast(int, value))
            if abs(number) > MAX_VARIABLE:
                raise InvalidPatch("The number is too large")
        target.set_value(number)
        return
    if isinstance(target, FloatNode):
        if not _is_number(value):
            raise InvalidPatch("The new value must keep the type of the old one")
        number_f = float(cast(float, value))
        if not math.isfinite(number_f):
            raise InvalidPatch("The number is not valid")
        if abs(number_f) > MAX_VARIABLE:
            raise InvalidPatch("The number is too large")
        target.set_value(number_f)
        return
    if isinstance(target, StringNode):
        if not isinstance(value, str):
            raise InvalidPatch("The new value must keep the type of the old one")
        if len(value) > MAX_STRING:
            raise InvalidPatch("The text is too long")
        if not target.is_editable_utf8():
            raise InvalidPatch("This text cannot be edited")
        target.set_text(value)
        return
    raise InvalidPatch("This value is read-only")


def _apply_set_count(sections: _Sections, patch: Mapping[str, object]) -> None:
    kind = patch.get("kind")
    ivar_map = {"item": b"@items", "weapon": b"@weapons", "armor": b"@armors"}
    if not isinstance(kind, str) or kind not in ivar_map:
        raise InvalidPatch("Unknown inventory kind")
    ident = _check_id(patch.get("id"))
    count = _check_int(patch.get("count"), 0, MAX_COUNT, "Count")
    if sections.party is None:
        raise InvalidPatch("This save has no party")
    held = _unwrap_link(_raw_ivar(sections.party, ivar_map[kind]))
    if not isinstance(held, HashNode):
        raise InvalidPatch("This save's inventory has an unexpected layout")
    for idx, (k, v) in enumerate(held.pairs):
        if isinstance(k, IntNode) and k.value == ident:
            if count == 0:
                del held.pairs[idx]
            elif isinstance(v, IntNode):
                v.set_value(count)
            else:
                held.pairs[idx] = (k, IntNode(count))
            return
    if count > 0:
        held.pairs.append((IntNode(ident), IntNode(count)))


def _apply_set_switch(sections: _Sections, patch: Mapping[str, object]) -> None:
    ident = _check_id(patch.get("id"))
    value = patch.get("value")
    if not isinstance(value, bool):
        raise InvalidPatch("A switch is on or off")
    arr = _unwrap_link(_raw_ivar(sections.switches, b"@data"))
    if not isinstance(arr, ArrayNode):
        raise InvalidPatch("This save has no switches data")
    _pad_nil(arr.items, ident)
    existing = arr.items[ident]
    if isinstance(existing, BoolNode):
        existing.value = value
    else:
        arr.items[ident] = BoolNode(value)


def _apply_set_variable(
    sections: _Sections, fmt: str, patch: Mapping[str, object]
) -> None:
    ident = _check_id(patch.get("id"))
    value = patch.get("value")
    arr = _unwrap_link(_raw_ivar(sections.variables, b"@data"))
    if not isinstance(arr, ArrayNode):
        raise InvalidPatch("This save has no variables data")
    old = arr.items[ident] if ident < len(arr.items) else None
    is_nil = old is None or isinstance(old, NilNode)
    is_num = (isinstance(old, IntNode) and abs(old.value) <= MAX_SAFE_JS_INT) or (
        isinstance(old, FloatNode) and math.isfinite(old.value)
    )
    is_str = isinstance(old, StringNode) and old.is_editable_utf8()
    if not (is_nil or is_num or is_str):
        raise InvalidPatch("This variable holds a value that cannot be edited")

    if isinstance(value, str):
        if not is_nil and not is_str:
            raise InvalidPatch("This variable holds a number")
        if len(value) > MAX_STRING:
            raise InvalidPatch("The text is too long")
        _pad_nil(arr.items, ident)
        if isinstance(old, StringNode):
            old.set_text(value)
        else:
            arr.items[ident] = StringNode(
                value.encode("utf-8"),
                ivars=_utf8_ivars() if fmt == FORMAT_VXACE else None,
            )
        return

    if _is_number(value):
        if is_str:
            raise InvalidPatch("This variable holds text")
        number = float(cast(float, value))
        if not math.isfinite(number):
            raise InvalidPatch("The number is not valid")
        if abs(number) > MAX_VARIABLE:
            raise InvalidPatch("The number is too large")
        if isinstance(old, IntNode) and not _plain_int(number):
            # A game script may use the value as an index or a count, where a
            # decimal number would fail, so a whole number stays whole.
            raise InvalidPatch("This variable holds a whole number")
        _pad_nil(arr.items, ident)
        if _plain_int(number) and not isinstance(old, FloatNode):
            if isinstance(old, IntNode):
                old.set_value(int(number))
            else:
                arr.items[ident] = IntNode(int(number))
        elif isinstance(old, FloatNode):
            old.set_value(number)
        else:
            arr.items[ident] = FloatNode(number)
        return

    raise InvalidPatch("A variable holds a number or text")


def _apply_set_actor(
    sections: _Sections, fmt: str, patch: Mapping[str, object]
) -> None:
    ident = _check_id(patch.get("id"), "Actor")
    arr = _unwrap_link(_raw_ivar(sections.actors, b"@data"))
    if not isinstance(arr, ArrayNode):
        raise InvalidPatch("This save has no actors data")
    raw_actor = arr.items[ident] if ident < len(arr.items) else None
    actor = _expect_class(raw_actor, "Game_Actor")
    if actor is None:
        raise InvalidPatch("That actor is not in the save")

    field = patch.get("field")
    value = patch.get("value")

    if field == "name":
        if not isinstance(value, str) or not value.strip() or len(value) > MAX_NAME:
            raise InvalidPatch("The name must be text of 1 to 200 characters")
        name_node = _raw_ivar(actor, b"@name")
        if not isinstance(name_node, StringNode) or not name_node.is_editable_utf8():
            raise InvalidPatch("This actor's name cannot be edited")
        name_node.set_text(value)
        return

    if field == "level":
        level = _check_int(value, 1, MAX_LEVEL, "Level")
        _set_int_ivar(actor, b"@level", level)
        if fmt in (FORMAT_XP, FORMAT_VX):
            exp_list = _unwrap_link(_raw_ivar(actor, b"@exp_list"))
            if isinstance(exp_list, ArrayNode) and 0 <= level < len(exp_list.items):
                entry = exp_list.items[level]
                exp_node = _raw_ivar(actor, b"@exp")
                if isinstance(entry, IntNode) and isinstance(exp_node, IntNode):
                    exp_node.set_value(entry.value)
        return

    if field == "exp":
        exp = _check_int(value, 0, MAX_EXP, "Experience")
        if fmt in (FORMAT_XP, FORMAT_VX):
            exp_node = _raw_ivar(actor, b"@exp")
            if not isinstance(exp_node, IntNode):
                raise InvalidPatch("This actor has no experience data")
            exp_node.set_value(exp)
            return
        class_node = _raw_ivar(actor, b"@class_id")
        exp_map = _unwrap_link(_raw_ivar(actor, b"@exp"))
        if not isinstance(class_node, IntNode) or not isinstance(exp_map, HashNode):
            raise InvalidPatch("This actor has no experience data")
        class_id = class_node.value
        for idx, (k, v) in enumerate(exp_map.pairs):
            if isinstance(k, IntNode) and k.value == class_id:
                if isinstance(v, IntNode):
                    v.set_value(exp)
                else:
                    exp_map.pairs[idx] = (k, IntNode(exp))
                return
        exp_map.pairs.append((IntNode(class_id), IntNode(exp)))
        return

    if field == "hp":
        _set_int_ivar(actor, b"@hp", _check_int(value, 0, MAX_STAT, "HP"))
        return

    if field == "mp":
        ivar_name = b"@sp" if fmt == FORMAT_XP else b"@mp"
        label = "SP" if fmt == FORMAT_XP else "MP"
        _set_int_ivar(actor, ivar_name, _check_int(value, 0, MAX_STAT, label))
        return

    if field == "paramPlus":
        if fmt in (FORMAT_XP, FORMAT_VX):
            index = _check_int(patch.get("index"), 0, 5, "Parameter index")
            bonus = _check_int(value, -MAX_BONUS, MAX_BONUS, "Bonus")
            ivars = _XP_BONUS_IVARS if fmt == FORMAT_XP else _VX_BONUS_IVARS
            _set_int_ivar(actor, ivars[index], bonus)
            return
        index = _check_int(patch.get("index"), 0, 7, "Parameter index")
        bonus = _check_int(value, -MAX_BONUS, MAX_BONUS, "Bonus")
        plus_arr = _unwrap_link(_raw_ivar(actor, b"@param_plus"))
        if not isinstance(plus_arr, ArrayNode):
            raise InvalidPatch("This actor has no parameter bonuses")
        while len(plus_arr.items) <= index:
            plus_arr.items.append(IntNode(0))
        existing = plus_arr.items[index]
        if isinstance(existing, IntNode):
            existing.set_value(bonus)
        else:
            plus_arr.items[index] = IntNode(bonus)
        return

    raise InvalidPatch("Unknown actor field")


def apply_patches(
    dumps: list[MarshalNode],
    fmt: str,
    patches: Sequence[object],
    nodes_by_id: Mapping[int, MarshalNode],
    editable_ids: set[int],
) -> None:
    """Validate and apply `patches` in order, in place. Raises InvalidPatch."""
    if len(patches) > 20_000:
        raise InvalidPatch("Too many edits at once")
    sections = _extract_sections(dumps, fmt)
    gold_node = _raw_ivar(sections.party, b"@gold")
    for raw in patches:
        if not isinstance(raw, dict):
            raise InvalidPatch("An edit is not valid")
        patch = cast(JsonDict, raw)
        op = patch.get("op")
        if op == "set_node":
            _apply_set_node(patch, nodes_by_id, editable_ids, gold_node)
        elif op == "set_count":
            _apply_set_count(sections, patch)
        elif op == "set_switch":
            _apply_set_switch(sections, patch)
        elif op == "set_variable":
            _apply_set_variable(sections, fmt, patch)
        elif op == "set_actor":
            _apply_set_actor(sections, fmt, patch)
        else:
            raise InvalidPatch("Unknown edit")


def encode_save(dumps: list[MarshalNode]) -> bytes:
    try:
        return dump_all(dumps)
    except MarshalUnsupported as exc:
        raise RpgmSaveError(
            "The edited save could not be encoded, nothing was written"
        ) from exc


def write_save(
    path: str,
    patches: Sequence[object],
    size: int,
    mtime_ns: int,
    backup: Callable[[], str],
) -> str:
    """Apply `patches` to an RGSS slot file safely and return the backup name.

    Follows the same safety pipeline as `rpgm_saves.write_save`: reject
    symlinks, read and fingerprint, size/mtime check, decode and patch,
    back up, encode, decode and compare the tree, write a temp file and
    fsync, recheck the fingerprint, then atomic replace.
    """
    reject_symlink(path)
    data, fingerprint = read_fingerprint(path, max_bytes=MAX_BYTES)
    if fingerprint[0] != size or fingerprint[1] != mtime_ns:
        raise SaveChanged(CHANGED_MESSAGE)
    fmt = format_for_name(os.path.basename(path))
    dumps = decode_save(data)
    nodes_by_id, editable_ids = assign_ids(dumps)
    apply_patches(dumps, fmt, patches, nodes_by_id, editable_ids)
    backup_name = backup()
    encoded = encode_save(dumps)
    if not trees_equal(decode_save(encoded), dumps):
        raise RpgmSaveError("The edited save failed its check, nothing was written")
    atomic_replace_checked(path, encoded, fingerprint)
    return backup_name
