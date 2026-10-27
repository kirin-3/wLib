# SPDX-License-Identifier: GPL-3.0-or-later
import pytest

from core.f95zone import split_thread_title


@pytest.mark.parametrize(
    ("heading", "expected"),
    [
        ("Eternum [v0.8] [Caribdis]", ("Eternum", "0.8", "Caribdis")),
        ("[RenPy] Eternum [v0.8] [Caribdis]", ("Eternum", "0.8", "Caribdis")),
        ("Game [Chapter 2 v1.1] [Dev]", ("Game", "Chapter 2 v1.1", "Dev")),
        ("Game [S2 E3] [Dev]", ("Game", "S2 E3", "Dev")),
        ("Game v1.2 [Dev]", ("Game", "1.2", "Dev")),
        ("Game [v1.2]", ("Game", "1.2", "")),
        ("Game", ("Game", "", "")),
        ("wLib - [v1.0] Game [Dev]", ("Game", "1.0", "Dev")),
        ("Game [Final release] [Dev]", ("Game", "Final release", "Dev")),
    ],
)
def test_split_thread_title(heading, expected):
    assert split_thread_title(heading) == expected
