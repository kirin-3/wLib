# SPDX-License-Identifier: GPL-3.0-or-later
from core import update_downloads
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
    from core.downloads import transfer
    from core.downloads.manager import manager

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
