# SPDX-License-Identifier: GPL-3.0-or-later
"""In-memory, short-lived download links exposed only through opaque IDs."""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass
from typing import final
from collections.abc import Callable

from core import database, scraper
from core.downloads.manager import DownloadManager, Resolve
from core.downloads.queue import DownloadQueue, QueueJob
from core.downloads.transfer import DownloadError

# A build identity: what a saved download stores instead of its link.
KEY_FIELDS = ('label', 'section', 'platform', 'host', 'part')
LINK_SECONDS = 300


@dataclass
class _Link:
    game_id: int
    key: tuple[str, str, str, str, str]
    url: str
    created: float
    version: str = ''


def _in_app(host: str, url: str) -> bool:
    """Whether wLib can download this mirror itself.

    A Buzzheavier file is only reachable through its share page, which the
    extension opens for F95Zone's masked /dl/ links; a direct Buzzheavier link
    has no such page to resolve, so it is opened in the browser."""
    return host != 'buzzheavier' or url.startswith('https://f95zone.to/dl/')


@final
class UpdateDownloads:
    def __init__(self, manager: DownloadManager, queue: DownloadQueue | None = None) -> None:
        self.manager = manager
        self.queue = queue
        self.lock = threading.RLock()
        self.links: dict[str, _Link] = {}
        self.generations: dict[int, int] = {}

    def get_links(self, game_id: int) -> dict[str, object]:
        game = database.get_game(game_id)
        if not game:
            return {'success':False, 'error':'Game not found'}
        f95_url = str(game.get('f95_url') or '')
        if not f95_url:
            return {'success':False, 'error':'Game has no F95Zone thread'}
        with self.lock:
            generation = self.generations.get(game_id, 0) + 1
            self.generations[game_id] = generation
        result = scraper.fetch_thread_download_links(f95_url)
        if not result.get('success'):
            return {'success':False, 'error':str(result.get('error') or 'Could not load download links')}
        from core.download_links import Build
        from typing import cast
        builds = cast(list[Build], result['builds'])
        output: list[dict[str, object]] = []
        now = time.monotonic()
        new_links: dict[str, _Link] = {}
        for build in builds:
            mirrors: list[dict[str, object]] = []
            for mirror in build['mirrors']:
                identifier = uuid.uuid4().hex
                key = (build['label'], build['section'], build['platform'], mirror['host'], mirror['part'])
                new_links[identifier] = _Link(game_id, key, mirror['url'], now, str(result.get('version') or ''))
                mirrors.append({'link_id':identifier, 'host':mirror['host'], 'label':mirror['label'], 'part':mirror['part'], 'split':mirror['split'],
                                'in_app':_in_app(mirror['host'], mirror['url'])})
            output.append({**{k:v for k,v in build.items() if k != 'mirrors'}, 'mirrors':mirrors})
        with self.lock:
            if self.generations.get(game_id) != generation:
                return {'success':False, 'error':'Download picker was closed'}
            # Retain stale identities for refresh matching, but discard their URLs.
            for link in self.links.values():
                if link.game_id == game_id:
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
        return {'success':True, 'version':result['version'], 'builds':output}

    def clear(self, game_id: int) -> None:
        with self.lock:
            self.generations[game_id] = self.generations.get(game_id, 0) + 1
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
            key = link.key
            link.url = ''
        result = self.get_links(game_id)
        if not result.get('success'):
            raise DownloadError(str(result.get('error') or 'Could not reload links'))
        with self.lock:
            match = next((candidate for candidate in self.links.values() if candidate.game_id == game_id and candidate.key == key and candidate.url), None)
            if match is None:
                raise DownloadError('Mirror no longer available; choose another mirror')
            return match

    @staticmethod
    def _resolver(url: str, host: str) -> Resolve:
        def resolve(progress: Callable[[str], None], cancel: threading.Event) -> dict[str, object]:
            if url.startswith('https://f95zone.to/dl/'):
                return scraper.resolve_download_link(url, host, progress, cancel)
            return {'ok':True, 'url':url, 'host':host}
        return resolve

    def start(self, game_id: int, link_id: str, replaces: str | None = None) -> str:
        """Add the chosen mirror to the download queue; it resolves at once."""
        from core.downloads.workers import HostContext

        if self.queue is None:
            raise DownloadError('Downloads are not ready yet')
        link = self._find(game_id, link_id)
        if link.key[4]:
            raise DownloadError('Split archives are not supported yet; open in browser')
        url, host = link.url, link.key[3]
        if not _in_app(host, url):
            raise DownloadError('This Buzzheavier link cannot be downloaded in-app; open in browser')
        game = database.get_game(game_id) or {}
        source: dict[str, object] = dict(zip(KEY_FIELDS, link.key, strict=True))
        job = self.queue.add(
            'host', game_id, str(game.get('title') or ''), link.version, source,
            HostContext(self._resolver(url, host)), replaces=replaces,
        )
        return job.id

    def relink(self, job: QueueJob) -> Resolve:
        """A resolve for a saved job: the held link when it is fresh, else the thread read again.

        Raises DownloadError when the thread cannot be read or no longer offers the mirror.
        """
        key = (
            str(job.source.get('label') or ''), str(job.source.get('section') or ''),
            str(job.source.get('platform') or ''), str(job.source.get('host') or ''),
            str(job.source.get('part') or ''),
        )
        now = time.monotonic()
        with self.lock:
            link = next((candidate for candidate in self.links.values()
                         if candidate.game_id == job.game_id and candidate.key == key and candidate.url
                         and now - candidate.created < LINK_SECONDS), None)
        if link is None:
            result = self.get_links(job.game_id)
            if not result.get('success'):
                raise DownloadError(str(result.get('error') or 'Could not reload links'))
            with self.lock:
                link = next((candidate for candidate in self.links.values()
                             if candidate.game_id == job.game_id and candidate.key == key and candidate.url), None)
        if link is None:
            raise DownloadError('Mirror no longer available; choose another mirror')
        return self._resolver(link.url, key[3])

    def browser_link(self, game_id: int, link_id: str) -> str:
        return self._find(game_id, link_id).url
