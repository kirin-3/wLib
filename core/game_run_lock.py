# SPDX-License-Identifier: GPL-3.0-or-later
"""Cross-process game launch locks."""
from __future__ import annotations

import os
from pathlib import Path


def acquire_launch_lock(game_id: int) -> int | None:
    """Reserve a game across GUI and runner processes for the full launch."""
    import fcntl

    from core.database import DB_PATH

    if game_id <= 0:
        raise ValueError('Invalid game ID')
    path = Path(DB_PATH).parent / 'runs' / f'{game_id}.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except BlockingIOError:
        os.close(fd)
        return None
    except BaseException:
        os.close(fd)
        raise


