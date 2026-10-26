# SPDX-License-Identifier: GPL-3.0-or-later
import json
import re

from catalog_fakes import fixture_bytes, fixture_json, json_response, make_context, rows
from catalog_server.net import RateLimited, Response
from catalog_server.runner import run_source
from catalog_server.sources.f95 import F95Source, load_names


def _page(number, entries, total=3):
    payload = fixture_json("f95_latest_page.json")
    payload["msg"]["data"] = entries
    payload["msg"]["pagination"] = {"page": number, "total": total}
    return json_response(payload)


def _entries():
    return fixture_json("f95_latest_page.json")["msg"]["data"]


def _page_number(request):
    return int(re.search(r"page=(\d+)", request.url).group(1))


def test_rows_store_ids_tags_and_prefixes(tmp_path):
    transport_pages = {1: _page(1, _entries(), total=1)}
    ctx, _ = make_context(tmp_path, lambda request: transport_pages[_page_number(request)])
    assert F95Source().sweep_step(ctx) is True
    row = rows(
        ctx.catalog,
        "SELECT title, creator, version, tags, prefixes, cover_url, url, updated_at "
        "FROM f95_threads WHERE thread_id = 223393",
    )[0]
    title, creator, version, tags, prefixes, cover, url, updated_at = row
    assert (title, creator, url) == ("Sarah's House", "AceStudio", "https://f95zone.to/threads/223393/")
    assert version.startswith("v")
    assert json.loads(tags) and all(isinstance(tag, int) for tag in json.loads(tags))
    assert json.loads(prefixes)
    assert cover.startswith("https://") and updated_at.endswith("Z")


def test_sweep_resumes_from_its_cursor_and_429_keeps_collected_rows(tmp_path):
    entries = _entries()
    pages = {1: _page(1, entries[:2]), 2: _page(2, entries[2:4]), 3: _page(3, entries[4:])}
    limited = {"on": True}

    def transport(request):
        number = _page_number(request)
        if number == 2 and limited["on"]:
            return Response(429, fixture_bytes("f95_rate_limited.json"))
        return pages[number]

    ctx, _ = make_context(tmp_path, transport)
    result = run_source(F95Source(), ctx)
    assert result.status == "rate_limited"
    assert ctx.catalog.count("f95") == 2
    assert ctx.state.get("f95", "sweep_page") == 2

    # Next run: a new pacer (no pause), continues at page 2.
    limited["on"] = False
    from catalog_fakes import FakeClock
    from catalog_server.net import Pacer

    clock = FakeClock(start=1_900_000_000.0)
    ctx.pacer = Pacer(ctx.state, transport, clock=clock, sleep=clock.sleep)
    result = run_source(F95Source(), ctx)
    assert result.status == "ok" and result.sweep_completed
    assert ctx.catalog.count("f95") == 5


def test_rate_limit_body_with_200_counts_as_limit(tmp_path):
    ctx, _ = make_context(tmp_path, lambda request: Response(200, fixture_bytes("f95_rate_limited.json")))
    try:
        F95Source().sweep_step(ctx)
    except RateLimited:
        pass
    else:
        raise AssertionError("expected RateLimited")


def test_incremental_stops_at_known_entries(tmp_path):
    entries = sorted(_entries(), key=lambda entry: -entry["ts"])
    fetched = []

    def transport(request):
        number = _page_number(request)
        fetched.append(number)
        return {1: _page(1, entries[:2], total=3), 2: _page(2, entries[2:4], total=3), 3: _page(3, entries[4:], total=3)}[number]

    ctx, _ = make_context(tmp_path, transport)
    # Everything up to entries[2] was collected before.
    ctx.state.put("f95", "hwm_ts", entries[2]["ts"])
    F95Source().incremental(ctx)
    # Page 1 is all newer, page 2 reaches the mark, page 3 is never asked for.
    assert fetched == [1, 2]
    assert ctx.state.get("f95", "hwm_ts") == entries[0]["ts"]


def test_incremental_does_nothing_on_first_install(tmp_path):
    ctx, _ = make_context(tmp_path, lambda request: (_ for _ in ()).throw(AssertionError("no request")))
    F95Source().incremental(ctx)


def test_first_sweep_page_sets_the_high_water_mark(tmp_path):
    entries = _entries()
    ctx, _ = make_context(tmp_path, lambda request: _page(1, entries, total=2))
    assert F95Source().sweep_step(ctx) is False
    assert ctx.state.get("f95", "hwm_ts") == max(entry["ts"] for entry in entries)


def test_tag_map_names_known_ids_and_keeps_unknown_ones(tmp_path):
    entries = _entries()
    ctx, _ = make_context(tmp_path, lambda request: _page(1, entries, total=1))
    known_tag = entries[0]["tags"][0]
    known_prefix = entries[0]["prefixes"][0]
    tags_file = tmp_path / "f95_tags.json"
    tags_file.write_text(
        json.dumps(
            {
                "tags": {str(known_tag): "2dcg"},
                "prefixes": {str(known_prefix): {"name": "Ren'Py", "category": "Engine"}},
            }
        )
    )
    load_names(ctx.catalog, str(tags_file))
    F95Source().sweep_step(ctx)
    stored = json.loads(rows(ctx.catalog, "SELECT tags FROM f95_threads WHERE thread_id = ?", (entries[0]["thread_id"],))[0][0])
    assert stored == entries[0]["tags"]
    named = dict(
        rows(
            ctx.catalog,
            "SELECT j.value, n.name FROM f95_threads t, json_each(t.tags) j "
            "LEFT JOIN f95_tag_names n ON n.id = j.value WHERE t.thread_id = ?",
            (entries[0]["thread_id"],),
        )
    )
    assert named[known_tag] == "2dcg"
    assert any(name is None for name in named.values())
    assert rows(ctx.catalog, "SELECT name, category FROM f95_prefix_names") == [("Ren'Py", "Engine")]


def test_import_tool_converts_latest_updates_object(tmp_path):
    from catalog_server.tools.import_f95_tags import main

    page = tmp_path / "page.html"
    page.write_text(
        "<script>var latestUpdates = "
        + json.dumps(
            {
                "tags": {"130": "2dcg", "107": "3dcg"},
                "prefixes": {
                    "games": [
                        {"id": 1, "name": "Engine", "prefixes": [{"id": 7, "name": "Ren'Py", "class": "pre-renpy"}]},
                        {"id": 4, "name": "Status", "prefixes": [{"id": 18, "name": "Completed"}]},
                    ]
                },
            }
        )
        + ";</script>"
    )
    output = tmp_path / "out.json"
    assert main([str(page), "--output", str(output)]) == 0
    data = json.loads(output.read_text())
    assert data["tags"] == {"107": "3dcg", "130": "2dcg"}
    assert data["prefixes"]["7"] == {"name": "Ren'Py", "category": "Engine"}
    assert data["prefixes"]["18"] == {"name": "Completed", "category": "Status"}
