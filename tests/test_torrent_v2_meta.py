# SPDX-License-Identifier: GPL-3.0-or-later
"""v2-only and hybrid (BEP 52) `.torrent` metadata: hashes, size and magnet link."""

import hashlib

import pytest

from core.downloads.bencode import parse_torrent
from core.downloads.torrent_export import magnet_link


def bstr(raw: bytes) -> bytes:
    return str(len(raw)).encode() + b":" + raw


ROOT = bstr(b"pieces root") + bstr(b"r" * 32)
# Keys in sorted order, as bencode needs.
TREE = (
    b"9:file treed"
    + bstr(b"a.bin")
    + b"d0:d6:lengthi40000e"
    + ROOT
    + b"ee"
    + bstr(b"sub")
    + b"d"
    + bstr(b"b.bin")
    + b"d0:d6:lengthi100e"
    + ROOT
    + b"eeee"
)
V2_INFO = b"d" + TREE + b"12:meta versioni2e4:name4:game12:piece lengthi16384ee"
HYBRID_INFO = (
    b"d"
    + TREE
    + b"5:filesld6:lengthi40000e4:pathl5:a.bineed6:lengthi100e4:pathl3:sub5:b.bineee"
    + b"12:meta versioni2e4:name4:game12:piece lengthi16384e6:pieces20:"
    + b"x" * 20
    + b"e"
)


def torrent(info: bytes) -> bytes:
    return b"d8:announce" + bstr(b"http://t/ann/PASS") + b"4:info" + info + b"e"


def test_v2_only_torrent_is_parsed_with_its_sha256_hash():
    meta = parse_torrent(torrent(V2_INFO))
    v2 = hashlib.sha256(V2_INFO).hexdigest()
    assert (meta.info_hash, meta.info_hash_v2) == (v2, v2)
    assert (meta.name, meta.size, meta.file_count) == ("game", 40100, 2)


def test_hybrid_torrent_keeps_the_v1_hash_and_adds_the_v2_hash():
    meta = parse_torrent(torrent(HYBRID_INFO))
    assert meta.info_hash == hashlib.sha1(HYBRID_INFO).hexdigest()
    assert meta.info_hash_v2 == hashlib.sha256(HYBRID_INFO).hexdigest()
    assert (meta.size, meta.file_count) == (40100, 2)


def test_v1_torrent_has_no_v2_hash():
    info = (
        b"d6:lengthi5e4:name4:game12:piece lengthi16384e6:pieces20:" + b"x" * 20 + b"e"
    )
    assert parse_torrent(torrent(info)).info_hash_v2 == ""


def test_magnet_uses_btmh_for_the_v2_hash():
    v2 = hashlib.sha256(V2_INFO).hexdigest()
    assert magnet_link(torrent(V2_INFO)).startswith(
        f"magnet:?xt=urn:btmh:1220{v2}&dn=game&tr="
    )
    hybrid = magnet_link(torrent(HYBRID_INFO))
    assert hybrid.startswith(
        f"magnet:?xt=urn:btih:{hashlib.sha1(HYBRID_INFO).hexdigest()}"
        f"&xt=urn:btmh:1220{hashlib.sha256(HYBRID_INFO).hexdigest()}&dn=game"
    )


@pytest.mark.parametrize(
    "tree",
    [
        b"9:file treedee",
        b"9:file treei3e",
        b"9:file treed" + bstr(b"a") + b"d0:d6:lengthi-1eeee",
        b"9:file treed" + bstr(b"a") + b"d0:d4:name1:xeeee",
    ],
)
def test_bad_v2_file_tree_is_refused(tree: bytes):
    info = b"d" + tree + b"12:meta versioni2e4:name4:gamee"
    with pytest.raises(ValueError):
        _ = parse_torrent(torrent(info))


def test_matches_libtorrent_for_a_real_v2_only_torrent(tmp_path):
    lt = pytest.importorskip("libtorrent")
    game = tmp_path / "game"
    (game / "sub").mkdir(parents=True)
    _ = (game / "a.bin").write_bytes(b"x" * 40000)
    _ = (game / "sub" / "b.bin").write_bytes(b"y" * 100)
    fs = lt.file_storage()
    lt.add_files(fs, str(game))
    creator = lt.create_torrent(fs, 0, lt.create_torrent.v2_only)
    lt.set_piece_hashes(creator, str(tmp_path))
    data = lt.bencode(creator.generate())
    meta = parse_torrent(data)
    hashes = lt.torrent_info(data).info_hashes()
    assert not hashes.has_v1()
    assert meta.info_hash == meta.info_hash_v2 == str(hashes.v2)
    assert (meta.size, meta.file_count) == (40100, 2)
