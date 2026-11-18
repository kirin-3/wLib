# SPDX-License-Identifier: GPL-3.0-or-later
"""LewdCorner thread identity and per-game activation."""

import re
from collections.abc import Iterable, Mapping
from urllib.parse import urlsplit

from core.f95zone import normalize_prefix_label

LC_HOSTS = frozenset({"lewdcorner.com", "www.lewdcorner.com"})
_THREAD_PATH = re.compile(r"^/threads/(?:[^/]+\.)?([0-9]+)(?:/.*)?$", re.IGNORECASE)
LC_ENGINE_PREFIXES = (
    "Flash", "HTML", "Java", "QSP", "RAGS", "RPGM", "Ren'Py", "Unity",
    "Unreal Engine", "WebGL", "Wolf RPG", "Godot", "Other",
)
_ENGINES = {normalize_prefix_label(name): name for name in LC_ENGINE_PREFIXES}
LC_SETTINGS = ("lc_enabled", "lc_plus")


def hosts_url(url: str) -> bool:
    try:
        host = urlsplit(url).hostname or ""
        return host == "lewdcorner.com" or host.endswith(".lewdcorner.com")
    except ValueError:
        return False


def extract_thread_id(url: object) -> str:
    if not isinstance(url, str):
        return ""
    value = url.strip()
    if value.lower().startswith(("lewdcorner.com/", "www.lewdcorner.com/")):
        value = "https://" + value
    try:
        parts = urlsplit(value)
        if (
            parts.scheme not in {"http", "https"}
            or parts.hostname not in LC_HOSTS
            or parts.username is not None
            or parts.port not in {None, 80 if parts.scheme == "http" else 443}
        ):
            return ""
    except ValueError:
        return ""
    match = _THREAD_PATH.fullmatch(parts.path)
    return match[1].lstrip("0") if match else ""


def normalize_thread_url(url: object) -> str:
    thread_id = extract_thread_id(url)
    return f"https://lewdcorner.com/threads/{thread_id}/" if thread_id else ""


def thread_urls_match(left: object, right: object) -> bool:
    thread_id = extract_thread_id(left)
    return bool(thread_id and thread_id == extract_thread_id(right))


def validated_thread_url(value: object) -> str:
    url = normalize_thread_url(value)
    if str(value or "").strip() and not url:
        raise ValueError("The URL is not a LewdCorner thread")
    return url


def normalize_mode(value: object) -> str:
    mode = str(value or "").strip().lower()
    if mode not in {"", "on", "off"}:
        raise ValueError("Invalid LewdCorner mode")
    return mode


def setting_enabled(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "on", "yes"}


def lc_active(game: Mapping[str, object], lc_enabled: bool) -> bool:
    mode = game.get("lc_mode") or ""
    return bool(game.get("lc_url")) and (
        mode == "on" or (mode == "" and (lc_enabled or not game.get("f95_url")))
    )


def prefix_details(prefixes: Iterable[object]) -> tuple[str, bool]:
    labels = [normalize_prefix_label(prefix) for prefix in prefixes]
    engine = next((_ENGINES[label] for label in labels if label in _ENGINES), "")
    return engine, "completed" in labels
