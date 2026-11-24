# SPDX-License-Identifier: GPL-3.0-or-later
"""Imported link pairs (catalog_server.tools.import_links)."""

import os

from catalog_server.__main__ import _run_lock  # pyright: ignore[reportPrivateUsage]
from catalog_server.store import STATE_FILE, StateStore
from catalog_server.tools import import_links


def _stored(folder):
    state = StateStore(os.path.join(folder, STATE_FILE))
    try:
        return state.imported_links()
    finally:
        state.close()


def test_good_and_bad_lines(tmp_path, capsys):
    pairs = tmp_path / "pairs.tsv"
    pairs.write_text(
        "# comment\n"
        "f95\t223393\tdlsite\tRJ01234567\n"
        "\n"
        "lc\t6\tf95\t223393\n"
        "f95\t12\tdlsite\tRG12345\n"
        "f95\t12\tfanza\td_1\n"
        "f95\t12\tf95\t13\n"
        "only\tthree\tfields\n",
        encoding="utf-8",
    )
    assert import_links.main([str(pairs), "--data-dir", str(tmp_path)]) == 0
    captured = capsys.readouterr()
    assert "imported 2 pairs, skipped 4 lines" in captured.out
    assert "line 5" in captured.err and "line 8" in captured.err
    assert _stored(str(tmp_path)) == [
        ("f95", "223393", "dlsite", "RJ01234567"),
        ("lc", "6", "f95", "223393"),
    ]


def test_import_replaces_and_empty_file_clears(tmp_path):
    first = tmp_path / "a.tsv"
    first.write_text("f95\t1\tdlsite\tRJ01234567\n", encoding="utf-8")
    second = tmp_path / "b.tsv"
    second.write_text("f95\t2\tvndb\tv7\n", encoding="utf-8")
    empty = tmp_path / "c.tsv"
    empty.write_text("", encoding="utf-8")
    import_links.main([str(first), "--data-dir", str(tmp_path)])
    import_links.main([str(second), "--data-dir", str(tmp_path)])
    assert _stored(str(tmp_path)) == [("f95", "2", "vndb", "v7")]
    import_links.main([str(empty), "--data-dir", str(tmp_path)])
    assert _stored(str(tmp_path)) == []


def test_import_refuses_during_a_run(tmp_path, capsys):
    pairs = tmp_path / "pairs.tsv"
    pairs.write_text("f95\t1\tdlsite\tRJ01234567\n", encoding="utf-8")
    with _run_lock(str(tmp_path)) as acquired:
        assert acquired
        assert import_links.main([str(pairs), "--data-dir", str(tmp_path)]) == 1
    assert "in progress" in capsys.readouterr().err
    assert _stored(str(tmp_path)) == []
