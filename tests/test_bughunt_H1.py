# SPDX-License-Identifier: GPL-3.0-or-later
"""A saved download relinks only to the mirror and release the user chose."""
import pytest

from core import update_downloads
from core.downloads.manager import DownloadManager
from core.downloads.queue import QueueJob
from core.downloads.transfer import DownloadError
from core.update_downloads import UpdateDownloads


def _mirror(url, label='Pixeldrain'):
    return {'url': url, 'host': 'pixeldrain', 'label': label, 'part': '', 'split': False}


def _thread(version, mirrors):
    return {'success': True, 'version': version, 'builds': [
        {'label': 'Win', 'section': '', 'platform': 'windows', 'kind': 'full', 'mirrors': mirrors}]}


def _service(monkeypatch, reads):
    monkeypatch.setattr(update_downloads.database, 'get_game',
                        lambda _: {'f95_url': 'https://f95zone.to/threads/1/', 'title': 'Game'})
    monkeypatch.setattr(update_downloads.scraper, 'fetch_thread_download_links', lambda _: reads[0])
    return UpdateDownloads(DownloadManager())


def _saved(service, link_id):
    """The job as the queue would load it after a restart."""
    link = service.links[link_id]
    source = dict(zip(update_downloads.KEY_FIELDS, link.key, strict=True))
    return QueueJob(id='saved', kind='host', game_id=1, state='queued', version=link.version, source=source)


def _url(service, job):
    return service.relink(job)(lambda _stage: None, None)['url']


def test_second_of_two_same_host_mirrors_relinks_to_the_second(monkeypatch):
    reads = [_thread('v1', [_mirror('https://pixeldrain.com/u/first'), _mirror('https://pixeldrain.com/u/second')])]
    service = _service(monkeypatch, reads)
    mirrors = service.get_links(1)['builds'][0]['mirrors']
    job = _saved(service, mirrors[1]['link_id'])
    # A restart: the held links are gone.
    service.clear(1)
    assert _url(service, job) == 'https://pixeldrain.com/u/second'
    job = _saved(service, next(iter(service.links)))
    service.clear(1)
    assert _url(service, job) == 'https://pixeldrain.com/u/first'


def test_mirror_label_tells_same_host_mirrors_apart(monkeypatch):
    reads = [_thread('v1', [_mirror('https://pixeldrain.com/u/a', 'PD 1'), _mirror('https://pixeldrain.com/u/b', 'PD 2')])]
    service = _service(monkeypatch, reads)
    mirrors = service.get_links(1)['builds'][0]['mirrors']
    job = _saved(service, mirrors[1]['link_id'])
    service.clear(1)
    # The forum moved the mirrors; the label still finds the chosen one.
    reads[0] = _thread('v1', [_mirror('https://pixeldrain.com/u/b', 'PD 2'), _mirror('https://pixeldrain.com/u/a', 'PD 1')])
    assert _url(service, job) == 'https://pixeldrain.com/u/b'


def test_relink_refuses_a_new_release_under_the_same_labels(monkeypatch):
    reads = [_thread('v1', [_mirror('https://pixeldrain.com/u/v1')])]
    service = _service(monkeypatch, reads)
    job = _saved(service, service.get_links(1)['builds'][0]['mirrors'][0]['link_id'])
    assert job.version == 'v1'
    service.clear(1)
    reads[0] = _thread('v2', [_mirror('https://pixeldrain.com/u/v2')])
    with pytest.raises(DownloadError, match='Mirror no longer available for v1'):
        _ = service.relink(job)


def test_picker_refresh_refuses_a_new_release(monkeypatch):
    reads = [_thread('v1', [_mirror('https://pixeldrain.com/u/v1')])]
    service = _service(monkeypatch, reads)
    link_id = service.get_links(1)['builds'][0]['mirrors'][0]['link_id']
    service.links[link_id].created -= update_downloads.LINK_SECONDS + 1
    reads[0] = _thread('v2', [_mirror('https://pixeldrain.com/u/v2')])
    with pytest.raises(DownloadError, match='Mirror no longer available for v1'):
        _ = service.browser_link(1, link_id)


def test_picker_refresh_keeps_the_chosen_duplicate(monkeypatch):
    reads = [_thread('v1', [_mirror('https://pixeldrain.com/u/first'), _mirror('https://pixeldrain.com/u/second')])]
    service = _service(monkeypatch, reads)
    link_id = service.get_links(1)['builds'][0]['mirrors'][1]['link_id']
    service.links[link_id].created -= update_downloads.LINK_SECONDS + 1
    assert service.browser_link(1, link_id) == 'https://pixeldrain.com/u/second'


def test_old_saved_job_refuses_an_ambiguous_mirror(monkeypatch):
    reads = [_thread('', [_mirror('https://pixeldrain.com/u/first'), _mirror('https://pixeldrain.com/u/second')])]
    service = _service(monkeypatch, reads)
    # Saved before mirrors had their own identity.
    job = QueueJob(id='saved', kind='host', game_id=1, state='queued',
                   source={'label': 'Win', 'section': '', 'platform': 'windows', 'host': 'pixeldrain', 'part': ''})
    with pytest.raises(DownloadError, match='more than one') as caught:
        _ = service.relink(job)
    from core.downloads import failures
    assert failures.classify(str(caught.value)) == failures.MIRROR_GONE
    reads[0] = _thread('', [_mirror('https://pixeldrain.com/u/only')])
    service.clear(1)
    assert _url(service, job) == 'https://pixeldrain.com/u/only'
