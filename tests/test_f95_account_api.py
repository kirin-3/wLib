# SPDX-License-Identifier: GPL-3.0-or-later
import sqlite3
import threading
from pathlib import Path

import pytest

from core import database, extension_bridge, f95_lists
from core.api import Api
from core.database import add_game, get_all_games, init_db

FIXTURES = Path(__file__).parent / "fixtures" / "f95_lists"
WATCHED_1 = "https://f95zone.to/watched/threads?tab=games&unread=0&sort=title&page=1"
BOOKMARKS_1 = (
    "https://f95zone.to/watched/threads?tab=bookmarks&bm=games&sort=title&page=1"
)


def _page(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _fixture_fetch(url: str) -> tuple[int, str, str]:
    pages = {
        WATCHED_1: _page("watched_page1.html"),
        BOOKMARKS_1: _page("bookmarks_page1.html"),
    }
    return 200, url, pages.get(url, _page("past_end.html"))


@pytest.fixture
def api() -> Api:
    init_db()
    extension_bridge.reset_for_tests()
    return Api()


def _use_reader(api: Api, fetch=_fixture_fetch, clock=None) -> f95_lists.AccountReader:
    reader = f95_lists.AccountReader(fetch=fetch, preflight=lambda: None, clock=clock)
    api.f95_account_reader = reader
    return reader


def test_no_extension_and_outdated_extension_make_no_request(api: Api):
    result = api.start_f95_account_read()
    assert result["success"] is False and result["error_code"] == "no_extension"
    conn = extension_bridge.BridgeConnection(
        send_text=lambda _text: pytest.fail("no request expected")
    )
    extension_bridge._register_connection(conn)  # pyright: ignore[reportPrivateUsage]
    extension_bridge._handle_message(
        conn, {"type": "hello", "protocol": 2, "can_fetch": True}
    )  # pyright: ignore[reportPrivateUsage]
    result = api.start_f95_account_read()
    assert result["success"] is False and result["error_code"] == "extension_outdated"
    extension_bridge.reset_for_tests()


def test_read_preview_import_and_update_check_for_added_ids_only(
    api: Api, monkeypatch: pytest.MonkeyPatch
):
    existing = add_game(
        "Natsu", "/games/natsu/run.sh", "https://f95zone.to/threads/natsu.323748/"
    )
    assert existing is not None
    checked: list[list[int]] = []
    monkeypatch.setattr(
        api,
        "check_all_updates",
        lambda ids=None, *a, **k: checked.append(list(ids or [])) or {"success": True},
    )
    reader = _use_reader(api)
    started = api.start_f95_account_read()
    assert started["success"] is True
    reader.join(5)
    status = api.get_f95_account_read_status()
    assert status["state"] == "done"
    preview = status["preview"]
    assert isinstance(preview, dict)
    assert (preview["total"], preview["new"], preview["existing"]) == (2, 1, 1)
    result = api.import_f95_account_lists(str(started["read_id"]))
    assert result["success"] is True
    assert (result["added"], result["tagged"]) == (1, 1)
    new_ids = [game["id"] for game in get_all_games() if game["id"] != existing]
    assert checked == [new_ids]
    assert result["metadata_check"] == "started"
    again = api.import_f95_account_lists(str(started["read_id"]))
    assert again["success"] is False and again["error_code"] == "not_found"


@pytest.mark.parametrize(
    ("catalog", "catalog_off", "without_tags"),
    [("missing", False, 2), ("off", True, 2), ("full", False, 0)],
)
def test_account_preview_and_import_report_missing_thread_tags(
    api: Api,
    monkeypatch: pytest.MonkeyPatch,
    catalog: str,
    catalog_off: bool,
    without_tags: int,
):
    from catalog_server.store import CATALOG_SCHEMA

    if catalog != "missing":
        with sqlite3.connect(Path(database.DB_PATH).with_name("catalog.db")) as conn:
            _ = conn.executescript(CATALOG_SCHEMA)
            _ = conn.execute("INSERT INTO f95_tag_names (id, name) VALUES (1, '3dcg')")
            _ = conn.executemany(
                "INSERT INTO f95_threads "
                "(thread_id, title, tags, url, first_seen, last_seen) "
                "VALUES (?, 'T', '[1]', 'u', '', '')",
                [(123456,), (323748,)],
            )
    if catalog_off:
        _ = database.update_setting("catalog_enabled", "0")
    monkeypatch.setattr(
        api, "check_all_updates", lambda *_args, **_kwargs: {"success": True}
    )

    reader = _use_reader(api)
    started = api.start_f95_account_read()
    reader.join(5)
    preview = api.get_f95_account_read_status()["preview"]
    assert isinstance(preview, dict)
    assert (preview["without_tags"], preview["catalog_off"]) == (
        without_tags,
        catalog_off,
    )

    result = api.import_f95_account_lists(str(started["read_id"]))

    assert (result["success"], result["added"], result["without_tags"]) == (
        True,
        2,
        without_tags,
    )
    assert [game["f95_tags"] for game in get_all_games()] == [
        "3dcg" if catalog == "full" else ""
    ] * 2


def test_busy_cancel_and_not_running(api: Api):
    gate = threading.Event()
    requested: list[str] = []

    def blocking_fetch(url: str) -> tuple[int, str, str]:
        requested.append(url)
        _ = gate.wait(5)
        return _fixture_fetch(url)

    reader = _use_reader(api, blocking_fetch)
    first = api.start_f95_account_read()
    assert first["success"] is True
    busy = api.start_f95_account_read()
    assert busy["success"] is False and busy["error_code"] == "busy"
    busy_import = api.import_f95_account_lists(str(first["read_id"]))
    assert busy_import["error_code"] == "busy"
    assert api.cancel_f95_account_read()["success"] is True
    gate.set()
    reader.join(5)
    status = api.get_f95_account_read_status()
    assert status["state"] == "cancelled" and status["error_code"] == "cancelled"
    assert requested == [WATCHED_1]
    assert get_all_games() == []
    not_running = api.cancel_f95_account_read()
    assert (
        not_running["success"] is False and not_running["error_code"] == "not_running"
    )


@pytest.mark.parametrize(
    ("page", "code"),
    [
        ((200, "https://f95zone.to/login/", "signed_out.html"), "login_required"),
        ((403, WATCHED_1, "challenge.html"), "blocked"),
    ],
)
def test_read_errors_are_reported(api: Api, page: tuple[int, str, str], code: str):
    reader = _use_reader(api, lambda _url: (page[0], page[1], _page(page[2])))
    started = api.start_f95_account_read()
    reader.join(5)
    status = api.get_f95_account_read_status()
    assert (
        status["success"] is False
        and status["error_code"] == code
        and status["state"] == "error"
    )
    result = api.import_f95_account_lists(str(started["read_id"]))
    assert result["success"] is False and result["error_code"] == "not_found"


def test_transport_errors_are_reported(api: Api):
    def failing(_url: str) -> tuple[int, str, str]:
        raise f95_lists.ListReadError("timeout")

    reader = _use_reader(api, failing)
    _ = api.start_f95_account_read()
    reader.join(5)
    assert api.get_f95_account_read_status()["error_code"] == "timeout"


def test_empty_lists_cannot_be_imported(api: Api):
    reader = _use_reader(api, lambda url: (200, url, _page("past_end.html")))
    started = api.start_f95_account_read()
    reader.join(5)
    status = api.get_f95_account_read_status()
    assert status["state"] == "done"
    assert status["preview"]["total"] == 0  # pyright: ignore[reportIndexIssue]
    result = api.import_f95_account_lists(str(started["read_id"]))
    assert result["success"] is False and result["error_code"] == "empty"


def test_preview_expires_after_two_hours(api: Api):
    now = [1000.0]
    reader = _use_reader(api, clock=lambda: now[0])
    started = api.start_f95_account_read()
    reader.join(5)
    now[0] += f95_lists.PREVIEW_TTL_SECONDS + 1
    status = api.get_f95_account_read_status()
    assert status["state"] == "expired" and status["error_code"] == "expired"
    result = api.import_f95_account_lists(str(started["read_id"]))
    assert result["success"] is False and result["error_code"] == "expired"


def test_wrong_read_id_and_database_error(api: Api, monkeypatch: pytest.MonkeyPatch):
    reader = _use_reader(api)
    started = api.start_f95_account_read()
    reader.join(5)
    assert api.import_f95_account_lists("other")["error_code"] == "not_found"

    def broken(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise sqlite3.OperationalError("database is locked")

    real_apply = f95_lists.apply_import
    monkeypatch.setattr("core.f95_lists.apply_import", broken)
    result = api.import_f95_account_lists(str(started["read_id"]))
    assert result["success"] is False and result["error_code"] == "import_failed"
    assert get_all_games() == []

    # The library did not change, so the same preview can be imported again.
    monkeypatch.setattr("core.f95_lists.apply_import", real_apply)
    monkeypatch.setattr(
        api, "check_all_updates", lambda _ids, **_kwargs: {"success": True}
    )
    monkeypatch.setattr(api, "_refresh_synced_library", lambda: None)
    retried = api.import_f95_account_lists(str(started["read_id"]))
    assert retried["success"] is True and retried["added"] == 2
    assert (
        api.import_f95_account_lists(str(started["read_id"]))["error_code"]
        == "not_found"
    )
