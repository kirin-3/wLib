# SPDX-License-Identifier: GPL-3.0-or-later
import os

import pytest

from core.downloads import torrent_export
from core.downloads.bencode import parse_torrent, trackers
from core.downloads.torrent_export import magnet_link, safe_file_name, write_torrent

INFO = b"4:infod6:lengthi5e4:name4:game12:piece lengthi16384e6:pieces20:" + b"x" * 20 + b"e"


def bstr(text: str) -> bytes:
    raw = text.encode()
    return str(len(raw)).encode() + b":" + raw


def torrent(announce: str | None = None, tiers: bytes | None = None, info: bytes = INFO) -> bytes:
    body = b"d"
    if announce is not None:
        body += b"8:announce" + bstr(announce)
    if tiers is not None:
        body += b"13:announce-list" + tiers
    return body + info + b"e"


def tier_list(*tiers: list[str]) -> bytes:
    return b"l" + b"".join(b"l" + b"".join(bstr(u) for u in tier) + b"e" for tier in tiers) + b"e"


def test_single_tracker():
    assert trackers(torrent("http://a/announce")) == ["http://a/announce"]


def test_multi_tier_without_duplicates():
    data = torrent("http://a/x", tier_list(["http://a/x", "http://b/y"], ["udp://c:1", "http://b/y"]))
    assert trackers(data) == ["http://a/x", "http://b/y", "udp://c:1"]


def test_no_tracker():
    assert trackers(torrent()) == []


def test_malformed_announce_list_is_skipped():
    tiers = b"l" + bstr("http://bare") + b"li5e" + bstr("") + bstr("http://ok") + b"ee"
    assert trackers(torrent("http://a", tiers)) == ["http://a", "http://ok"]
    assert trackers(torrent("http://a", b"i3e")) == ["http://a"]
    with pytest.raises(ValueError):
        trackers(b"not bencode")


def test_magnet_holds_hash_name_and_trackers():
    info = b"4:infod6:lengthi5e4:name9:a b&c.zip12:piece lengthi16384e6:pieces20:" + b"x" * 20 + b"e"
    data = torrent("http://t/ann/PASS?k=1", tier_list(["udp://u:80"]), info)
    link = magnet_link(data)
    assert link == (
        f"magnet:?xt=urn:btih:{parse_torrent(data).info_hash}"
        "&dn=a%20b%26c.zip&tr=http%3A%2F%2Ft%2Fann%2FPASS%3Fk%3D1&tr=udp%3A%2F%2Fu%3A80"
    )


def test_safe_file_name():
    assert safe_file_name('a/b:c?d*"<>|\\e', "rpdl-5") == "a_b_c_d______e.torrent"
    assert safe_file_name("x\x00y\x1fz", "rpdl-5") == "x_y_z.torrent"
    assert safe_file_name(" ..game.. ", "rpdl-5") == "game.torrent"
    assert safe_file_name("", "rpdl-5") == "rpdl-5.torrent"
    assert safe_file_name("...", "rpdl-5") == "rpdl-5.torrent"
    assert safe_file_name("game.torrent", "rpdl-5") == "game.torrent"


def test_safe_file_name_cuts_at_150_bytes_without_breaking_a_character():
    name = safe_file_name("é" * 100, "rpdl-5")
    stem = name.removesuffix(".torrent")
    assert len(stem.encode()) <= 150 and stem == "é" * 75
    assert len(safe_file_name("a" * 300, "x").removesuffix(".torrent")) == 150


def test_write_replaces_an_existing_file(tmp_path):
    (tmp_path / "g.torrent").write_bytes(b"old")
    path = write_torrent(str(tmp_path), "g.torrent", b"new")
    assert path == str(tmp_path / "g.torrent")
    assert (tmp_path / "g.torrent").read_bytes() == b"new"
    assert sorted(os.listdir(tmp_path)) == ["g.torrent"]


def test_write_failure_leaves_nothing(tmp_path, monkeypatch):
    def full(fd):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(torrent_export.os, "fsync", full)
    with pytest.raises(OSError):
        write_torrent(str(tmp_path), "g.torrent", b"data")
    assert os.listdir(tmp_path) == []
