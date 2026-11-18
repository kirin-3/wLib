# SPDX-License-Identifier: GPL-3.0-or-later
"""A Discord webhook report after each run that publishes (or tries to).

The report gives each step's new, removed and total rows, the requests it
sent, and every problem: failed or rate-limited steps, lost logins, steps
skipped for a missing cookie, a refused publish and an error that ended the run.
"""

import json
import logging
from collections.abc import Mapping
from datetime import datetime
from typing import cast

from core.catalog_format import MANIFEST_SNAPSHOT, SNAPSHOT_PATH, SNAPSHOT_SIZE

from .config import USER_AGENT
from .net import Request, Transport, urllib_transport
from .runner import RunResult, SourceResult
from .sources.f95_threads import NO_LOGIN as F95_NO_LOGIN
from .sources.lc import NO_LOGIN as LC_NO_LOGIN

log = logging.getLogger("catalog_server.notify")

COLOR_OK = 0x2ECC71
COLOR_WARNING = 0xE67E22
COLOR_FAILED = 0xE74C3C

# Discord's limits are 1024 characters per field value and 4096 per description.
_FIELD_LIMIT = 1024
_DETAIL_LIMIT = 300

_LOGIN_TEXTS = (F95_NO_LOGIN, LC_NO_LOGIN)


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _is_login_problem(outcome: SourceResult) -> bool:
    return any(text in (outcome.detail or "") for text in _LOGIN_TEXTS)


def _duration(start: str, end: str) -> str:
    try:
        seconds = int(
            (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds()
        )
    except ValueError:
        return "?"
    hours, rest = divmod(max(seconds, 0), 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{hours}h {minutes}m" if hours else f"{minutes}m {seconds}s"


def _step_line(name: str, outcome: SourceResult, is_source: bool) -> str:
    if is_source:
        counts = f"+{outcome.added:,} new · −{outcome.removed:,} removed · {outcome.total:,} total"
    else:
        counts = f"{outcome.rows:,} pages read · {outcome.total:,} total"
    sweep = " · sweep done" if outcome.sweep_completed else ""
    return f"**{name}** ({outcome.status}): {counts} · {outcome.requests:,} req{sweep}"


def build_report(
    result: RunResult,
    *,
    sources: frozenset[str],
    skipped: Mapping[str, str],
) -> dict[str, object]:
    """The webhook payload. `sources` are the steps with their own rows (not
    detail steps); `skipped` maps steps left out of the run to the reason."""
    problems: list[str] = []
    notes: list[str] = []
    for name, outcome in result.sources.items():
        detail = _cut(outcome.detail or "", _DETAIL_LIMIT)
        if outcome.status == "ok":
            continue
        if _is_login_problem(outcome):
            problems.append(f"🔑 **{name}** lost its login: {detail}")
        elif outcome.status == "budget":
            notes.append(f"{name}: {detail}")
        else:
            problems.append(f"**{name}** {outcome.status}: {detail}")
    for name, reason in skipped.items():
        problems.append(f"🔑 **{name}** skipped: {reason}")
    if result.publish_refused:
        problems.append(f"Publish refused: {result.publish_refused}")
    if result.error:
        problems.append(f"Run failed: {_cut(result.error, _DETAIL_LIMIT)}")

    published = result.manifest is not None
    if not published:
        title, color = "Catalog not published", COLOR_FAILED
    elif problems:
        title, color = "Catalog published, with problems", COLOR_WARNING
    else:
        title, color = "Catalog published", COLOR_OK

    lines = [f"Run #{result.run_id} · {_duration(result.started_at, result.finished_at)}"]
    if result.manifest is not None:
        snapshot = result.manifest.get(MANIFEST_SNAPSHOT)
        if isinstance(snapshot, dict):
            info = cast(dict[str, object], snapshot)
            path = info.get(SNAPSHOT_PATH)
            size = info.get(SNAPSHOT_SIZE)
            megabytes = f" ({size / 1_000_000:.1f} MB)" if isinstance(size, int) else ""
            lines.append(f"Snapshot `{path}`{megabytes}")
    if result.links is not None:
        lines.append(f"Links: {result.links:,}")

    steps = [
        _step_line(name, outcome, name in sources) for name, outcome in result.sources.items()
    ]
    fields: list[dict[str, object]] = [
        {
            "name": "Sources",
            "value": _cut("\n".join(steps) or "No sources ran.", _FIELD_LIMIT),
        },
        {
            "name": "Problems",
            "value": _cut("\n".join(problems) or "None.", _FIELD_LIMIT),
        },
    ]
    if notes:
        fields.append({"name": "Notes", "value": _cut("\n".join(notes), _FIELD_LIMIT)})
    return {
        "username": "wLib catalog",
        # Text from sites and errors must never ping anyone.
        "allowed_mentions": {"parse": []},
        "embeds": [
            {
                "title": title,
                "color": color,
                "description": "\n".join(lines),
                "fields": fields,
                "timestamp": result.finished_at or None,
            }
        ],
    }


def send_report(
    webhook: str, payload: Mapping[str, object], transport: Transport = urllib_transport
) -> bool:
    """POST the report; a failure is logged and never raised. The log never shows the URL."""
    request = Request(
        "POST",
        webhook,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        body=json.dumps(payload).encode(),
    )
    try:
        response = transport(request)
    except ConnectionError as exc:
        log.warning("the Discord report was not sent: %s", type(exc).__name__)
        return False
    if response.status >= 300:
        log.warning("Discord refused the report: HTTP %s", response.status)
        return False
    return True
