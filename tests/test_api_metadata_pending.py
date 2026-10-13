import threading
from core.api import Api

def test_add_game_metadata_pending(monkeypatch):
    monkeypatch.setattr("core.api.Api._get_bulk_check_delay_seconds", lambda self: 0)
    monkeypatch.setattr("core.database.get_all_games", lambda: [])

    api = Api()

    def bg():
        for i in range(10000):
            with api._metadata_lock:
                api._metadata_pending.add(i)

    t = threading.Thread(target=bg)
    t.start()

    try:
        for _ in range(100):
            _ = api.get_games()
    except Exception as e:
        assert False, f"Exception raised: {e}"

    t.join()
