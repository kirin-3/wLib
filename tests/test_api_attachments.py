# SPDX-License-Identifier: GPL-3.0-or-later
import json

import pytest

from core import database
from core.api import Api
from core.database import add_game, init_db


@pytest.fixture(autouse=True)
def setup_dirs(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("WLIB_DATA_DIR", str(data_dir))
    monkeypatch.setattr(database, "DATA_DIR", str(data_dir))
    monkeypatch.setattr("core.database.DB_PATH", str(tmp_path / "wlib.db"))
    init_db()
    yield


def _pdf(path, body=b"content"):
    path.write_bytes(b"%PDF-1.4\n" + body)
    return str(path)


def test_attachment_bridge_round_trip_is_json_serializable(tmp_path, monkeypatch):
    api = Api()
    game_id = add_game("Game", "/game.exe")
    assert game_id is not None
    opened = []

    def fake_opener(target, error):
        opened.append((target, error))
        return {"success": True}

    monkeypatch.setattr(api, "_open_with_system_handler", fake_opener)

    source = _pdf(tmp_path / "guide.pdf")
    added = api.add_game_attachment(game_id, source)
    assert added["success"] is True
    assert added["attachment"]["name"] == "guide.pdf"
    json.dumps(added)

    listed = api.list_game_attachments(game_id)
    assert listed["success"] is True
    assert [item["name"] for item in listed["attachments"]] == ["guide.pdf"]
    json.dumps(listed)

    opened_result = api.open_game_attachment(game_id, "guide.pdf")
    assert opened_result["success"] is True
    assert opened and opened[0][0].endswith("guide.pdf")
    json.dumps(opened_result)

    removed = api.remove_game_attachment(game_id, "guide.pdf")
    assert removed["success"] is True
    assert removed["attachments"] == []
    json.dumps(removed)


def test_attachment_bridge_rejects_unknown_game():
    api = Api()
    assert api.list_game_attachments(999999)["error_code"] == "game_not_found"
    assert api.add_game_attachment(999999, "/tmp/x.pdf")["error_code"] == "game_not_found"
    assert api.open_game_attachment(999999, "x.pdf")["error_code"] == "game_not_found"
    assert api.remove_game_attachment(999999, "x.pdf")["error_code"] == "game_not_found"
    assert api.list_game_attachments("bad")["error_code"] == "game_not_found"


def test_add_attachment_rejects_non_pdf(tmp_path):
    api = Api()
    game_id = add_game("Game", "/game.exe")
    assert game_id is not None
    notes = tmp_path / "notes.txt"
    notes.write_bytes(b"%PDF-1.4\ncontent")
    result = api.add_game_attachment(game_id, str(notes))
    assert result["success"] is False
    assert result["error_code"] == "not_a_pdf"
    fake = tmp_path / "fake.pdf"
    fake.write_bytes(b"nope")
    result = api.add_game_attachment(game_id, str(fake))
    assert result["error_code"] == "not_a_pdf"
    json.dumps(result)


def test_open_missing_reports_attachment_missing_and_lists(tmp_path, monkeypatch):
    api = Api()
    game_id = add_game("Game", "/game.exe")
    assert game_id is not None
    source = _pdf(tmp_path / "guide.pdf")
    assert api.add_game_attachment(game_id, source)["success"] is True

    def fake_opener(target, error):
        return {"success": True}

    monkeypatch.setattr(api, "_open_with_system_handler", fake_opener)
    # Delete the file outside wLib.
    from core.game_attachments import attachments_dir
    import os

    os.remove(os.path.join(attachments_dir(game_id), "guide.pdf"))
    result = api.open_game_attachment(game_id, "guide.pdf")
    assert result["success"] is False
    assert result["error_code"] == "attachment_missing"
    assert api.list_game_attachments(game_id)["attachments"] == []
    remove_result = api.remove_game_attachment(game_id, "guide.pdf")
    assert remove_result["error_code"] == "attachment_missing"


def test_open_uses_system_handler_error(tmp_path, monkeypatch):
    api = Api()
    game_id = add_game("Game", "/game.exe")
    assert game_id is not None
    source = _pdf(tmp_path / "guide.pdf")
    assert api.add_game_attachment(game_id, source)["success"] is True

    def failing_opener(target, error):
        return {"success": False, "error": "no viewer"}

    monkeypatch.setattr(api, "_open_with_system_handler", failing_opener)
    result = api.open_game_attachment(game_id, "guide.pdf")
    assert result["success"] is False
    assert "no viewer" in result["error"]


def test_browse_pdf_file_passes_file_types(monkeypatch):
    api = Api()
    seen = {}

    def fake_linux_dialog(kind, directory="", file_types=()):
        seen["kind"] = kind
        seen["file_types"] = tuple(file_types)
        return "/picked/guide.pdf"

    monkeypatch.setattr(api, "_browse_linux_dialog", fake_linux_dialog)
    monkeypatch.setattr("sys.platform", "linux")
    assert api.browse_pdf_file("") == "/picked/guide.pdf"
    assert seen["kind"] == "file"
    assert seen["file_types"] == ("PDF Files (*.pdf)", "All files (*.*)")


def test_delete_game_removes_attachments_folder(tmp_path):
    import os

    from core.game_attachments import attachments_dir

    api = Api()
    game_id = add_game("Game", "/game.exe")
    assert game_id is not None
    source = _pdf(tmp_path / "guide.pdf")
    assert api.add_game_attachment(game_id, source)["success"] is True
    assert os.path.isdir(attachments_dir(game_id))
    assert api.delete_game(game_id)["success"] is True
    assert not os.path.exists(attachments_dir(game_id))


def test_delete_game_succeeds_when_attachment_cleanup_fails(tmp_path, monkeypatch):
    api = Api()
    game_id = add_game("Game", "/game.exe")
    assert game_id is not None

    def failing_remove(game_id_arg):
        raise OSError("cannot clean")

    monkeypatch.setattr(
        "core.game_attachments.remove_game_attachments", failing_remove
    )
    result = api.delete_game(game_id)
    assert result["success"] is True


def test_api_init_prunes_orphans_and_survives_errors(tmp_path, monkeypatch):
    import os

    from core.game_attachments import attachments_dir

    game_id = add_game("Game", "/game.exe")
    assert game_id is not None
    orphan = os.path.join(str(database.DATA_DIR), "attachments", "999999")
    os.makedirs(orphan, exist_ok=True)
    with open(os.path.join(orphan, "guide.pdf"), "wb") as handle:
        handle.write(b"%PDF-1.4 orphan")

    Api()
    assert not os.path.exists(orphan)
    assert os.path.isdir(attachments_dir(game_id)) or not os.path.exists(
        attachments_dir(game_id)
    )

    orphan_again = os.path.join(str(database.DATA_DIR), "attachments", "888888")
    os.makedirs(orphan_again, exist_ok=True)

    def failing_prune(existing_ids):
        raise OSError("prune failed")

    monkeypatch.setattr(
        "core.game_attachments.prune_orphan_attachments", failing_prune
    )
    # Construction must not raise even when pruning fails.
    Api()
    assert os.path.isdir(orphan_again)
