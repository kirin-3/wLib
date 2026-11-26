# SPDX-License-Identifier: GPL-3.0-or-later
"""Safe, preview-confirmed storage cleanup actions (design Decision 8).

Every cleanup action declares:
- its current target list (`targets(ctx)`),
- its allowed root folders (`allowed_roots(ctx)`),
- its optional blocked reason (`blocked(ctx)`).

`preview(action)` lists and measures the targets and stores them in memory
under a random token for 15 minutes. `run(preview_id)` drops expired tokens,
acquires the shared storage task lock, re-checks `blocked(ctx)`, re-lists
`targets(ctx)`, verifies each path stays inside an allowed root without
following symlinks, deletes only previewed targets that are still valid, and
updates the saved storage report in place.
"""

from __future__ import annotations

import logging
import os
import secrets
import shutil
import threading
import time
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, final

from core.archive_install import is_within_real, real_path
from core.game_saves import BACKUP_NAME_PATTERN, BACKUP_TIMESTAMP_FORMAT, list_backups
from core.host_platform import get_data_dir
from core.storage_usage import (
    ActionSummary,
    MeasureBudget,
    StorageReport,
    StorageScanService,
    measure,
    recalculate_category_percents,
)

_log = logging.getLogger(__name__)

PREVIEW_TTL_SECONDS = 15 * 60
# How many of the largest targets a preview names for the confirmation.
PREVIEW_ITEM_LIMIT = 8
# Name prefix of the temporary archives that `core.rpgmaker_runner` downloads.
RUNNER_DOWNLOAD_PREFIX = "rpgm-"

CleanupActionId = Literal[
    "download_archives",
    "unpack_leftovers",
    "removed_game_data",
    "save_backups",
    "proton_builds",
    "cover_cache",
    "bepinex_cache",
]

ACTION_ORDER: tuple[CleanupActionId, ...] = (
    "download_archives",
    "unpack_leftovers",
    "removed_game_data",
    "save_backups",
    "proton_builds",
    "cover_cache",
    "bepinex_cache",
)

ACTION_LABELS: dict[CleanupActionId, str] = {
    "download_archives": "Finished download archives",
    "unpack_leftovers": "Temporary install files",
    "removed_game_data": "Data of removed games",
    "save_backups": "Old save backups",
    "proton_builds": "Unused Proton builds",
    "cover_cache": "Cover cache",
    "bepinex_cache": "BepInEx download cache",
}

CleanupTier = Literal["safe", "review", "risky"]

# How much the user can lose with each action:
# - safe: caches and leftovers that wLib downloads or makes again when needed;
# - review: no saves, but the files come back only with a new download;
# - risky: the files can hold game saves or are save backups.
ACTION_TIERS: dict[CleanupActionId, CleanupTier] = {
    "download_archives": "review",
    "unpack_leftovers": "safe",
    "removed_game_data": "risky",
    "save_backups": "risky",
    "proton_builds": "review",
    "cover_cache": "safe",
    "bepinex_cache": "safe",
}


@dataclass(frozen=True)
class CleanupTarget:
    path: str
    bytes: int
    game_id: int | None = None
    label: str = ""


@dataclass(frozen=True)
class PreviewEntry:
    preview_id: str
    action: CleanupActionId
    created_at: float
    targets: tuple[CleanupTarget, ...]
    total_bytes: int
    what_stays: str
    warning: str


@dataclass(frozen=True)
class CleanupContext:
    """Dependencies for cleanup rules, injectable for tests."""

    data_dir: Callable[[], str] = get_data_dir
    running_games: Callable[[], Sequence[int]] = lambda: ()
    open_archives: Callable[[], Collection[str]] = lambda: ()
    ready_queue_archives: Callable[[], Collection[str]] = lambda: ()
    archive_install_running: Callable[[], bool] = lambda: False
    rpgm_runner_preparing: Callable[[], bool] = lambda: False
    bepinex_running: Callable[[], bool] = lambda: False
    save_backup_keep: Callable[[], int | None] = lambda: None
    saves_busy_games: Callable[[], Collection[int]] = lambda: ()
    protected_backups: Callable[[int], Collection[str]] = lambda _gid: ()
    default_proton_path: Callable[[], str] = lambda: ""
    game_proton_versions: Callable[[], Sequence[str]] = lambda: ()
    purge_cover_cache: Callable[[], dict[str, object]] | None = None
    game_title: Callable[[int], str] = lambda _gid: ""


def _norm_key(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _path_strictly_inside_roots(path: str, allowed_roots: Sequence[str]) -> bool:
    """Check that `path` sits strictly inside one of `allowed_roots` without following a final link."""
    if not path:
        return False
    abs_path = os.path.abspath(path)
    parent = os.path.dirname(abs_path)
    if not parent or parent == abs_path:
        return False
    parent_real = real_path(parent)
    is_link = os.path.islink(abs_path)
    target_real = "" if is_link else real_path(abs_path)

    for root in allowed_roots:
        if not root:
            continue
        root_real = real_path(root)
        if not is_within_real(parent_real, root_real):
            continue
        if is_link:
            # The link itself lives inside `root_real`; deleting it unlinks the link.
            return True
        if target_real != root_real and is_within_real(target_real, root_real):
            return True
    return False


def _safe_delete(path: str) -> None:
    """Delete a file, symlink, or directory tree without following symlinks/junctions."""
    if os.path.islink(path) or not os.path.isdir(path):
        os.unlink(path)
    else:
        # On Python 3.12+, shutil.rmtree refuses to enter symlinks or junctions
        # and unlinks them directly.
        shutil.rmtree(path)


# ── Per-action rules ─────────────────────────────────────────────────────


def _allowed_roots(action: CleanupActionId, ctx: CleanupContext) -> list[str]:
    base = ctx.data_dir()
    if action == "download_archives":
        return [os.path.join(base, "downloads")]
    if action == "unpack_leftovers":
        return [os.path.join(base, "install-scratch")]
    if action == "removed_game_data":
        return [
            os.path.join(base, "sandbox", "removed"),
            os.path.join(base, "web-player", "removed"),
        ]
    if action == "save_backups":
        return [os.path.join(base, "save-backups")]
    if action == "proton_builds":
        return [os.path.join(base, "proton")]
    if action == "cover_cache":
        return [
            os.path.join(base, "covers", "cache"),
            os.path.join(base, "covers", "cards"),
        ]
    return [os.path.join(base, "bepinex-cache")]


def action_blocked_reason(action: CleanupActionId, ctx: CleanupContext) -> str | None:
    if action == "unpack_leftovers":
        if ctx.archive_install_running():
            return "An archive install is running."
        if ctx.rpgm_runner_preparing():
            return "The RPG Maker runner is being set up."
    elif action == "save_backups":
        if ctx.save_backup_keep() is None:
            return '"Save backups to keep per game" is set to Keep all.'
    elif action == "proton_builds":
        if ctx.running_games():
            return "A game started by wLib is running."
    elif action == "bepinex_cache":
        if ctx.bepinex_running():
            return "A BepInEx install or update is running."
    return None


def _action_copy(action: CleanupActionId, ctx: CleanupContext) -> tuple[str, str]:
    """Return `(what_stays, warning)` copy for the confirmation dialog."""
    if action == "download_archives":
        return (
            "Partial downloads, downloads waiting to be installed, open archives, and torrent data stay.",
            "",
        )
    if action == "unpack_leftovers":
        return ("Installed games and downloads stay.", "")
    if action == "removed_game_data":
        return (
            "Active games, their sandboxes, and save backups stay.",
            "Removed game sandboxes and built-in player storage can hold game saves.",
        )
    if action == "save_backups":
        keep = ctx.save_backup_keep()
        if keep is None:
            return ("All save backups stay.", "")
        if keep == 1:
            return ("The newest backup for each game stays.", "")
        return (f"The newest {keep} backups for each game stay.", "")
    if action == "proton_builds":
        return (
            "Proton builds used by any game, the default runner, and Steam, Lutris or umu builds stay.",
            "",
        )
    if action == "cover_cache":
        return ("User-chosen local covers stay.", "")
    return ("Installed BepInEx files inside game folders stay.", "")


def _stamp_text(stamp: str) -> str:
    """`2026-11-20_10-30-00` as `2026-11-20 10:30`, or "" for another text."""
    try:
        when = datetime.strptime(stamp, BACKUP_TIMESTAMP_FORMAT)
    except ValueError:
        return ""
    return when.strftime("%Y-%m-%d %H:%M")


def _target_item_label(
    action: CleanupActionId, target: CleanupTarget, ctx: CleanupContext
) -> str:
    """The name of one target as the confirmation lists it."""
    name = target.label or os.path.basename(target.path)
    if action == "removed_game_data":
        # `<game id>_<removal time>`, made by `game_sandbox._retire_folder`.
        kind = (
            "Built-in player data"
            if os.path.basename(os.path.dirname(os.path.dirname(target.path)))
            == "web-player"
            else "Sandbox"
        )
        _gid, _sep, stamp = name.partition("_")
        when = _stamp_text(stamp)
        return f"{kind} of a game removed {when}" if when else f"{kind}: {name}"
    if action == "save_backups":
        title = ctx.game_title(target.game_id) if target.game_id is not None else ""
        match = BACKUP_NAME_PATTERN.match(name)
        when = _stamp_text(match.group(1)) if match else ""
        backup = f"backup from {when}" if when else f"backup {name}"
        return f"{title}: {backup}" if title else backup.capitalize()
    return name


def preview_items(
    action: CleanupActionId, targets: Sequence[CleanupTarget], ctx: CleanupContext
) -> tuple[list[dict[str, object]], int]:
    """The largest targets by name and size, and how many more there are.

    Cover cache files have hashed names that mean nothing to the user, so
    that action names no items.
    """
    if action == "cover_cache" or not targets:
        return [], 0
    largest = sorted(targets, key=lambda t: t.bytes, reverse=True)
    items: list[dict[str, object]] = [
        {"label": _target_item_label(action, t, ctx), "bytes": t.bytes}
        for t in largest[:PREVIEW_ITEM_LIMIT]
    ]
    return items, max(0, len(targets) - PREVIEW_ITEM_LIMIT)


def _list_download_archive_targets(
    ctx: CleanupContext, budget: MeasureBudget
) -> list[CleanupTarget]:
    downloads_dir = os.path.join(ctx.data_dir(), "downloads")
    if not os.path.isdir(downloads_dir):
        return []
    protected_real = {
        os.path.normcase(real_path(path)) for path in ctx.open_archives() if path
    }
    for path in ctx.ready_queue_archives():
        if path:
            protected_real.add(os.path.normcase(real_path(path)))

    # The RPG Maker runner and mkxp-z installs download into the same folder
    # and unpack from there; keep their archives while that install runs.
    runner_busy = ctx.rpgm_runner_preparing()

    targets: list[CleanupTarget] = []
    try:
        entries = sorted(os.scandir(downloads_dir), key=lambda e: e.name)
    except OSError:
        return targets

    for entry in entries:
        budget.check_cancelled()
        if entry.name == "torrents":
            continue
        if entry.name.endswith(".part"):
            continue
        if runner_busy and entry.name.startswith(RUNNER_DOWNLOAD_PREFIX):
            continue
        # Only files in the download staging folder; subdirectories (like torrents/) are untouched.
        try:
            if not entry.is_symlink() and entry.is_dir(follow_symlinks=False):
                continue
        except OSError:
            continue
        entry_real = os.path.normcase(real_path(entry.path))
        if entry_real in protected_real:
            continue
        m = measure(entry.path, budget)
        targets.append(CleanupTarget(path=entry.path, bytes=m.bytes, label=entry.name))
    return targets


def _list_children_targets(
    folders: Sequence[str], budget: MeasureBudget
) -> list[CleanupTarget]:
    targets: list[CleanupTarget] = []
    for folder in folders:
        if not os.path.isdir(folder):
            continue
        try:
            entries = sorted(os.scandir(folder), key=lambda e: e.name)
        except OSError:
            continue
        for entry in entries:
            budget.check_cancelled()
            m = measure(entry.path, budget)
            targets.append(
                CleanupTarget(path=entry.path, bytes=m.bytes, label=entry.name)
            )
    return targets


def _list_save_backup_targets(
    ctx: CleanupContext, budget: MeasureBudget
) -> list[CleanupTarget]:
    keep = ctx.save_backup_keep()
    if keep is None:
        return []
    root = os.path.join(ctx.data_dir(), "save-backups")
    if not os.path.isdir(root):
        return []
    busy_games = set(ctx.saves_busy_games())
    targets: list[CleanupTarget] = []
    try:
        game_dirs = sorted(os.scandir(root), key=lambda e: e.name)
    except OSError:
        return targets

    for entry in game_dirs:
        budget.check_cancelled()
        if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
            continue
        if not entry.name.isascii() or not entry.name.isdigit():
            continue
        game_id = int(entry.name)
        if game_id in busy_games:
            continue
        protected = set(ctx.protected_backups(game_id))
        excess = list_backups(entry.path)[keep:]
        for backup_name in excess:
            if backup_name in protected:
                continue
            backup_path = os.path.join(entry.path, backup_name)
            m = measure(backup_path, budget)
            targets.append(
                CleanupTarget(
                    path=backup_path,
                    bytes=m.bytes,
                    game_id=game_id,
                    label=backup_name,
                )
            )
    return targets


def _resolve_used_proton_paths(proton_dir: str, raw_paths: Sequence[str]) -> list[str]:
    used: list[str] = []
    for raw in raw_paths:
        text = raw.strip()
        if not text:
            continue
        expanded = os.path.expanduser(text)
        if os.path.isabs(expanded):
            used.append(real_path(expanded))
        else:
            # Bare build folder name inside wLib's proton directory.
            used.append(real_path(os.path.join(proton_dir, expanded)))
    return used


def _list_proton_targets(
    ctx: CleanupContext, budget: MeasureBudget
) -> list[CleanupTarget]:
    proton_dir = os.path.join(ctx.data_dir(), "proton")
    if not os.path.isdir(proton_dir):
        return []
    raw_used = [ctx.default_proton_path(), *ctx.game_proton_versions()]
    used_reals = _resolve_used_proton_paths(proton_dir, raw_used)

    targets: list[CleanupTarget] = []
    try:
        entries = sorted(os.scandir(proton_dir), key=lambda e: e.name)
    except OSError:
        return targets

    for entry in entries:
        budget.check_cancelled()
        if not entry.is_symlink() and not entry.is_dir(follow_symlinks=False):
            continue
        build_real = real_path(entry.path)
        if any(
            used == build_real or is_within_real(used, build_real)
            for used in used_reals
        ):
            continue
        m = measure(entry.path, budget)
        targets.append(CleanupTarget(path=entry.path, bytes=m.bytes, label=entry.name))
    return targets


def _list_cover_cache_targets(
    ctx: CleanupContext, budget: MeasureBudget
) -> list[CleanupTarget]:
    covers_root = os.path.join(ctx.data_dir(), "covers")
    cache_dir = os.path.join(covers_root, "cache")
    cards_dir = os.path.join(covers_root, "cards")

    targets: list[CleanupTarget] = []
    if os.path.isdir(cache_dir):
        try:
            for entry in sorted(os.scandir(cache_dir), key=lambda e: e.name):
                budget.check_cancelled()
                if not entry.is_symlink() and entry.is_dir(follow_symlinks=False):
                    continue
                m = measure(entry.path, budget)
                targets.append(
                    CleanupTarget(path=entry.path, bytes=m.bytes, label=entry.name)
                )
        except OSError:
            pass

    # Only card copies belonging to cached covers (not user-chosen local covers).
    local_stems: set[str] = set()
    if os.path.isdir(covers_root):
        try:
            for entry in os.scandir(covers_root):
                if entry.is_file(follow_symlinks=False):
                    local_stems.add(entry.name.split(".", 1)[0])
        except OSError:
            pass

    if os.path.isdir(cards_dir):
        try:
            for entry in sorted(os.scandir(cards_dir), key=lambda e: e.name):
                budget.check_cancelled()
                if not entry.is_symlink() and entry.is_dir(follow_symlinks=False):
                    continue
                # Hidden `.card-*` files are copies still being written.
                if entry.name.startswith("."):
                    continue
                # After the purge only local covers remain, so the sweep keeps
                # only their card copies.
                if entry.name.split(".", 1)[0] in local_stems:
                    continue
                m = measure(entry.path, budget)
                targets.append(
                    CleanupTarget(path=entry.path, bytes=m.bytes, label=entry.name)
                )
        except OSError:
            pass

    return targets


def list_action_targets(
    action: CleanupActionId,
    ctx: CleanupContext,
    budget: MeasureBudget | None = None,
) -> list[CleanupTarget]:
    active_budget = budget if budget is not None else MeasureBudget()
    if action == "download_archives":
        return _list_download_archive_targets(ctx, active_budget)
    if action == "unpack_leftovers":
        return _list_children_targets(
            [os.path.join(ctx.data_dir(), "install-scratch")], active_budget
        )
    if action == "removed_game_data":
        return _list_children_targets(
            [
                os.path.join(ctx.data_dir(), "sandbox", "removed"),
                os.path.join(ctx.data_dir(), "web-player", "removed"),
            ],
            active_budget,
        )
    if action == "save_backups":
        return _list_save_backup_targets(ctx, active_budget)
    if action == "proton_builds":
        return _list_proton_targets(ctx, active_budget)
    if action == "cover_cache":
        return _list_cover_cache_targets(ctx, active_budget)
    return _list_children_targets(
        [os.path.join(ctx.data_dir(), "bepinex-cache")], active_budget
    )


def measure_all_actions(
    ctx: CleanupContext, budget: MeasureBudget | None = None
) -> dict[str, ActionSummary]:
    """Compute summary counts and sizes for all 7 cleanup actions (stored in report)."""
    active_budget = budget if budget is not None else MeasureBudget()
    summary: dict[str, ActionSummary] = {}
    for action in ACTION_ORDER:
        active_budget.check_cancelled()
        # Use a separate inode set per action so hard links inside the scan don't
        # zero out cleanup sizes, while keeping the scan's cancel event.
        action_budget = MeasureBudget(cancel=active_budget.cancel)
        targets = list_action_targets(action, ctx, action_budget)
        summary[action] = {
            "bytes": sum(t.bytes for t in targets),
            "count": len(targets),
        }
    return summary


# ── Cleanup service (previews + execution + report updates) ──────────────


@final
class StorageCleanupService:
    """Manages cleanup previews, validated deletions and in-place report updates."""

    def __init__(
        self,
        scan_service: StorageScanService,
        context_factory: Callable[[], CleanupContext],
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._scan = scan_service
        self._context_factory = context_factory
        self._clock = clock
        self._lock = threading.Lock()
        self._previews: dict[str, PreviewEntry] = {}

    def _prune_expired_locked(self, now: float) -> None:
        expired = [
            pid
            for pid, entry in self._previews.items()
            if now - entry.created_at > PREVIEW_TTL_SECONDS
        ]
        for pid in expired:
            del self._previews[pid]

    def action_statuses(self, report: StorageReport | None) -> list[dict[str, object]]:
        """Action cards metadata combining saved report sizes with live blocked reasons."""
        ctx = self._context_factory()
        busy_reason: str | None = None
        if self._scan.is_busy():
            task = self._scan.current_task()
            busy_reason = (
                "A storage scan is running."
                if task in ("scan", "recount")
                else "A cleanup is running."
            )
        saved_actions = report.get("actions", {}) if report is not None else {}
        result: list[dict[str, object]] = []
        for action in ACTION_ORDER:
            summary = saved_actions.get(action, {"bytes": 0, "count": 0})
            blocked = busy_reason or action_blocked_reason(action, ctx)
            what_stays, warning = _action_copy(action, ctx)
            result.append(
                {
                    "id": action,
                    "label": ACTION_LABELS[action],
                    "tier": ACTION_TIERS[action],
                    "bytes": int(summary.get("bytes", 0)),
                    "count": int(summary.get("count", 0)),
                    "blocked_reason": blocked or "",
                    "what_stays": what_stays,
                    "warning": warning,
                }
            )
        return result

    def preview(self, action: str) -> dict[str, object]:
        if action not in ACTION_LABELS:
            return {"success": False, "error": f"Unknown cleanup action: {action}"}
        action_id: CleanupActionId = action  # type: ignore[assignment]
        if self._scan.is_busy():
            return {
                "success": False,
                "code": "task_running",
                "error": "A storage task is already running. Wait for it to finish.",
            }
        ctx = self._context_factory()
        blocked = action_blocked_reason(action_id, ctx)
        if blocked:
            return {"success": False, "code": "action_blocked", "error": blocked}

        targets = list_action_targets(action_id, ctx)
        total_bytes = sum(t.bytes for t in targets)
        what_stays, warning = _action_copy(action_id, ctx)
        items, more_items = preview_items(action_id, targets, ctx)
        preview_id = secrets.token_urlsafe(16)
        now = self._clock()
        entry = PreviewEntry(
            preview_id=preview_id,
            action=action_id,
            created_at=now,
            targets=tuple(targets),
            total_bytes=total_bytes,
            what_stays=what_stays,
            warning=warning,
        )
        with self._lock:
            self._prune_expired_locked(now)
            self._previews[preview_id] = entry

        return {
            "success": True,
            "preview_id": preview_id,
            "action": action_id,
            "label": ACTION_LABELS[action_id],
            "tier": ACTION_TIERS[action_id],
            "count": len(targets),
            "bytes": total_bytes,
            "what_stays": what_stays,
            "warning": warning,
            "items": items,
            "more_items": more_items,
        }

    def run(self, preview_id: str) -> dict[str, object]:
        now = self._clock()
        with self._lock:
            self._prune_expired_locked(now)
            entry = self._previews.pop(preview_id, None)
        if entry is None:
            return {
                "success": False,
                "code": "preview_expired",
                "error": "This cleanup preview has expired. Choose the action again.",
            }

        try:
            task_guard = self._scan.acquire_cleanup_task()
        except Exception as exc:
            return {"success": False, "code": "task_running", "error": str(exc)}

        try:
            with task_guard:
                ctx = self._context_factory()
                blocked = action_blocked_reason(entry.action, ctx)
                if blocked:
                    return {
                        "success": False,
                        "code": "action_blocked",
                        "error": blocked,
                    }

                if entry.action == "cover_cache":
                    freed, removed, failed, skipped = self._run_cover_cache(entry, ctx)
                else:
                    freed, removed, failed, skipped = self._run_path_targets(entry, ctx)

                self._update_report_after_cleanup(entry.action, entry.targets, ctx)
                return {
                    "success": True,
                    "action": entry.action,
                    "freed_bytes": freed,
                    "removed": removed,
                    "failed": failed,
                    "skipped": skipped,
                }
        except RuntimeError as exc:
            return {"success": False, "code": "task_running", "error": str(exc)}
        except Exception as exc:
            _log.exception("Cleanup action %s failed", entry.action)
            return {"success": False, "error": str(exc) or "Cleanup failed"}

    def _run_cover_cache(
        self, entry: PreviewEntry, ctx: CleanupContext
    ) -> tuple[int, int, int, int]:
        if not entry.targets:
            return 0, 0, 0, 0
        before_targets = list_action_targets("cover_cache", ctx)
        before_bytes = sum(t.bytes for t in before_targets)
        before_count = len(before_targets)
        if before_count == 0:
            return 0, 0, 0, len(entry.targets)

        if ctx.purge_cover_cache is not None:
            res = ctx.purge_cover_cache()
            if res.get("success") is False:
                raise OSError(str(res.get("error") or "Failed to purge cover cache"))
        else:
            # Fallback when invoked in isolation without Api: delete validated targets.
            allowed = _allowed_roots("cover_cache", ctx)
            for target in before_targets:
                if _path_strictly_inside_roots(target.path, allowed):
                    _safe_delete(target.path)

        after_targets = list_action_targets("cover_cache", ctx)
        after_bytes = sum(t.bytes for t in after_targets)
        after_count = len(after_targets)
        freed = max(0, before_bytes - after_bytes)
        removed = max(0, before_count - after_count)
        failed = after_count
        return freed, removed, failed, 0

    def _run_path_targets(
        self, entry: PreviewEntry, ctx: CleanupContext
    ) -> tuple[int, int, int, int]:
        allowed = _allowed_roots(entry.action, ctx)
        fresh_targets = list_action_targets(entry.action, ctx)
        fresh_by_key = {_norm_key(t.path): t for t in fresh_targets}

        freed = 0
        removed = 0
        failed = 0
        skipped = 0

        for previewed in entry.targets:
            key = _norm_key(previewed.path)
            current = fresh_by_key.get(key)
            if current is None:
                # Target disappeared or became protected between preview and run.
                skipped += 1
                continue
            if not _path_strictly_inside_roots(current.path, allowed):
                skipped += 1
                continue
            try:
                _safe_delete(current.path)
            except OSError as exc:
                _log.warning("Could not delete %s: %s", current.path, exc)
                failed += 1
                continue
            freed += current.bytes
            removed += 1

        return freed, removed, failed, skipped

    def _update_report_after_cleanup(
        self,
        action: CleanupActionId,
        targets: Sequence[CleanupTarget],
        ctx: CleanupContext,
    ) -> None:
        report = self._scan.get_report()
        if report is None:
            return
        data_dir = ctx.data_dir()
        cat_map = {cat["id"]: cat for cat in report["categories"]}

        def _set_cat(cat_id: str, total_bytes: int, total_files: int) -> None:
            if cat_id in cat_map:
                cat_map[cat_id]["bytes"] = total_bytes
                cat_map[cat_id]["files"] = total_files

        if action == "download_archives":
            downloads_dir = os.path.join(data_dir, "downloads")
            b_sum = f_sum = 0
            if os.path.isdir(downloads_dir):
                for entry in os.scandir(downloads_dir):
                    if entry.name == "torrents" or entry.is_symlink():
                        continue
                    m = measure(entry.path)
                    b_sum += m.bytes
                    f_sum += m.files
            _set_cat("download_archives", b_sum, f_sum)

        elif action in ("unpack_leftovers", "bepinex_cache"):
            b_sum = f_sum = 0
            for name in ("bepinex-cache", "install-scratch", "webview"):
                folder = os.path.join(data_dir, name)
                if os.path.isdir(folder) and not os.path.islink(folder):
                    m = measure(folder)
                    b_sum += m.bytes
                    f_sum += m.files
            _set_cat("other_caches", b_sum, f_sum)

        elif action == "removed_game_data":
            b_sum = f_sum = 0
            for sub in ("sandbox", "web-player"):
                folder = os.path.join(data_dir, sub, "removed")
                if os.path.isdir(folder) and not os.path.islink(folder):
                    m = measure(folder)
                    b_sum += m.bytes
                    f_sum += m.files
            _set_cat("removed_game_data", b_sum, f_sum)

        elif action == "save_backups":
            affected_gids = {t.game_id for t in targets if t.game_id is not None}
            save_root = os.path.join(data_dir, "save-backups")
            b_sum = f_sum = 0
            if os.path.isdir(save_root):
                for entry in os.scandir(save_root):
                    if entry.is_symlink():
                        continue
                    m = measure(entry.path)
                    b_sum += m.bytes
                    f_sum += m.files
                    if (
                        entry.name.isascii()
                        and entry.name.isdigit()
                        and int(entry.name) in affected_gids
                    ):
                        row = report["games"].get(entry.name)
                        if row is not None:
                            row["parts"]["saves"] = m.bytes
                            parts = row["parts"]
                            row["total_bytes"] = (
                                parts["game"]
                                + parts["saves"]
                                + parts["player"]
                                + parts["sandbox"]
                                + parts["prefix"]
                            )
            _set_cat("save_backups", b_sum, f_sum)

        elif action == "proton_builds":
            proton_root = os.path.join(data_dir, "proton")
            b_sum = f_sum = 0
            if os.path.isdir(proton_root):
                for entry in os.scandir(proton_root):
                    if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                        continue
                    m = measure(entry.path)
                    b_sum += m.bytes
                    f_sum += m.files
            _set_cat("proton_builds", b_sum, f_sum)

        elif action == "cover_cache":
            b_sum = f_sum = 0
            for sub in ("cache", "cards"):
                folder = os.path.join(data_dir, "covers", sub)
                if os.path.isdir(folder) and not os.path.islink(folder):
                    m = measure(folder)
                    b_sum += m.bytes
                    f_sum += m.files
            _set_cat("cover_cache", b_sum, f_sum)

        # Refresh action summary for the action that ran.
        remaining = list_action_targets(action, ctx)
        report.setdefault("actions", {})[action] = {
            "bytes": sum(t.bytes for t in remaining),
            "count": len(remaining),
        }
        report["total_files"] = sum(int(cat["files"]) for cat in report["categories"])
        recalculate_category_percents(report)
        self._scan.replace_report(report)
