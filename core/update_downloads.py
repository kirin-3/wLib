# SPDX-License-Identifier: GPL-3.0-or-later
"""In-memory, short-lived download links exposed only through opaque IDs."""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass
from typing import NamedTuple, TypedDict, cast, final
from collections.abc import Callable, Mapping

from core import database, extension_bridge, game_lookup, lewdcorner, scraper
from core.download_links import Build, masked_download_site
from core.downloads.manager import DownloadManager, Resolve
from core.downloads.queue import DownloadQueue, QueueJob
from core.downloads.transfer import DownloadError

# A build identity: what a saved download stores instead of its link.
# `mirror` is the mirror's own label and `copy` counts earlier mirrors with the
# same identity in one thread read, so two Pixeldrain links in one row differ.
class _LinkKey(NamedTuple):
    site: lewdcorner.Site
    label: str
    section: str
    platform: str
    host: str
    part: str
    mirror: str
    copy: int


KEY_FIELDS = _LinkKey._fields
LINK_SECONDS = 300


@dataclass
class _Link:
    game_id: int
    key: _LinkKey
    url: str
    created: float
    version: str = ''
    # Which thread read made this link; a later read replaces older links only.
    fetch: int = 0


    @property
    def site(self) -> lewdcorner.Site:
        return self.key.site


class DownloadSite(TypedDict):
    site: lewdcorner.Site
    version: str
    older: bool


def download_sites(
    game: Mapping[str, object], *, lc_enabled: bool, lc_plus: bool, catalog_tier: str = "",
) -> list[DownloadSite]:
    sites: list[lewdcorner.Site] = []
    if game.get("f95_url"):
        sites.append("f95")
    if lewdcorner.lc_active(game, lc_enabled) and (lc_plus or catalog_tier.casefold() != "premium"):
        sites.append("lc")
    latest = game.get("latest_site")
    options: list[DownloadSite] = []
    for site in sites:
        version = str(game.get(f"{site}_latest_version") or "")
        # An unknown version is not "older"; only a known version can lose.
        options.append({"site": site, "version": version,
                        "older": bool(version) and latest in ("f95", "lc") and latest != site})
    return options


def default_download_site(game: Mapping[str, object], sites: list[DownloadSite]) -> lewdcorner.Site | None:
    if game.get("latest_site") == "lc" and any(item["site"] == "lc" for item in sites):
        return "lc"
    return sites[0]["site"] if sites else None


def _in_app(host: str, url: str) -> bool:
    """Buzzheavier needs a masked link so the extension can open the share page."""
    return host != 'buzzheavier' or masked_download_site(url) is not None


@final
class UpdateDownloads:
    def __init__(self, manager: DownloadManager, queue: DownloadQueue | None = None) -> None:
        self.manager = manager
        self.queue = queue
        self.lock = threading.RLock()
        self.links: dict[str, _Link] = {}
        # Bumped only when the picker closes (`clear`).
        self.generations: dict[int, int] = {}
        self.fetches: dict[int, int] = {}
        self.picker_fetches: dict[int, int] = {}

    def get_links(self, game_id: int, site: str = "", *, picker: bool = True) -> dict[str, object]:
        """Read the thread's builds and hold their links.

        `picker` is False for a background read (a queued job's relink): it
        neither fails when the picker closes nor makes the picker's read fail.
        """
        game = database.get_game(game_id)
        if not game:
            return {'success':False, 'error':'Game not found'}
        lc_id = lewdcorner.extract_thread_id(game.get("lc_url"))
        catalog = game_lookup.lc_thread_entries([lc_id]).get(lc_id, {}) if lc_id else {}
        lc_enabled = bool(lc_id) and lewdcorner.setting_enabled(database.get_setting("lc_enabled"))
        lc_plus = bool(lc_id) and database.lc_plus_effective()
        tier = str(catalog.get("tier") or "")
        sites = download_sites(game, lc_enabled=lc_enabled, lc_plus=lc_plus, catalog_tier=tier)
        selected: lewdcorner.Site | None
        if site:
            selected = next((item["site"] for item in sites if item["site"] == site), None)
        else:
            selected = default_download_site(game, sites)
        if selected is None:
            error = (
                "This is an LC Plus thread. Use F95Zone or an LC Plus account."
                if site in ("", "lc") and tier.casefold() == "premium" and not lc_plus and lewdcorner.lc_active(game, lc_enabled)
                else "This game has no available thread for downloads"
            )
            return {"success": False, "error": error, "sites": sites}
        thread_url = str(game.get(f"{selected}_url") or "")
        with self.lock:
            generation = self.generations.get(game_id, 0)
            fetch = self.fetches.get(game_id, 0) + 1
            self.fetches[game_id] = fetch
            if picker:
                self.picker_fetches[game_id] = fetch
        result = (
            scraper.fetch_thread_download_links(thread_url, site="lc")
            if selected == "lc" else scraper.fetch_thread_download_links(thread_url)
        )
        if not result.get('success'):
            return {
                "success": False, "site": selected, "sites": sites, "code": result.get("code"),
                "error": str(result.get("error") or "Could not load download links"),
            }
        builds = cast(list[Build], result['builds'])
        output: list[dict[str, object]] = []
        now = time.monotonic()
        new_links: dict[str, _Link] = {}
        copies: dict[tuple[str, ...], int] = {}
        for build in builds:
            mirrors: list[dict[str, object]] = []
            for mirror in build['mirrors']:
                identifier = uuid.uuid4().hex
                base = (selected, build['label'], build['section'], build['platform'], mirror['host'], mirror['part'], mirror['label'])
                copy = copies.get(base, 0)
                copies[base] = copy + 1
                key = _LinkKey(*base, copy)
                new_links[identifier] = _Link(game_id, key, mirror['url'], now, str(result.get('version') or ''), fetch)
                mirrors.append({'link_id':identifier, 'host':mirror['host'], 'label':mirror['label'], 'part':mirror['part'], 'split':mirror['split'],
                                'in_app':_in_app(mirror['host'], mirror['url'])})
            output.append({**{k:v for k,v in build.items() if k != 'mirrors'}, 'mirrors':mirrors})
        with self.lock:
            if picker and self.generations.get(game_id, 0) != generation:
                return {'success':False, 'error':'Download picker was closed'}
            if picker and self.picker_fetches.get(game_id) != fetch:
                return {'success':False, 'error':'The picker has changed download sites'}
            # Retain stale identities for refresh matching, but discard their URLs.
            # A read that finished after a newer one keeps the newer links.
            for link in self.links.values():
                if link.game_id == game_id and link.fetch < fetch:
                    link.url = ''
            self.links.update(new_links)
        identifiers = tuple(new_links)
        def expire() -> None:
            with self.lock:
                for identifier in identifiers:
                    item = self.links.get(identifier)
                    if item is not None:
                        item.url = ''
        timer = threading.Timer(LINK_SECONDS, expire)
        timer.daemon = True
        timer.start()
        return {'success':True, 'site':selected, 'sites':sites, 'version':result['version'], 'builds':output}

    def clear(self, game_id: int) -> None:
        with self.lock:
            self.generations[game_id] = self.generations.get(game_id, 0) + 1
            _ = self.picker_fetches.pop(game_id, None)
            for link in self.links.values():
                if link.game_id == game_id:
                    link.url = ""
            self.links = {key: link for key, link in self.links.items() if link.game_id != game_id}

    def _find(self, game_id: int, link_id: str) -> _Link:
        with self.lock:
            link = self.links.get(link_id)
            if not link or link.game_id != game_id:
                raise DownloadError('Download link is unavailable; reload the picker')
            if link.url and time.monotonic() - link.created < LINK_SECONDS:
                return link
            key, version = link.key, link.version
            link.url = ''
        result = self.get_links(game_id, link.site)
        if not result.get('success'):
            raise DownloadError(str(result.get('error') or 'Could not reload links'))
        with self.lock:
            matches = self._matches(game_id, _full_match(key), version)
        if not matches:
            raise DownloadError(_gone(version))
        return matches[0]

    def _matches(self, game_id: int, same: Callable[[_LinkKey], bool], version: str,
                 fresh: bool = False) -> list[_Link]:
        """Held links with a URL for this build, mirror and release (call with the lock held)."""
        now = time.monotonic()
        wanted = version.strip().casefold()
        return [
            candidate for candidate in self.links.values()
            if candidate.game_id == game_id and candidate.url and same(candidate.key)
            # A refreshed thread that now offers another release is not this download.
            and (not wanted or candidate.version.strip().casefold() == wanted)
            and (not fresh or now - candidate.created < LINK_SECONDS)
        ]

    @staticmethod
    def _resolver(url: str, host: str) -> Resolve:
        def resolve(progress: Callable[[str], None], cancel: threading.Event) -> dict[str, object]:
            site = masked_download_site(url)
            if site == "f95":
                return scraper.resolve_download_link(url, host, progress, cancel)
            if site == "lc":
                try:
                    result = scraper.resolve_download_link(url, host, progress, cancel, site=site)
                except extension_bridge.BridgeUnavailable as exc:
                    if not extension_bridge.is_available(site="lc"):
                        raise DownloadError(
                            "Open a browser with the wLib extension and turn on its LewdCorner switch"
                        ) from exc
                    raise DownloadError("Turn on LewdCorner downloads in the wLib extension settings") from exc
                except extension_bridge.BridgeFailed as exc:
                    if "time" in str(exc).lower():
                        raise DownloadError("Finish the LewdCorner check in your browser") from exc
                    raise
                if result.get("ok") is not True:
                    error = str(result.get("error") or "")
                    if "timed out" in error or (error == "cancelled" and not cancel.is_set()):
                        raise DownloadError("Finish the LewdCorner check in your browser")
                    if "LewdCorner downloads" in error:
                        raise DownloadError(error)
                return result
            return {'ok':True, 'url':url, 'host':host}
        return resolve

    def start(self, game_id: int, link_id: str, replaces: str | None = None) -> QueueJob:
        """Add the chosen mirror to the download queue; it resolves at once."""
        from core.downloads.workers import HostContext

        if self.queue is None:
            raise DownloadError('Downloads are not ready yet')
        link = self._find(game_id, link_id)
        if link.key.part:
            raise DownloadError('Split archives are not supported yet; open in browser')
        url, host = link.url, link.key.host
        if not _in_app(host, url):
            raise DownloadError('This Buzzheavier link cannot be downloaded in-app; open in browser')
        game = database.get_game(game_id) or {}
        source: dict[str, object] = dict(zip(KEY_FIELDS, link.key, strict=True))
        return self.queue.add(
            'host', game_id, str(game.get('title') or ''), link.version, source,
            HostContext(self._resolver(url, host)), replaces=replaces,
        )

    def relink(self, job: QueueJob) -> Resolve:
        """A resolve for a saved job: the held link when it is fresh, else the thread read again.

        Raises DownloadError when the thread cannot be read or no longer offers the mirror.
        """
        source = job.source
        site: lewdcorner.Site = "lc" if source.get("site") == "lc" else "f95"
        build = (
            site, str(source.get('label') or ''), str(source.get('section') or ''),
            str(source.get('platform') or ''), str(source.get('host') or ''),
            str(source.get('part') or ''),
        )
        copy = source.get('copy')
        same: Callable[[_LinkKey], bool]
        if isinstance(copy, int) and not isinstance(copy, bool) and 'mirror' in source:
            same = _full_match(_LinkKey(*build, str(source.get('mirror') or ''), copy))
        else:
            # A job saved before mirrors had an identity: only a single match is safe.
            same = lambda key: tuple(key)[:len(build)] == build  # noqa: E731
        with self.lock:
            links = self._matches(job.game_id, same, job.version, fresh=True)
        if not links:
            result = self.get_links(job.game_id, site, picker=False)
            if not result.get('success'):
                raise DownloadError(str(result.get('error') or 'Could not reload links'))
            with self.lock:
                links = self._matches(job.game_id, same, job.version)
        if not links:
            raise DownloadError(_gone(job.version))
        if len({link.key for link in links}) > 1:
            raise DownloadError('Mirror no longer available: the thread has more than one such mirror; choose another mirror')
        return self._resolver(links[0].url, links[0].key.host)

    def browser_link(self, game_id: int, link_id: str) -> str:
        return self._find(game_id, link_id).url


def _full_match(key: _LinkKey) -> Callable[[_LinkKey], bool]:
    return lambda candidate: candidate == key


def _gone(version: str) -> str:
    if version.strip():
        return f'Mirror no longer available for {version.strip()}; choose another mirror'
    return 'Mirror no longer available; choose another mirror'
