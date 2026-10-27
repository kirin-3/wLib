# SPDX-License-Identifier: GPL-3.0-or-later
"""Command line: `python -m catalog_server run|status`."""

import argparse
import json
import logging
import os
import sys
import time
from collections.abc import Callable, Generator, Sequence
from contextlib import contextmanager
from typing import IO

from core.catalog_format import SOURCES

from .config import data_dir, r2_config_from_env
from .net import Pacer, Transport, urllib_transport
from .publish import R2Storage, Storage
from .runner import run
from .sources import get_sources
from .store import open_stores

log = logging.getLogger("catalog_server")


def _source_list(value: str) -> list[str]:
    if value == "all":
        return list(SOURCES)
    if value == "none":
        return []
    names = [part.strip() for part in value.split(",") if part.strip()]
    unknown = [name for name in names if name not in SOURCES]
    if unknown:
        raise argparse.ArgumentTypeError(
            f"unknown source(s): {', '.join(unknown)}; choose from {', '.join(SOURCES)}"
        )
    return names


def _until_swept(value: str) -> list[str]:
    if value == "none":
        raise argparse.ArgumentTypeError("use a source name or 'all'")
    return _source_list(value)


class Args(argparse.Namespace):
    data_dir: str | None = None
    command: str = ""
    sources: list[str] = []
    until_swept: list[str] = []
    no_publish: bool = False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m catalog_server",
        description="Build and publish the wLib game catalog.",
    )
    _ = parser.add_argument(
        "--data-dir",
        default=None,
        help="builder data folder (default: $WLIB_CATALOG_DATA_DIR or /var/lib/wlib-catalog)",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    run_parser = commands.add_parser(
        "run", help="collect from the sources, then build and publish a snapshot"
    )
    _ = run_parser.add_argument(
        "--sources",
        type=_source_list,
        default=list(SOURCES),
        help="comma-separated sources to collect, 'all' (default) or 'none' to only publish",
    )
    _ = run_parser.add_argument(
        "--until-swept",
        type=_until_swept,
        default=[],
        metavar="SOURCE|all",
        help="wait out request budgets until these sources finish a full sweep",
    )
    _ = run_parser.add_argument(
        "--no-publish", action="store_true", help="skip the snapshot and upload"
    )
    _ = commands.add_parser("status", help="show recent runs and source cursors")
    return parser


def _try_lock(handle: IO[str]) -> bool:
    # The builder runs on Linux; the Windows branch keeps the tests importable there.
    if sys.platform == "win32":
        import msvcrt  # pyright: ignore[reportUnreachable]

        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True
    import fcntl

    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


@contextmanager
def _run_lock(folder: str) -> Generator[bool]:
    """Yield False when another run holds the lock, so runs never overlap."""
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "run.lock"), "w") as handle:
        yield _try_lock(handle)


def _status(folder: str) -> int:
    catalog, state = open_stores(folder)
    try:
        report = {
            "runs": state.recent_runs(),
            "sources": {
                name: {
                    "rows": catalog.count(name),
                    **state.source_status(name),
                    "state": state.items(name),
                    "requests_last_hour": len(state.request_times(name, time.time() - 3600)),
                }
                for name in SOURCES
            },
        }
    finally:
        catalog.close()
        state.close()
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


def main(
    argv: Sequence[str] | None = None,
    *,
    transport: Transport = urllib_transport,
    sleep: Callable[[float], None] = time.sleep,
    storage_factory: Callable[[], Storage | None] | None = None,
) -> int:
    args = build_parser().parse_args(argv, namespace=Args())
    folder = args.data_dir or data_dir()
    if args.command == "status":
        return _status(folder)

    storage: Storage | None = None
    if not args.no_publish:
        if storage_factory is not None:
            storage = storage_factory()
        else:
            config = r2_config_from_env()
            if config is None:
                log.error(
                    "publishing needs R2_ACCOUNT_ID, R2_ACCESS_KEY_ID and "
                    + "R2_SECRET_ACCESS_KEY; pass --no-publish to skip it"
                )
                return 2
            storage = R2Storage(config)

    selected = list(args.sources)
    until = list(args.until_swept)
    for name in until:
        if name not in selected:
            selected.append(name)
    with _run_lock(folder) as acquired:
        if not acquired:
            log.info("another catalog run is in progress; skipping this one")
            return 0
        catalog, state = open_stores(folder)
        try:
            pacer = Pacer(state, transport, sleep=sleep, wait_for_budget=until)
            result = run(
                catalog,
                state,
                pacer,
                get_sources(),
                [name for name in SOURCES if name in selected],
                storage=storage,
                work_dir=folder,
            )
        finally:
            catalog.close()
            state.close()
    failed = [name for name, outcome in result.sources.items() if outcome.status == "error"]
    unswept = [
        name
        for name in until
        if name in result.sources and not result.sources[name].sweep_completed
        and result.sources[name].status != "ok"
    ]
    return 1 if failed or unswept else 0


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    sys.exit(main())
