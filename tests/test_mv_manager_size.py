# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path

import pytest

from core.game_native_size import NativeSize, detect_native_size


@pytest.mark.parametrize(
    "inactive",
    [
        "// SceneManager._screenWidth = 816;\n// SceneManager._screenHeight = 624;",
        "/* SceneManager._screenWidth = 816;\nSceneManager._screenHeight = 624; */",
        r"""var note = 'It\'s old: SceneManager._screenWidth = 816; SceneManager._screenHeight = 624;';""",
        r'''var note = "Old \"defaults\": SceneManager._screenWidth = 816; SceneManager._screenHeight = 624;";''',
        "var note = `SceneManager._screenWidth = 816;\nSceneManager._screenHeight = 624;`;",
    ],
    ids=["line-comment", "block-comment", "single-quoted", "double-quoted", "template"],
)
def test_mv_manager_size_ignores_inactive_assignments(tmp_path: Path, inactive: str):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "System.json").write_text("{}", encoding="utf-8")
    js = tmp_path / "js"
    js.mkdir()
    (js / "rpg_core.js").touch()
    (js / "rpg_managers.js").write_text(
        f"{inactive}\n"
        "SceneManager._screenWidth = 1280;\n"
        "SceneManager._screenHeight = 720;\n",
        encoding="utf-8",
    )

    assert detect_native_size(str(tmp_path)) == NativeSize(1280, 720, "js/rpg_managers.js")
