from pathlib import Path

from core import steam_sync


def test_debounce_and_pending_poll(monkeypatch, tmp_path):
    timers = []
    class Timer:
        def __init__(self, delay, target, kwargs=None):
            self.delay, self.target, self.kwargs = delay, target, kwargs or {}
            self.cancelled = False
            self.daemon = False
            timers.append(self)
        def start(self):
            pass
        def cancel(self):
            self.cancelled = True
    monkeypatch.setattr(steam_sync.threading, 'Timer', Timer)
    monkeypatch.setattr(steam_sync, 'environment', lambda: {'root': str(tmp_path), 'account_id': '123'})
    monkeypatch.setattr(steam_sync.database, 'get_setting', lambda _: '')
    monkeypatch.setattr(steam_sync, 'sync_games', lambda *_a, **_kw: {'success': True, 'pending': 1})
    worker = steam_sync.SteamSync(Path('/icon'))
    worker.schedule()
    worker.schedule()
    assert timers[0].cancelled and timers[1].delay == 5
    timers[1].target(**timers[1].kwargs)
    assert timers[-1].delay == 30
    monkeypatch.setattr(steam_sync, 'sync_games', lambda *_a, **_kw: {'success': True, 'pending': 0})
    timers[-1].target(**timers[-1].kwargs)
    assert len(timers) == 3
    worker.stop()
