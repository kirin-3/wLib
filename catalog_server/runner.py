# SPDX-License-Identifier: GPL-3.0-or-later
"""One builder run: each source's incremental fetch, then its sweep with the
remaining budget, then links, then (optionally) the snapshot and publish."""

import logging
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from core.catalog_format import SOURCE_COUNT, SOURCE_UPDATED_AT

from .links import rebuild_links
from .net import BudgetExhausted, Pacer, RateLimited
from .publish import Storage, publish
from .snapshot import build_snapshot
from .sources import Source, SourceContext
from .sources.f95 import load_names
from .store import CatalogStore, StateStore, utc_now

log = logging.getLogger("catalog_server.run")


@dataclass
class SourceResult:
    status: str
    rows: int = 0
    requests: int = 0
    detail: str | None = None
    sweep_completed: bool = False


@dataclass
class RunResult:
    run_id: int
    sources: dict[str, SourceResult] = field(default_factory=dict)
    manifest: dict[str, object] | None = None


def _run_sweep(source: Source, ctx: SourceContext) -> bool:
    started = ctx.state.get(source.name, "sweep_started_at")
    if not isinstance(started, str):
        started = ctx.now()
        ctx.state.put(source.name, "sweep_started_at", started)
    while not source.sweep_step(ctx):
        pass
    # Entries that moved to the front of a date-sorted feed mid-sweep were not
    # on the pages the sweep read; catch them before marking anything removed.
    try:
        source.incremental(ctx)
    except (BudgetExhausted, RateLimited):
        pass
    now = ctx.now()
    removed = ctx.catalog.mark_removed(source.name, started, now)
    if removed:
        log.info("%s: marked %d entries removed", source.name, removed)
    source.reset_sweep(ctx)
    ctx.state.put(source.name, "sweep_started_at", None)
    ctx.state.mark_sweep_completed(source.name, now)
    return True


def run_source(source: Source, ctx: SourceContext) -> SourceResult:
    sent_before = ctx.pacer.sent.get(source.name, 0)
    result = SourceResult(status="ok")
    try:
        source.incremental(ctx)
        if source.sweep_due(ctx):
            result.sweep_completed = _run_sweep(source, ctx)
    except BudgetExhausted:
        result.status = "budget"
        result.detail = "request budget used up; continues next run"
    except RateLimited as exc:
        result.status = "rate_limited"
        result.detail = exc.reason
    except Exception as exc:  # one failing source must not stop the others
        log.exception("%s failed", source.name)
        result.status = "error"
        result.detail = f"{type(exc).__name__}: {exc}"
    result.rows = ctx.rows
    result.requests = ctx.pacer.sent.get(source.name, 0) - sent_before
    return result


def manifest_sources(catalog: CatalogStore, state: StateStore) -> dict[str, dict[str, object]]:
    from core.catalog_format import SOURCES

    return {
        name: {
            SOURCE_COUNT: catalog.count(name),
            SOURCE_UPDATED_AT: state.source_status(name)["last_success_at"],
        }
        for name in SOURCES
    }


def run(
    catalog: CatalogStore,
    state: StateStore,
    pacer: Pacer,
    sources: Mapping[str, Source],
    selected: Sequence[str],
    *,
    storage: Storage | None,
    work_dir: str,
    now: Callable[[], str] = utc_now,
) -> RunResult:
    result = RunResult(run_id=state.start_run(now()))
    load_names(catalog)
    for name in selected:
        source = sources[name]
        ctx = SourceContext(catalog, state, pacer, now)
        outcome = run_source(source, ctx)
        state.record_source(
            result.run_id,
            name,
            outcome.status,
            outcome.rows,
            outcome.requests,
            outcome.detail,
            now(),
        )
        log.info(
            "%s: %s, %d rows, %d requests%s",
            name,
            outcome.status,
            outcome.rows,
            outcome.requests,
            f" ({outcome.detail})" if outcome.detail else "",
        )
        result.sources[name] = outcome
    links = rebuild_links(catalog)
    log.info("links: %d", links)
    published: str | None = None
    if storage is not None:
        snapshot = build_snapshot(catalog, os.path.join(work_dir, "snapshots"), now())
        try:
            result.manifest = publish(storage, snapshot, manifest_sources(catalog, state))
            published = snapshot.name
        finally:
            os.remove(snapshot.path)
    state.finish_run(result.run_id, now(), published)
    return result
