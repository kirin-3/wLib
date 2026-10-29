# SPDX-License-Identifier: GPL-3.0-or-later
import json
import re

import pytest

from catalog_fakes import FakeTransport, fixture_bytes, make_context, rows
from catalog_server.net import Response
from catalog_server.sources.ryuugames import RyuugamesSource, clean_title


def _transport():
    transport = FakeTransport()
    transport.route(
        "/wp-json/wp/v2/posts",
        Response(200, fixture_bytes("ryuugames_posts.json"), {"X-WP-Total": "3", "X-WP-TotalPages": "1"}),
    )
    return transport


def test_posts_store_ids_version_and_categories(tmp_path):
    ctx, _ = make_context(tmp_path, _transport())
    assert RyuugamesSource().sweep_step(ctx) is True
    stored = {
        row[0]: row[1:]
        for row in rows(
            ctx.catalog,
            "SELECT post_id, title, version, developer, categories, rj_codes, steam_ids, vndb_ids, cover_url "
            "FROM ryuu_posts",
        )
    }
    title, version, developer, categories, rj, steam, vndb, cover = stored[116270]
    assert (title, version, developer) == ("Snow x Hime", "V2.7.1", "Kazuya, tensun3d")
    assert json.loads(categories) == ["English", "Japanese RAW"]
    assert json.loads(steam) == ["3771570"] and json.loads(rj) == []
    assert cover.startswith("https://www.ryuugames.com/wp-content/uploads/")
    assert json.loads(stored[169623][4]) == ["RJ01071738"]
    assert json.loads(stored[24446][6]) == ["v2480"]


def test_an_empty_page_inside_the_post_list_is_a_failed_step(tmp_path):
    def transport(request):
        page = int(re.search(r"[?&]page=(\d+)", request.url).group(1))
        if page == 1:
            return Response(
                200, fixture_bytes("ryuugames_posts.json"), {"X-WP-TotalPages": "3"}
            )
        return Response(200, b"[]", {"X-WP-TotalPages": "3"})

    ctx, _ = make_context(tmp_path, transport)
    source = RyuugamesSource()
    assert source.sweep_step(ctx) is False
    with pytest.raises(ValueError, match="empty page"):
        source.sweep_step(ctx)


def test_a_400_on_the_first_page_does_not_empty_the_catalog(tmp_path):
    ctx, _ = make_context(
        tmp_path,
        lambda request: Response(400, b'{"code":"rest_post_invalid_page_number"}'),
    )
    ctx.catalog.upsert(
        "ryuu_posts",
        "post_id",
        [{"post_id": 1, "title": "T", "raw_title": "T", "url": "u"}],
        "2026-01-01T00:00:00Z",
    )
    with pytest.raises(ValueError, match="first page"):
        RyuugamesSource().sweep_step(ctx)


def test_download_links_are_never_stored(tmp_path):
    raw = fixture_bytes("ryuugames_posts.json").decode()
    assert "cloud.ryuugames.com" in raw and "ouo.io" in raw
    ctx, _ = make_context(tmp_path, _transport())
    RyuugamesSource().sweep_step(ctx)
    dump = "\n".join(ctx.catalog.conn.iterdump())
    assert "cloud.ryuugames.com" not in dump
    assert "ouo.io" not in dump
    assert "LINK DOWNLOAD" not in dump


def test_incremental_uses_modified_after(tmp_path):
    transport = _transport()
    ctx, clock = make_context(tmp_path, transport)
    source = RyuugamesSource()
    source.incremental(ctx)
    assert transport.requests == []  # first install: the sweep does the work
    source.sweep_step(ctx)
    source.reset_sweep(ctx)
    since = ctx.state.get("ryuugames", "modified_since")
    clock.now += 3600
    source.incremental(ctx)
    url = transport.requests[-1].url
    assert "orderby=modified" in url
    assert "modified_after=" + since.replace(":", "%3A") in url
    assert ctx.state.get("ryuugames", "modified_since") > since


def test_clean_title():
    assert clean_title("[ENG] Snow x Hime (V2.7.1) (V26.10.24) Uncensored") == ("Snow x Hime", "V2.7.1")
    assert clean_title("Aniyome wa Ijippari Free Download") == ("Aniyome wa Ijippari", None)
    assert clean_title("Saimin App (RJ01071738)")[0] == "Saimin App"
