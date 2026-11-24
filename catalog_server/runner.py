# SPDX-License-Identifier: GPL-3.0-or-later
"""One builder run: each source's incremental fetch, then its sweep with the
remaining budget, then links, then (optionally) the snapshot and publish."""

import logging
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from core.catalog_format import SOURCE_COUNT, SOURCE_UPDATED_AT

from .groups import rebuild_groups
from .links import rebuild_links
from .net import BudgetExhausted, Pacer, RateLimited
from .publish import Storage, publish, publish_refusal, published_sources
from .snapshot import build_snapshot
from .sources import Source, SourceContext
from .sources.f95 import load_names
from .store import SOURCE_TABLES, CatalogStore, StateStore, utc_now

log = logging.getLogger("catalog_server.run")


@dataclass
class SourceResult:
    status: str
    rows: int = 0
    requests: int = 0
    detail: str | None = None
    sweep_completed: bool = False
    # Rows first seen and rows marked removed in this run; sources only, not detail steps.
    added: int = 0
    removed: int = 0
    total: int = 0


@dataclass
class RunResult:
    run_id: int
    sources: dict[str, SourceResult] = field(default_factory=dict)
    manifest: dict[str, object] | None = None
    # Why the snapshot was not published, when the safety check refused it.
    publish_refused: str = ""
    started_at: str = ""
    finished_at: str = ""
    links: int | None = None
    groups: int | None = None
    # The error that ended the run after the sources, e.g. a failed upload.
    error: str = ""


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
    # An entry deleted on an earlier page shifts the rest back a place, so a
    # sweep can miss one that still exists. Only entries missing from two
    # completed sweeps in a row are marked removed.
    previous = ctx.state.get(source.name, "previous_sweep_started_at")
    if isinstance(previous, str):
        removed = ctx.catalog.mark_removed(source.name, previous, now)
        if removed:
            log.info("%s: marked %d entries removed", source.name, removed)
    ctx.state.put(source.name, "previous_sweep_started_at", started)
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


def manifest_sources(
    catalog: CatalogStore, state: StateStore
) -> dict[str, dict[str, object]]:
    from core.catalog_format import SOURCES

    return {
        name: {
            # The whole table: the LC count includes the Plus threads.
            SOURCE_COUNT: catalog.count(name, whole_table=True),
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
    force_publish: bool = False,
    report: Callable[[RunResult], None] | None = None,
) -> RunResult:
    """`report` gets the result when the run ends, also when it ends with an error."""
    started_at = now()
    result = RunResult(run_id=state.start_run(started_at), started_at=started_at)
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
        if name in SOURCE_TABLES:
            outcome.added, outcome.removed = catalog.changes_since(name, started_at)
        outcome.total = catalog.count(name)
        result.sources[name] = outcome
    published: str | None = None
    try:
        links = rebuild_links(catalog, state.imported_links())
        result.links = links
        log.info("links: %d", links)
        groups = rebuild_groups(catalog)
        result.groups = groups
        log.info("groups: %d", groups)
        if storage is not None:
            counts = manifest_sources(catalog, state)
            refusal = publish_refusal(published_sources(storage), counts)
            if refusal and not force_publish:
                result.publish_refused = refusal
                log.error(
                    "not publishing: %s; pass --force-publish if this is intended",
                    refusal,
                )
            else:
                snapshot = build_snapshot(
                    catalog, os.path.join(work_dir, "snapshots"), now()
                )

                def _published(manifest: dict[str, object]) -> None:
                    # The manifest is live: a later retention error must not
                    # report this run as unpublished.
                    result.manifest = manifest

                try:
                    result.manifest = publish(
                        storage, snapshot, counts, on_published=_published
                    )
                finally:
                    if result.manifest is not None:
                        published = snapshot.name
                    os.remove(snapshot.path)
    except Exception as exc:
        result.error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        # A failed link rebuild or publish still closes the run, with nothing marked published.
        result.finished_at = now()
        state.finish_run(result.run_id, result.finished_at, published)
        if report is not None:
            try:
                report(result)
            except Exception:  # noqa: BLE001 - a failed report must not fail the run
                log.exception("sending the run report failed")
    return result
