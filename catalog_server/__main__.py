# SPDX-License-Identifier: GPL-3.0-or-later
"""Command line: `python -m catalog_server run|status|check-f95-login|check-lc-login`."""

import argparse
import json
import logging
import os
import re
import sys
import time
from collections.abc import Callable, Generator, Mapping, Sequence
from contextlib import contextmanager
from typing import IO

from core.catalog_format import SOURCES

from .browser import BrowserTransport, f95_login_cookies, lc_login_cookies
from .config import (
    BROWSER_SITES_ENV,
    browser_sites_from_env,
    data_dir,
    discord_webhook_from_env,
    f95_cookie_from_env,
    lc_cookie_from_env,
    r2_config_from_env,
    site_proxies_from_env,
)
from .net import Pacer, Request, Transport, urllib_transport
from .notify import build_report, send_report
from .publish import R2Storage, Storage
from .runner import RunResult, run
from .sources import STEPS, get_sources
from .store import SOURCE_TABLES, open_stores

log = logging.getLogger("catalog_server")


def _source_list(value: str) -> list[str]:
    if value == "all":
        return list(STEPS)
    if value == "none":
        return []
    names = [part.strip() for part in value.split(",") if part.strip()]
    unknown = [name for name in names if name not in STEPS]
    if unknown:
        raise argparse.ArgumentTypeError(
            f"unknown source(s): {', '.join(unknown)}; choose from {', '.join(STEPS)}"
        )
    return names


def _until_swept(value: str) -> list[str]:
    if value == "none":
        raise argparse.ArgumentTypeError("use a source name or 'all'")
    if value == "all":
        # Only the sources have a sweep; builder steps such as f95_threads do not.
        return list(SOURCES)
    names = _source_list(value)
    unswept = [name for name in names if name not in SOURCE_TABLES]
    if unswept:
        raise argparse.ArgumentTypeError(f"{', '.join(unswept)} has no sweep")
    return names


class Args(argparse.Namespace):
    data_dir: str | None = None
    command: str = ""
    sources: list[str] = []
    until_swept: list[str] = []
    no_publish: bool = False
    force_publish: bool = False


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
        default=list(STEPS),
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
    _ = run_parser.add_argument(
        "--force-publish",
        action="store_true",
        help="publish even when the catalog is empty or a source lost over half its rows",
    )
    _ = commands.add_parser("status", help="show recent runs and source cursors")
    _ = commands.add_parser(
        "check-f95-login", help="open F95 in the browser with the login cookie and report the account"
    )
    _ = commands.add_parser(
        "check-lc-login",
        help="open LewdCorner in the browser with the login cookie and report the account",
    )
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
                for name in STEPS
            },
        }
    finally:
        catalog.close()
        state.close()
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


F95_HOME_URL = "https://f95zone.to/"
LC_HOME_URL = "https://lewdcorner.com/"
_LOGGED_IN_RE = re.compile(r'<html[^>]*\sdata-logged-in="true"')
_ACCOUNT_NAME_RE = re.compile(
    r'<a href="/account/"[^>]*>.*?<span class="p-navgroup-linkText">([^<]+)</span>', re.S
)


# Pacer keys whose requests carry the site's login cookie. LC's thread pages
# and Plus listings are read as a guest.
LOGIN_SITES = frozenset({"f95", "f95_threads", "lc"})


class NetworkSettings:
    """Proxies, browser sites and the F95 and LC logins, read from the environment."""

    def __init__(self) -> None:
        self.proxies: dict[str, str] = site_proxies_from_env()
        sites = set(browser_sites_from_env())
        # Thread pages and the Plus listings go the same way as their feed.
        for feed, steps in (("f95", ("f95_threads",)), ("lc", ("lc_plus", "lc_threads"))):
            if feed in sites:
                sites.update(steps)
        self.browser_sites: frozenset[str] = frozenset(sites)
        self.f95_cookie: str | None = f95_cookie_from_env()
        if self.f95_cookie is not None and "f95" not in self.browser_sites:
            # Only the browser keeps the cookie to f95zone.to.
            raise ValueError(f"the F95 login cookie needs f95 in {BROWSER_SITES_ENV}")
        self.lc_cookie: str | None = lc_cookie_from_env()
        if self.lc_cookie is not None and "lc" not in self.browser_sites:
            # Only the browser keeps the cookie to lewdcorner.com.
            raise ValueError(f"the LC login cookie needs lc in {BROWSER_SITES_ENV}")

    def cookies(self) -> list[dict[str, object]]:
        """Each login cookie is scoped to its own site's domain."""
        cookies: list[dict[str, object]] = []
        if self.f95_cookie:
            cookies.extend(f95_login_cookies(self.f95_cookie))
        if self.lc_cookie:
            cookies.extend(lc_login_cookies(self.lc_cookie))
        return cookies

    def transports(self, browser: BrowserTransport | None) -> dict[str, Transport] | None:
        """The browser view per browser site: the login view for the feeds
        that need it, the guest view (no cookies) for everything else."""
        if browser is None:
            return None
        guest = browser.guest()
        return {
            site: browser if site in LOGIN_SITES else guest for site in self.browser_sites
        }

    def browser(self, given: BrowserTransport | None) -> BrowserTransport | None:
        if not self.browser_sites:
            return None
        if given is not None:
            return given
        return BrowserTransport(cookies=self.cookies())


def _check_login(
    folder: str,
    browser: BrowserTransport | None,
    *,
    site: str,
    home_url: str,
    cookie_hint: str,
) -> int:
    if browser is None:  # NetworkSettings needs the site in the browser sites for a cookie.
        return 2
    with _run_lock(folder) as acquired:
        if not acquired:
            log.error("a catalog run is in progress; try again when it ends")
            return 1
        # One request to the home page, outside the Pacer: its budget is for
        # the site's rate-limited feed, which this does not use.
        try:
            response = browser(Request("GET", home_url))
        except ConnectionError as exc:
            log.error("could not open %s: %s", site, exc)
            return 1
        finally:
            browser.close()
    if response.status != 200:
        log.error("%s answered HTTP %s", site, response.status)
        return 1
    html = response.text()
    if not _LOGGED_IN_RE.search(html):
        log.error("%s does not see a login; %s", site, cookie_hint)
        return 1
    name = _ACCOUNT_NAME_RE.search(html)
    print(f"Logged in to {site} as {name.group(1).strip() if name else '(unknown name)'}")
    return 0


def _check_f95_login(folder: str, settings: NetworkSettings, given: BrowserTransport | None) -> int:
    if settings.f95_cookie is None:
        log.error("no F95 login: set WLIB_CATALOG_F95_COOKIE in /etc/wlib-catalog/f95.env")
        return 2
    return _check_login(
        folder,
        settings.browser(given),
        site="F95",
        home_url=F95_HOME_URL,
        cookie_hint="the xf_user cookie is wrong or expired",
    )


def _check_lc_login(folder: str, settings: NetworkSettings, given: BrowserTransport | None) -> int:
    if settings.lc_cookie is None:
        log.error("no LC login: set WLIB_CATALOG_LC_COOKIE in /etc/wlib-catalog/lc.env")
        return 2
    return _check_login(
        folder,
        settings.browser(given),
        site="LewdCorner",
        home_url=LC_HOME_URL,
        cookie_hint="the LC xf_user cookie is wrong or expired",
    )


def _discord_report(
    webhook: str, skipped: Mapping[str, str], transport: Transport
) -> Callable[[RunResult], None]:
    def report(result: RunResult) -> None:
        payload = build_report(result, sources=frozenset(SOURCE_TABLES), skipped=skipped)
        _ = send_report(webhook, payload, transport)

    return report


def main(
    argv: Sequence[str] | None = None,
    *,
    transport: Transport = urllib_transport,
    browser: BrowserTransport | None = None,
    sleep: Callable[[float], None] = time.sleep,
    storage_factory: Callable[[], Storage | None] | None = None,
    webhook_transport: Transport = urllib_transport,
) -> int:
    args = build_parser().parse_args(argv, namespace=Args())
    folder = args.data_dir or data_dir()
    if args.command == "status":
        return _status(folder)

    try:
        settings = NetworkSettings()
        webhook = discord_webhook_from_env() if args.command == "run" else None
    except ValueError as exc:
        log.error("%s", exc)
        return 2
    if args.command == "check-f95-login":
        return _check_f95_login(folder, settings, browser)
    if args.command == "check-lc-login":
        return _check_lc_login(folder, settings, browser)

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

    for site, proxy in sorted(settings.proxies.items()):
        log.info("%s requests go through the proxy %s", site, proxy)
    if settings.browser_sites:
        log.info("%s requests go through the browser", ", ".join(sorted(settings.browser_sites)))
    if settings.f95_cookie is not None:
        log.info("f95 requests send the login cookie")
    if settings.lc_cookie is not None:
        log.info("lc feed requests send the login cookie")
    log.info("lc_plus and lc_threads requests are guest requests and send no cookie")

    selected = list(args.sources)
    until = list(args.until_swept)
    for name in until:
        if name not in selected:
            selected.append(name)
    skipped: dict[str, str] = {}
    if "f95_threads" in selected and settings.f95_cookie is None:
        # F95 hides links from guests, so thread pages are read only when logged in.
        skipped["f95_threads"] = "it needs the F95 login cookie"
    if settings.lc_cookie is None and "lc" in selected:
        # Without the login LC shows no feed. The Plus listings and the thread
        # pages are read as a guest, so they still run.
        skipped["lc"] = "it needs the LC login cookie"
    for name, reason in skipped.items():
        log.info("%s: skipped, %s", name, reason)
        selected.remove(name)

    # Only runs that publish (or try to) send a report.
    report = (
        _discord_report(webhook, skipped, webhook_transport)
        if webhook is not None and storage is not None
        else None
    )
    with _run_lock(folder) as acquired:
        if not acquired:
            log.info("another catalog run is in progress; skipping this one")
            return 0
        catalog, state = open_stores(folder)
        browser = settings.browser(browser)
        try:
            pacer = Pacer(
                state,
                transport,
                sleep=sleep,
                wait_for_budget=until,
                proxies=settings.proxies,
                transports=settings.transports(browser),
            )
            result = run(
                catalog,
                state,
                pacer,
                get_sources(),
                [name for name in STEPS if name in selected],
                storage=storage,
                work_dir=folder,
                force_publish=args.force_publish,
                report=report,
            )
        finally:
            if browser is not None:
                browser.close()
            catalog.close()
            state.close()
    failed = [name for name, outcome in result.sources.items() if outcome.status == "error"]
    unswept = [
        name
        for name in until
        if name in result.sources and not result.sources[name].sweep_completed
        and result.sources[name].status != "ok"
    ]
    return 1 if failed or unswept or result.publish_refused else 0


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    sys.exit(main())
