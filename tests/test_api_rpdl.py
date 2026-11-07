# SPDX-License-Identifier: GPL-3.0-or-later
import pytest

from core.api import Api
from core.database import add_game, init_db
from core.downloads import credentials, torrents
from core.downloads.hosts import rpdl
from core.downloads.transfer import DownloadError


@pytest.fixture
def store(monkeypatch):
    values: dict[str, str] = {}
    monkeypatch.setattr(credentials, "get", lambda key: values.get(key, ""))
    monkeypatch.setattr(credentials, "put", values.update)
    return values


@pytest.fixture
def api(monkeypatch, tmp_path):
    monkeypatch.setenv("WLIB_DATA_DIR", str(tmp_path / "data"))
    init_db()
    return Api()


def game(title="Eternum", developer="Caribdis"):
    return add_game(title=title, exe_path="x.exe", developer=developer)


def test_connect_and_disconnect(api, store, monkeypatch):
    monkeypatch.setattr(rpdl, "login", lambda u, p: ("tok", "Kim"))
    assert api.connect_rpdl_account("kim", "pw") == {"success": True, "username": "Kim"}
    assert api.get_download_accounts()["rpdl"] == {"connected": True, "username": "Kim"}
    assert api.disconnect_rpdl_account() == {"success": True}
    assert api.get_download_accounts()["rpdl"]["connected"] is False
    assert api.connect_rpdl_account(None, "pw")["success"] is False


def test_connect_failure_stores_nothing(api, store, monkeypatch):
    def bad(u, p):
        raise rpdl.RpdlError(rpdl.KIND_BAD_LOGIN, "RPDL username or password is wrong")

    monkeypatch.setattr(rpdl, "login", bad)
    result = api.connect_rpdl_account("kim", "x")
    assert result["success"] is False and "wrong" in str(result["error"])
    assert not any(store.values())


def test_find_scores_results_and_reports_sign_in(api, store, monkeypatch):
    gid = game()
    seen = []

    def search(query, refresh=False):
        seen.append((query, refresh))
        return [
            {"id": 1, "title": "Eternum-0.8-pc", "size": 5, "seeders": 4, "leechers": 0, "date": 1},
            {"id": 2, "title": "Eternum-OST", "size": 1, "seeders": 4, "leechers": 0, "date": 1},
        ]

    monkeypatch.setattr(rpdl, "search", search)
    result = api.find_rpdl_torrents(gid, "0.8", True)
    assert result["success"] is True and result["signed_in"] is False
    assert result["preselected_id"] == 1 and seen == [("Eternum", True)]
    assert result["results"][0]["version_match"] is True
    store[credentials.RPDL_USERNAME] = "kim"
    assert api.find_rpdl_torrents(gid, "0.8")["signed_in"] is True


def test_find_failure_is_reported_by_cause(api, store, monkeypatch):
    gid = game()

    def boom(query, refresh=False):
        raise rpdl.RpdlError(rpdl.KIND_UNAVAILABLE, "RPDL is unavailable")

    monkeypatch.setattr(rpdl, "search", boom)
    result = api.find_rpdl_torrents(gid, "0.8")
    assert result == {"success": False, "error": "RPDL is unavailable", "error_code": "unavailable"}
    assert api.find_rpdl_torrents(999)["success"] is False


def test_start_requires_sign_in_and_a_valid_torrent(api, store, monkeypatch):
    gid = game()
    monkeypatch.setattr(rpdl, "fetch_torrent", lambda tid: pytest.fail("must not contact RPDL"))
    result = api.start_rpdl_download(gid, 5, "0.8")
    assert result["success"] is False and "Sign in to RPDL" in str(result["error"])
    store[credentials.RPDL_TOKEN] = "tok"
    assert api.start_rpdl_download(gid, "x", "0.8")["success"] is False
    assert api.start_rpdl_download(999, 5, "0.8")["success"] is False


def test_start_refused_while_game_runs(api, store, monkeypatch):
    gid = game()
    store[credentials.RPDL_TOKEN] = "tok"
    monkeypatch.setattr(api, "get_running_games", lambda: [gid])
    result = api.start_rpdl_download(gid, 5, "0.8")
    assert result["success"] is False and "Close the game" in str(result["error"])


def test_start_runs_a_torrent_job(api, store, monkeypatch):
    gid = game()
    store[credentials.RPDL_TOKEN] = "tok"
    started = {}

    class FakeSupervisor:
        def status(self):
            return []

        def start(self, game_id, title, version, rpdl_id, fetch):
            started.update(game_id=game_id, title=title, version=version, rpdl_id=rpdl_id)
            return "job1"

    monkeypatch.setattr(torrents, "supervisor", FakeSupervisor())
    result = api.start_rpdl_download(gid, 5, " 0.8 ")
    assert result == {"success": True, "job_id": "job1"}
    assert started == {"game_id": gid, "title": "Eternum", "version": "0.8", "rpdl_id": 5}


def test_start_reports_a_job_already_running(api, store, monkeypatch):
    gid = game()
    store[credentials.RPDL_TOKEN] = "tok"

    class FakeSupervisor:
        def status(self):
            return []

        def start(self, *args):
            raise DownloadError("A download is already running for this game")

    monkeypatch.setattr(torrents, "supervisor", FakeSupervisor())
    assert api.start_rpdl_download(gid, 5, "")["error"] == "A download is already running for this game"


def test_fetch_checks_the_info_hash(api, store, monkeypatch):
    from test_torrents import torrent_bytes  # type: ignore[import-not-found]

    from core.downloads.bencode import parse_torrent

    gid = game()
    store[credentials.RPDL_TOKEN] = "tok"
    data = torrent_bytes()
    captured = {}

    class FakeSupervisor:
        def status(self):
            return []

        def start(self, game_id, title, version, rpdl_id, fetch):
            captured["fetch"] = fetch
            return "j"

    monkeypatch.setattr(torrents, "supervisor", FakeSupervisor())
    monkeypatch.setattr(rpdl, "fetch_torrent", lambda tid: data)
    api.start_rpdl_download(gid, 5, "")
    monkeypatch.setattr(rpdl, "details", lambda tid: {"info_hash": parse_torrent(data).info_hash.upper()})
    assert captured["fetch"]() == data
    monkeypatch.setattr(rpdl, "details", lambda tid: {"info_hash": "0" * 40})
    with pytest.raises(rpdl.RpdlError):
        captured["fetch"]()

    def no_details(tid):
        raise rpdl.RpdlError(rpdl.KIND_UNAVAILABLE, "RPDL is unavailable")

    monkeypatch.setattr(rpdl, "details", no_details)
    assert captured["fetch"]() == data


def test_torrent_controls_report_failures(api, monkeypatch):
    calls = []

    class FakeSupervisor:
        def pause(self, job_id):
            calls.append(("pause", job_id))
            return job_id == "ok"

        def resume(self, job_id):
            return job_id == "ok"

        def status(self):
            return [{"job_id": "low", "error": torrents.MSG_LOW_DISK}]

        def answer_seed(self, job_id, seed, remember):
            calls.append(("answer", job_id, seed, remember))
            return job_id == "ok"

        def stop_seeding(self, job_id):
            return job_id == "ok"

    monkeypatch.setattr(torrents, "supervisor", FakeSupervisor())
    assert api.pause_torrent("ok")["success"] and not api.pause_torrent("no")["success"]
    assert api.resume_torrent("ok")["success"]
    assert api.resume_torrent("low")["error"] == torrents.MSG_LOW_DISK
    assert not api.resume_torrent(5)["success"]
    assert api.answer_seed_prompt("ok", True, True)["success"] and ("answer", "ok", True, True) in calls
    assert not api.answer_seed_prompt("ok", "yes")["success"]
    assert not api.answer_seed_prompt("no", False)["success"]
    assert api.stop_seeding("ok")["success"] and not api.stop_seeding("no")["success"]
