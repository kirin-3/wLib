# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import cast

import pytest

from core.ruby_marshal import (
    MARSHAL_HEADER,
    ArrayNode,
    BoolNode,
    ClassRefNode,
    ExtendedNode,
    FloatNode,
    HashNode,
    IntNode,
    LinkNode,
    MarshalUnsupported,
    NilNode,
    ObjectNode,
    RegexpNode,
    StringNode,
    StructNode,
    SymbolNode,
    UserClassNode,
    UserDefNode,
    UserMarshalNode,
    assign_ids,
    dump_all,
    load_all,
    project_dumps,
    trees_equal,
)

FIXTURES_ROOT = Path(__file__).parent / "fixtures" / "rgss"


def _one(payload: bytes):
    dumps = load_all(MARSHAL_HEADER + payload)
    assert len(dumps) == 1
    return dumps[0]


def test_primitives_and_fixnums():
    assert isinstance(_one(b"0"), NilNode)
    t = _one(b"T")
    assert isinstance(t, BoolNode) and t.value is True
    f = _one(b"F")
    assert isinstance(f, BoolNode) and f.value is False

    # Fixnums across all 1-byte and multi-byte representations
    cases = [
        (b"i\x00", 0),
        (b"i\x06", 1),
        (b"i\x7f", 122),
        (b"i\xfa", -1),
        (b"i\x80", -123),
        (b"i\x01\x7b", 123),
        (b"i\x01\xff", 255),
        (b"i\x02\x00\x01", 256),
        (b"i\x03\x01\x02\x03", 0x030201),
        (b"i\x04\xff\xff\xff\x3f", (1 << 30) - 1),
        (b"i\xff\x80", -128),
        (b"i\xff\x00", -256),
        (b"i\xfe\xff\xfe", -257),
        (b"i\xfc\x00\x00\x00\xc0", -(1 << 30)),
    ]
    for raw, expected in cases:
        node = _one(raw)
        assert isinstance(node, IntNode)
        assert node.value == expected


def test_bignums_and_floats():
    pos_big = _one(b"l+\x09\x01\x00\x00\x00\x00\x00\x00\x01")
    assert isinstance(pos_big, IntNode)
    assert pos_big.value == (1 << 56) + 1

    neg_big = _one(b"l-\x08\x02\x00\x00\x00\x00\x01")
    assert isinstance(neg_big, IntNode)
    assert neg_big.value == -((1 << 40) + 2)

    fl = _one(b"f\x081.5")
    assert isinstance(fl, FloatNode)
    assert fl.value == 1.5

    fl_old = _one(b"f\x0b1.25\x00\x00")
    assert isinstance(fl_old, FloatNode)
    assert fl_old.value == 1.25

    assert math.isinf(_one(b"f\x08inf").value)
    neg_inf = _one(b"f\x09-inf")
    assert isinstance(neg_inf, FloatNode) and math.isinf(neg_inf.value) and neg_inf.value < 0
    nan = _one(b"f\x08nan")
    assert isinstance(nan, FloatNode) and math.isnan(nan.value)


def test_strings_symbols_and_ivars():
    s = _one(b'"\x0ahello')
    assert isinstance(s, StringNode)
    assert s.data == b"hello"
    assert s.ivars is None

    utf8_s = _one(b'I"\x07hi\x06:\x06ET')
    assert isinstance(utf8_s, StringNode)
    assert utf8_s.data == b"hi"
    assert utf8_s.ivars is not None
    assert len(utf8_s.ivars) == 1
    assert utf8_s.ivars[0][0].text == "E"
    assert isinstance(utf8_s.ivars[0][1], BoolNode) and utf8_s.ivars[0][1].value is True

    # Symbols and symlink
    arr = _one(b"[\x07:\x08foo;\x00")
    assert isinstance(arr, ArrayNode)
    assert len(arr.items) == 2
    assert isinstance(arr.items[0], SymbolNode) and arr.items[0].text == "foo"
    assert isinstance(arr.items[1], SymbolNode) and arr.items[1].text == "foo"
    assert arr.items[0] is not arr.items[1]

    # UTF-8 symbol with I wrapper
    utf8_sym_arr = _one(b"[\x07I:\x0e\xe6\x97\xa5\xe6\x9c\xac\xe8\xaa\x9e\x06:\x06ET;\x00")
    assert isinstance(utf8_sym_arr, ArrayNode)
    assert isinstance(utf8_sym_arr.items[0], SymbolNode)
    assert utf8_sym_arr.items[0].text == "日本語"
    assert isinstance(utf8_sym_arr.items[1], SymbolNode)
    assert utf8_sym_arr.items[1].text == "日本語"


def test_containers_objects_structs_and_wrappers():
    # Hash without default
    h = _one(b"{\x06:\x06ai\x0a")
    assert isinstance(h, HashNode)
    assert h.default is None
    assert len(h.pairs) == 1
    assert isinstance(h.pairs[0][0], SymbolNode) and h.pairs[0][0].text == "a"
    assert isinstance(h.pairs[0][1], IntNode) and h.pairs[0][1].value == 5

    # Hash with default
    hd = _one(b"}\x06:\x06ai\x06i\x00")
    assert isinstance(hd, HashNode)
    assert isinstance(hd.default, IntNode) and hd.default.value == 0

    # Struct with cycle
    st = _one(b"S:\x0dMyStruct\x07:\x06ai\x0f:\x06b@\x00")
    assert isinstance(st, StructNode)
    assert st.class_name.text == "MyStruct"
    assert len(st.members) == 2
    assert isinstance(st.members[1][1], LinkNode)
    assert st.members[1][1].target is st

    # Object
    obj = _one(b"o:\x08Foo\x06:\x07@xi\x0a")
    assert isinstance(obj, ObjectNode)
    assert obj.class_name.text == "Foo"
    assert obj.attributes[0][0].text == "@x"
    assert isinstance(obj.attributes[0][1], IntNode) and obj.attributes[0][1].value == 5

    # UserDef (u) and I+u (Time-style object numbering)
    u = _one(b"u:\x09Tone\x08abc")
    assert isinstance(u, UserDefNode)
    assert u.class_name.text == "Tone"
    assert u.data == b"abc"

    # Array holding I+u (with string ivar) followed by link to u (index 2, not 1!)
    iu_pair = _one(b'[\x07Iu:\x09Time\x08abc\x06:\x09zoneI"\x08UTC\x06:\x06EF@\x07')
    assert isinstance(iu_pair, ArrayNode)
    assert isinstance(iu_pair.items[0], UserDefNode)
    assert isinstance(iu_pair.items[1], LinkNode)
    assert iu_pair.items[1].target is iu_pair.items[0]

    # UserMarshal (U) with link
    um_pair = _one(b"[\x07U:\x08MyU[\x07i\x06i\x07@\x06")
    assert isinstance(um_pair, ArrayNode)
    assert isinstance(um_pair.items[0], UserMarshalNode)
    assert um_pair.items[0].class_name.text == "MyU"
    assert isinstance(um_pair.items[1], LinkNode)
    assert um_pair.items[1].target is um_pair.items[0]

    # Extended (e) and UserClass (C) and I ivars with self-link
    iec = _one(b"[\x07Ie:\x09ModBe:\x09ModAC:\x0aMyArr[\x06i\x06\x06:\x07@k@\x06@\x06")
    assert isinstance(iec, ArrayNode)
    ext = iec.items[0]
    assert isinstance(ext, ExtendedNode)
    assert ext.module.text == "ModB"
    assert isinstance(ext.inner, ExtendedNode)
    assert ext.inner.module.text == "ModA"
    assert isinstance(ext.inner.inner, UserClassNode)
    assert ext.inner.inner.class_name.text == "MyArr"
    inner_arr = ext.inner.inner.inner
    assert isinstance(inner_arr, ArrayNode)
    assert ext.ivars is not None
    assert isinstance(ext.ivars[0][1], LinkNode)
    assert ext.ivars[0][1].target is inner_arr
    assert isinstance(iec.items[1], LinkNode)
    assert iec.items[1].target is inner_arr

    # Regexp
    rx = _one(b"/\x08abc\x05")
    assert isinstance(rx, RegexpNode)
    assert rx.pattern == b"abc"
    assert rx.options == 5

    # Class / Module / Old module refs
    for kind in ("c", "m", "M"):
        ref = _one(kind.encode() + b"\x0bString")
        assert isinstance(ref, ClassRefNode)
        assert ref.kind == kind
        assert ref.name == b"String"


def test_limits_and_malformed_inputs():
    # Empty
    with pytest.raises(MarshalUnsupported):
        load_all(b"")

    # Bad version
    with pytest.raises(MarshalUnsupported):
        load_all(b"\x04\x070")
    with pytest.raises(MarshalUnsupported):
        load_all(b"\x05\x080")

    # Unknown tag
    with pytest.raises(MarshalUnsupported):
        load_all(MARSHAL_HEADER + b"!")

    # `d` (TYPE_DATA) explicitly unsupported
    with pytest.raises(MarshalUnsupported):
        load_all(MARSHAL_HEADER + b"d:\x06X0")

    # Trailing bytes after a valid dump
    with pytest.raises(MarshalUnsupported):
        load_all(MARSHAL_HEADER + b"0\x00")
    with pytest.raises(MarshalUnsupported):
        load_all(MARSHAL_HEADER + b"0garbage")

    # Cut-off reads
    for truncated in (
        b"\x04",
        MARSHAL_HEADER,
        MARSHAL_HEADER + b"i",
        MARSHAL_HEADER + b"i\x02\x01",
        MARSHAL_HEADER + b'"\x0ahel',
        MARSHAL_HEADER + b"[\x07i\x06",
        MARSHAL_HEADER + b"/\x06a",
        MARSHAL_HEADER + b"l+\x06\x01",
    ):
        with pytest.raises(MarshalUnsupported):
            load_all(truncated)

    # Oversized length field (claims 1_000_000 bytes / items)
    with pytest.raises(MarshalUnsupported):
        load_all(MARSHAL_HEADER + b'"\x03\x40\x42\x0f')
    with pytest.raises(MarshalUnsupported):
        load_all(MARSHAL_HEADER + b"[\x03\x40\x42\x0f")
    with pytest.raises(MarshalUnsupported):
        load_all(MARSHAL_HEADER + b"l+\x03\x40\x42\x0f")

    # Invalid symbol/object links
    with pytest.raises(MarshalUnsupported):
        load_all(MARSHAL_HEADER + b";\x00")
    with pytest.raises(MarshalUnsupported):
        load_all(MARSHAL_HEADER + b"@\x00")

    # Depth limit exceeded
    deep = MARSHAL_HEADER + (b"[\x06" * 300) + b"0"
    with pytest.raises(MarshalUnsupported):
        load_all(deep)

    # 32 MiB size cap
    with pytest.raises(MarshalUnsupported):
        load_all(MARSHAL_HEADER + b"0", max_bytes=2)
    with pytest.raises(MarshalUnsupported):
        load_all(b"\x00" * (32 * 1024 * 1024 + 1))

    # Node limit
    many = MARSHAL_HEADER + b"[\x0f" + (b"0" * 10)
    with pytest.raises(MarshalUnsupported):
        load_all(many, max_nodes=5)


def test_fixtures_decode_cleanly():
    files = sorted(FIXTURES_ROOT.rglob("*.r*data*"))
    assert len(files) == 18
    for path in files:
        raw = path.read_bytes()
        dumps = load_all(raw)
        assert len(dumps) >= 1
        assert dump_all(dumps) == raw
        assert trees_equal(dumps, load_all(dump_all(dumps)))
    assert len(load_all((FIXTURES_ROOT / "xp" / "Save1.rxdata").read_bytes())) == 12
    assert len(load_all((FIXTURES_ROOT / "vx" / "Save1.rvdata").read_bytes())) == 14
    assert len(load_all((FIXTURES_ROOT / "vxace" / "Save01.rvdata2").read_bytes())) == 2


def test_roundtrip_all_tags_byte_identical():
    samples = [
        b"0",
        b"T",
        b"F",
        b"i\x00",
        b"i\x05",  # non-canonical zero preserved via raw bytes
        b"i\x7f",
        b"i\x80",
        b"i\x04\xff\xff\xff\x3f",
        b"i\xfc\x00\x00\x00\xc0",
        b"l+\x09\x01\x00\x00\x00\x00\x00\x00\x01",
        b"l-\x08\x02\x00\x00\x00\x00\x01",
        b"f\x091.50",  # preserves trailing zero
        b"f\x0b1.25\x00\x00",
        b"f\x08inf",
        b"f\x09-inf",
        b"f\x08nan",
        b'I"\x07hi\x06:\x06ET',
        b"[\x07I:\x0e\xe6\x97\xa5\xe6\x9c\xac\xe8\xaa\x9e\x06:\x06ET;\x00",
        b"{\x06:\x06ai\x0a",
        b"}\x06:\x06ai\x06i\x00",
        b"S:\x0dMyStruct\x07:\x06ai\x0f:\x06b@\x00",
        b"o:\x08Foo\x06:\x07@xi\x0a",
        b'[\x07Iu:\x09Time\x08abc\x06:\x09zoneI"\x08UTC\x06:\x06EF@\x07',
        b"[\x07U:\x08MyU[\x07i\x06i\x07@\x06",
        b"[\x07Ie:\x09ModBe:\x09ModAC:\x0aMyArr[\x06i\x06\x06:\x07@k@\x06@\x06",
        b"/\x08abc\x05",
        b"c\x0bString",
        b"m\x0bKernel",
        b"M\x0bObject",
    ]
    for payload in samples:
        raw = MARSHAL_HEADER + payload
        assert dump_all(load_all(raw)) == raw


def test_link_renumbering_when_fixnum_becomes_bignum():
    # Array [fixnum(1), "shared", link(@1)]
    # Object table initially: 0 = Array, 1 = "shared"
    raw = MARSHAL_HEADER + b'[\x08i\x06"\x0bshared@\x06'
    dumps = load_all(raw)
    arr = dumps[0]
    assert isinstance(arr, ArrayNode)
    num, shared, link = arr.items
    assert isinstance(num, IntNode)
    assert isinstance(shared, StringNode)
    assert isinstance(link, LinkNode)
    assert link.target is shared

    # Turn the fixnum into a bignum: it now takes object slot 1, pushing
    # "shared" to slot 2, so the link MUST renumber to @2 (`@\x07`).
    big = (1 << 70) + 42
    num.set_value(big)
    encoded = dump_all(dumps)
    reloaded = load_all(encoded)
    assert trees_equal(dumps, reloaded)
    arr2 = reloaded[0]
    assert isinstance(arr2, ArrayNode)
    assert isinstance(arr2.items[0], IntNode) and arr2.items[0].value == big
    assert isinstance(arr2.items[1], StringNode) and arr2.items[1].data == b"shared"
    assert isinstance(arr2.items[2], LinkNode) and arr2.items[2].target is arr2.items[1]

    # And turning the bignum back into a fixnum renumbers the link back to @1.
    assert isinstance(arr2.items[0], IntNode)
    arr2.items[0].set_value(1)
    assert dump_all(reloaded) == raw


def test_linked_bignum_edited_into_fixnum_range_stays_linked():
    # Array [bignum, link(@1)]: object table 0 = Array, 1 = bignum.
    big = (1 << 40) + 7
    arr = ArrayNode()
    shared = IntNode(big)
    arr.items = [shared, LinkNode(shared)]
    raw = dump_all([arr])
    dumps = load_all(raw)
    first = dumps[0]
    assert isinstance(first, ArrayNode)
    number = first.items[0]
    assert isinstance(number, IntNode)
    assert isinstance(first.items[1], LinkNode) and first.items[1].target is number

    # A small value would be a fixnum, which has no object-table entry, so the
    # writer keeps it as a bignum and the link still points to it.
    number.set_value(5)
    reloaded = load_all(dump_all(dumps))
    assert trees_equal(dumps, reloaded)
    again = reloaded[0]
    assert isinstance(again, ArrayNode)
    value = again.items[0]
    assert isinstance(value, IntNode) and value.value == 5
    assert isinstance(again.items[1], LinkNode) and again.items[1].target is value


def test_edited_scalars_encode_cleanly():
    dumps = load_all(MARSHAL_HEADER + b"[\x09i\x06f\x081.5f\x082.0f\x083.0")
    arr = dumps[0]
    assert isinstance(arr, ArrayNode)
    assert isinstance(arr.items[0], IntNode)
    arr.items[0].set_value(-1000)
    assert isinstance(arr.items[1], FloatNode)
    arr.items[1].set_value(math.inf)
    assert isinstance(arr.items[2], FloatNode)
    arr.items[2].set_value(-math.inf)
    assert isinstance(arr.items[3], FloatNode)
    arr.items[3].set_value(math.nan)

    reloaded = load_all(dump_all(dumps))
    assert trees_equal(dumps, reloaded)
    arr2 = reloaded[0]
    assert isinstance(arr2, ArrayNode)
    assert isinstance(arr2.items[0], IntNode) and arr2.items[0].value == -1000
    assert isinstance(arr2.items[1], FloatNode) and math.isinf(arr2.items[1].value) and arr2.items[1].value > 0
    assert isinstance(arr2.items[2], FloatNode) and math.isinf(arr2.items[2].value) and arr2.items[2].value < 0
    assert isinstance(arr2.items[3], FloatNode) and math.isnan(arr2.items[3].value)


def test_projection_id_stability_cycle_and_json_serializable():
    # Every fixture projects deterministically and serializes cleanly without NaN/Inf.
    for path in sorted(FIXTURES_ROOT.rglob("*.r*data*")):
        raw = path.read_bytes()
        proj1, idx1, edit1 = project_dumps(load_all(raw))
        proj2, idx2, edit2 = project_dumps(load_all(raw))
        assert proj1 == proj2
        assert set(idx1.keys()) == set(idx2.keys())
        assert edit1 == edit2
        serialized = json.dumps(proj1, allow_nan=False)
        assert isinstance(serialized, str) and serialized

    # Cycle projects without recursion
    st_raw = MARSHAL_HEADER + b"S:\x0dMyStruct\x07:\x06ai\x0f:\x06b@\x00"
    proj, _idx, _edit = project_dumps(load_all(st_raw))
    root = proj[0]
    assert root["t"] == "struct"
    assert root["id"] == 0
    ivars = cast(list[list[object]], root["ivars"])
    assert ivars[0][0] == "a"
    a_node = cast(dict[str, object], ivars[0][1])
    assert a_node["t"] == "int" and a_node["v"] == 10 and a_node.get("editable") is True
    b_node = cast(dict[str, object], ivars[1][1])
    assert b_node["t"] == "link" and b_node["to"] == 0 and "editable" not in b_node


def test_projection_editability_rules():
    # Hash with string key (must NOT be editable) and UTF-8 string value (MUST be editable)
    h_raw = MARSHAL_HEADER + b'{\x06"\x08key"\x08val'
    proj, _idx, editable_ids = project_dumps(load_all(h_raw))
    pairs = cast(list[list[dict[str, object]]], proj[0]["pairs"])
    key_node, val_node = pairs[0]
    assert key_node["t"] == "str" and "editable" not in key_node
    assert key_node["id"] not in editable_ids
    assert val_node["t"] == "str" and val_node.get("editable") is True
    assert val_node["id"] in editable_ids

    # Symbols, links, u blobs, non-UTF-8 strings, huge bignums and non-finite floats are read-only
    samples = load_all(
        MARSHAL_HEADER
        + b'[\x0c:\x08symu:\x09Tone\x08abc"\x07\xff\xfel+\x0a\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01f\x08nan@\x060'
    )
    proj2, nodes, editable2 = project_dumps(samples)
    items = cast(list[dict[str, object]], proj2[0]["items"])
    # sym, userdef, binary str, bignum > 2^53, nan float, link, nil -> none editable
    for item in items:
        assert "editable" not in item, f"Expected read-only: {item}"
        assert item["id"] not in editable2
    # u blob has class and size only
    assert items[1] == {"id": items[1]["id"], "t": "userdef", "class": "Tone", "size": 3}
    # binary str uses replacement characters
    assert "\ufffd" in cast(str, items[2]["v"])
    # bignum serialized as string so JS never loses precision
    assert isinstance(items[3]["v"], str)
    # nan serialized as string so JSON is valid
    assert items[4]["v"] == "nan"
    # link points to Tone userdef
    assert items[5]["t"] == "link" and items[5]["to"] == items[1]["id"]
    assert len(nodes) > 0

    # assign_ids alone matches project_dumps
    ids_only, edit_only = assign_ids(load_all(h_raw))
    assert edit_only == editable_ids
    assert set(ids_only.keys()) == set(_idx.keys())
