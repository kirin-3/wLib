# SPDX-License-Identifier: GPL-3.0-or-later
"""Run orchestration, removal marking and the CLI."""

import json

import pytest

from catalog_fakes import FakeClock, FakeTransport, fixture_bytes, make_context, rows
from catalog_server.__main__ import main
from catalog_server.net import Response
from catalog_server.runner import run
from catalog_server.sources.base import SourceContext
from catalog_server.sources.dlsite import DlsiteSource
from catalog_server.sources.ryuugames import RyuugamesSource
from catalog_server.sources.vndb import VndbSource


class FakeSource:
    def __init__(self, name, table, key, *, fail=False):
        self.name = name
        self.table = table
        self.key = key
        self.fail = fail
        self.listed = []
        self.calls = []

    def incremental(self, ctx: SourceContext) -> None:
        self.calls.append("incremental")
        if self.fail:
            raise ConnectionError("timed out")

    def sweep_due(self, ctx):
        return True

    def sweep_step(self, ctx):
        self.calls.append("sweep")
        ctx.upsert(self.table, self.key, self.listed)
        return True

    def reset_sweep(self, ctx):
        pass


def _ryuu_row(post_id, title="T"):
    return {"post_id": post_id, "title": title, "raw_title": title, "url": f"u{post_id}"}


def test_failing_source_is_recorded_and_others_still_update(tmp_path):
    ctx, clock = make_context(tmp_path, FakeTransport())
    broken = FakeSource("dlsite", "dlsite_works", "product_id", fail=True)
    healthy = FakeSource("ryuugames", "ryuu_posts", "post_id")
    healthy.listed = [_ryuu_row(1)]
    ctx.catalog.upsert(
        "dlsite_works", "product_id", [{"product_id": "RJ1", "title": "old", "url": "u"}], "2026-01-01T00:00:00Z"
    )
    result = run(
        ctx.catalog,
        ctx.state,
        ctx.pacer,
        {"dlsite": broken, "ryuugames": healthy},
        ["dlsite", "ryuugames"],
        storage=None,
        work_dir=str(tmp_path),
        now=clock.iso,
    )
    assert result.sources["dlsite"].status == "error"
    assert "timed out" in (result.sources["dlsite"].detail or "")
    assert result.sources["ryuugames"].status == "ok"
    assert ctx.catalog.count("ryuugames") == 1
    # The failing source's previous data stays.
    assert ctx.catalog.count("dlsite") == 1
    history = ctx.state.recent_runs()[0]["sources"]
    assert {entry["source"]: entry["status"] for entry in history} == {"dlsite": "error", "ryuugames": "ok"}
    assert ctx.state.source_status("dlsite")["last_error"]
    assert ctx.state.source_status("dlsite")["last_success_at"] is None
    assert healthy.calls[0] == "incremental" and "sweep" in healthy.calls


def test_entries_missing_from_two_sweeps_are_marked_removed_and_cleared_on_return(tmp_path):
    ctx, clock = make_context(tmp_path, FakeTransport())
    source = FakeSource("ryuugames", "ryuu_posts", "post_id")
    sources = {"ryuugames": source}

    def sweep():
        run(ctx.catalog, ctx.state, ctx.pacer, sources, ["ryuugames"], storage=None, work_dir=str(tmp_path), now=clock.iso)
        clock.now += 86400
        return dict(rows(ctx.catalog, "SELECT post_id, removed_at FROM ryuu_posts"))

    source.listed = [_ryuu_row(1), _ryuu_row(2)]
    assert sweep() == {1: None, 2: None}
    source.listed = [_ryuu_row(1)]
    # One missed sweep can be a page shift on a live feed, not a removal.
    assert sweep() == {1: None, 2: None}
    third = sweep()
    assert third[1] is None and third[2] is not None
    source.listed = [_ryuu_row(1), _ryuu_row(2)]
    assert sweep() == {1: None, 2: None}


@pytest.mark.parametrize("source_cls", [DlsiteSource, RyuugamesSource, VndbSource])
def test_full_resweeps_of_unbudgeted_sources_wait_a_week(tmp_path, source_cls):
    ctx, clock = make_context(tmp_path, FakeTransport())
    source = source_cls()
    assert source.sweep_due(ctx)  # first full load
    ctx.state.mark_sweep_completed(source.name, clock.iso())
    clock.now += 6 * 86400
    assert not source.sweep_due(ctx)
    clock.now += 86400
    assert source.sweep_due(ctx)


def test_interrupted_periodic_sweep_resumes_before_the_week_is_up(tmp_path):
    ctx, clock = make_context(tmp_path, FakeTransport())
    source = DlsiteSource()
    ctx.state.mark_sweep_completed(source.name, clock.iso())
    ctx.state.put(source.name, "sweep_started_at", clock.iso())
    assert source.sweep_due(ctx)


def test_cli_run_no_publish_ryuugames(tmp_path, capsys):
    transport = FakeTransport()
    transport.route(
        "/wp-json/wp/v2/posts",
        Response(200, fixture_bytes("ryuugames_posts.json"), {"X-WP-TotalPages": "1"}),
    )
    clock = FakeClock()
    code = main(
        ["--data-dir", str(tmp_path), "run", "--no-publish", "--sources", "ryuugames"],
        transport=transport,
        sleep=clock.sleep,
    )
    assert code == 0
    assert transport.requests and all("ryuugames.com" in request.url for request in transport.requests)
    assert main(["--data-dir", str(tmp_path), "status"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["sources"]["ryuugames"]["rows"] == 3
    assert report["runs"][0]["sources"][0]["status"] == "ok"
    assert report["runs"][0]["published"] is None


def test_cli_publish_needs_credentials(tmp_path, monkeypatch):
    for name in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"):
        monkeypatch.delenv(name, raising=False)
    assert main(["--data-dir", str(tmp_path), "run", "--sources", "none"]) == 2


def test_cli_sources_none_only_publishes(tmp_path):
    from test_catalog_publish import MemoryStorage

    storage = MemoryStorage()
    transport = FakeTransport()
    code = main(
        ["--data-dir", str(tmp_path), "run", "--sources", "none"],
        transport=transport,
        storage_factory=lambda: storage,
    )
    assert code == 0 and transport.requests == []
    assert "v1/manifest.json" in storage.objects


def test_failed_publish_still_finishes_the_run(tmp_path):
    import pytest

    from test_catalog_publish import MemoryStorage

    ctx, clock = make_context(tmp_path, FakeTransport())
    with pytest.raises(ConnectionError):
        run(ctx.catalog, ctx.state, ctx.pacer, {}, [], storage=MemoryStorage(fail_on="v1/"), work_dir=str(tmp_path), now=clock.iso)
    latest = ctx.state.recent_runs()[0]
    assert latest["finished_at"] and latest["published"] is None
    assert not list((tmp_path / "snapshots").iterdir())


def test_a_second_run_skips_while_one_holds_the_lock(tmp_path):
    from catalog_server.__main__ import _run_lock

    transport = FakeTransport()
    with _run_lock(str(tmp_path)) as acquired:
        assert acquired
        code = main(["--data-dir", str(tmp_path), "run", "--no-publish", "--sources", "ryuugames"], transport=transport)
    assert code == 0 and transport.requests == []
