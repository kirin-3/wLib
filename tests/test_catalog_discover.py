# SPDX-License-Identifier: GPL-3.0-or-later
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import cast

import pytest

from discover_catalog import make_catalog
from core import catalog_discover
from core.catalog_discover import (
    INDEX_FORMAT,
    build_index,
    card_cover_url,
    index_is_current,
    normalize_text,
    tag_key,
    weighted_rating,
)


def _games(index: Path) -> dict[str, dict[str, object]]:
    with closing(sqlite3.connect(index)) as conn:
        conn.row_factory = sqlite3.Row
        return {
            str(row["key"]): {key: cast(object, row[key]) for key in row.keys()}
            for row in cast(
                list[sqlite3.Row], conn.execute("SELECT * FROM games").fetchall()
            )
        }


@pytest.fixture
def index(tmp_path: Path) -> Path:
    target = tmp_path / "discover-index.db"
    _ = build_index(make_catalog(tmp_path), target, catalog_sha256="abc")
    return target


def test_one_card_per_group_without_appx_or_removed(index: Path) -> None:
    games = _games(index)
    assert sorted(games) == [
        "dlsite:RJ01234567",
        "f95:100",
        "f95:200",
        "lc:400",
        "ryuugames:5",
        "vndb:v88",
    ]
    card = games["f95:100"]
    bits = catalog_discover.SITE_BITS
    assert card["sites"] == bits["f95"] | bits["lc"]
    assert (card["f95_id"], card["lc_id"], card["lc_tier"]) == (100, 300, "Premium")


def test_card_fields_follow_the_site_order(index: Path) -> None:
    games = _games(index)
    assert games["f95:100"]["title"] == "Echo Park"
    assert games["f95:100"]["developer"] == "EchoDev"
    # DLsite English title and circle win over VNDB.
    assert games["dlsite:RJ01234567"]["title"] == "English Title"
    assert games["dlsite:RJ01234567"]["developer"] == "Circle EN"
    # LC thread page developer wins over the feed creator.
    assert games["lc:400"]["developer"] == "LC Studio"


def test_version_is_the_newest_thread_version(index: Path) -> None:
    games = _games(index)
    assert (games["f95:100"]["version"], games["f95:100"]["version_site"]) == (
        "v0.6",
        "lc",
    )
    # A DLsite product code is never a version.
    assert games["ryuugames:5"]["version"] == ""


def test_covers_use_the_preview_host_and_never_lewdcorner(index: Path) -> None:
    games = _games(index)
    assert games["f95:100"]["cover_url"] == "https://preview.f95zone.to/2023/01/a.jpg"
    assert games["lc:400"]["cover_url"] == ""
    assert games["dlsite:RJ01234567"]["cover_url"] == "https://img.dlsite.jp/a.jpg"
    assert games["f95:200"]["cover_url"] == ""


def test_tags_merge_f95_names_and_lc_slugs(index: Path) -> None:
    games = _games(index)
    assert games["f95:100"]["tags"] == "|bigtits|lconlytag|sandbox|"
    assert games["lc:400"]["tags"] == "|bigtits|"
    assert games["dlsite:RJ01234567"]["tags"] == ""
    with closing(sqlite3.connect(index)) as conn:
        tags = cast(
            list[tuple[str, str, int]],
            conn.execute("SELECT key, name, count FROM tags ORDER BY key").fetchall(),
        )
    assert tags == [
        ("bigtits", "big tits", 2),
        ("lconlytag", "lc only tag", 1),
        ("sandbox", "sandbox", 1),
    ]


def test_status_and_engine_come_from_f95_then_lc(index: Path) -> None:
    games = _games(index)
    assert (games["f95:100"]["status"], games["f95:100"]["engine"]) == (
        "completed",
        "Ren'Py",
    )
    assert (games["f95:200"]["status"], games["f95:200"]["engine"]) == (
        "ongoing",
        "Unity",
    )
    assert (games["lc:400"]["status"], games["lc:400"]["engine"]) == (
        "abandoned",
        "RPGM",
    )
    assert games["dlsite:RJ01234567"]["status"] == ""


def test_weighted_rating_and_dates(index: Path) -> None:
    games = _games(index)
    assert games["f95:100"]["rating_count"] == 1200
    assert games["f95:100"]["wrating"] == pytest.approx(4.571428571428571)
    assert games["f95:200"]["wrating"] is None
    assert games["f95:100"]["updated_ts"] == 1710000000
    assert games["lc:400"]["released_at"] == "2022-03-04"
    assert games["dlsite:RJ01234567"]["released_at"] == "2021-05-06"
    assert games["ryuugames:5"]["released_at"] == "2019-02-03"
    # A planned release is no update and no release yet.
    assert (games["vndb:v88"]["updated_ts"], games["vndb:v88"]["released_at"]) == (
        0,
        "",
    )


def test_search_text_holds_titles_aliases_and_developers(index: Path) -> None:
    text = str(_games(index)["f95:100"]["search_text"])
    for word in ("echo park", "エコーパーク", "echo town", "echodev", "echo park lc"):
        assert word in text.split("\n")
    dlsite = str(_games(index)["dlsite:RJ01234567"]["search_text"]).split("\n")
    for word in ("english title", "日本語タイトル", "circle en", "サークル", "原題"):
        assert word in dlsite


def test_entry_ids_hold_entries_and_stated_store_ids(index: Path) -> None:
    with closing(sqlite3.connect(index)) as conn:
        rows = set(
            cast(
                list[tuple[str, str, str]],
                conn.execute("SELECT kind, value, key FROM entry_ids").fetchall(),
            )
        )
    assert {
        ("f95", "100", "f95:100"),
        ("lc", "300", "f95:100"),
        ("steam", "943700", "f95:100"),
        ("dlsite", "RJ01234567", "f95:100"),
        ("dlsite", "RJ01234567", "dlsite:RJ01234567"),
        ("vndb", "v77", "dlsite:RJ01234567"),
        ("steam", "111", "ryuugames:5"),
    } <= rows


def test_missing_groups_give_one_card_per_entry(tmp_path: Path) -> None:
    target = tmp_path / "discover-index.db"
    result = build_index(make_catalog(tmp_path, groups=False), target)
    games = _games(target)
    assert result.games == result.entries == 8
    assert "lc:300" in games
    assert games["vndb:v77"]["title"] == "VN Title"


def test_index_meta_and_current_check(index: Path) -> None:
    assert index_is_current(index, "abc")
    assert not index_is_current(index, "other")
    assert not index_is_current(index.with_name("missing.db"), "abc")
    with closing(sqlite3.connect(index)) as conn, conn:
        _ = conn.execute(
            "UPDATE meta SET value = ? WHERE key = 'format'", (str(INDEX_FORMAT + 1),)
        )
    assert not index_is_current(index, "abc")


def test_failed_build_keeps_the_old_index(index: Path, tmp_path: Path) -> None:
    before = index.read_bytes()
    broken = tmp_path / "broken.db"
    _ = broken.write_bytes(b"not a database")
    with pytest.raises(sqlite3.DatabaseError):
        _ = build_index(broken, index, catalog_sha256="new")
    assert index.read_bytes() == before
    assert [path.name for path in tmp_path.glob("discover-index.db*")] == [
        "discover-index.db"
    ]


def test_weighted_rating_matches_the_shared_vector() -> None:
    fixture = Path(__file__).parent / "fixtures" / "weighted_rating_cases.json"
    cases = cast(
        list[list[float | None]],
        cast(dict[str, object], json.loads(fixture.read_text(encoding="utf-8")))[
            "cases"
        ],
    )
    for rating, count, expected in cases:
        score = weighted_rating(rating, int(count) if count is not None else None)
        if expected is None:
            assert score is None, (rating, count)
        else:
            assert score == pytest.approx(expected), (rating, count)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Ren'Py Café!", "renpy cafe"),
        ("  BIG-tits ", "big tits"),
        ("ガールズ・ゲーム", "ガールズ ゲーム"),
        ("Ｆｕｌｌ　Ｗｉｄｔｈ", "full width"),
    ],
)
def test_normalize_text(value: str, expected: str) -> None:
    assert normalize_text(value) == expected


def test_tag_key_and_cover_rules() -> None:
    assert tag_key("big tits") == tag_key("big-tits") == "bigtits"
    assert card_cover_url("lc", "https://cdn.lewdcorner.com/a.jpg") == ""
    assert card_cover_url("f95", "javascript:alert(1)") == ""
    assert (
        card_cover_url("f95", "https://preview.f95zone.to/x.jpg")
        == "https://preview.f95zone.to/x.jpg"
    )


def test_index_lifecycle_builds_only_when_stale(tmp_path: Path) -> None:
    catalog = make_catalog(tmp_path)
    data = tmp_path / "data"
    data.mkdir()
    _ = (data / "discover-index.db.tmp-123").write_bytes(b"partial")
    enabled = [True]
    manager = catalog_discover.DiscoverIndex(str(data), enabled=lambda: enabled[0])
    assert not (data / "discover-index.db.tmp-123").exists()
    assert not manager.ready()
    assert manager.ensure(catalog, "sha-1") is True
    assert manager.ready() and index_is_current(manager.path, "sha-1")
    # The same catalog: no second build.
    assert manager.ensure(catalog, "sha-1") is False
    # A new catalog snapshot: built again.
    assert manager.ensure(catalog, "sha-2") is True
    assert index_is_current(manager.path, "sha-2")
    # No installed catalog: the index goes.
    assert manager.ensure(catalog, "") is False
    assert not manager.path.exists()


def test_index_lifecycle_with_the_catalog_turned_off(tmp_path: Path) -> None:
    catalog = make_catalog(tmp_path)
    enabled = [True]
    manager = catalog_discover.DiscoverIndex(str(tmp_path), enabled=lambda: enabled[0])
    assert manager.ensure(catalog, "sha") is True
    manager.remove()
    assert not manager.path.exists()
    enabled[0] = False
    assert manager.ensure(catalog, "sha") is False
    assert not manager.path.exists()


def test_index_turned_off_during_a_build_is_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = make_catalog(tmp_path)
    enabled = [True]
    manager = catalog_discover.DiscoverIndex(str(tmp_path), enabled=lambda: enabled[0])
    real = catalog_discover.build_index

    def build_then_turn_off(
        *args: object, **kwargs: str
    ) -> catalog_discover.BuildResult:
        result = real(*args, **kwargs)  # pyright: ignore[reportArgumentType]
        enabled[0] = False
        return result

    monkeypatch.setattr(catalog_discover, "build_index", build_then_turn_off)
    assert manager.ensure(catalog, "sha") is False
    assert not manager.path.exists()


def test_failed_build_reports_an_error(tmp_path: Path) -> None:
    broken = tmp_path / "broken.db"
    _ = broken.write_bytes(b"not a database")
    manager = catalog_discover.DiscoverIndex(str(tmp_path), enabled=lambda: True)
    assert manager.ensure(broken, "sha") is False
    assert manager.error and not manager.ready()


def _keys(result: dict[str, object]) -> list[str]:
    return [str(game["key"]) for game in cast(list[dict[str, object]], result["games"])]


def _search(index: Path, **params: object) -> dict[str, object]:
    return catalog_discover.search(index, catalog_discover.parse_query(params))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("エコーパーク", ["f95:100"]),  # original title, card title is English
        ("echodev", ["f95:100"]),  # developer
        ("circle en", ["dlsite:RJ01234567"]),  # DLsite English circle
        ("echo town", ["f95:100"]),  # alias
        ("ECHO   park!", ["f95:100"]),  # case and punctuation
        ("echo missing", []),  # every word is needed
    ],
)
def test_search_words(index: Path, text: str, expected: list[str]) -> None:
    assert _keys(_search(index, search=text)) == expected


def test_search_ignores_accents(tmp_path: Path) -> None:
    catalog = make_catalog(tmp_path)
    with closing(sqlite3.connect(catalog)) as conn, conn:
        _ = conn.execute(
            "UPDATE f95_threads SET title = 'Café Night' WHERE thread_id = 200"
        )
    target = tmp_path / "index.db"
    _ = build_index(catalog, target)
    assert _keys(_search(target, search="cafe")) == ["f95:200"]


def test_source_include_and_exclude(index: Path) -> None:
    assert sorted(_keys(_search(index, include_sites=["f95"]))) == [
        "f95:100",
        "f95:200",
    ]
    # Excluding VNDB hides a VNDB-only game and keeps one also on DLsite.
    excluded = _keys(_search(index, exclude_sites=["vndb"]))
    assert "vndb:v88" not in excluded and "dlsite:RJ01234567" in excluded
    assert len(excluded) == 5


def test_tag_filters(index: Path) -> None:
    # The LC slug and the F95 name are one tag; DLsite-only games have no tags.
    assert sorted(_keys(_search(index, include_tags=["big tits"]))) == [
        "f95:100",
        "lc:400",
    ]
    assert _keys(_search(index, include_tags=["big tits", "sandbox"])) == ["f95:100"]
    without = _keys(_search(index, exclude_tags=["big-tits"]))
    assert "f95:100" not in without and "dlsite:RJ01234567" in without


def test_status_engine_and_rating_filters(index: Path) -> None:
    assert _keys(_search(index, statuses=["completed"])) == ["f95:100"]
    assert sorted(_keys(_search(index, statuses=["ongoing", "abandoned"]))) == [
        "f95:200",
        "lc:400",
    ]
    assert _keys(_search(index, engines=["RPGM"])) == ["lc:400"]
    assert _keys(_search(index, min_rating=4.5)) == ["f95:100"]
    assert _keys(_search(index, min_rating=4.0)) == ["f95:100"]
    # Only the rating steps count.
    assert catalog_discover.parse_query({"min_rating": 1.0}).min_rating is None


def test_sorts_with_title_tiebreak(index: Path) -> None:
    assert _keys(_search(index)) == [
        "f95:100",  # 2024-03-09 (LC thread)
        "lc:400",  # 2022-04-29
        "dlsite:RJ01234567",  # 2021-05-06 (DLsite release)
        "f95:200",  # 2020-09-13
        "ryuugames:5",  # 2019-02-03 (post date)
        "vndb:v88",  # no past date
    ]
    assert _keys(_search(index, sort="rating"))[0] == "f95:100"
    # Games without a rating are ordered by title.
    assert _keys(_search(index, sort="rating"))[1:] == [
        "dlsite:RJ01234567",
        "lc:400",
        "vndb:v88",
        "ryuugames:5",
        "f95:200",
    ]
    assert _keys(_search(index, sort="released")) == [
        "lc:400",
        "dlsite:RJ01234567",
        "ryuugames:5",
        "f95:100",
        "vndb:v88",
        "f95:200",
    ]
    assert _keys(_search(index, sort="title")) == [
        "f95:100",
        "dlsite:RJ01234567",
        "lc:400",
        "vndb:v88",
        "ryuugames:5",
        "f95:200",
    ]


def test_paging(index: Path) -> None:
    first = _search(index, limit=4)
    assert first["total"] == 6 and first["next_offset"] == 4
    second = _search(index, limit=4, offset=4)
    assert len(_keys(second)) == 2 and second["next_offset"] is None
    assert catalog_discover.parse_query({"limit": 5000}).limit == 120


def test_parse_query_drops_unknown_values() -> None:
    query = catalog_discover.parse_query(
        {
            "include_sites": ["f95", "nope", 3],
            "statuses": ["completed", "done"],
            "sort": "random",
            "offset": -5,
            "hide_library": "yes",
        }
    )
    assert query.include_sites == ["f95"]
    assert query.statuses == ["completed"]
    assert (query.sort, query.offset, query.hide_library) == ("updated", 0, False)
    assert catalog_discover.parse_query("junk") == catalog_discover.DiscoverQuery()


def _library(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_path / "library.db")
    _ = conn.executescript(
        """
        CREATE TABLE games (id INTEGER PRIMARY KEY, title TEXT, f95_url TEXT,
          lc_url TEXT, exe_path TEXT NOT NULL);
        CREATE TABLE game_links (game_id INTEGER, source TEXT, external_id TEXT);
        INSERT INTO games VALUES
          (1, 'Wish', 'https://f95zone.to/threads/echo-park.100/', NULL, ''),
          (2, 'Store game', NULL, NULL, '/games/a/game.exe'),
          (3, 'LC game', NULL, 'https://lewdcorner.com/threads/only.400/', '/x.exe');
        INSERT INTO game_links VALUES (2, 'dlsite', 'RJ01234567'), (2, 'other', 'x');
        """
    )
    return conn


def test_library_markers_and_hide_my_games(index: Path, tmp_path: Path) -> None:
    with closing(_library(tmp_path)) as library:
        identities = catalog_discover.library_identities(library)
    result = catalog_discover.search(
        index, catalog_discover.parse_query({}), identities
    )
    marks = {
        str(game["key"]): game["library"]
        for game in cast(list[dict[str, object]], result["games"])
    }
    # The F95 thread is wishlisted, but the group also states RJ01234567,
    # which an installed game links: the installed game wins.
    assert marks["f95:100"] == {"game_id": 2, "state": "installed"}
    assert marks["dlsite:RJ01234567"] == {"game_id": 2, "state": "installed"}
    assert marks["lc:400"] == {"game_id": 3, "state": "installed"}
    assert marks["f95:200"] is None
    hidden = catalog_discover.search(
        index, catalog_discover.parse_query({"hide_library": True}), identities
    )
    assert sorted(_keys(hidden)) == ["f95:200", "ryuugames:5", "vndb:v88"]
    assert hidden["total"] == 3


def test_wishlisted_marker_from_an_f95_thread(index: Path) -> None:
    identities = [("f95", "100", 7, False)]
    card = catalog_discover.game_card(index, "f95:100", identities)
    assert card is not None
    assert card["library"] == {"game_id": 7, "state": "wishlisted"}
    assert card["sites"] == ["f95", "lc"]
    assert card["tags"] == ["big tits", "lc only tag", "sandbox"]
    assert {"kind": "steam", "value": "943700"} in cast(
        list[dict[str, str]], card["entry_ids"]
    )
    assert catalog_discover.game_card(index, "f95:999") is None


def test_filters_list_tags_engines_and_statuses(index: Path) -> None:
    result = catalog_discover.filters(index)
    assert cast(list[dict[str, object]], result["tags"])[0] == {
        "key": "bigtits",
        "name": "big tits",
        "count": 2,
    }
    assert result["engines"] == [
        {"name": "Ren'Py", "count": 1},
        {"name": "RPGM", "count": 1},
        {"name": "Unity", "count": 1},
    ]
    assert result["statuses"] == [
        {"key": "ongoing", "count": 1},
        {"key": "completed", "count": 1},
        {"key": "onhold", "count": 0},
        {"key": "abandoned", "count": 1},
    ]
