# SPDX-License-Identifier: GPL-3.0-or-later
"""Discord run reports."""

import json

import pytest

from catalog_fakes import FakeTransport, make_context
from catalog_server.__main__ import main
from catalog_server.config import discord_webhook_from_env
from catalog_server.net import Response
from catalog_server.notify import (
    COLOR_FAILED,
    COLOR_OK,
    COLOR_WARNING,
    build_report,
    send_report,
)
from catalog_server.runner import RunResult, SourceResult, run
from catalog_server.sources.f95_threads import NO_LOGIN as F95_NO_LOGIN
from catalog_server.sources.lc import NO_LOGIN as LC_NO_LOGIN

WEBHOOK = "https://discord.com/api/webhooks/1/token"
SOURCES = frozenset({"f95", "lc", "dlsite", "ryuugames", "vndb"})


def _embed(payload):
    return payload["embeds"][0]


def _field(payload, name):
    return next(f["value"] for f in _embed(payload)["fields"] if f["name"] == name)


def _result(**sources):
    return RunResult(
        run_id=7,
        sources=sources,
        manifest={"snapshot": {"path": "v1/snapshots/x.sqlite.xz", "size": 2_500_000}},
        started_at="2026-11-18T05:00:00Z",
        finished_at="2026-11-18T05:42:10Z",
        links=1234,
    )


def test_webhook_setting_accepts_only_discord_webhooks(monkeypatch):
    monkeypatch.delenv("WLIB_CATALOG_DISCORD_WEBHOOK", raising=False)
    assert discord_webhook_from_env() is None
    monkeypatch.setenv("WLIB_CATALOG_DISCORD_WEBHOOK", f" {WEBHOOK} ")
    assert discord_webhook_from_env() == WEBHOOK
    for bad in (
        "http://discord.com/api/webhooks/1/t",
        "https://evil.example/api/webhooks/1/t",
        "https://discord.com/channels/1",
    ):
        monkeypatch.setenv("WLIB_CATALOG_DISCORD_WEBHOOK", bad)
        with pytest.raises(ValueError) as error:
            discord_webhook_from_env()
        assert bad not in str(error.value)


def test_clean_publish_report_shows_counts_per_source():
    result = _result(
        f95=SourceResult("ok", rows=50, requests=12, added=3, removed=1, total=18_000),
        f95_threads=SourceResult("ok", rows=40, requests=40, total=9_000),
    )
    payload = build_report(result, sources=SOURCES, skipped={})
    embed = _embed(payload)
    assert embed["title"] == "Catalog published" and embed["color"] == COLOR_OK
    assert "Run #7 · 42m 10s" in embed["description"]
    assert "v1/snapshots/x.sqlite.xz` (2.5 MB)" in embed["description"]
    steps = _field(payload, "Sources")
    assert "**f95** (ok): +3 new · −1 removed · 18,000 total · 12 req" in steps
    assert "**f95_threads** (ok): 40 pages read · 9,000 total · 40 req" in steps
    assert _field(payload, "Problems") == "None."
    assert payload["allowed_mentions"] == {"parse": []}


def test_login_loss_and_missing_cookie_are_problems():
    result = _result(
        f95_threads=SourceResult("error", detail=f"ValueError: {F95_NO_LOGIN}"),
        lc=SourceResult("error", detail=f"LcLoginError: {LC_NO_LOGIN} (HTTP 403)"),
        dlsite=SourceResult("rate_limited", detail="HTTP 429"),
        vndb=SourceResult(
            "budget", detail="request budget used up; continues next run"
        ),
    )
    payload = build_report(
        result, sources=SOURCES, skipped={"lc_threads": "it needs the LC login cookie"}
    )
    assert _embed(payload)["color"] == COLOR_WARNING
    problems = _field(payload, "Problems")
    assert "🔑 **f95_threads** lost its login" in problems
    assert "🔑 **lc** lost its login" in problems
    assert "🔑 **lc_threads** skipped: it needs the LC login cookie" in problems
    assert "**dlsite** rate_limited: HTTP 429" in problems
    assert "vndb" not in problems and "vndb" in _field(payload, "Notes")


def test_unpublished_run_is_red_with_the_reason():
    result = RunResult(run_id=1, publish_refused="f95 dropped from 1000 to 1 rows")
    payload = build_report(result, sources=SOURCES, skipped={})
    assert _embed(payload)["title"] == "Catalog not published"
    assert _embed(payload)["color"] == COLOR_FAILED
    assert "Publish refused: f95 dropped from 1000 to 1 rows" in _field(
        payload, "Problems"
    )


def test_long_details_fit_discord_limits():
    result = _result(dlsite=SourceResult("error", detail="x" * 5000))
    payload = build_report(result, sources=SOURCES, skipped={})
    assert all(len(f["value"]) <= 1024 for f in _embed(payload)["fields"])


def test_send_report_posts_json_and_never_raises(caplog):
    transport = FakeTransport()
    transport.route(WEBHOOK, Response(204, b""))
    assert send_report(WEBHOOK, {"content": "hi"}, transport)
    request = transport.requests[0]
    assert request.method == "POST" and json.loads(request.body) == {"content": "hi"}

    def down(_request):
        raise ConnectionError(f"{WEBHOOK}: refused")

    assert not send_report(WEBHOOK, {}, down)
    refusing = FakeTransport()
    refusing.route(WEBHOOK, Response(401, b""))
    assert not send_report(WEBHOOK, {}, refusing)
    assert "token" not in caplog.text


def test_run_reports_new_and_removed_rows(tmp_path):
    ctx, clock = make_context(tmp_path, FakeTransport())
    ctx.catalog.upsert(
        "ryuu_posts",
        "post_id",
        [
            {"post_id": n, "title": "T", "raw_title": "T", "url": f"u{n}"}
            for n in (1, 2)
        ],
        "2026-01-01T00:00:00Z",
    )

    class Adds:
        name = "ryuugames"

        def incremental(self, ctx):
            ctx.upsert(
                "ryuu_posts",
                "post_id",
                [
                    {"post_id": n, "title": "T", "raw_title": "T", "url": f"u{n}"}
                    for n in (2, 3)
                ],
            )
            ctx.catalog.conn.execute(
                "UPDATE ryuu_posts SET removed_at = ? WHERE post_id = 1", (ctx.now(),)
            )
            ctx.catalog.conn.commit()

        def sweep_due(self, ctx):
            return False

    reports = []
    run(
        ctx.catalog,
        ctx.state,
        ctx.pacer,
        {"ryuugames": Adds()},
        ["ryuugames"],
        storage=None,
        work_dir=str(tmp_path),
        now=clock.iso,
        report=reports.append,
    )
    outcome = reports[0].sources["ryuugames"]
    assert (outcome.added, outcome.removed, outcome.total) == (1, 1, 2)


def test_run_reports_even_when_the_upload_fails(tmp_path):
    from test_catalog_publish import MemoryStorage

    ctx, clock = make_context(tmp_path, FakeTransport())
    reports = []
    with pytest.raises(ConnectionError):
        run(
            ctx.catalog,
            ctx.state,
            ctx.pacer,
            {},
            [],
            storage=MemoryStorage(fail_on="v1/"),
            work_dir=str(tmp_path),
            now=clock.iso,
            force_publish=True,
            report=reports.append,
        )
    assert reports[0].error == "ConnectionError: upload interrupted"
    assert reports[0].manifest is None


def test_cli_sends_a_report_only_for_publishing_runs(tmp_path, monkeypatch):
    from test_catalog_publish import MemoryStorage

    monkeypatch.setenv("WLIB_CATALOG_DISCORD_WEBHOOK", WEBHOOK)
    monkeypatch.delenv("WLIB_CATALOG_LC_COOKIE", raising=False)
    monkeypatch.delenv("WLIB_CATALOG_F95_COOKIE", raising=False)
    monkeypatch.delenv("WLIB_CATALOG_BROWSER_SITES", raising=False)
    webhook = FakeTransport()
    webhook.route(WEBHOOK, Response(204, b""))
    code = main(
        ["--data-dir", str(tmp_path), "run", "--sources", "none", "--force-publish"],
        transport=FakeTransport(),
        storage_factory=MemoryStorage,
        webhook_transport=webhook,
    )
    assert code == 0 and len(webhook.requests) == 1
    assert _embed(json.loads(webhook.requests[0].body))["title"] == "Catalog published"

    code = main(
        ["--data-dir", str(tmp_path), "run", "--sources", "none", "--no-publish"],
        transport=FakeTransport(),
        webhook_transport=webhook,
    )
    assert code == 0 and len(webhook.requests) == 1


def test_cli_lists_steps_skipped_for_a_missing_cookie(tmp_path, monkeypatch):
    from test_catalog_publish import MemoryStorage

    monkeypatch.setenv("WLIB_CATALOG_DISCORD_WEBHOOK", WEBHOOK)
    monkeypatch.delenv("WLIB_CATALOG_LC_COOKIE", raising=False)
    monkeypatch.delenv("WLIB_CATALOG_BROWSER_SITES", raising=False)
    webhook = FakeTransport()
    webhook.route(WEBHOOK, Response(204, b""))
    _ = main(
        ["--data-dir", str(tmp_path), "run", "--sources", "lc", "--force-publish"],
        transport=FakeTransport(),
        storage_factory=MemoryStorage,
        webhook_transport=webhook,
    )
    problems = _field(json.loads(webhook.requests[0].body), "Problems")
    assert "🔑 **lc** skipped: it needs the LC login cookie" in problems


def test_cli_refuses_a_bad_webhook(tmp_path, monkeypatch):
    monkeypatch.setenv("WLIB_CATALOG_DISCORD_WEBHOOK", "https://example.com/hook")
    assert (
        main(["--data-dir", str(tmp_path), "run", "--no-publish", "--sources", "none"])
        == 2
    )
