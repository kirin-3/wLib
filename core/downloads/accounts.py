# SPDX-License-Identifier: GPL-3.0-or-later
"""Connect and disconnect the file-host accounts used for in-app downloads."""
from __future__ import annotations

from core.downloads import credentials
from core.downloads.hosts import mega, pixeldrain


def status() -> dict[str, object]:
    """Account state for the UI; never includes keys or session ids."""
    return {
        "pixeldrain": {
            "connected": bool(credentials.get(credentials.PIXELDRAIN_API_KEY)),
            "username": credentials.get(credentials.PIXELDRAIN_USERNAME),
        },
        "mega": {
            "connected": bool(credentials.get(credentials.MEGA_SESSION)),
            "email": credentials.get(credentials.MEGA_EMAIL),
        },
    }


def connect_pixeldrain(api_key: str) -> str:
    api_key = api_key.strip()
    username = pixeldrain.verify_key(api_key)
    credentials.put({
        credentials.PIXELDRAIN_API_KEY: api_key,
        credentials.PIXELDRAIN_USERNAME: username,
    })
    return username


def disconnect_pixeldrain() -> None:
    credentials.put({credentials.PIXELDRAIN_API_KEY: "", credentials.PIXELDRAIN_USERNAME: ""})


def connect_mega(email: str, password: str, code: str = "") -> str:
    session = mega.login(email, password, code)
    previous = credentials.get(credentials.MEGA_SESSION)
    email = email.strip().lower()
    credentials.put({credentials.MEGA_SESSION: session, credentials.MEGA_EMAIL: email})
    if previous and previous != session:
        mega.logout(previous)
    return email


def disconnect_mega() -> None:
    session = credentials.get(credentials.MEGA_SESSION)
    credentials.put({credentials.MEGA_SESSION: "", credentials.MEGA_EMAIL: ""})
    mega.logout(session)
