# SPDX-License-Identifier: GPL-3.0-or-later
"""Serialized, debounced Steam shortcut reconciliation in the GUI process."""
from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import cast, final

from core import database, steam_env
from core.host_platform import get_data_dir
from core.steam_shortcuts import add_wlib, sync_games


def environment() -> dict[str, object]:
    root = steam_env.steam_root()
    accounts = steam_env.accounts(root) if root else []
    chosen = database.get_setting('steam_account_id') or ''
    account = chosen if chosen in accounts else (steam_env.most_recent_account(root) if root else None)
    account = account or (accounts[0] if accounts else '')
    return {'root': str(root) if root else '', 'found': root is not None,
            'flatpak_steam': steam_env.flatpak_steam_only(), 'accounts': accounts,
            'account_id': account, 'running': steam_env.steam_running(root) if root else False,
            'deck': steam_env.is_deck(), 'gaming_mode': steam_env.gaming_mode(),
            'mode': steam_env.effective_mode(database.get_setting('steam_games_mode'))}


@final
class SteamSync:
    def __init__(self, icon: Path, on_conversions: Callable[[], None] | None = None) -> None:
        self.icon = icon
        self.on_conversions = on_conversions
        # Covers waiting for the window to convert them, keyed by their PNG name.
        self._conversions: dict[str, Path] = {}
        self._failed_conversions: set[str] = set()
        self.script = Path(get_data_dir()) / 'steam/wlib-steam'
        self._lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._stopped = False
        self._retry_removed = False
        self._status: dict[str, object] = {'running': False}

    def status(self) -> dict[str, object]:
        with self._state_lock:
            return dict(self._status)

    def schedule(self, delay: float = 5, *, retry_removed: bool = False, only_if_idle: bool = False) -> None:
        with self._state_lock:
            if self._stopped or (only_if_idle and self._timer is not None):
                return
            if self._timer:
                self._timer.cancel()
            # A later debounced change must not drop a pending "Sync now" retry.
            self._retry_removed = self._retry_removed or retry_removed
            self._timer = threading.Timer(delay, self._run, kwargs={'retry_removed': self._retry_removed})
            self._timer.daemon = True
            self._timer.start()

    def _run(self, *, retry_removed: bool = False) -> None:
        with self._lock:
            with self._state_lock:
                if self._stopped:
                    return
                self._timer = None
                self._retry_removed = False
                self._status = {'running': True}
            try:
                env = environment()
                root = str(env['root'])
                account = str(env['account_id'])
                if not root or not account:
                    result: dict[str, object] = {'success': False, 'error': 'Steam account not found'}
                else:
                    result = sync_games(Path(root), account, self.script, retry_removed=retry_removed)
                    if database.get_setting(f'steam_wlib_pending_{account}') == 'true':
                        wlib_result = add_wlib(Path(root), account, self.script, self.icon)
                        if wlib_result.get('pending'):
                            result['pending'] = int(str(result.get('pending') or 0)) + 1
                        if not wlib_result.get('success'):
                            result = wlib_result
            except Exception as exc:
                result = {'success': False, 'error': str(exc)}
            from core.steam_shortcuts import converted_cover_path

            sources = [Path(str(path)) for path in cast(list[object], result.pop('convert', None) or [])]
            with self._state_lock:
                self._status = {'running': False, **result}
                self._conversions = {converted_cover_path(source).name: source for source in sources
                                     if converted_cover_path(source).name not in self._failed_conversions}
                notify = bool(self._conversions)
            if notify and self.on_conversions is not None:
                self.on_conversions()
            if result.get('pending'):
                self.schedule(30, only_if_idle=True)

    def queue_add_wlib(self) -> None:
        def work() -> None:
            result = self.add_wlib()
            with self._state_lock:
                self._status = {"running": False, **result}
                if result.get("pending"):
                    self._status["pending"] = 1
                if result.get("added"):
                    self._status["added"] = 1
        with self._state_lock:
            self._status = {"running": True}
        threading.Thread(target=work, name="steam-add-wlib", daemon=True).start()

    def add_wlib(self) -> dict[str, object]:
        env = environment()
        if not env['root'] or not env['account_id']:
            return {'success': False, 'error': 'Steam account not found'}
        with self._lock:
            result = add_wlib(Path(str(env['root'])), str(env['account_id']), self.script, self.icon)
        if result.get('pending'):
            self.schedule(30)
        return result

    def conversions(self, limit: int = 8) -> dict[str, Path]:
        with self._state_lock:
            return dict(list(self._conversions.items())[:limit])

    def finish_conversion(self, token: str, png: bytes | None) -> bool:
        """Store the window's PNG for a queued cover, or remember that it failed."""
        import os
        import tempfile

        from core.steam_shortcuts import converted_cover_path

        with self._state_lock:
            source = self._conversions.pop(token, None)
            if source is None:
                return False
            if png is None:
                self._failed_conversions.add(token)
            remaining = bool(self._conversions)
        if png is not None:
            target = converted_cover_path(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, temp = tempfile.mkstemp(prefix='.wlib-png-', dir=target.parent)
            try:
                with os.fdopen(fd, 'wb') as stream:
                    _ = stream.write(png)
                os.replace(temp, target)
            finally:
                if os.path.exists(temp):
                    os.unlink(temp)
        if not remaining:
            self.schedule(1)
        return True

    def stop(self) -> None:
        with self._state_lock:
            self._stopped = True
            if self._timer:
                self._timer.cancel()
                self._timer = None
