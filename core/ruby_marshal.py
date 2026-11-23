# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure-Python Ruby Marshal 4.8 reader and writer for untrusted RGSS data.

Decodes Ruby Marshal streams strictly into plain data nodes without executing
code, importing classes, or invoking Ruby hooks (`_load`, `marshal_load`).
Unchanged nodes retain enough wire metadata to round-trip byte-identically.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

MARSHAL_MAJOR = 4
MARSHAL_MINOR = 8
MARSHAL_HEADER = bytes((MARSHAL_MAJOR, MARSHAL_MINOR))

MAX_BYTES = 32 * 1024 * 1024
MAX_DEPTH = 256
MAX_NODES = 4_000_000

FIXNUM_MIN = -(1 << 30)
FIXNUM_MAX = (1 << 30) - 1
MAX_SAFE_JS_INT = (1 << 53) - 1


class MarshalUnsupported(Exception):
    """Raised when input is not supported, valid, or safe Ruby Marshal 4.8."""


# ------------------------------------------------------------------ node model


@dataclass(eq=False)
class MarshalNode:
    """Base node for decoded Ruby Marshal 4.8 values."""

    node_id: int | None = field(default=None, init=False, repr=False)
    ivars: list[tuple[SymbolNode, MarshalNode]] | None = field(
        default=None, kw_only=True
    )


@dataclass(eq=False)
class NilNode(MarshalNode):
    pass


@dataclass(eq=False)
class BoolNode(MarshalNode):
    value: bool


@dataclass(eq=False)
class IntNode(MarshalNode):
    value: int
    raw_tag: bytes | None = field(default=None, kw_only=True, repr=False)
    raw_bytes: bytes | None = field(default=None, kw_only=True, repr=False)

    def set_value(self, value: int) -> None:
        self.value = value
        self.raw_tag = None
        self.raw_bytes = None


@dataclass(eq=False)
class FloatNode(MarshalNode):
    value: float
    raw_bytes: bytes | None = field(default=None, kw_only=True, repr=False)

    def set_value(self, value: float) -> None:
        self.value = value
        self.raw_bytes = None


@dataclass(eq=False)
class StringNode(MarshalNode):
    data: bytes

    def decode_utf8(self) -> str | None:
        try:
            return self.data.decode("utf-8")
        except UnicodeDecodeError:
            return None

    def edit_codec(self) -> str | None:
        """The Python codec for edits that fits the string's encoding marker.

        `:E => true` is UTF-8 and `:E => false` is US-ASCII. A string without
        a marker (all XP/VX strings, and binary ones) is edited as UTF-8.
        A named `:encoding` (for example Shift_JIS) returns None: wLib cannot
        check that new bytes are valid in it, so the string is read-only.
        """
        for key, value in self.ivars or ():
            if key.name == b"E":
                if isinstance(value, BoolNode):
                    return "utf-8" if value.value else "ascii"
                return None
            if key.name == b"encoding":
                return None
        return "utf-8"

    def encode_text(self, text: str) -> bytes | None:
        """`text` in the string's own encoding, or None if it cannot hold it."""
        codec = self.edit_codec()
        if codec is None:
            return None
        try:
            return text.encode(codec)
        except UnicodeEncodeError:
            return None

    def is_editable_utf8(self) -> bool:
        codec = self.edit_codec()
        if codec is None:
            return False
        try:
            _ = self.data.decode(codec)
        except UnicodeDecodeError:
            return False
        return True

    def display_text(self) -> str:
        return self.data.decode("utf-8", errors="replace")

    def set_text(self, text: str) -> None:
        """Replace the text; raises ValueError if the encoding cannot hold it."""
        encoded = self.encode_text(text)
        if encoded is None:
            raise ValueError("The text does not fit the string's encoding")
        self.data = encoded


@dataclass(eq=False)
class SymbolNode(MarshalNode):
    name: bytes

    @property
    def text(self) -> str:
        return self.name.decode("utf-8", errors="replace")


@dataclass(eq=False)
class ArrayNode(MarshalNode):
    items: list[MarshalNode] = field(default_factory=list)


@dataclass(eq=False)
class HashNode(MarshalNode):
    pairs: list[tuple[MarshalNode, MarshalNode]] = field(default_factory=list)
    default: MarshalNode | None = None


@dataclass(eq=False)
class ObjectNode(MarshalNode):
    class_name: SymbolNode
    attributes: list[tuple[SymbolNode, MarshalNode]] = field(default_factory=list)


@dataclass(eq=False)
class StructNode(MarshalNode):
    class_name: SymbolNode
    members: list[tuple[SymbolNode, MarshalNode]] = field(default_factory=list)


@dataclass(eq=False)
class UserDefNode(MarshalNode):
    class_name: SymbolNode
    data: bytes


@dataclass(eq=False)
class UserMarshalNode(MarshalNode):
    class_name: SymbolNode
    value: MarshalNode


@dataclass(eq=False)
class ExtendedNode(MarshalNode):
    module: SymbolNode
    inner: MarshalNode


@dataclass(eq=False)
class UserClassNode(MarshalNode):
    class_name: SymbolNode
    inner: MarshalNode


@dataclass(eq=False)
class RegexpNode(MarshalNode):
    pattern: bytes
    options: int


@dataclass(eq=False)
class ClassRefNode(MarshalNode):
    kind: str  # "c", "m", or "M"
    name: bytes


@dataclass(eq=False)
class LinkNode(MarshalNode):
    target: MarshalNode


# ---------------------------------------------------------------------- reader


def _parse_ruby_float(raw: bytes) -> float:
    """Parse Ruby's Marshal float representation (including old NUL-padded form)."""
    text_bytes = raw.split(b"\x00", 1)[0]
    try:
        text = text_bytes.decode("ascii")
    except UnicodeDecodeError as exc:
        raise MarshalUnsupported("Invalid float encoding") from exc
    if text == "inf":
        return math.inf
    if text == "-inf":
        return -math.inf
    if text == "nan":
        return math.nan
    try:
        return float(text)
    except ValueError as exc:
        raise MarshalUnsupported(f"Invalid float literal: {text!r}") from exc


class _Reader:
    def __init__(
        self,
        data: bytes,
        offset: int,
        *,
        max_depth: int = MAX_DEPTH,
        max_nodes: int = MAX_NODES,
    ) -> None:
        self._data: bytes = data
        self._len: int = len(data)
        self.pos: int = offset
        self._max_depth: int = max_depth
        self._max_nodes: int = max_nodes
        self._nodes_count: int = 0
        self._symbols: list[SymbolNode] = []
        self._objects: list[MarshalNode] = []

    @property
    def nodes_count(self) -> int:
        return self._nodes_count

    def _remaining(self) -> int:
        return self._len - self.pos

    def _count_node(self) -> None:
        self._nodes_count += 1
        if self._nodes_count > self._max_nodes:
            raise MarshalUnsupported("Marshal data exceeds the node limit")

    def _read_byte(self) -> int:
        if self.pos >= self._len:
            raise MarshalUnsupported("Unexpected end of Marshal data")
        value = self._data[self.pos]
        self.pos += 1
        return value

    def _read_bytes(self, length: int) -> bytes:
        if length < 0 or length > self._remaining():
            raise MarshalUnsupported("Marshal length exceeds remaining data")
        start = self.pos
        self.pos = start + length
        return self._data[start : self.pos]

    def _read_long_with_raw(self) -> tuple[int, bytes]:
        start = self.pos
        raw_c = self._read_byte()
        c = raw_c - 256 if raw_c >= 128 else raw_c
        if c == 0:
            value = 0
        elif c > 4:
            value = c - 5
        elif c < -4:
            value = c + 5
        elif c > 0:
            body = self._read_bytes(c)
            value = int.from_bytes(body, "little", signed=False)
        else:
            count = -c
            body = self._read_bytes(count)
            value = int.from_bytes(body + b"\xff", "little", signed=True)
        return value, self._data[start : self.pos]

    def _read_long(self) -> int:
        value, _ = self._read_long_with_raw()
        return value

    def _read_length(self) -> int:
        length = self._read_long()
        if length < 0 or length > self._remaining():
            raise MarshalUnsupported("Marshal length exceeds remaining data")
        return length

    def _read_raw_string(self) -> bytes:
        length = self._read_length()
        return self._read_bytes(length)

    def _read_ivars(self, depth: int) -> list[tuple[SymbolNode, MarshalNode]]:
        count = self._read_length()
        ivars: list[tuple[SymbolNode, MarshalNode]] = []
        for _ in range(count):
            name = self._read_symbol(depth + 1)
            val = self._read_object(depth + 1)
            ivars.append((name, val))
        return ivars

    def _read_symbol(self, depth: int) -> SymbolNode:
        if depth > self._max_depth:
            raise MarshalUnsupported("Marshal nesting limit exceeded")
        tag = self._read_byte()
        if tag == ord(":"):
            self._count_node()
            sym = SymbolNode(self._read_raw_string())
            self._symbols.append(sym)
            return sym
        if tag == ord(";"):
            self._count_node()
            idx = self._read_long()
            if idx < 0 or idx >= len(self._symbols):
                raise MarshalUnsupported("Invalid Marshal symbol link")
            orig = self._symbols[idx]
            return SymbolNode(orig.name, ivars=orig.ivars)
        if tag == ord("I"):
            next_tag = self._read_byte()
            if next_tag != ord(":"):
                raise MarshalUnsupported("Expected symbol after ivar marker")
            self._count_node()
            sym = SymbolNode(self._read_raw_string())
            self._symbols.append(sym)
            sym.ivars = self._read_ivars(depth + 1)
            return sym
        raise MarshalUnsupported(f"Expected Marshal symbol, got tag 0x{tag:02x}")

    def _read_object(
        self,
        depth: int,
        *,
        pending_ivar: list[list[tuple[SymbolNode, MarshalNode]] | None] | None = None,
    ) -> MarshalNode:
        if depth > self._max_depth:
            raise MarshalUnsupported("Marshal nesting limit exceeded")
        tag = self._read_byte()

        if tag == ord("0"):
            self._count_node()
            return NilNode()
        if tag == ord("T"):
            self._count_node()
            return BoolNode(True)
        if tag == ord("F"):
            self._count_node()
            return BoolNode(False)
        if tag == ord("i"):
            self._count_node()
            val, raw = self._read_long_with_raw()
            return IntNode(val, raw_tag=b"i", raw_bytes=raw)
        if tag == ord("l"):
            self._count_node()
            start = self.pos
            sign_byte = self._read_byte()
            if sign_byte not in (ord("+"), ord("-")):
                raise MarshalUnsupported("Invalid bignum sign")
            word_count = self._read_long()
            if word_count < 0 or word_count * 2 > self._remaining():
                raise MarshalUnsupported("Bignum length exceeds remaining data")
            mag_bytes = self._read_bytes(word_count * 2)
            mag = int.from_bytes(mag_bytes, "little", signed=False)
            val = mag if sign_byte == ord("+") else -mag
            node = IntNode(val, raw_tag=b"l", raw_bytes=self._data[start : self.pos])
            self._objects.append(node)
            return node
        if tag == ord("f"):
            self._count_node()
            raw = self._read_raw_string()
            val = _parse_ruby_float(raw)
            node = FloatNode(val, raw_bytes=raw)
            self._objects.append(node)
            return node
        if tag == ord('"'):
            self._count_node()
            node = StringNode(self._read_raw_string())
            self._objects.append(node)
            return node
        if tag == ord(":"):
            self._count_node()
            sym = SymbolNode(self._read_raw_string())
            self._symbols.append(sym)
            return sym
        if tag == ord(";"):
            self._count_node()
            idx = self._read_long()
            if idx < 0 or idx >= len(self._symbols):
                raise MarshalUnsupported("Invalid Marshal symbol link")
            orig = self._symbols[idx]
            return SymbolNode(orig.name, ivars=orig.ivars)
        if tag == ord("@"):
            self._count_node()
            idx = self._read_long()
            if idx < 0 or idx >= len(self._objects):
                raise MarshalUnsupported("Invalid Marshal object link")
            return LinkNode(self._objects[idx])
        if tag == ord("/"):
            self._count_node()
            pattern = self._read_raw_string()
            options = self._read_byte()
            node = RegexpNode(pattern, options)
            self._objects.append(node)
            return node
        if tag == ord("["):
            self._count_node()
            count = self._read_length()
            node = ArrayNode()
            self._objects.append(node)
            for _ in range(count):
                node.items.append(self._read_object(depth + 1))
            return node
        if tag == ord("{") or tag == ord("}"):
            self._count_node()
            count = self._read_length()
            node = HashNode()
            self._objects.append(node)
            for _ in range(count):
                key = self._read_object(depth + 1)
                val = self._read_object(depth + 1)
                node.pairs.append((key, val))
            if tag == ord("}"):
                node.default = self._read_object(depth + 1)
            return node
        if tag == ord("S"):
            self._count_node()
            class_name = self._read_symbol(depth + 1)
            count = self._read_length()
            node = StructNode(class_name)
            self._objects.append(node)
            for _ in range(count):
                member = self._read_symbol(depth + 1)
                val = self._read_object(depth + 1)
                node.members.append((member, val))
            return node
        if tag == ord("o"):
            self._count_node()
            class_name = self._read_symbol(depth + 1)
            node = ObjectNode(class_name)
            self._objects.append(node)
            count = self._read_length()
            for _ in range(count):
                ivar_name = self._read_symbol(depth + 1)
                val = self._read_object(depth + 1)
                node.attributes.append((ivar_name, val))
            return node
        if tag == ord("u"):
            self._count_node()
            class_name = self._read_symbol(depth + 1)
            raw = self._read_raw_string()
            node = UserDefNode(class_name, raw)
            # In CRuby marshal.c, TYPE_USERDEF wrapped in `I` reads the ivars
            # onto the raw data string BEFORE registering the loaded object in
            # the object table (so e.g. Time's `:zone` string gets an earlier
            # object id than the Time object itself).
            if pending_ivar is not None:
                pending_ivar[0] = self._read_ivars(depth + 1)
            self._objects.append(node)
            return node
        if tag == ord("U"):
            self._count_node()
            class_name = self._read_symbol(depth + 1)
            # Register before reading the inner dumped representation so
            # cycles inside marshal_dump resolve to this object.
            node = UserMarshalNode(class_name, NilNode())
            self._objects.append(node)
            node.value = self._read_object(depth + 1)
            return node
        if tag == ord("e"):
            self._count_node()
            module = self._read_symbol(depth + 1)
            inner = self._read_object(depth + 1, pending_ivar=pending_ivar)
            return ExtendedNode(module, inner)
        if tag == ord("C"):
            self._count_node()
            class_name = self._read_symbol(depth + 1)
            inner = self._read_object(depth + 1, pending_ivar=pending_ivar)
            return UserClassNode(class_name, inner)
        if tag == ord("I"):
            consumed: list[list[tuple[SymbolNode, MarshalNode]] | None] = [None]
            inner = self._read_object(depth + 1, pending_ivar=consumed)
            if inner.ivars is not None:
                raise MarshalUnsupported("Duplicate ivar wrapper on Marshal object")
            if consumed[0] is not None:
                inner.ivars = consumed[0]
            else:
                inner.ivars = self._read_ivars(depth + 1)
            return inner
        if tag in (ord("c"), ord("m"), ord("M")):
            self._count_node()
            name = self._read_raw_string()
            node = ClassRefNode(chr(tag), name)
            self._objects.append(node)
            return node
        if tag == ord("d"):
            raise MarshalUnsupported("Marshal data (d) objects are not supported")
        raise MarshalUnsupported(f"Unsupported Marshal type tag 0x{tag:02x}")

    def read_dump(self) -> MarshalNode:
        if self._remaining() < 2:
            raise MarshalUnsupported("Unexpected end of Marshal header")
        major = self._read_byte()
        minor = self._read_byte()
        if major != MARSHAL_MAJOR or minor != MARSHAL_MINOR:
            raise MarshalUnsupported(
                f"Unsupported Marshal version {major}.{minor} (expected 4.8)"
            )
        return self._read_object(0)


def load_all(
    data: bytes,
    *,
    max_bytes: int = MAX_BYTES,
    max_depth: int = MAX_DEPTH,
    max_nodes: int = MAX_NODES,
) -> list[MarshalNode]:
    """Decode one or more back-to-back Ruby Marshal 4.8 dumps from `data`."""
    if not data:
        raise MarshalUnsupported("Empty Marshal data")
    if len(data) > max_bytes:
        raise MarshalUnsupported(
            f"Marshal file exceeds the {max_bytes // (1024 * 1024)} MiB size limit"
        )
    dumps: list[MarshalNode] = []
    offset = 0
    total_nodes = 0
    length = len(data)
    while offset < length:
        reader = _Reader(
            data,
            offset,
            max_depth=max_depth,
            max_nodes=max_nodes - total_nodes,
        )
        dumps.append(reader.read_dump())
        total_nodes += reader.nodes_count
        offset = reader.pos
    return dumps


def load_prefix(
    data: bytes,
    count: int,
    *,
    max_bytes: int = MAX_BYTES,
    max_depth: int = MAX_DEPTH,
    max_nodes: int = MAX_NODES,
) -> list[MarshalNode]:
    """Decode up to the first `count` Ruby Marshal 4.8 dumps from `data`."""
    if not data or count <= 0:
        raise MarshalUnsupported("Empty Marshal data")
    if len(data) > max_bytes:
        raise MarshalUnsupported(
            f"Marshal file exceeds the {max_bytes // (1024 * 1024)} MiB size limit"
        )
    dumps: list[MarshalNode] = []
    offset = 0
    total_nodes = 0
    length = len(data)
    while offset < length and len(dumps) < count:
        reader = _Reader(
            data,
            offset,
            max_depth=max_depth,
            max_nodes=max_nodes - total_nodes,
        )
        dumps.append(reader.read_dump())
        total_nodes += reader.nodes_count
        offset = reader.pos
    return dumps


# ---------------------------------------------------------------------- writer


def _format_ruby_float(value: float) -> bytes:
    if math.isnan(value):
        return b"nan"
    if math.isinf(value):
        return b"inf" if value > 0 else b"-inf"
    return repr(value).encode("ascii")


def _symbol_key(
    sym: SymbolNode,
) -> tuple[bytes, tuple[tuple[bytes, object], ...] | None]:
    if sym.ivars is None:
        return (sym.name, None)
    sig: list[tuple[bytes, object]] = []
    for k, v in sym.ivars:
        if isinstance(v, BoolNode):
            sig.append((k.name, ("bool", v.value)))
        elif isinstance(v, StringNode):
            sig.append((k.name, ("str", v.data)))
        else:
            sig.append((k.name, id(v)))
    return (sym.name, tuple(sig))


def _link_targets(root: MarshalNode) -> set[int]:
    """`id()` of every node that a link in `root` points to."""
    targets: set[int] = set()
    stack: list[MarshalNode] = [root]
    seen: set[int] = set()
    while stack:
        node = stack.pop()
        if id(node) in seen:
            continue
        seen.add(id(node))
        if isinstance(node, LinkNode):
            targets.add(id(node.target))
            continue
        if node.ivars is not None:
            stack.extend(value for _name, value in node.ivars)
        if isinstance(node, ArrayNode):
            stack.extend(node.items)
        elif isinstance(node, HashNode):
            for key, value in node.pairs:
                stack.append(key)
                stack.append(value)
            if node.default is not None:
                stack.append(node.default)
        elif isinstance(node, (ObjectNode, StructNode)):
            pairs = node.attributes if isinstance(node, ObjectNode) else node.members
            stack.extend(value for _name, value in pairs)
        elif isinstance(node, UserMarshalNode):
            stack.append(node.value)
        elif isinstance(node, (ExtendedNode, UserClassNode)):
            stack.append(node.inner)
    return targets


class _Writer:
    def __init__(self, link_targets: set[int] | None = None) -> None:
        self._out: bytearray = bytearray()
        self._symbols: dict[
            tuple[bytes, tuple[tuple[bytes, object], ...] | None], int
        ] = {}
        self._objects: dict[int, int] = {}
        self._next_obj_id: int = 0
        # A linked integer must stay a registered bignum: as a fixnum it would
        # leave no object-table entry for the link to point to.
        self._link_targets: set[int] = link_targets or set()

    def _register_object(self, node: MarshalNode) -> None:
        self._objects[id(node)] = self._next_obj_id
        self._next_obj_id += 1

    def _write_long(self, value: int) -> None:
        if value == 0:
            self._out.append(0)
            return
        if 1 <= value <= 122:
            self._out.append(value + 5)
            return
        if -123 <= value <= -1:
            self._out.append((value - 5) & 0xFF)
            return
        if value > 122:
            byte_len = (value.bit_length() + 7) // 8
            if byte_len > 4:
                raise MarshalUnsupported("Marshal integer exceeds fixnum range")
            self._out.append(byte_len)
            self._out.extend(value.to_bytes(byte_len, "little", signed=False))
            return
        buf = bytearray()
        v = value
        for byte_len in range(1, 5):
            buf.append(v & 0xFF)
            v >>= 8
            if v == -1:
                self._out.append((-byte_len) & 0xFF)
                self._out.extend(buf)
                return
        raise MarshalUnsupported("Marshal integer exceeds fixnum range")

    def _write_raw_bytes(self, raw: bytes) -> None:
        self._write_long(len(raw))
        self._out.extend(raw)

    def _write_ivars(self, ivars: list[tuple[SymbolNode, MarshalNode]]) -> None:
        self._write_long(len(ivars))
        for k, v in ivars:
            self._write_symbol(k)
            self._write_node(v)

    def _write_symbol(self, sym: SymbolNode) -> None:
        key = _symbol_key(sym)
        existing = self._symbols.get(key)
        if existing is not None:
            self._out.append(ord(";"))
            self._write_long(existing)
            return
        idx = len(self._symbols)
        self._symbols[key] = idx
        if sym.ivars is not None:
            self._out.append(ord("I"))
        self._out.append(ord(":"))
        self._write_raw_bytes(sym.name)
        if sym.ivars is not None:
            self._write_ivars(sym.ivars)

    def _write_body(
        self,
        node: MarshalNode,
        defer_userdef: list[UserDefNode | None] | None = None,
    ) -> None:
        if isinstance(node, NilNode):
            self._out.append(ord("0"))
            return
        if isinstance(node, BoolNode):
            self._out.append(ord("T") if node.value else ord("F"))
            return
        if isinstance(node, IntNode):
            if node.raw_tag is not None and node.raw_bytes is not None:
                self._out.extend(node.raw_tag)
                self._out.extend(node.raw_bytes)
                if node.raw_tag == b"l":
                    self._register_object(node)
                return
            if (
                FIXNUM_MIN <= node.value <= FIXNUM_MAX
                and id(node) not in self._link_targets
            ):
                self._out.append(ord("i"))
                self._write_long(node.value)
                return
            self._register_object(node)
            self._out.append(ord("l"))
            self._out.append(ord("+") if node.value >= 0 else ord("-"))
            mag = abs(node.value)
            word_count = max(1, (mag.bit_length() + 15) // 16)
            self._write_long(word_count)
            self._out.extend(mag.to_bytes(word_count * 2, "little", signed=False))
            return
        if isinstance(node, FloatNode):
            self._register_object(node)
            self._out.append(ord("f"))
            raw = (
                node.raw_bytes
                if node.raw_bytes is not None
                else _format_ruby_float(node.value)
            )
            self._write_raw_bytes(raw)
            return
        if isinstance(node, StringNode):
            self._register_object(node)
            self._out.append(ord('"'))
            self._write_raw_bytes(node.data)
            return
        if isinstance(node, RegexpNode):
            self._register_object(node)
            self._out.append(ord("/"))
            self._write_raw_bytes(node.pattern)
            self._out.append(node.options & 0xFF)
            return
        if isinstance(node, ArrayNode):
            self._register_object(node)
            self._out.append(ord("["))
            self._write_long(len(node.items))
            for item in node.items:
                self._write_node(item)
            return
        if isinstance(node, HashNode):
            self._register_object(node)
            self._out.append(ord("}") if node.default is not None else ord("{"))
            self._write_long(len(node.pairs))
            for k, v in node.pairs:
                self._write_node(k)
                self._write_node(v)
            if node.default is not None:
                self._write_node(node.default)
            return
        if isinstance(node, StructNode):
            self._out.append(ord("S"))
            self._write_symbol(node.class_name)
            self._register_object(node)
            self._write_long(len(node.members))
            for member, val in node.members:
                self._write_symbol(member)
                self._write_node(val)
            return
        if isinstance(node, ObjectNode):
            self._out.append(ord("o"))
            self._write_symbol(node.class_name)
            self._register_object(node)
            self._write_long(len(node.attributes))
            for ivar_name, val in node.attributes:
                self._write_symbol(ivar_name)
                self._write_node(val)
            return
        if isinstance(node, UserDefNode):
            self._out.append(ord("u"))
            self._write_symbol(node.class_name)
            self._write_raw_bytes(node.data)
            if defer_userdef is not None:
                defer_userdef[0] = node
            else:
                self._register_object(node)
            return
        if isinstance(node, UserMarshalNode):
            self._out.append(ord("U"))
            self._write_symbol(node.class_name)
            self._register_object(node)
            self._write_node(node.value)
            return
        if isinstance(node, ExtendedNode):
            self._out.append(ord("e"))
            self._write_symbol(node.module)
            self._write_node(node.inner, defer_userdef=defer_userdef)
            return
        if isinstance(node, UserClassNode):
            self._out.append(ord("C"))
            self._write_symbol(node.class_name)
            self._write_node(node.inner, defer_userdef=defer_userdef)
            return
        if isinstance(node, ClassRefNode):
            self._register_object(node)
            self._out.append(ord(node.kind))
            self._write_raw_bytes(node.name)
            return
        raise MarshalUnsupported(f"Cannot encode node type {type(node).__name__}")

    def _write_node(
        self,
        node: MarshalNode,
        *,
        defer_userdef: list[UserDefNode | None] | None = None,
    ) -> None:
        if isinstance(node, SymbolNode):
            self._write_symbol(node)
            return
        if isinstance(node, LinkNode):
            idx = self._objects.get(id(node.target))
            if idx is None:
                raise MarshalUnsupported("Dangling Marshal object link")
            self._out.append(ord("@"))
            self._write_long(idx)
            return
        if node.ivars is not None:
            self._out.append(ord("I"))
            deferred: list[UserDefNode | None] = [None]
            self._write_body(node, defer_userdef=deferred)
            self._write_ivars(node.ivars)
            if deferred[0] is not None:
                self._register_object(deferred[0])
            return
        self._write_body(node, defer_userdef=defer_userdef)

    def write_dump(self, root: MarshalNode) -> bytes:
        self._out.extend(MARSHAL_HEADER)
        self._write_node(root)
        return bytes(self._out)


def dump_all(dumps: list[MarshalNode]) -> bytes:
    """Encode one or more Ruby Marshal 4.8 dumps back to bytes."""
    if not dumps:
        raise MarshalUnsupported("Cannot encode an empty dump list")
    parts: list[bytes] = []
    for root in dumps:
        parts.append(_Writer(_link_targets(root)).write_dump(root))
    return b"".join(parts)


# --------------------------------------------------------- structural equality


def _ivars_equal(
    a: list[tuple[SymbolNode, MarshalNode]] | None,
    b: list[tuple[SymbolNode, MarshalNode]] | None,
    link_map: dict[int, int],
) -> bool:
    if a is None or b is None:
        return a is b
    if len(a) != len(b):
        return False
    for (ka, va), (kb, vb) in zip(a, b, strict=True):
        if not _nodes_equal(ka, kb, link_map) or not _nodes_equal(va, vb, link_map):
            return False
    return True


def _nodes_equal(a: MarshalNode, b: MarshalNode, link_map: dict[int, int]) -> bool:
    if type(a) is not type(b):
        return False
    if isinstance(a, LinkNode) and isinstance(b, LinkNode):
        return link_map.get(id(a.target)) == id(b.target)
    link_map[id(a)] = id(b)
    if not _ivars_equal(a.ivars, b.ivars, link_map):
        return False
    if isinstance(a, NilNode):
        return True
    if isinstance(a, BoolNode) and isinstance(b, BoolNode):
        return a.value is b.value
    if isinstance(a, IntNode) and isinstance(b, IntNode):
        return a.value == b.value
    if isinstance(a, FloatNode) and isinstance(b, FloatNode):
        if math.isnan(a.value) and math.isnan(b.value):
            return True
        if a.value != b.value:
            return False
        if a.value == 0.0:
            return math.copysign(1.0, a.value) == math.copysign(1.0, b.value)
        return True
    if isinstance(a, StringNode) and isinstance(b, StringNode):
        return a.data == b.data
    if isinstance(a, SymbolNode) and isinstance(b, SymbolNode):
        return a.name == b.name
    if isinstance(a, RegexpNode) and isinstance(b, RegexpNode):
        return a.pattern == b.pattern and a.options == b.options
    if isinstance(a, ClassRefNode) and isinstance(b, ClassRefNode):
        return a.kind == b.kind and a.name == b.name
    if isinstance(a, ArrayNode) and isinstance(b, ArrayNode):
        return len(a.items) == len(b.items) and all(
            _nodes_equal(x, y, link_map) for x, y in zip(a.items, b.items, strict=True)
        )
    if isinstance(a, HashNode) and isinstance(b, HashNode):
        if len(a.pairs) != len(b.pairs):
            return False
        for (ka, va), (kb, vb) in zip(a.pairs, b.pairs, strict=True):
            if not _nodes_equal(ka, kb, link_map) or not _nodes_equal(va, vb, link_map):
                return False
        if a.default is None or b.default is None:
            return a.default is b.default
        return _nodes_equal(a.default, b.default, link_map)
    if isinstance(a, StructNode) and isinstance(b, StructNode):
        if not _nodes_equal(a.class_name, b.class_name, link_map):
            return False
        return len(a.members) == len(b.members) and all(
            _nodes_equal(ka, kb, link_map) and _nodes_equal(va, vb, link_map)
            for (ka, va), (kb, vb) in zip(a.members, b.members, strict=True)
        )
    if isinstance(a, ObjectNode) and isinstance(b, ObjectNode):
        if not _nodes_equal(a.class_name, b.class_name, link_map):
            return False
        return len(a.attributes) == len(b.attributes) and all(
            _nodes_equal(ka, kb, link_map) and _nodes_equal(va, vb, link_map)
            for (ka, va), (kb, vb) in zip(a.attributes, b.attributes, strict=True)
        )
    if isinstance(a, UserDefNode) and isinstance(b, UserDefNode):
        return _nodes_equal(a.class_name, b.class_name, link_map) and a.data == b.data
    if isinstance(a, UserMarshalNode) and isinstance(b, UserMarshalNode):
        return _nodes_equal(a.class_name, b.class_name, link_map) and _nodes_equal(
            a.value, b.value, link_map
        )
    if isinstance(a, ExtendedNode) and isinstance(b, ExtendedNode):
        return _nodes_equal(a.module, b.module, link_map) and _nodes_equal(
            a.inner, b.inner, link_map
        )
    if isinstance(a, UserClassNode) and isinstance(b, UserClassNode):
        return _nodes_equal(a.class_name, b.class_name, link_map) and _nodes_equal(
            a.inner, b.inner, link_map
        )
    return False


def trees_equal(a: list[MarshalNode], b: list[MarshalNode]) -> bool:
    """True when two decoded Marshal dump lists have identical structure and links."""
    if len(a) != len(b):
        return False
    for root_a, root_b in zip(a, b, strict=True):
        if not _nodes_equal(root_a, root_b, {}):
            return False
    return True


# --------------------------------------------------- node ids & JSON projection


def _is_edit_safe_scalar(node: MarshalNode) -> bool:
    if isinstance(node, BoolNode):
        return True
    if isinstance(node, IntNode):
        return abs(node.value) <= MAX_SAFE_JS_INT
    if isinstance(node, FloatNode):
        return math.isfinite(node.value)
    if isinstance(node, StringNode):
        return node.is_editable_utf8()
    return False


def assign_ids(
    dumps: list[MarshalNode],
) -> tuple[dict[int, MarshalNode], set[int]]:
    """Assign deterministic preorder node ids across `dumps`.

    Returns `(nodes_by_id, editable_ids)`. Links get their own id and are not
    followed, so shared objects and cycles never recurse. Nodes inside hash
    keys are never placed in `editable_ids`.
    """
    nodes_by_id: dict[int, MarshalNode] = {}
    editable_ids: set[int] = set()
    next_id = 0

    def visit(node: MarshalNode, in_hash_key: bool) -> None:
        nonlocal next_id
        nid = next_id
        next_id += 1
        node.node_id = nid
        nodes_by_id[nid] = node
        if not in_hash_key and _is_edit_safe_scalar(node):
            editable_ids.add(nid)

        if isinstance(node, ArrayNode):
            for item in node.items:
                visit(item, in_hash_key)
        elif isinstance(node, HashNode):
            for k, v in node.pairs:
                visit(k, True)
                visit(v, in_hash_key)
            if node.default is not None:
                visit(node.default, in_hash_key)
        elif isinstance(node, ObjectNode):
            for _k, v in node.attributes:
                visit(v, in_hash_key)
        elif isinstance(node, StructNode):
            for _k, v in node.members:
                visit(v, in_hash_key)
        elif isinstance(node, UserMarshalNode):
            visit(node.value, in_hash_key)
        elif isinstance(node, ExtendedNode):
            visit(node.inner, in_hash_key)
        elif isinstance(node, UserClassNode):
            visit(node.inner, in_hash_key)
        # LinkNode, UserDefNode, and scalar/leaf nodes are not traversed further.

    for root in dumps:
        visit(root, False)
    return nodes_by_id, editable_ids


def _project_node(node: MarshalNode, editable_ids: set[int]) -> dict[str, object]:
    nid = node.node_id if node.node_id is not None else -1
    editable = nid in editable_ids

    if isinstance(node, NilNode):
        return {"id": nid, "t": "nil"}
    if isinstance(node, BoolNode):
        out: dict[str, object] = {"id": nid, "t": "bool", "v": node.value}
        if editable:
            out["editable"] = True
        return out
    if isinstance(node, IntNode):
        val: int | str = (
            node.value if abs(node.value) <= MAX_SAFE_JS_INT else str(node.value)
        )
        out = {"id": nid, "t": "int", "v": val}
        if editable:
            out["editable"] = True
        return out
    if isinstance(node, FloatNode):
        if math.isfinite(node.value):
            out = {"id": nid, "t": "float", "v": node.value}
            if editable:
                out["editable"] = True
            return out
        return {
            "id": nid,
            "t": "float",
            "v": _format_ruby_float(node.value).decode("ascii"),
        }
    if isinstance(node, StringNode):
        utf8 = node.decode_utf8()
        out = {
            "id": nid,
            "t": "str",
            "v": utf8 if utf8 is not None else node.display_text(),
        }
        if editable:
            out["editable"] = True
        return out
    if isinstance(node, SymbolNode):
        return {"id": nid, "t": "sym", "v": node.text}
    if isinstance(node, ArrayNode):
        return {
            "id": nid,
            "t": "array",
            "items": [_project_node(item, editable_ids) for item in node.items],
        }
    if isinstance(node, HashNode):
        out = {
            "id": nid,
            "t": "hash",
            "pairs": [
                [_project_node(k, editable_ids), _project_node(v, editable_ids)]
                for k, v in node.pairs
            ],
        }
        if node.default is not None:
            out["default"] = _project_node(node.default, editable_ids)
        return out
    if isinstance(node, ObjectNode):
        return {
            "id": nid,
            "t": "object",
            "class": node.class_name.text,
            "ivars": [
                [k.text, _project_node(v, editable_ids)] for k, v in node.attributes
            ],
        }
    if isinstance(node, StructNode):
        return {
            "id": nid,
            "t": "struct",
            "class": node.class_name.text,
            "ivars": [
                [k.text, _project_node(v, editable_ids)] for k, v in node.members
            ],
        }
    if isinstance(node, UserDefNode):
        return {
            "id": nid,
            "t": "userdef",
            "class": node.class_name.text,
            "size": len(node.data),
        }
    if isinstance(node, UserMarshalNode):
        return {
            "id": nid,
            "t": "usermarshal",
            "class": node.class_name.text,
            "value": _project_node(node.value, editable_ids),
        }
    if isinstance(node, ExtendedNode):
        return {
            "id": nid,
            "t": "extended",
            "module": node.module.text,
            "inner": _project_node(node.inner, editable_ids),
        }
    if isinstance(node, UserClassNode):
        return {
            "id": nid,
            "t": "userclass",
            "class": node.class_name.text,
            "inner": _project_node(node.inner, editable_ids),
        }
    if isinstance(node, RegexpNode):
        return {
            "id": nid,
            "t": "regexp",
            "v": node.pattern.decode("utf-8", errors="replace"),
            "options": node.options,
        }
    if isinstance(node, ClassRefNode):
        return {
            "id": nid,
            "t": "classref",
            "kind": node.kind,
            "v": node.name.decode("utf-8", errors="replace"),
        }
    if isinstance(node, LinkNode):
        target_id = node.target.node_id if node.target.node_id is not None else -1
        return {"id": nid, "t": "link", "to": target_id}
    return {"id": nid, "t": "unknown"}


def project_dumps(
    dumps: list[MarshalNode],
) -> tuple[list[dict[str, object]], dict[int, MarshalNode], set[int]]:
    """Assign ids and project `dumps` into a JSON-serializable raw tree."""
    nodes_by_id, editable_ids = assign_ids(dumps)
    projected = [_project_node(root, editable_ids) for root in dumps]
    return projected, nodes_by_id, editable_ids
