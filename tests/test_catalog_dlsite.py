# SPDX-License-Identifier: GPL-3.0-or-later
import json
import re

import pytest

from catalog_fakes import (
    FakeTransport,
    fixture_bytes,
    fixture_json,
    json_response,
    make_context,
    rows,
)
from catalog_server.net import HttpStatusError, Response
from catalog_server.sources.dlsite import (
    DlsiteSource,
    parse_english_listing,
    parse_genre_list,
    parse_listing,
    parse_product_genres,
)


def _en_listing_for(circles: dict[str, str], count: int = 100) -> dict[str, object]:
    items = "".join(
        f'<li data-list_item_product_id="{pid}">'
        f'<dd class="maker_name"><a href="https://www.dlsite.com/maniax/circle/profile/=/maker_id/RG1.html">{circle}</a></dd>'
        f"</li>"
        for pid, circle in circles.items()
    )
    return {
        "search_result": f'<div id="search_result_list"><ul>{items}</ul></div>',
        "page_info": {"count": count, "first_indice": 1, "last_indice": count},
    }


def _jp_listing_for(
    entries: list[tuple[str, str, str, list[tuple[str, str]]]],
    *,
    site: str = "maniax",
    count: int | None = None,
) -> dict[str, object]:
    rows_html = []
    for pid, circle_id, circle, tags in entries:
        extra = "work_category/pc/" if site == "pro" else ""
        links = "".join(
            f'<a href="https://www.dlsite.com/{site}/fsr/=/genre/{gid}/{extra}from/work.genre">{name}</a>'
            for gid, name in tags
        )
        rows_html.append(
            f'<tr data-list_item_product_id="{pid}">'
            f'<dd class="maker_name"><a href="https://www.dlsite.com/{site}/circle/profile/=/maker_id/{circle_id}.html">{circle}</a></dd>'
            f'<dd class="search_tag">{links}</dd></tr>'
        )
    total = len(entries) if count is None else count
    return {
        "search_result": f"<table>{''.join(rows_html)}</table>",
        "page_info": {"count": total, "first_indice": 1, "last_indice": total},
    }


def _transport(
    listing=None,
    *,
    en_listing=None,
    details=None,
    en_details=None,
    genre_list_html=None,
    product_json=None,
):
    transport = FakeTransport()
    transport.route(
        lambda r: "/product/info/ajax" in r.url and "locale=en_US" in r.url,
        json_response(
            fixture_json("dlsite_product_info.json")
            if en_details is None
            else en_details
        ),
    )
    transport.route(
        "/product/info/ajax",
        json_response(
            fixture_json("dlsite_product_info.json") if details is None else details
        ),
    )
    transport.route(
        lambda r: "/fsr/ajax/" in r.url and "locale=en_US" in r.url,
        json_response(
            fixture_json("dlsite_listing_en.json") if en_listing is None else en_listing
        ),
    )
    transport.route(
        "/fsr/ajax/",
        json_response(listing or fixture_json("dlsite_listing.json")),
    )
    transport.route(
        "/genre/list",
        Response(
            200,
            fixture_bytes("dlsite_genre_list_en.html")
            if genre_list_html is None
            else genre_list_html.encode(),
        ),
    )
    transport.route(
        "/api/=/product.json",
        product_json
        if isinstance(product_json, Response)
        else json_response(
            fixture_json("dlsite_product_json_en.json")
            if product_json is None
            else product_json
        ),
    )
    return transport


def test_listing_parses_ids_circles_and_genres():
    items, total = parse_listing(fixture_json("dlsite_listing.json"))
    assert total == 39684
    assert [item["product_id"] for item in items] == [
        "VJ01006811",
        "VJ01006817",
        "VJ01006709",
    ]
    assert items[0]["circle"] == "アニム" and items[0]["circle_id"] == "VG01022"
    assert items[0]["genres"] == [
        "巫女",
        "パイズリ",
        "フェラチオ",
        "巨乳/爆乳",
        "黒髪",
        "獣耳",
    ]
    # /pro/ tag links include work_category/pc/
    assert items[0]["genre_ids"] == ["080", "122", "138", "182", "171", "176"]
    # /home/ (same shape as /maniax/) omits work_category/pc/
    assert items[2]["genre_ids"] == [
        "055",
        "400",
        "111",
        "074",
        "001",
        "005",
        "007",
        "032",
    ]
    maniax_items, _ = parse_listing(
        _jp_listing_for(
            [
                (
                    "RJ01609862",
                    "RG1",
                    "湯宮の森",
                    [("269", "変身ヒロイン"), ("008", "日常/生活")],
                )
            ]
        )
    )
    assert maniax_items[0]["genres"] == ["変身ヒロイン", "日常/生活"]
    assert maniax_items[0]["genre_ids"] == ["269", "008"]


def test_english_listing_parses_li_markup():
    circles = parse_english_listing(fixture_json("dlsite_listing_en.json"))
    assert circles["RJ01609862"] == "oginomesoft"
    assert circles["RJ01776894"] == "Corrupt"
    assert circles["RJ01776743"] == "ささスタジオ"


def test_genre_list_and_product_json_parsers():
    genre_list = parse_genre_list(
        fixture_bytes("dlsite_genre_list_en.html").decode("utf-8")
    )
    assert genre_list["269"] == "Transforming Girl"
    assert genre_list["080"] == "Shrine Maiden"
    assert genre_list["008"] == "Slice of Life / Daily Living"

    # Newer genres appear in Japanese on the English list; they stay unknown.
    mixed = parse_genre_list(
        '<a href="https://www.dlsite.com/maniax/fsr/=/genre/637">キス<span class="number">(3,503)</span></a>'
        + '<a href="https://www.dlsite.com/maniax/fsr/=/genre/269">Transforming Girl</a>'
    )
    assert mixed == {"269": "Transforming Girl"}

    product_genres = parse_product_genres(fixture_json("dlsite_product_json_en.json"))
    assert product_genres["113"] == "Dub-con"
    assert product_genres["058"] == "Totally Happy"


def test_non_adult_works_are_dropped_and_circles_merged(tmp_path):
    transport = _transport()
    ctx, _ = make_context(tmp_path, transport)
    source = DlsiteSource()
    ctx.state.put("dlsite", "sweep_cursor", [1, 1])
    source.sweep_step(ctx)
    stored = rows(
        ctx.catalog,
        "SELECT product_id, circle, age_category, genres, cover_url, url FROM dlsite_works ORDER BY product_id",
    )
    assert [row[0] for row in stored] == ["VJ01006811", "VJ01006817"]
    assert stored[0][1] == "アニム" and stored[1][1] == "縁"
    assert {row[2] for row in stored} == {3}
    assert json.loads(stored[0][3])
    assert stored[0][4].startswith("https://img.dlsite.jp/")
    assert (
        stored[0][5] == "https://www.dlsite.com/pro/work/=/product_id/VJ01006811.html"
    )
    # The all-ages work is remembered as skipped, not stored.
    assert ctx.state.skipped_dlsite(["VJ01006709"]) == {"VJ01006709"}
    info_requests = [
        request for request in transport.requests if "info/ajax" in request.url
    ]
    assert "VJ01006811,VJ01006817,VJ01006709" in info_requests[0].url


def test_sweep_walks_maniax_then_pro(tmp_path):
    listing = fixture_json("dlsite_listing.json")
    listing["page_info"]["count"] = 150
    transport = _transport(listing)
    ctx, _ = make_context(tmp_path, transport)
    source = DlsiteSource()
    done = []
    while not (finished := source.sweep_step(ctx)):
        done.append(ctx.state.get("dlsite", "sweep_cursor"))
    assert finished
    assert done == [[0, 2], [1, 1], [1, 2]]
    jp_listing_urls = [
        request.url
        for request in transport.requests
        if "/fsr/ajax/" in request.url and "locale=en_US" not in request.url
    ]
    en_listing_urls = [
        request.url
        for request in transport.requests
        if "/fsr/ajax/" in request.url and "locale=en_US" in request.url
    ]
    assert [url.split("/")[3] for url in jp_listing_urls] == [
        "maniax",
        "maniax",
        "pro",
        "pro",
    ]
    assert [url.split("/")[3] for url in en_listing_urls] == [
        "maniax",
        "maniax",
        "pro",
        "pro",
    ]


def test_an_empty_listing_page_is_a_failed_step(tmp_path):
    listing = fixture_json("dlsite_listing.json")
    listing["page_info"]["count"] = 150

    def transport(request):
        if "/fsr/ajax/" in request.url and "locale=en_US" in request.url:
            return json_response(fixture_json("dlsite_listing_en.json"))
        if "/fsr/ajax/" in request.url:
            page = int(re.search(r"/page/(\d+)/", request.url).group(1))
            payload = json.loads(json.dumps(listing))
            if page == 2:
                payload["search_result"] = ""
            return json_response(payload)
        if "/genre/list" in request.url:
            return Response(200, fixture_bytes("dlsite_genre_list_en.html"))
        if "/api/=/product.json" in request.url:
            return json_response(fixture_json("dlsite_product_json_en.json"))
        return json_response(fixture_json("dlsite_product_info.json"))

    ctx, _ = make_context(tmp_path, transport)
    source = DlsiteSource()
    assert source.sweep_step(ctx) is False
    # The listing says page 2 has works, so an empty page is a failed request.
    with pytest.raises(ValueError, match="empty page"):
        source.sweep_step(ctx)


def test_empty_english_listing_on_non_empty_japanese_page_raises(tmp_path):
    transport = _transport(
        en_listing={"search_result": "<ul></ul>", "page_info": {"count": 10}}
    )
    ctx, _ = make_context(tmp_path, transport)
    with pytest.raises(ValueError, match="empty English listing"):
        DlsiteSource().sweep_step(ctx)


def test_empty_product_details_are_a_failed_step(tmp_path):
    transport = _transport(details={}, en_details={})
    ctx, _ = make_context(tmp_path, transport)
    ctx.catalog.upsert(
        "dlsite_works",
        "product_id",
        [{"product_id": "VJ01006811", "title": "old", "url": "u"}],
        "2026-01-01T00:00:00Z",
    )
    with pytest.raises(ValueError, match="no details"):
        DlsiteSource().sweep_step(ctx)


def test_a_new_work_without_details_does_not_stall_the_source(tmp_path):
    transport = _transport(details={}, en_details={})
    ctx, _ = make_context(tmp_path, transport)
    ctx.state.mark_sweep_completed("dlsite", "2026-01-01T00:00:00Z")
    # Nothing stored is at risk, so missing details are retried later instead.
    DlsiteSource().incremental(ctx)
    assert DlsiteSource().sweep_step(ctx) is False


def test_incremental_fetches_details_only_for_new_ids(tmp_path):
    transport = _transport()
    ctx, _ = make_context(tmp_path, transport)
    ctx.state.mark_sweep_completed("dlsite", "2026-01-01T00:00:00Z")
    ctx.catalog.upsert(
        "dlsite_works",
        "product_id",
        [{"product_id": "VJ01006811", "title": "old", "url": "u"}],
        "2026-01-01T00:00:00Z",
    )
    ctx.state.add_skipped_dlsite(["VJ01006709"])
    DlsiteSource().incremental(ctx)
    info = [request.url for request in transport.requests if "info/ajax" in request.url]
    assert info and all("product_id=VJ01006817&" in url for url in info)


def test_english_work_japanese_only_work_and_missing_from_en_listing(tmp_path):
    en_details = fixture_json("dlsite_product_info_en.json")
    jp_details = {
        "RJ01609862": {
            **en_details["RJ01609862"],
            "work_name": "なつまじょ！ -二人の魔女と真夏の田舎生活-",
        },
        "VJ01006811": en_details["VJ01006811"],
        "RJ99999999": {
            "site_id": "maniax",
            "maker_id": "RG99",
            "age_category": 3,
            "work_name": "日本語のみ",
            "work_image": "//img.dlsite.jp/a.jpg",
            "regist_date": "2026-11-01 00:00:00",
            "work_type": "RPG",
        },
    }
    en_details_full = {
        **en_details,
        "RJ99999999": jp_details["RJ99999999"],
    }
    jp_listing = _jp_listing_for(
        [
            (
                "RJ01609862",
                "RG1",
                "湯宮の森",
                [("269", "変身ヒロイン"), ("008", "日常/生活")],
            ),
            ("VJ01006811", "VG01022", "アニム", [("080", "巫女")]),
            ("RJ99999999", "RG99", "既存サークル", [("182", "巨乳/爆乳")]),
        ]
    )
    # RJ99999999 shifted off the English page; VJ01006811 has identical JP/EN circle.
    en_listing = _en_listing_for({"RJ01609862": "Yumiyanomori", "VJ01006811": "アニム"})
    transport = _transport(
        jp_listing,
        en_listing=en_listing,
        details=jp_details,
        en_details=en_details_full,
    )
    ctx, _ = make_context(tmp_path, transport)
    ctx.catalog.upsert(
        "dlsite_works",
        "product_id",
        [
            {
                "product_id": "RJ99999999",
                "title": "日本語のみ",
                "circle": "既存サークル",
                "circle_en": "Kept Circle",
                "url": "u",
            }
        ],
        "2026-01-01T00:00:00Z",
    )
    DlsiteSource().sweep_step(ctx)

    stored = {
        row[0]: row[1:]
        for row in rows(
            ctx.catalog,
            "SELECT product_id, title, title_en, circle, circle_en, genres, genres_en FROM dlsite_works",
        )
    }
    assert stored["RJ01609862"] == (
        "なつまじょ！ -二人の魔女と真夏の田舎生活-",
        "Summer Witches! ~Midsummer Country Life with Two Witches~",
        "湯宮の森",
        "Yumiyanomori",
        json.dumps(["変身ヒロイン", "日常/生活"], ensure_ascii=False),
        json.dumps(["Transforming Girl", "Slice of Life / Daily Living"]),
    )
    assert stored["VJ01006811"] == (
        "【DLsite限定版】きつ×みこ ～お狐さまと巫女お姉さんと過ごす、ちょっと不思議でとっても甘い夏休み～",
        None,
        "アニム",
        None,
        json.dumps(["巫女"], ensure_ascii=False),
        json.dumps(["Shrine Maiden"]),
    )
    # Missing from English listing: previously stored circle_en is kept.
    assert stored["RJ99999999"][1] is None
    assert stored["RJ99999999"][3] == "Kept Circle"
    assert json.loads(stored["RJ99999999"][5]) == ["Big Breasts"]


def test_genre_map_loads_lists_and_resolves_unknown_once(tmp_path):
    jp_listing = _jp_listing_for(
        [
            (
                "RJ01776743",
                "RG58294",
                "ささスタジオ",
                [("113", "合意なし"), ("182", "巨乳/爆乳")],
            )
        ],
        count=150,
    )
    en_listing = _en_listing_for({"RJ01776743": "Sasa Studio"}, count=150)
    details = {
        "RJ01776743": {
            "site_id": "maniax",
            "maker_id": "RG58294",
            "age_category": 3,
            "work_name": "日本語",
            "work_image": "",
            "regist_date": "2026-11-24 00:00:00",
            "work_type": "RPG",
        }
    }
    transport = _transport(
        jp_listing,
        en_listing=en_listing,
        details=details,
        en_details=details,
    )
    ctx, _ = make_context(tmp_path, transport)
    source = DlsiteSource()
    assert source.sweep_step(ctx) is False
    genre_list_reqs = [r.url for r in transport.requests if "/genre/list" in r.url]
    product_json_reqs = [
        r.url for r in transport.requests if "/api/=/product.json" in r.url
    ]
    assert len(genre_list_reqs) == 2
    assert len(product_json_reqs) == 1
    stored_genres = rows(
        ctx.catalog,
        "SELECT genres_en FROM dlsite_works WHERE product_id = 'RJ01776743'",
    )[0][0]
    assert json.loads(stored_genres) == ["Dub-con", "Big Breasts"]

    # Page 2 has the same genre 113: neither genre list nor product.json is requested again.
    assert source.sweep_step(ctx) is False
    assert len([r for r in transport.requests if "/genre/list" in r.url]) == 2
    assert len([r for r in transport.requests if "/api/=/product.json" in r.url]) == 1


def test_genre_without_english_name_is_not_asked_for_again(tmp_path):
    jp_listing = _jp_listing_for(
        [("RJ01776743", "RG58294", "ささスタジオ", [("999", "未翻訳ジャンル")])],
        count=150,
    )
    en_listing = _en_listing_for({"RJ01776743": "Sasa Studio"}, count=150)
    details = {
        "RJ01776743": {
            "site_id": "maniax",
            "maker_id": "RG58294",
            "age_category": 3,
            "work_name": "日本語",
            "work_image": "",
            "regist_date": "2026-11-24 00:00:00",
            "work_type": "RPG",
        }
    }
    # product.json answers but does not name genre 999.
    transport = _transport(
        jp_listing,
        en_listing=en_listing,
        details=details,
        en_details=details,
        product_json=[{"workno": "RJ01776743", "genres_replaced": []}],
    )
    ctx, _ = make_context(tmp_path, transport)
    source = DlsiteSource()
    assert source.sweep_step(ctx) is False
    stored_genres = rows(
        ctx.catalog,
        "SELECT genres_en FROM dlsite_works WHERE product_id = 'RJ01776743'",
    )[0][0]
    assert json.loads(stored_genres) == ["未翻訳ジャンル"]
    assert ctx.state.unknown_dlsite_genres(["999"]) == []

    assert source.sweep_step(ctx) is False
    assert len([r for r in transport.requests if "/api/=/product.json" in r.url]) == 1


def test_failed_product_json_stores_work_and_leaves_genre_unknown(tmp_path):
    jp_listing = _jp_listing_for(
        [
            (
                "RJ01776743",
                "RG58294",
                "ささスタジオ",
                [("113", "合意なし"), ("182", "巨乳/爆乳")],
            )
        ]
    )
    en_listing = _en_listing_for({"RJ01776743": "Sasa Studio"})
    details = {
        "RJ01776743": {
            "site_id": "maniax",
            "maker_id": "RG58294",
            "age_category": 3,
            "work_name": "日本語",
            "work_image": "",
            "regist_date": "2026-11-24 00:00:00",
            "work_type": "RPG",
        }
    }
    transport = _transport(
        jp_listing,
        en_listing=en_listing,
        details=details,
        en_details=details,
        product_json=Response(500, b"error"),
    )
    ctx, _ = make_context(tmp_path, transport)
    DlsiteSource().sweep_step(ctx)
    stored_genres = rows(
        ctx.catalog,
        "SELECT genres_en FROM dlsite_works WHERE product_id = 'RJ01776743'",
    )[0][0]
    assert json.loads(stored_genres) == ["合意なし", "Big Breasts"]
    assert ctx.state.unknown_dlsite_genres(["113"]) == ["113"]


def test_failed_english_listing_or_empty_english_details_keeps_stored_title_en(
    tmp_path,
):
    jp_listing = _jp_listing_for(
        [("RJ01609862", "RG1", "湯宮の森", [("269", "変身ヒロイン")])]
    )
    jp_details = {
        "RJ01609862": {
            "site_id": "maniax",
            "maker_id": "RG1",
            "age_category": 3,
            "work_name": "なつまじょ！",
            "work_image": "",
            "regist_date": "2026-11-07 00:00:00",
            "work_type": "SLN",
        }
    }
    for mode in ("bad_en_listing", "empty_en_details"):
        sub = tmp_path / mode
        sub.mkdir()
        transport = FakeTransport()
        transport.route(
            lambda r: "/product/info/ajax" in r.url and "locale=en_US" in r.url,
            json_response({}),
        )
        transport.route("/product/info/ajax", json_response(jp_details))
        transport.route(
            lambda r: "/fsr/ajax/" in r.url and "locale=en_US" in r.url,
            Response(500, b"error")
            if mode == "bad_en_listing"
            else json_response(_en_listing_for({"RJ01609862": "Yumiyanomori"})),
        )
        transport.route("/fsr/ajax/", json_response(jp_listing))
        ctx, _ = make_context(sub, transport)
        ctx.catalog.upsert(
            "dlsite_works",
            "product_id",
            [
                {
                    "product_id": "RJ01609862",
                    "title": "なつまじょ！",
                    "title_en": "Summer Witches!",
                    "url": "u",
                }
            ],
            "2026-01-01T00:00:00Z",
        )
        expected = HttpStatusError if mode == "bad_en_listing" else ValueError
        with pytest.raises(expected):
            DlsiteSource().sweep_step(ctx)
        assert rows(
            ctx.catalog,
            "SELECT title_en FROM dlsite_works WHERE product_id = 'RJ01609862'",
        ) == [("Summer Witches!",)]
