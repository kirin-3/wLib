# SPDX-License-Identifier: GPL-3.0-or-later
import json
import zipfile
from pathlib import Path

from core import database
from core.api import Api


def test_archive_install_keeps_valid_lewdcorner_screenshots(tmp_path: Path):
    api = Api()
    library = tmp_path / "library"
    library.mkdir()
    database.update_setting("library_folder", str(library))
    archive = tmp_path / "Game.zip"
    with zipfile.ZipFile(archive, "w") as game_zip:
        game_zip.writestr("Game.exe", b"game")

    thread = "https://lewdcorner.com/threads/24724/"
    first = "https://lewdcorner.com/attachments/first-png.123/"
    second = "https://images.example/second.png"
    result = api.install_archive({
        "archive_path": str(archive),
        "title": "Game",
        "version": "1",
        "lc_url": thread,
        "screenshots": [f" {first} ", first, "http://images.example/no.png", 123, second],
    })

    assert result["success"] is True, result
    game_id = result["game_id"]
    assert isinstance(game_id, int)
    game = database.get_game(game_id)
    assert game is not None
    assert game["lc_url"] == thread
    assert json.loads(str(game["screenshots"])) == [first, second]
