# SPDX-License-Identifier: GPL-3.0-or-later
import json
import sqlite3
from collections.abc import Mapping, Sequence
from contextlib import closing
from pathlib import Path

import pytest

from core import f95_lists
from core.database import (
    add_game,
    get_all_games,
    get_connection,
    get_setting,
    get_tag_colors,
    init_db,
    update_game,
    update_setting,
)
from core.f95_lists import ListEntry, ListReadError, ListReadResult

FIXTURES = Path(__file__).parent / "fixtures" / "f95_lists"
WATCHED_URL = "https://f95zone.to/watched/threads?tab=games&unread=0&sort=title&page={}"
BOOKMARKS_URL = (
    "https://f95zone.to/watched/threads?tab=bookmarks&bm=games&sort=title&page={}"
)


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _row(thread_id: int, title: str, prefix: str = "Ren'Py") -> str:
    return (
        f'<div class="structItem structItem--thread is-prefix7 js-threadListItem-{thread_id}">'
        f'<div class="structItem-title" uix-href="/threads/x.{thread_id}/">'
        f'<a href="/forums/games.2/?prefix_id=7" class="labelLink"><span class="label">{prefix}</span></a>'
        f'<a href="/threads/x.{thread_id}/" data-tp-primary="on">{title}</a></div></div>'
    )


def _list_page(
    rows: Sequence[str], watched_total: int = 0, bookmarks_total: int = 0
) -> str:
    """A list page in the fixture shell with the given rows."""
    shell = _fixture("past_end.html")
    start = shell.index('<div class="structItem structItem--note">')
    end = shell.index("</div></div>", start) + len("</div></div>")
    page = shell[:start] + "".join(rows) + shell[end:]
    page = page.replace(
        'Watched games <span class="f95wg-count">1</span>',
        f'Watched games <span class="f95wg-count">{watched_total}</span>',
    )
    return page.replace(
        'Bookmarks <span class="f95wg-count">1</span>',
        f'Bookmarks <span class="f95wg-count">{bookmarks_total}</span>',
    )


class FakeFetch:
    def __init__(self, pages: Mapping[str, str | tuple[int, str, str]]) -> None:
        self.pages = dict(pages)
        self.requested: list[str] = []

    def __call__(self, url: str) -> tuple[int, str, str]:
        self.requested.append(url)
        page = self.pages.get(url, _fixture("past_end.html"))
        if isinstance(page, tuple):
            return page
        return 200, url, page


# --- fixtures and parser --------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "watched_page1.html",
        "bookmarks_page1.html",
        "past_end.html",
        "signed_out.html",
        "challenge.html",
    ],
)
def test_fixtures_load_without_private_values(name: str):
    html = _fixture(name)
    assert html.strip().startswith("<!DOCTYPE html>")
    assert 'name="_xfToken" value=""' in html or "_xfToken" not in html
    assert "avatar--default" in html or "avatar" not in html


def test_watched_row_parsed():
    rows = f95_lists.parse_rows(_fixture("watched_page1.html"))
    assert len(rows) == 1
    row = rows[0]
    assert row.thread_id == "323748"
    assert row.title == "Natsu-nee ~The 30 Days I Fell for Her~ [v1.1] [Sin Gularity]"
    assert row.prefix_ids == [7, 13]
    assert row.prefix_names == ["Ren'Py", "VN"]
    assert row.list_version == "v1.1"


def test_bookmark_row_parsed_without_version():
    rows = f95_lists.parse_rows(_fixture("bookmarks_page1.html"))
    assert [
        (row.thread_id, row.title, row.prefix_names, row.list_version) for row in rows
    ] == [("123456", "Another Game & Friends [Ch. 3] [Studio X]", ["RPGM"], "")]


def test_sidebar_and_menu_thread_links_are_ignored():
    html = _fixture("watched_page1.html")
    assert "999999" in html and "menu-link.111" in html
    assert [row.thread_id for row in f95_lists.parse_rows(html)] == ["323748"]
    assert f95_lists.parse_rows(_fixture("past_end.html")) == []


def test_row_id_fallbacks_and_label_links_before_thread_link():
    no_class_id = (
        '<div class="structItem structItem--thread">'
        '<div class="structItem-title" uix-href="/threads/fallback.4242/">'
        '<a href="/forums/games.2/?prefix_id=7" class="labelLink"><span class="label">Unity</span></a>'
        '<a href="/threads/fallback.4242/">Fallback Game [v2] [Dev]</a></div></div>'
    )
    primary_only = (
        '<div class="structItem structItem--thread">'
        '<div class="structItem-title">'
        '<a href="/forums/games.2/?prefix_id=4" class="labelLink"><span class="label">Completed</span></a>'
        '<a href="/threads/primary.5151/" data-tp-primary="on">Primary Game [v1.0] [Studio]</a></div></div>'
    )
    rows = f95_lists.parse_rows(_list_page([no_class_id, primary_only]))
    assert [(row.thread_id, row.title, row.prefix_names) for row in rows] == [
        ("4242", "Fallback Game [v2] [Dev]", ["Unity"]),
        ("5151", "Primary Game [v1.0] [Studio]", ["Completed"]),
    ]


# --- classifier and counts ------------------------------------------------


def test_page_classifier():
    assert (
        f95_lists.classify_page(
            200, WATCHED_URL.format(1), _fixture("watched_page1.html")
        )
        is None
    )
    assert (
        f95_lists.classify_page(200, BOOKMARKS_URL.format(1), _fixture("past_end.html"))
        is None
    )
    assert (
        f95_lists.classify_page(
            200, "https://f95zone.to/login/", _fixture("signed_out.html")
        )
        == "login_required"
    )
    assert (
        f95_lists.classify_page(
            200, "https://f95zone.to/login/?redirect=x", _fixture("watched_page1.html")
        )
        == "login_required"
    )
    signed_out_list = _fixture("watched_page1.html").replace(
        'data-logged-in="true"', 'data-logged-in="false"'
    )
    assert (
        f95_lists.classify_page(200, WATCHED_URL.format(1), signed_out_list)
        == "login_required"
    )
    assert (
        f95_lists.classify_page(403, WATCHED_URL.format(1), _fixture("challenge.html"))
        == "blocked"
    )
    other = _fixture("watched_page1.html").replace(
        'data-template="f95wg_watched"', 'data-template="forum_view"', 1
    )
    assert f95_lists.classify_page(200, WATCHED_URL.format(1), other) == "not_a_list"
    assert (
        f95_lists.classify_page(
            200, WATCHED_URL.format(1), '<html data-logged-in="true"></html>'
        )
        == "not_a_list"
    )


def test_tab_counts():
    assert f95_lists.read_tab_counts(_fixture("watched_page1.html")) == {
        "watched": 1,
        "bookmarks": 1,
    }
    assert f95_lists.read_tab_counts(_list_page([], 1234, 7)) == {
        "watched": 1234,
        "bookmarks": 7,
    }
    assert f95_lists.read_tab_counts("<html></html>") == {
        "watched": None,
        "bookmarks": None,
    }


# --- title split ----------------------------------------------------------


def test_title_split_and_engine_from_prefixes():
    entry = f95_lists.entry_from_row(
        f95_lists.parse_rows(_fixture("watched_page1.html"))[0], "watched"
    )
    assert (entry.title, entry.version, entry.developer, entry.engine) == (
        "Natsu-nee ~The 30 Days I Fell for Her~",
        "1.1",
        "Sin Gularity",
        "Ren'Py",
    )
    bookmark = f95_lists.entry_from_row(
        f95_lists.parse_rows(_fixture("bookmarks_page1.html"))[0], "bookmarks"
    )
    assert (bookmark.title, bookmark.version, bookmark.developer, bookmark.engine) == (
        "Another Game & Friends",
        "Ch. 3",
        "Studio X",
        "RPGM",
    )


def test_title_without_brackets_uses_list_version():
    row = f95_lists.ListRow("1", "Plain Title", [1], ["VN", "Others"], "v0.5")
    entry = f95_lists.entry_from_row(row, "watched")
    assert (entry.title, entry.version, entry.developer, entry.engine) == (
        "Plain Title",
        "0.5",
        "",
        "Others",
    )
    bare = f95_lists.entry_from_row(f95_lists.ListRow("2", "Plain Title"), "bookmarks")
    assert (bare.title, bare.version, bare.developer, bare.engine) == (
        "Plain Title",
        "",
        "",
        "",
    )


# --- the walk -------------------------------------------------------------


def test_walk_reads_every_page_and_merges_lists():
    watched_1 = [_row(i, f"Game {i} [v1] [Dev]") for i in range(1, 21)]
    watched_2 = [_row(i, f"Game {i} [v1] [Dev]") for i in range(21, 26)]
    bookmarks_1 = [_row(i, f"Game {i} [v1] [Dev]") for i in (5, 100, 101)]
    fetch = FakeFetch(
        {
            WATCHED_URL.format(1): _list_page(watched_1, 25, 3),
            WATCHED_URL.format(2): _list_page(watched_2, 25, 3),
            BOOKMARKS_URL.format(1): _list_page(bookmarks_1, 25, 3),
        }
    )
    progress: list[dict[str, object]] = []
    result = f95_lists.read_lists(
        fetch, progress=lambda item: progress.append(dict(item))
    )
    assert fetch.requested == [
        WATCHED_URL.format(1),
        WATCHED_URL.format(2),
        WATCHED_URL.format(3),
        BOOKMARKS_URL.format(1),
        BOOKMARKS_URL.format(2),
    ]
    assert result.counts == {"watched": 25, "bookmarks": 3}
    assert len(result.entries) == 27
    by_id = {entry.thread_id: entry for entry in result.entries}
    assert by_id["5"].lists == ["watched", "bookmarks"]
    assert by_id["100"].lists == ["bookmarks"]
    assert progress[-1] == {
        "list": "bookmarks",
        "list_label": "Bookmarks",
        "page": 2,
        "found": 28,
        "total": 28,
    }
    assert {
        "list": "watched",
        "list_label": "Watched games",
        "page": 2,
        "found": 20,
        "total": 28,
    } in progress


def test_walk_stops_on_a_clamped_repeat_page():
    page = _list_page([_row(1, "One"), _row(2, "Two")])
    fetch = FakeFetch({WATCHED_URL.format(n): page for n in range(1, 6)})
    result = f95_lists.read_lists(fetch)
    assert fetch.requested[:2] == [WATCHED_URL.format(1), WATCHED_URL.format(2)]
    assert WATCHED_URL.format(3) not in fetch.requested
    assert result.counts["watched"] == 2


def test_walk_stops_after_page_200():
    class EndlessFetch:
        def __init__(self) -> None:
            self.requested: list[str] = []

        def __call__(self, url: str) -> tuple[int, str, str]:
            self.requested.append(url)
            if "bm=games" in url:
                return 200, url, _fixture("past_end.html")
            return 200, url, _list_page([_row(len(self.requested), "Game")])

    fetch = EndlessFetch()
    result = f95_lists.read_lists(fetch)
    assert fetch.requested[199] == WATCHED_URL.format(200)
    assert fetch.requested[200] == BOOKMARKS_URL.format(1)
    assert result.counts["watched"] == 200


def test_walk_stops_on_login_page_mid_walk_and_nothing_is_applied():
    init_db()
    fetch = FakeFetch(
        {
            WATCHED_URL.format(1): _list_page([_row(1, "One")]),
            WATCHED_URL.format(2): (
                200,
                "https://f95zone.to/login/",
                _fixture("signed_out.html"),
            ),
        }
    )
    with pytest.raises(ListReadError) as excinfo:
        _ = f95_lists.read_lists(fetch)
    assert excinfo.value.code == "login_required"
    assert BOOKMARKS_URL.format(1) not in fetch.requested
    assert get_all_games() == []


def test_walk_cancel_sends_no_further_request():
    calls = {"n": 0}
    fetch = FakeFetch(
        {WATCHED_URL.format(n): _list_page([_row(n, f"G{n}")]) for n in range(1, 10)}
    )

    def cancelled() -> bool:
        calls["n"] += 1
        return len(fetch.requested) >= 2

    with pytest.raises(ListReadError) as excinfo:
        _ = f95_lists.read_lists(fetch, cancelled=cancelled)
    assert excinfo.value.code == "cancelled"
    assert fetch.requested == [WATCHED_URL.format(1), WATCHED_URL.format(2)]


def test_extension_fetch_reports_missing_and_outdated_extension(monkeypatch):
    from core import extension_bridge

    extension_bridge.reset_for_tests()
    with pytest.raises(ListReadError) as excinfo:
        _ = f95_lists.fetch_page_via_extension(WATCHED_URL.format(1))
    assert excinfo.value.code == "no_extension"
    conn = extension_bridge.BridgeConnection(send_text=lambda _text: None)
    extension_bridge._register_connection(conn)  # pyright: ignore[reportPrivateUsage]
    extension_bridge._handle_message(
        conn, {"type": "hello", "protocol": 2, "can_fetch": True}
    )  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(ListReadError) as excinfo:
        _ = f95_lists.fetch_page_via_extension(WATCHED_URL.format(1))
    assert excinfo.value.code == "extension_outdated"
    extension_bridge.reset_for_tests()


# --- preview and apply ----------------------------------------------------


def _entry(thread_id: int, lists: Sequence[str], title: str = "") -> ListEntry:
    return ListEntry(
        thread_id=str(thread_id),
        raw_title=title or f"Game {thread_id} [v1] [Dev]",
        title=title or f"Game {thread_id}",
        version="1",
        developer="Dev",
        engine="Ren'Py",
        prefixes=["Ren'Py"],
        lists=list(lists),  # pyright: ignore[reportArgumentType]
    )


def _no_catalog(_ids: Sequence[str]) -> dict[str, dict[str, object]]:
    return {}


def _game(game_id: int) -> dict[str, object]:
    return next(game for game in get_all_games() if game["id"] == game_id)


def test_preview_counts_games_on_both_lists_once():
    init_db()
    for thread_id in range(1, 6):
        _ = add_game(
            f"Old {thread_id}", "", f"https://f95zone.to/threads/old-name.{thread_id}/"
        )
    entries = [_entry(i, ["watched"]) for i in range(1, 9)]
    entries += [
        _entry(9, ["watched", "bookmarks"]),
        _entry(10, ["watched", "bookmarks"]),
    ]
    entries += [_entry(11, ["bookmarks"]), _entry(12, ["bookmarks"])]
    preview = f95_lists.build_preview(
        ListReadResult(entries, {"watched": 10, "bookmarks": 4})
    )
    assert preview["lists"] == {
        "watched": {"found": 10, "new": 5, "existing": 5},
        "bookmarks": {"found": 4, "new": 4, "existing": 0},
    }
    assert (preview["total"], preview["new"], preview["existing"]) == (12, 7, 5)
    assert preview["without_tags"] == 7
    assert preview["catalog_off"] is False


@pytest.mark.parametrize(
    ("missing_tags", "without_tags"), [("", 1), (" , ", 1), ("rpg", 0)]
)
def test_preview_looks_up_thread_tags_for_new_games_only(missing_tags, without_tags):
    init_db()
    assert add_game("Old", "", "https://f95zone.to/threads/1/") is not None
    entries = [
        _entry(1, ["watched"]),
        _entry(2, ["bookmarks"]),
        _entry(3, ["watched", "bookmarks"]),
    ]

    def lookup(ids: Sequence[str]) -> dict[str, dict[str, object]]:
        assert list(ids) == ["2", "3"]
        return {"2": {"tags": "3dcg"}, "3": {"tags": missing_tags}}

    preview = f95_lists.build_preview(
        ListReadResult(entries, {}), catalog_lookup=lookup
    )
    assert preview["without_tags"] == without_tags
    assert preview["catalog_off"] is False


def test_apply_adds_wishlisted_games_and_tags_existing_ones_only():
    init_db()
    playing = add_game(
        "Installed",
        "/games/installed/run.sh",
        "https://f95zone.to/threads/old-slug.323748/",
        version="0.9",
        tags="3dcg, sandbox",
    )
    assert playing is not None
    _ = update_game(playing, {"play_status": "Playing"})
    before = _game(playing)
    entries = [
        _entry(323748, ["watched"], "Natsu-nee"),
        _entry(200, ["watched", "bookmarks"]),
        _entry(300, ["bookmarks"]),
    ]
    catalog = {
        "200": {
            "title": "Catalog Title",
            "version": "2.0",
            "developer": "Cat Dev",
            "engine": "Unity",
            "tags": "rpg, sandbox",
            "coverImage": "https://x/c.png",
        }
    }
    result = f95_lists.apply_import(entries, catalog_lookup=lambda _ids: catalog)
    assert (result["added"], result["tagged"]) == (2, 1)
    assert result["without_tags"] == 1
    after = _game(playing)
    assert after["tags"] == "3dcg, sandbox, Watched on F95"
    for key in (
        "title",
        "version",
        "exe_path",
        "play_status",
        "f95_url",
        "developer",
        "engine",
    ):
        assert after[key] == before[key]
    added = {game["f95_url"]: game for game in get_all_games() if game["id"] != playing}
    catalog_game = added["https://f95zone.to/threads/200/"]
    assert (
        catalog_game["title"],
        catalog_game["version"],
        catalog_game["developer"],
        catalog_game["engine"],
    ) == ("Catalog Title", "2.0", "Cat Dev", "Unity")
    assert catalog_game["exe_path"] == ""
    assert catalog_game["tags"] == "rpg, sandbox, Watched on F95, Bookmarked on F95"
    assert catalog_game["f95_tags"] == "rpg, sandbox"
    row_game = added["https://f95zone.to/threads/300/"]
    assert (row_game["title"], row_game["version"], row_game["developer"]) == (
        "Game 300",
        "1",
        "Dev",
    )
    assert row_game["tags"] == "Bookmarked on F95"
    from core.database import DEFAULT_PLAY_STATUS

    assert row_game["play_status"] == DEFAULT_PLAY_STATUS
    assert get_tag_colors() == {
        "watched on f95": "orange",
        "bookmarked on f95": "purple",
    }


def test_second_import_adds_no_duplicate_tags():
    init_db()
    entries = [_entry(1, ["watched", "bookmarks"])]
    first = f95_lists.apply_import(entries, catalog_lookup=_no_catalog)
    assert first["added"] == 1
    assert first["without_tags"] == 1
    second = f95_lists.apply_import(entries, catalog_lookup=_no_catalog)
    assert (second["added"], second["tagged"]) == (0, 0)
    assert second["without_tags"] == 0
    games = get_all_games()
    assert len(games) == 1
    assert games[0]["tags"] == "Watched on F95, Bookmarked on F95"


def test_failed_import_leaves_library_unchanged(monkeypatch):
    init_db()
    existing = add_game("Existing", "", "https://f95zone.to/threads/1/", tags="rpg")
    assert existing is not None
    from core import database

    real_insert = database.insert_wishlist_game

    def failing_insert(cursor: sqlite3.Cursor, **kwargs: object) -> int:
        if kwargs.get("f95_url") == "https://f95zone.to/threads/3/":
            raise sqlite3.OperationalError("disk I/O error")
        return real_insert(cursor, **kwargs)  # pyright: ignore[reportArgumentType]

    monkeypatch.setattr(database, "insert_wishlist_game", failing_insert)
    entries = [_entry(1, ["watched"]), _entry(2, ["watched"]), _entry(3, ["bookmarks"])]
    with pytest.raises(sqlite3.OperationalError):
        _ = f95_lists.apply_import(entries, catalog_lookup=_no_catalog)
    games = get_all_games()
    assert [(game["title"], game["tags"]) for game in games] == [("Existing", "rpg")]
    assert get_setting("tag_colors") in (None, "{}")
    assert get_setting(f95_lists.LIST_TAG_COLORS_SEEDED_KEY) is None


# --- default colors -------------------------------------------------------


def test_colors_seed_once_and_keep_user_choices():
    from core.api import Api

    init_db()
    api = Api()
    _ = api.set_tag_color("Watched on F95", "teal")
    _ = f95_lists.apply_import(
        [_entry(1, ["watched", "bookmarks"])], catalog_lookup=_no_catalog
    )
    assert get_tag_colors() == {"watched on f95": "teal", "bookmarked on f95": "purple"}
    assert get_setting(f95_lists.LIST_TAG_COLORS_SEEDED_KEY) == "true"
    _ = api.set_tag_color("Bookmarked on F95", None)
    _ = f95_lists.apply_import([_entry(2, ["bookmarks"])], catalog_lookup=_no_catalog)
    assert get_tag_colors() == {"watched on f95": "teal"}


def test_seed_marker_survives_json_export_and_import(tmp_path: Path):
    from core.api import Api
    from core.library_backup import SECTION_SETTINGS_GENERAL

    init_db()
    api = Api()
    _ = f95_lists.apply_import([_entry(1, ["watched"])], catalog_lookup=_no_catalog)
    destination = tmp_path / "backup.json"
    assert api.export_library_backup(
        {"sections": [SECTION_SETTINGS_GENERAL]}, str(destination)
    )["success"]
    backup = json.loads(destination.read_text())
    assert backup["settings"]["general"][f95_lists.LIST_TAG_COLORS_SEEDED_KEY] == "true"
    with closing(get_connection()) as conn:
        _ = conn.execute(
            "DELETE FROM settings WHERE key IN (?, 'tag_colors')",
            (f95_lists.LIST_TAG_COLORS_SEEDED_KEY,),
        )
        conn.commit()
    assert api.import_library_backup(
        str(destination), {"sections": [SECTION_SETTINGS_GENERAL]}
    )["success"]
    assert get_setting(f95_lists.LIST_TAG_COLORS_SEEDED_KEY) == "true"
    # The marker came back, so a second device does not seed a cleared color again.
    update_setting("tag_colors", "{}")
    _ = f95_lists.apply_import([_entry(2, ["bookmarks"])], catalog_lookup=_no_catalog)
    assert get_tag_colors() == {}
