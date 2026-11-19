# SPDX-License-Identifier: GPL-3.0-or-later
import pytest

from core import database, update_downloads
from core.api import Api
from core.downloads.manager import DownloadManager
from core.update_downloads import UpdateDownloads


def test_opaque_links_refresh_and_browser(monkeypatch):
    version = [0]
    monkeypatch.setattr(update_downloads.database, 'get_game', lambda _: {'f95_url':'https://f95zone.to/threads/1/'})
    def fetch(_):
        version[0] += 1
        return {'success':True,'version':'v1','builds':[{'label':'Win','section':'','platform':'windows','kind':'full','mirrors':[{'url':f'https://f95zone.to/dl/1/?u=fake{version[0]}','host':'mega','label':'MEGA','part':'','split':False}]}]}
    monkeypatch.setattr(update_downloads.scraper, 'fetch_thread_download_links', fetch)
    service = UpdateDownloads(DownloadManager())
    response = service.get_links(1)
    assert '/dl/' not in str(response)
    identifier = response['builds'][0]['mirrors'][0]['link_id']
    assert service.browser_link(1, identifier).endswith('fake1')
    service.links[identifier].created -= 301
    assert service.browser_link(1, identifier).endswith('fake2')
    assert not service.links[identifier].url
    assert version[0] == 2
    api = Api.__new__(Api)
    api._update_downloads = service
    monkeypatch.setattr(api, 'get_running_games', lambda: [1])
    assert not api.start_update_download(1, identifier)['success']
    assert not api.open_update_download_in_browser(2, identifier)['success']
    api.close_update_download_links(1)
    assert not service.links


def test_archive_inspection_identifies_staging(tmp_path, monkeypatch):
    from core.database import init_db
    from core.downloads import transfer
    from core.downloads.manager import manager

    init_db()
    stage = tmp_path / 'downloads'
    stage.mkdir()
    inside = stage / 'update.zip'
    outside = tmp_path / 'external.zip'
    inside.write_bytes(b'PK\x03\x04')
    outside.write_bytes(b'PK\x03\x04')
    monkeypatch.setattr(transfer, 'staging_dir', lambda: stage)
    api = Api.__new__(Api)
    api._update_downloads = UpdateDownloads(manager)
    assert api.inspect_archive(str(inside))['downloaded_by_wlib'] is True
    assert api.inspect_archive(str(outside))['downloaded_by_wlib'] is False
    manager.release(str(inside))


def _saved_job(host='pixeldrain', label='Win'):
    from core.downloads.queue import QueueJob

    return QueueJob(id='saved', kind='host', game_id=1, state='queued',
                    source={'label': label, 'section': '', 'platform': 'windows', 'host': host, 'part': ''})


def _thread(mirrors):
    return {'success': True, 'version': 'v2', 'builds': [
        {'label': 'Win', 'section': '', 'platform': 'windows', 'kind': 'full', 'mirrors': mirrors}]}


def test_saved_job_reads_the_thread_and_matches_its_build(monkeypatch):
    import pytest

    from core.downloads.transfer import DownloadError

    monkeypatch.setattr(update_downloads.database, 'get_game', lambda _: {'f95_url': 'https://f95zone.to/threads/1/'})
    reads = []

    def fetch(_):
        reads.append(1)
        return _thread([
            {'url': 'https://mega.nz/file/x', 'host': 'mega', 'label': 'MEGA', 'part': '', 'split': False},
            {'url': 'https://pixeldrain.com/u/match', 'host': 'pixeldrain', 'label': 'PD', 'part': '', 'split': False},
        ])

    monkeypatch.setattr(update_downloads.scraper, 'fetch_thread_download_links', fetch)
    service = UpdateDownloads(DownloadManager())
    resolve = service.relink(_saved_job())
    assert resolve(lambda _stage: None, None) == {'ok': True, 'url': 'https://pixeldrain.com/u/match', 'host': 'pixeldrain'}
    # A fresh link is used again without reading the thread.
    _ = service.relink(_saved_job())
    assert reads == [1]
    # The mirror is no longer offered.
    with pytest.raises(DownloadError, match='Mirror no longer available'):
        _ = service.relink(_saved_job(label='Linux'))


def test_saved_job_without_the_extension_needs_the_user(monkeypatch):
    import pytest

    from core.downloads import failures
    from core.downloads.transfer import DownloadError

    monkeypatch.setattr(update_downloads.database, 'get_game', lambda _: {'f95_url': 'https://f95zone.to/threads/1/'})
    monkeypatch.setattr(update_downloads.scraper, 'fetch_thread_download_links',
                        lambda _: {'success': False, 'code': 'extension_unavailable', 'error': 'Open a browser with the wLib extension'})
    service = UpdateDownloads(DownloadManager())
    with pytest.raises(DownloadError) as caught:
        _ = service.relink(_saved_job())
    assert failures.classify(str(caught.value)) == failures.EXTENSION


def test_saved_job_in_the_queue_needs_the_user_when_the_mirror_is_gone(tmp_path, monkeypatch):
    import time

    from core.database import init_db, insert_download_job
    from core.downloads.queue import DownloadQueue
    from core.downloads.workers import HostWorker

    init_db()
    monkeypatch.setattr(update_downloads.database, 'get_game', lambda _: {'f95_url': 'https://f95zone.to/threads/1/'})
    monkeypatch.setattr(update_downloads.scraper, 'fetch_thread_download_links', lambda _: _thread([]))
    manager = DownloadManager()
    service = UpdateDownloads(manager)
    queue = DownloadQueue([HostWorker(manager, service.relink)], run_thread=False, free_space=lambda: 0)
    service.queue = queue
    insert_download_job({'id': 'saved', 'kind': 'host', 'game_id': 1, 'state': 'queued',
                         'source': '{"host": "pixeldrain", "label": "Win", "platform": "windows"}'})
    queue.load()
    deadline = time.monotonic() + 5
    while queue.get('saved').state != 'attention' and time.monotonic() < deadline:
        queue.tick()
        time.sleep(0.01)
    assert queue.get('saved').error_code == 'mirror_gone'


def _api_with_fake_queue(monkeypatch):
    from core.database import init_db
    from core.downloads.queue import DownloadQueue
    from test_download_queue import FakeWorker  # type: ignore[import-not-found]

    init_db()
    api = Api()
    worker = FakeWorker()
    queue = DownloadQueue([worker], run_thread=False, free_space=lambda: 10**9)
    api._download_queue = queue
    api._update_downloads.queue = queue
    return api, queue, worker


def test_queue_calls_return_payloads_and_refuse_unknown_jobs(monkeypatch):
    api, queue, worker = _api_with_fake_queue(monkeypatch)
    ids = [queue.add("host", game_id, f"Game {game_id}", "1.0", {"host": "pixeldrain"}).id for game_id in (1, 2, 3)]
    queue.tick()
    status = api.get_download_queue()
    assert status["success"] is True and [job["job_id"] for job in status["jobs"]] == ids
    assert status["queue"]["max_active"] == 2 and status["queue"]["active"] == 2
    assert "history" not in status["queue"] and isinstance(status["seeding"], list)
    assert "history" in api.get_download_queue(True)["queue"]
    assert set(status["queue"]["settings"]) == {
        "download_limit_kib", "download_max_active", "download_pause_while_playing", "download_pause_on_battery"}

    assert api.reorder_downloads([ids[2], ids[1]]) == {"success": True}
    assert [job["job_id"] for job in api.get_download_queue()["jobs"]] == [ids[2], ids[1], ids[0]]
    assert api.reorder_downloads("nope")["success"] is False
    assert api.start_download_now(ids[2])["success"] is True
    assert api.pause_download(ids[0]) == {"success": True}
    assert api.resume_download(ids[0]) == {"success": True}
    assert api.pause_all_downloads()["success"] is True
    assert api.resume_all_downloads()["count"] == 3
    running = next(job.id for job in queue.jobs() if job.state == "running")
    waiting = next(job.id for job in queue.jobs() if job.state == "queued")
    worker.fail(running, "link expired or removed")
    queue.tick()
    assert api.retry_download(running) == {"success": True}
    assert api.retry_download(waiting)["success"] is False
    assert api.resume_downloads_now() == {"success": False, "error": "Downloads are not waiting"}
    assert api.remove_download(ids[0]) == {"success": True}

    for call in (api.start_download_now, api.pause_download, api.resume_download,
                 api.retry_download, api.remove_download):
        assert call("missing") == {"success": False, "error": "The download was not found"}
        assert call(5)["success"] is False


def test_start_update_download_adds_to_the_queue_and_replaces(monkeypatch):
    api, queue, worker = _api_with_fake_queue(monkeypatch)
    monkeypatch.setattr(update_downloads.database, 'get_game', lambda _: {'f95_url': 'https://f95zone.to/threads/1/', 'title': 'Game'})
    monkeypatch.setattr(update_downloads.scraper, 'fetch_thread_download_links', lambda _: _thread([
        {'url': 'https://pixeldrain.com/u/a', 'host': 'pixeldrain', 'label': 'PD', 'part': '', 'split': False},
        {'url': 'https://mega.nz/file/b', 'host': 'mega', 'label': 'MEGA', 'part': '', 'split': False},
    ]))
    monkeypatch.setattr(api, 'get_running_games', lambda: [])
    links = api.get_update_download_links(1)['builds'][0]['mirrors']
    first = api.start_update_download(1, links[0]['link_id'])
    assert first['success'] is True
    job = queue.get(first['job_id'])
    assert (job.title, job.version, job.source['host']) == ('Game', 'v2', 'pixeldrain')
    again = api.start_update_download(1, links[1]['link_id'])
    assert again == {'success': False, 'error': 'This game is already in the downloads'}
    worker.fail(first['job_id'], 'link expired or removed')
    queue.tick()
    replaced = api.start_update_download(1, links[1]['link_id'], first['job_id'])
    assert replaced['success'] is True
    assert [item.id for item in queue.jobs()] == [replaced['job_id']]
    assert worker.removed == [first['job_id']]


def test_deleting_a_game_removes_its_downloads(monkeypatch):
    from core.database import add_game

    api, queue, worker = _api_with_fake_queue(monkeypatch)
    game_id = add_game(title='Game', exe_path='index.html')
    job = queue.add('host', game_id, 'Game', '1.0', {'host': 'pixeldrain'})
    assert api.delete_game(game_id)['success'] is True
    assert queue.get(job.id) is None and worker.removed == [job.id]


def test_background_read_does_not_break_the_picker(monkeypatch):
    monkeypatch.setattr(update_downloads.database, 'get_game', lambda _: {'f95_url': 'https://f95zone.to/threads/1/'})
    service = UpdateDownloads(DownloadManager())
    calls = [0]

    def fetch(_):
        calls[0] += 1
        number = calls[0]
        if number == 1:
            # A queued job reads the thread again while the picker loads.
            assert service.get_links(1, picker=False)['success']
        return {'success': True, 'version': 'v1', 'builds': [{
            'label': 'Win', 'section': '', 'platform': 'windows', 'kind': 'full',
            'mirrors': [{'url': f'https://f95zone.to/dl/1/?u=fake{number}', 'host': 'mega',
                         'label': 'MEGA', 'part': '', 'split': False}],
        }]}

    monkeypatch.setattr(update_downloads.scraper, 'fetch_thread_download_links', fetch)
    response = service.get_links(1)
    assert response['success']
    identifier = response['builds'][0]['mirrors'][0]['link_id']
    assert service.browser_link(1, identifier).endswith('fake1')
    # The older read finished last but kept the newer read's link.
    assert sorted(link.url[-5:] for link in service.links.values()) == ['fake1', 'fake2']


def test_closed_picker_does_not_fail_a_background_read(monkeypatch):
    monkeypatch.setattr(update_downloads.database, 'get_game', lambda _: {'f95_url': 'https://f95zone.to/threads/1/'})
    service = UpdateDownloads(DownloadManager())

    def fetch(_):
        service.clear(1)
        return {'success': True, 'version': 'v1', 'builds': []}

    monkeypatch.setattr(update_downloads.scraper, 'fetch_thread_download_links', fetch)
    assert service.get_links(1, picker=False)['success']
    assert service.get_links(1) == {'success': False, 'error': 'Download picker was closed'}

@pytest.mark.parametrize(("exe", "latest", "thread_version", "expected"), [
    ("", "", "v2", "v2"),
    ("  ", "v1", "v2", "v2"),
    ("", "v1", "", "v1"),
    ("game.exe", "v1", "v2", "v1"),
])
def test_start_update_download_records_wishlist_version(monkeypatch, exe, latest, thread_version, expected):
    api, queue, _ = _api_with_fake_queue(monkeypatch)
    game_id = database.add_game("Game", exe, "https://f95zone.to/threads/1/")
    database.update_game(game_id, {"latest_version": latest})
    thread = _thread([
        {"url": "https://pixeldrain.com/u/a", "host": "pixeldrain", "label": "PD", "part": "", "split": False},
    ])
    monkeypatch.setattr(update_downloads.scraper, "fetch_thread_download_links",
                        lambda _: {**thread, "version": thread_version})
    mirror = api.get_update_download_links(game_id)["builds"][0]["mirrors"][0]

    result = api.start_update_download(game_id, mirror["link_id"])

    assert result["success"] is True
    assert queue.get(result["job_id"]).version == thread_version
    assert database.get_game(game_id)["latest_version"] == expected


def test_queue_reads_live_wishlist_status_once_per_game(monkeypatch):
    api, queue, worker = _api_with_fake_queue(monkeypatch)
    wishlist = database.add_game("Wishlist", "")
    installed = database.add_game("Installed", "game.exe")
    deleted = database.add_game("Deleted", "")
    first = queue.add("host", wishlist, "Wishlist", "1", {})
    queue.tick()
    worker.finish(first.id)
    queue.tick()
    for game_id in (wishlist, installed, deleted):
        queue.add("host", game_id, "Game", "2", {})
    database.delete_game(deleted)
    get_game = database.get_game
    reads = []

    def record_read(game_id):
        reads.append(game_id)
        return get_game(game_id)

    monkeypatch.setattr(database, "get_game", record_read)
    jobs = api.get_download_queue()["jobs"]
    assert [job["game_wishlisted"] for job in jobs] == [True, True, False, False]
    assert reads == [wishlist, installed, deleted]

    database.update_game(wishlist, {"exe_path": "game.exe"})
    assert not any(job["game_wishlisted"] for job in api.get_download_queue()["jobs"])


@pytest.mark.parametrize(("latest_site", "expected"), [("lc", "lc"), ("f95", "f95"), ("both", "f95"), ("", "f95")])
def test_default_download_site(latest_site, expected):
    game = {"lc_url": "https://lewdcorner.com/threads/12/", "f95_url": "https://f95zone.to/threads/1/",
            "latest_site": latest_site, "f95_latest_version": "v1", "lc_latest_version": "v2"}
    sites = update_downloads.download_sites(game, lc_enabled=True, lc_plus=False)
    assert update_downloads.default_download_site(game, sites) == expected
    assert [item["version"] for item in sites] == ["v1", "v2"]
    assert [item["older"] for item in sites] == [latest_site == "lc", latest_site == "f95"]
    unknown = update_downloads.download_sites({**game, "lc_latest_version": ""}, lc_enabled=True, lc_plus=False)
    assert not unknown[1]["older"]
    blocked = update_downloads.download_sites(game, lc_enabled=True, lc_plus=False, catalog_tier="Premium")
    assert update_downloads.default_download_site(game, blocked) == "f95"
    assert update_downloads.download_sites(game, lc_enabled=True, lc_plus=True, catalog_tier="Premium") == sites


def test_saved_job_relinks_its_site_even_when_the_default_has_changed(monkeypatch):
    lc_url = "https://lewdcorner.com/threads/12/"
    f95_url = "https://f95zone.to/threads/1/"
    game = {"lc_url": lc_url, "f95_url": f95_url, "lc_mode": "on", "latest_site": "lc"}
    database.init_db()
    monkeypatch.setattr(database, "get_game", lambda _: game)
    reads = []

    def fetch(url, site="f95"):
        reads.append((url, site))
        return _thread([{"url": "https://pixeldrain.com/u/" + site, "host": "pixeldrain",
                         "label": "PD", "part": "", "split": False}])

    monkeypatch.setattr(update_downloads.scraper, "fetch_thread_download_links", fetch)
    service = UpdateDownloads(DownloadManager())
    assert service.get_links(1)["site"] == "lc"
    service.clear(1)
    old_job = _saved_job()
    assert service.relink(old_job)(lambda _: None, None)["url"].endswith("/f95")
    lc_job = _saved_job()
    lc_job.source["site"] = "lc"
    game["latest_site"] = "f95"
    assert service.relink(lc_job)(lambda _: None, None)["url"].endswith("/lc")
    assert reads == [(lc_url, "lc"), (f95_url, "f95"), (lc_url, "lc")]


@pytest.mark.parametrize(("url", "expected"), [
    ("https://f95zone.to/dl/1/?fake", True),
    ("https://lewdcorner.com/masked/out?t=fake", True),
    ("https://buzzheavier.com/share", False),
])
def test_buzzheavier_requires_a_masked_link(url, expected):
    assert update_downloads._in_app("buzzheavier", url) is expected


@pytest.mark.parametrize(("lc_extension", "message"), [
    (False, "Open a browser with the wLib extension and turn on its LewdCorner switch"),
    (True, "Turn on LewdCorner downloads in the wLib extension settings"),
])
def test_lc_resolve_without_support_names_the_missing_step(monkeypatch, lc_extension, message):
    import threading

    from core import extension_bridge, scraper
    from core.downloads.transfer import DownloadError

    def resolve(*_args, **_kwargs):
        raise extension_bridge.BridgeUnavailable("no extension with LewdCorner download resolution connected")

    monkeypatch.setattr(scraper, "resolve_download_link", resolve)
    monkeypatch.setattr(extension_bridge, "is_available", lambda *, site="f95": lc_extension)
    resolver = UpdateDownloads._resolver("https://lewdcorner.com/masked/out?t=fake", "pixeldrain")
    with pytest.raises(DownloadError, match=message):
        _ = resolver(lambda _: None, threading.Event())
