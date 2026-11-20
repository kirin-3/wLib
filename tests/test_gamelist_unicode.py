# SPDX-License-Identifier: GPL-3.0-or-later

from pathlib import Path

import pytest

from core.gamelist_hyperfile import read_gamelist_table
from test_gamelist_hyperfile import gamelist_record, write_gamelist


@pytest.mark.parametrize("space", ["\u00a0", "\u202f", "\u3000"])
def test_reads_single_record_with_unicode_space(tmp_path: Path, space: str) -> None:
    title = f"Sample{space}Game"
    path = write_gamelist(tmp_path, gamelist_record(name=title))

    table = read_gamelist_table(path)

    assert [record.name for record in table.records] == [title]
    assert table.failed == 0
