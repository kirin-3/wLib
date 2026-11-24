# SPDX-License-Identifier: GPL-3.0-or-later
"""The device-local Discover index, built from the installed catalog.

`discover-index.db` in the data folder holds one row per catalog game group
(`game_groups`; one entry per group when the catalog has no groups), with the
card fields already resolved, so Discover can search, filter, sort and page
without reading the catalog's per-site tables. The file is rebuilt after each
catalog install and is never exported, synced or backed up.
"""

import json
import os
import re
import sqlite3
import threading
import time
import unicodedata
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit, urlunsplit

from core import f95zone, lewdcorner
from core.store_links import MATCHABLE_SOURCES

INDEX_FILE = "discover-index.db"
# Raise when the index layout changes, so the next start builds it again.
INDEX_FORMAT = 1
META_FORMAT = "format"
META_CATALOG_SHA256 = "catalog_sha256"
META_BUILT_AT = "built_at"

SITE_ORDER: tuple[str, ...] = ("f95", "lc", "dlsite", "vndb", "ryuugames")
SITE_BITS: dict[str, int] = {site: 1 << index for index, site in enumerate(SITE_ORDER)}
# Weighted F95 rating (f95-game-rating): (v * R + m * C) / (v + m).
F95_RATING_PRIOR_MEAN = 3.2
F95_RATING_PRIOR_REVIEWS = 25
STATUSES = ("ongoing", "completed", "onhold", "abandoned")

Node = tuple[str, str]

_SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE games (
    key TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    sort_title TEXT NOT NULL,
    developer TEXT NOT NULL,
    version TEXT NOT NULL,
    version_site TEXT NOT NULL,
    cover_url TEXT NOT NULL,
    sites INTEGER NOT NULL,
    f95_id INTEGER,
    lc_id INTEGER,
    lc_tier TEXT NOT NULL,
    dlsite_id TEXT NOT NULL,
    vndb_id TEXT NOT NULL,
    ryuu_id INTEGER,
    tags TEXT NOT NULL,
    status TEXT NOT NULL,
    engine TEXT NOT NULL,
    rating REAL,
    rating_count INTEGER,
    wrating REAL,
    released_at TEXT NOT NULL,
    updated_ts INTEGER NOT NULL,
    search_text TEXT NOT NULL
);
CREATE INDEX games_title ON games (sort_title, key);
CREATE INDEX games_updated ON games (updated_ts DESC, sort_title, key);
CREATE INDEX games_wrating ON games (wrating DESC, sort_title, key);
CREATE INDEX games_released ON games (released_at DESC, sort_title, key);
CREATE TABLE tags (key TEXT PRIMARY KEY, name TEXT NOT NULL, count INTEGER NOT NULL);
-- Every identity of every entry of a group (its own entry IDs and the store
-- IDs its first posts state), for the library markers.
CREATE TABLE entry_ids (
    kind TEXT NOT NULL,
    value TEXT NOT NULL,
    key TEXT NOT NULL,
    PRIMARY KEY (kind, value, key)
) WITHOUT ROWID;
"""

_GAME_COLUMNS = 23
_LEWDCORNER_HOSTS = ("lewdcorner.com",)
_DATE_RE = re.compile(r"^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?")
_PRODUCT_CODE_RE = re.compile(r"(?:RJ|VJ|BJ)\d+", re.I)
_APOSTROPHES_RE = re.compile(r"['’`]")
_NON_WORD_RE = re.compile(r"[^\w]+|_+")
_COMBINING_RE = re.compile(r"[̀-ͯ]")


def normalize_text(value: object) -> str:
    """Search form of a text: no case, Latin accents, apostrophes or punctuation.

    Kana voicing marks stay, so Japanese text keeps its meaning. Words are
    separated by one space.
    """
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = unicodedata.normalize(
        "NFC", _COMBINING_RE.sub("", unicodedata.normalize("NFD", text))
    )
    text = _APOSTROPHES_RE.sub("", text)
    return " ".join(_NON_WORD_RE.sub(" ", text).split())


def tag_key(name: object) -> str:
    """One key for an F95 tag name and an LC tag slug ("big tits", "big-tits")."""
    return f95zone.normalize_prefix_label(normalize_text(name))


def weighted_rating(rating: object, count: object) -> float | None:
    """Bayesian weighted F95 rating, or None without a rating in (0, 5] and a positive count."""
    if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
        return None
    if isinstance(rating, bool) or not isinstance(rating, (int, float)):
        return None
    if not 0 < rating <= 5:
        return None
    return (count * rating + F95_RATING_PRIOR_REVIEWS * F95_RATING_PRIOR_MEAN) / (
        count + F95_RATING_PRIOR_REVIEWS
    )


def card_cover_url(site: str, url: object) -> str:
    """A cover URL the wLib window may load, or "".

    F95 covers load from the preview host; covers on lewdcorner.com are never
    loaded by the window (the card shows the placeholder).
    """
    text = str(url or "").strip()
    if text.startswith("//"):
        text = "https:" + text
    try:
        parts = urlsplit(text)
    except ValueError:
        return ""
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or not host:
        return ""
    if any(host == name or host.endswith("." + name) for name in _LEWDCORNER_HOSTS):
        return ""
    if site == "f95" and host == "attachments.f95zone.to":
        return urlunsplit(("https", "preview.f95zone.to", parts.path, parts.query, ""))
    return text


def _iso_date(value: object) -> str:
    """`YYYY-MM-DD` from an ISO-like date; a missing month or day becomes 01."""
    match = _DATE_RE.match(str(value or "").strip())
    if not match:
        return ""
    year, month, day = match.group(1), match.group(2) or "01", match.group(3) or "01"
    if year == "0000":
        return ""
    return f"{year}-{month}-{day}"


def _epoch(value: object) -> int:
    """The time of a past date; 0 for a missing or a planned (future) date."""
    date = _iso_date(value)
    if not date:
        return 0
    try:
        stamp = int(datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=UTC).timestamp())
    except ValueError:
        return 0
    return stamp if stamp <= time.time() else 0


def _json(raw: object, default: object) -> object:
    try:
        return cast(object, json.loads(str(raw))) if raw else default
    except ValueError:
        return default


def _items(raw: object) -> list[object]:
    value = _json(raw, [])
    return cast(list[object], value) if isinstance(value, list) else []


def _strings(raw: object) -> list[str]:
    return [
        item.strip() for item in _items(raw) if isinstance(item, str) and item.strip()
    ]


def _named(raw: object, names: Mapping[int, str]) -> list[str]:
    """Names of a JSON list of IDs or names (prefixes, F95 tags)."""
    values: list[str] = []
    for item in _items(raw):
        if isinstance(item, int) and not isinstance(item, bool):
            name = names.get(item, "")
        elif isinstance(item, str) and item.isascii() and item.isdigit():
            name = names.get(int(item), "")
        else:
            name = item if isinstance(item, str) else ""
        if name.strip():
            values.append(name.strip())
    return values


def _lc_prefixes(raw: object, names: Mapping[int, str]) -> list[lewdcorner.LcPrefix]:
    prefixes: list[lewdcorner.LcPrefix] = []
    for item in _items(raw):
        if isinstance(item, int) and not isinstance(item, bool):
            prefix = lewdcorner.lc_prefix(names.get(item, ""), item)
        elif isinstance(item, str) and item.isascii() and item.isdigit():
            prefix = lewdcorner.lc_prefix(names.get(int(item), ""), int(item))
        else:
            prefix = lewdcorner.lc_prefix(item)
        if prefix:
            prefixes.append(prefix)
    return prefixes


def _stated_ids(raw: object) -> dict[str, list[str]]:
    """Store IDs a first post states, by kind (`external_ids` JSON)."""
    value = _json(raw, {})
    found: dict[str, list[str]] = {}
    if not isinstance(value, dict):
        return found
    for kind, entries in cast(dict[str, object], value).items():
        if kind not in MATCHABLE_SOURCES or not isinstance(entries, list):
            continue
        for entry in cast(list[object], entries):
            identity = (
                cast(dict[str, object], entry).get("id")
                if isinstance(entry, dict)
                else entry
            )
            if isinstance(identity, str) and identity.strip():
                found.setdefault(kind, []).append(identity.strip())
    return found


def _status(flags: f95zone.ThreadPrefixFlags) -> str:
    if flags.abandoned:
        return "abandoned"
    if flags.onhold:
        return "onhold"
    if flags.completed:
        return "completed"
    return "ongoing"


def _int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _version_text(value: object) -> str:
    text = str(value or "").strip()
    return "" if _PRODUCT_CODE_RE.fullmatch(text) else text


@dataclass
class Entry:
    """One catalog entry with the fields the index needs."""

    site: str
    id: str
    title: str = ""
    titles: list[str] = field(default_factory=list)
    developer: str = ""
    developers: list[str] = field(default_factory=list)
    version: str = ""
    cover: str = ""
    updated_ts: int = 0
    released_at: str = ""
    tags: list[str] = field(default_factory=list)
    status: str = ""
    engine: str = ""
    rating: float | None = None
    rating_count: int | None = None
    tier: str = ""
    stated: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class BuildResult:
    games: int
    entries: int
    seconds: float
    size: int


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {
        str(name)
        for (name,) in cast(
            list[tuple[object]],
            conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall(),
        )
    }


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {
        str(row[1])
        for row in cast(
            list[tuple[object, ...]],
            conn.execute(f"PRAGMA table_info({table})").fetchall(),
        )
    }


def _names(conn: sqlite3.Connection, tables: set[str], table: str) -> dict[int, str]:
    if table not in tables:
        return {}
    return {
        int(identity): str(name)
        for identity, name in cast(
            list[tuple[int, str]],
            conn.execute(f"SELECT id, name FROM {table}").fetchall(),
        )
        if name
    }


def _rows(conn: sqlite3.Connection, query: str) -> list[dict[str, object]]:
    return _rows_with_args(conn, query, ())


def _optional(columns: set[str], alias: str, name: str) -> str:
    return f"{alias}.{name}" if name in columns else f"NULL AS {name}"


def _read_f95(conn: sqlite3.Connection, tables: set[str]) -> list[Entry]:
    prefix_names = _names(conn, tables, "f95_prefix_names")
    tag_names = _names(conn, tables, "f95_tag_names")
    if "f95_thread_details" in tables:
        details = _columns(conn, "f95_thread_details")
        fields = ", ".join(
            _optional(details, "d", name)
            for name in ("original_title", "aliases", "rating_count", "external_ids")
        )
        join = "LEFT JOIN f95_thread_details d ON d.thread_id = t.thread_id AND d.status = 'ok'"
    else:
        fields = "NULL AS original_title, NULL AS aliases, NULL AS rating_count, NULL AS external_ids"
        join = ""
    entries: list[Entry] = []
    for row in _rows(
        conn,
        "SELECT t.thread_id, t.title, t.creator, t.version, t.ts, t.rating, t.prefixes, "
        + f"t.tags, t.cover_url, {fields} FROM f95_threads t {join} WHERE t.removed_at IS NULL",
    ):
        prefixes = _named(row["prefixes"], prefix_names)
        labels = [f95zone.normalize_prefix_label(prefix) for prefix in prefixes]
        engine = next(
            (
                prefix
                for prefix, label in zip(prefixes, labels, strict=True)
                if label not in f95zone.NON_ENGINE_PREFIXES
            ),
            "",
        )
        rating = row["rating"]
        count = row["rating_count"]
        title = str(row["title"] or "").strip()
        entries.append(
            Entry(
                site="f95",
                id=str(row["thread_id"]),
                title=title,
                titles=[
                    title,
                    str(row["original_title"] or ""),
                    *_strings(row["aliases"]),
                ],
                developer=str(row["creator"] or "").strip(),
                developers=[str(row["creator"] or "")],
                version=_version_text(row["version"]),
                cover=card_cover_url("f95", row["cover_url"]),
                updated_ts=_int(row["ts"]),
                tags=_named(row["tags"], tag_names),
                status=_status(f95zone.thread_prefix_flags(prefixes))
                if prefixes
                else "",
                engine=engine,
                rating=float(rating)
                if isinstance(rating, (int, float)) and not isinstance(rating, bool)
                else None,
                rating_count=count
                if isinstance(count, int) and not isinstance(count, bool)
                else None,
                stated=_stated_ids(row["external_ids"]),
            )
        )
    return entries


def _read_lc(conn: sqlite3.Connection, tables: set[str]) -> list[Entry]:
    prefix_names = _names(conn, tables, "lc_prefix_names")
    if "lc_thread_details" in tables:
        fields = "d.developer, d.released_at, d.external_ids"
        join = "LEFT JOIN lc_thread_details d ON d.thread_id = t.thread_id AND d.status = 'ok'"
    else:
        fields = "NULL AS developer, NULL AS released_at, NULL AS external_ids"
        join = ""
    entries: list[Entry] = []
    for row in _rows(
        conn,
        "SELECT t.thread_id, t.title, t.creator, t.version, t.ts, t.tier, t.prefixes, "
        + f"t.tags, t.cover_url, {fields} FROM lc_threads t {join} WHERE t.removed_at IS NULL",
    ):
        prefixes = _lc_prefixes(row["prefixes"], prefix_names)
        engine, flags = lewdcorner.prefix_details(prefixes)
        developer = str(row["developer"] or row["creator"] or "").strip()
        title = str(row["title"] or "").strip()
        entries.append(
            Entry(
                site="lc",
                id=str(row["thread_id"]),
                title=title,
                titles=[title],
                developer=developer,
                developers=[str(row["developer"] or ""), str(row["creator"] or "")],
                version=_version_text(row["version"]),
                cover=card_cover_url("lc", row["cover_url"]),
                updated_ts=_int(row["ts"]),
                released_at=_iso_date(row["released_at"]),
                tags=_strings(row["tags"]),
                status=_status(flags) if prefixes else "",
                engine=engine,
                tier=str(row["tier"] or ""),
                stated=_stated_ids(row["external_ids"]),
            )
        )
    return entries


def _read_dlsite(conn: sqlite3.Connection, tables: set[str]) -> list[Entry]:
    if "dlsite_works" not in tables:
        return []
    columns = _columns(conn, "dlsite_works")
    entries: list[Entry] = []
    for row in _rows(
        conn,
        "SELECT w.product_id, w.title, w.circle, w.released_at, w.cover_url, "
        + f"{_optional(columns, 'w', 'title_en')}, {_optional(columns, 'w', 'circle_en')} "
        + "FROM dlsite_works w WHERE w.removed_at IS NULL AND COALESCE(w.site, '') != 'appx'",
    ):
        title_en = str(row["title_en"] or "").strip()
        title = str(row["title"] or "").strip()
        circle_en = str(row["circle_en"] or "").strip()
        circle = str(row["circle"] or "").strip()
        released = _iso_date(row["released_at"])
        entries.append(
            Entry(
                site="dlsite",
                id=str(row["product_id"]),
                title=title_en or title,
                titles=[title_en, title],
                developer=circle_en or circle,
                developers=[circle_en, circle],
                cover=card_cover_url("dlsite", row["cover_url"]),
                updated_ts=_epoch(released),
                released_at=released,
            )
        )
    return entries


def _read_vndb(conn: sqlite3.Connection, tables: set[str]) -> list[Entry]:
    if "vndb_vns" not in tables:
        return []
    entries: list[Entry] = []
    for row in _rows(
        conn,
        "SELECT vn_id, title, alttitle, developers, released, cover_url "
        + "FROM vndb_vns WHERE removed_at IS NULL",
    ):
        developers = _strings(row["developers"])
        released = _iso_date(row["released"])
        title = str(row["title"] or "").strip()
        entries.append(
            Entry(
                site="vndb",
                id=str(row["vn_id"]),
                title=title,
                titles=[title, str(row["alttitle"] or "")],
                developer=developers[0] if developers else "",
                developers=developers,
                cover=card_cover_url("vndb", row["cover_url"]),
                updated_ts=_epoch(released),
                released_at=released,
            )
        )
    return entries


def _read_ryuu(conn: sqlite3.Connection, tables: set[str]) -> list[Entry]:
    if "ryuu_posts" not in tables:
        return []
    entries: list[Entry] = []
    for row in _rows(
        conn,
        "SELECT post_id, title, developer, version, published_at, cover_url "
        + "FROM ryuu_posts WHERE removed_at IS NULL",
    ):
        published = _iso_date(row["published_at"])
        title = str(row["title"] or "").strip()
        entries.append(
            Entry(
                site="ryuugames",
                id=str(row["post_id"]),
                title=title,
                titles=[title],
                developer=str(row["developer"] or "").strip(),
                developers=[str(row["developer"] or "")],
                version=_version_text(row["version"]),
                cover=card_cover_url("ryuugames", row["cover_url"]),
                # A post's modification time changes on site-wide edits, so
                # the post date stands for the update time.
                updated_ts=_epoch(published),
                released_at=published,
            )
        )
    return entries


def _steam_links(conn: sqlite3.Connection, tables: set[str]) -> dict[Node, list[str]]:
    found: dict[Node, list[str]] = defaultdict(list)
    if "links" not in tables:
        return found
    for src, identity, steam_id in cast(
        list[tuple[str, str, str]],
        conn.execute(
            "SELECT src_a, id_a, id_b FROM links WHERE src_b = 'steam'"
        ).fetchall(),
    ):
        found[(str(src), str(identity))].append(str(steam_id))
    return found


def _group_keys(
    conn: sqlite3.Connection, tables: set[str], nodes: Iterable[Node]
) -> dict[Node, str]:
    """Each entry's group key; an entry outside `game_groups` is its own group."""
    keys: dict[Node, str] = {}
    if "game_groups" in tables:
        keys = {
            (str(src), str(identity)): str(key)
            for src, identity, key in cast(
                list[tuple[str, str, str]],
                conn.execute("SELECT src, id, group_key FROM game_groups").fetchall(),
            )
        }
    return {node: keys.get(node) or f"{node[0]}:{node[1]}" for node in nodes}


def _first(values: Iterable[str]) -> str:
    return next((value for value in values if value), "")


def _sort_title(title: str) -> str:
    return normalize_text(title) or title.casefold()


def _card(
    key: str, members: list[Entry], steam: Mapping[Node, list[str]]
) -> tuple[tuple[object, ...], dict[str, tuple[str, bool]], set[tuple[str, str]]]:
    """The `games` row of a group, its tags (key -> name) and its identities."""
    by_site = {entry.site: entry for entry in members}
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    ordered = [by_site[site] for site in SITE_ORDER if site in by_site]
    f95, lc = by_site.get("f95"), by_site.get("lc")
    title = _first(entry.title for entry in ordered)
    developer = _first(entry.developer for entry in ordered)
    version, latest = lewdcorner.pick_latest(
        (f95.version, f"{f95.updated_ts:020d}") if f95 else None,
        (lc.version, f"{lc.updated_ts:020d}") if lc else None,
    )
    version_site = {"both": "f95"}.get(latest, latest)
    if not version:
        other = next(
            (
                entry
                for entry in ordered
                if entry.site not in ("f95", "lc") and entry.version
            ),
            None,
        )
        version, version_site = (other.version, other.site) if other else ("", "")
    cover = _first(
        by_site[site].cover
        for site in ("f95", "dlsite", "vndb", "ryuugames", "lc")
        if site in by_site
    )
    # Tag key -> (display name, from F95). The F95 name wins; an LC slug
    # shows with spaces.
    tags: dict[str, tuple[str, bool]] = {}
    for entry in (f95, lc):
        if entry is None:
            continue
        for name in entry.tags:
            if tag := tag_key(name):
                _ = tags.setdefault(
                    tag,
                    (name.replace("-", " "), False)
                    if entry.site == "lc"
                    else (name, True),
                )
    thread = f95 if f95 and f95.status else lc if lc and lc.status else None
    status = thread.status if thread else ""
    engine = (f95.engine if f95 else "") or (lc.engine if lc else "")
    rating = f95.rating if f95 else None
    count = f95.rating_count if f95 else None
    search = dict.fromkeys(
        text
        for entry in ordered
        for value in (*entry.titles, *entry.developers)
        if (text := normalize_text(value))
    )
    identities: set[tuple[str, str]] = set()
    for entry in ordered:
        identities.add((entry.site, entry.id))
        for kind, values in entry.stated.items():
            identities.update((kind, value) for value in values)
        identities.update(
            ("steam", value) for value in steam.get((entry.site, entry.id), ())
        )
    row = (
        key,
        title,
        _sort_title(title),
        developer,
        version,
        version_site,
        cover,
        sum(SITE_BITS[entry.site] for entry in ordered),
        int(f95.id) if f95 else None,
        int(lc.id) if lc else None,
        lc.tier if lc else "",
        by_site["dlsite"].id if "dlsite" in by_site else "",
        by_site["vndb"].id if "vndb" in by_site else "",
        int(by_site["ryuugames"].id) if "ryuugames" in by_site else None,
        "|" + "|".join(sorted(tags)) + "|" if tags else "",
        status,
        engine,
        rating,
        count,
        weighted_rating(rating, count),
        _first(entry.released_at for entry in ordered if entry.released_at <= today),
        max((entry.updated_ts for entry in ordered), default=0),
        "\n".join(search),
    )
    return row, tags, identities


def _identity(kind: str, value: str) -> tuple[str, str]:
    """Library form of an identity (`game_links` and thread IDs)."""
    if kind == "dlsite":
        return kind, value.upper()
    if kind in ("vndb", "fanza", "itch"):
        return kind, value.lower()
    return kind, value


def build_index(
    catalog_path: str | os.PathLike[str],
    target: str | os.PathLike[str],
    *,
    catalog_sha256: str = "",
) -> BuildResult:
    """Build the index from a catalog file into `target`, replacing it atomically.

    Raises `sqlite3.Error` or `OSError` on failure; `target` is then unchanged.
    """
    started = time.monotonic()
    source = Path(catalog_path)
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as conn:
        tables = _tables(conn)
        entries = [
            *(_read_f95(conn, tables) if "f95_threads" in tables else []),
            *(_read_lc(conn, tables) if "lc_threads" in tables else []),
            *_read_dlsite(conn, tables),
            *_read_vndb(conn, tables),
            *_read_ryuu(conn, tables),
        ]
        steam = _steam_links(conn, tables)
        keys = _group_keys(conn, tables, ((entry.site, entry.id) for entry in entries))
    groups: dict[str, list[Entry]] = defaultdict(list)
    for entry in entries:
        groups[keys[(entry.site, entry.id)]].append(entry)
    final = Path(target)
    temporary = final.with_name(f"{final.name}.tmp-{os.getpid()}")
    _remove(temporary)
    try:
        with closing(sqlite3.connect(temporary)) as out:
            _ = out.execute("PRAGMA journal_mode = OFF")
            _ = out.execute("PRAGMA synchronous = OFF")
            _ = out.executescript(_SCHEMA)
            tag_names: dict[str, tuple[str, bool]] = {}
            tag_counts: dict[str, int] = defaultdict(int)
            rows: list[tuple[object, ...]] = []
            identities: list[tuple[str, str, str]] = []
            for key, members in groups.items():
                row, tags, ids = _card(key, members, steam)
                rows.append(row)
                for tag, name in tags.items():
                    tag_counts[tag] += 1
                    if tag not in tag_names or (name[1] and not tag_names[tag][1]):
                        tag_names[tag] = name
                identities.extend((*_identity(kind, value), key) for kind, value in ids)
            with out:
                _ = out.executemany(
                    f"INSERT INTO games VALUES ({', '.join('?' * _GAME_COLUMNS)})",
                    rows,
                )
                _ = out.executemany(
                    "INSERT OR IGNORE INTO entry_ids (kind, value, key) VALUES (?, ?, ?)",
                    identities,
                )
                _ = out.executemany(
                    "INSERT INTO tags (key, name, count) VALUES (?, ?, ?)",
                    (
                        (tag, tag_names[tag][0], count)
                        for tag, count in tag_counts.items()
                    ),
                )
                _ = out.executemany(
                    "INSERT INTO meta (key, value) VALUES (?, ?)",
                    (
                        (META_FORMAT, str(INDEX_FORMAT)),
                        (META_CATALOG_SHA256, catalog_sha256),
                        (
                            META_BUILT_AT,
                            datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        ),
                    ),
                )
            _ = out.execute("ANALYZE")
        os.replace(temporary, final)
    except BaseException:
        _remove(temporary)
        raise
    return BuildResult(
        games=len(groups),
        entries=len(entries),
        seconds=time.monotonic() - started,
        size=final.stat().st_size,
    )


def _remove(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def index_meta(path: str | os.PathLike[str]) -> dict[str, str]:
    """The index file's meta values, or {} when it is missing or unreadable."""
    target = Path(path)
    if not target.is_file():
        return {}
    try:
        with closing(sqlite3.connect(target.as_uri() + "?mode=ro", uri=True)) as conn:
            return {
                str(key): str(value)
                for key, value in cast(
                    list[tuple[object, object]],
                    conn.execute("SELECT key, value FROM meta").fetchall(),
                )
            }
    except (sqlite3.Error, OSError, ValueError):
        return {}


def index_is_current(path: str | os.PathLike[str], catalog_sha256: str) -> bool:
    """True when the index exists, has this layout and was built from this catalog."""
    meta = index_meta(path)
    return (
        meta.get(META_FORMAT) == str(INDEX_FORMAT)
        and meta.get(META_CATALOG_SHA256) == catalog_sha256
    )


class DiscoverIndex:
    """The index file's lifecycle: build when stale, replace atomically, remove.

    `ensure` runs on the catalog thread; the bridge reads `state` and `path`.
    """

    def __init__(self, data_dir: str, *, enabled: Callable[[], bool]) -> None:
        self.path: Path = Path(data_dir) / INDEX_FILE
        self._enabled: Callable[[], bool] = enabled
        # One build at a time. remove() does not wait for a build: the
        # setting is saved first, and a build checks it again after writing.
        self._lock: threading.Lock = threading.Lock()
        self._building: bool = False
        self.error: str = ""
        for stale in self.path.parent.glob(f"{INDEX_FILE}.tmp-*"):
            _remove(stale)

    @property
    def building(self) -> bool:
        return self._building

    def ready(self) -> bool:
        return not self._building and self.path.is_file()

    def ensure(self, catalog_path: str | os.PathLike[str], catalog_sha256: str) -> bool:
        """Build the index unless it is current; True when a build ran and succeeded."""
        with self._lock:
            if not self._enabled() or not catalog_sha256:
                _remove(self.path)
                return False
            if index_is_current(self.path, catalog_sha256):
                return False
            self._building = True
            try:
                result = build_index(
                    catalog_path, self.path, catalog_sha256=catalog_sha256
                )
            except (sqlite3.Error, OSError) as exc:
                self.error = str(exc) or type(exc).__name__
                print(f"[wLib] Could not build the Discover index: {self.error}")
                return False
            finally:
                self._building = False
            self.error = ""
            print(
                f"[wLib] Discover index: {result.games} games from {result.entries} "
                + f"entries in {result.seconds:.1f} s ({result.size // 1024} KiB)"
            )
            if not self._enabled():
                _remove(self.path)
                return False
            return True

    def remove(self) -> None:
        """Delete the index; used when the catalog is turned off."""
        _remove(self.path)
        self.error = ""


# --- Queries ---------------------------------------------------------------

PAGE_SIZE = 120
SORTS: dict[str, str] = {
    # SQLite puts NULL and "" last in a descending order.
    "updated": "updated_ts DESC, sort_title, key",
    "rating": "wrating DESC, sort_title, key",
    "released": "released_at DESC, sort_title, key",
    "title": "sort_title, key",
}
RATING_STEPS = (3.5, 4.0, 4.5)
_CARD_COLUMNS = (
    "key, title, developer, version, version_site, cover_url, sites, f95_id, lc_id, "
    + "lc_tier, dlsite_id, vndb_id, ryuu_id, status, engine, rating, rating_count, "
    + "wrating, released_at, updated_ts"
)


@dataclass
class DiscoverQuery:
    search: str = ""
    include_sites: list[str] = field(default_factory=list)
    exclude_sites: list[str] = field(default_factory=list)
    include_tags: list[str] = field(default_factory=list)
    exclude_tags: list[str] = field(default_factory=list)
    statuses: list[str] = field(default_factory=list)
    engines: list[str] = field(default_factory=list)
    min_rating: float | None = None
    hide_library: bool = False
    sort: str = "updated"
    offset: int = 0
    limit: int = PAGE_SIZE


def _string_list(value: object, allowed: Iterable[str] | None = None) -> list[str]:
    if not isinstance(value, list):
        return []
    known = set(allowed) if allowed is not None else None
    found = dict.fromkeys(
        item.strip()
        for item in cast(list[object], value)
        if isinstance(item, str)
        and item.strip()
        and (known is None or item.strip() in known)
    )
    return list(found)


def _count(value: object, default: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return max(0, min(value, maximum))


def parse_query(params: object) -> DiscoverQuery:
    """A query from untrusted bridge parameters; unknown values are dropped."""
    raw = cast(dict[str, object], params) if isinstance(params, dict) else {}
    search = raw.get("search")
    rating = raw.get("min_rating")
    sort = raw.get("sort")
    return DiscoverQuery(
        search=search[:200] if isinstance(search, str) else "",
        include_sites=_string_list(raw.get("include_sites"), SITE_ORDER),
        exclude_sites=_string_list(raw.get("exclude_sites"), SITE_ORDER),
        include_tags=[
            tag for tag in map(tag_key, _string_list(raw.get("include_tags"))) if tag
        ],
        exclude_tags=[
            tag for tag in map(tag_key, _string_list(raw.get("exclude_tags"))) if tag
        ],
        statuses=_string_list(raw.get("statuses"), STATUSES),
        engines=_string_list(raw.get("engines")),
        min_rating=float(rating)
        if isinstance(rating, (int, float))
        and not isinstance(rating, bool)
        and float(rating) in RATING_STEPS
        else None,
        hide_library=raw.get("hide_library") is True,
        sort=sort if isinstance(sort, str) and sort in SORTS else "updated",
        offset=_count(raw.get("offset"), 0, 10_000_000),
        limit=_count(raw.get("limit"), PAGE_SIZE, PAGE_SIZE) or PAGE_SIZE,
    )


def _like(word: str) -> str:
    escaped = word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _where(query: DiscoverQuery) -> tuple[str, list[object]]:
    terms: list[str] = []
    args: list[object] = []
    for word in normalize_text(query.search).split():
        terms.append("search_text LIKE ? ESCAPE '\\'")
        args.append(_like(word))
    if query.include_sites:
        terms.append("(sites & ?) != 0")
        args.append(sum(SITE_BITS[site] for site in query.include_sites))
    if query.exclude_sites:
        # Hide only games found on excluded sites alone.
        terms.append("(sites & ~?) != 0")
        args.append(sum(SITE_BITS[site] for site in query.exclude_sites))
    for tag in query.include_tags:
        terms.append("tags LIKE ? ESCAPE '\\'")
        args.append(_like(f"|{tag}|"))
    for tag in query.exclude_tags:
        terms.append("tags NOT LIKE ? ESCAPE '\\'")
        args.append(_like(f"|{tag}|"))
    if query.statuses:
        terms.append(f"status IN ({', '.join('?' * len(query.statuses))})")
        args.extend(query.statuses)
    if query.engines:
        terms.append(f"engine IN ({', '.join('?' * len(query.engines))})")
        args.extend(query.engines)
    if query.min_rating is not None:
        terms.append("wrating >= ?")
        args.append(query.min_rating)
    if query.hide_library:
        terms.append("key NOT IN (SELECT key FROM temp.library_keys)")
    return (" WHERE " + " AND ".join(terms)) if terms else "", args


@dataclass(frozen=True)
class LibraryMark:
    game_id: int
    state: str  # "installed" or "wishlisted"


LibraryIdentity = tuple[str, str, int, bool]


def library_identities(conn: sqlite3.Connection) -> list[LibraryIdentity]:
    """`(kind, value, game id, installed)` for each identity of each library game."""
    found: list[LibraryIdentity] = []
    games = cast(
        list[tuple[int, object, object, object]],
        conn.execute("SELECT id, f95_url, lc_url, exe_path FROM games").fetchall(),
    )
    installed: dict[int, bool] = {}
    for game_id, f95_url, lc_url, exe_path in games:
        installed[game_id] = bool(str(exe_path or "").strip())
        if thread := f95zone.extract_thread_id(f95_url):
            found.append(("f95", thread, game_id, installed[game_id]))
        if thread := lewdcorner.extract_thread_id(lc_url):
            found.append(("lc", thread, game_id, installed[game_id]))
    for game_id, source, external_id in cast(
        list[tuple[int, str, str]],
        conn.execute("SELECT game_id, source, external_id FROM game_links").fetchall(),
    ):
        if game_id in installed and source in MATCHABLE_SOURCES:
            kind, value = _identity(str(source), str(external_id))
            found.append((kind, value, game_id, installed[game_id]))
    return found


def _library_marks(
    conn: sqlite3.Connection, identities: Iterable[LibraryIdentity]
) -> dict[str, LibraryMark]:
    """Group key -> the library game it matches; an installed game wins."""
    _ = conn.execute(
        "CREATE TEMP TABLE IF NOT EXISTS library_ids "
        + "(kind TEXT, value TEXT, game_id INTEGER, installed INTEGER)"
    )
    _ = conn.execute("DELETE FROM temp.library_ids")
    _ = conn.executemany("INSERT INTO temp.library_ids VALUES (?, ?, ?, ?)", identities)
    marks: dict[str, LibraryMark] = {}
    for key, game_id, installed in cast(
        list[tuple[str, int, int]],
        conn.execute(
            "SELECT e.key, l.game_id, l.installed FROM temp.library_ids l "
            + "JOIN entry_ids e ON e.kind = l.kind AND e.value = l.value "
            + "ORDER BY l.installed DESC, l.game_id"
        ).fetchall(),
    ):
        _ = marks.setdefault(
            key, LibraryMark(game_id, "installed" if installed else "wishlisted")
        )
    _ = conn.execute(
        "CREATE TEMP TABLE IF NOT EXISTS library_keys (key TEXT PRIMARY KEY)"
    )
    _ = conn.execute("DELETE FROM temp.library_keys")
    _ = conn.executemany(
        "INSERT INTO temp.library_keys VALUES (?)", ((key,) for key in marks)
    )
    return marks


def _open(path: str | os.PathLike[str]) -> sqlite3.Connection:
    return sqlite3.connect(Path(path).as_uri() + "?mode=ro", uri=True)


def _card_dict(
    row: Mapping[str, object], marks: Mapping[str, LibraryMark]
) -> dict[str, object]:
    key = str(row["key"])
    sites = _int(row["sites"])
    mark = marks.get(key)
    return {
        **row,
        "sites": [site for site in SITE_ORDER if sites & SITE_BITS[site]],
        "library": {"game_id": mark.game_id, "state": mark.state} if mark else None,
    }


def search(
    path: str | os.PathLike[str],
    query: DiscoverQuery,
    identities: Iterable[LibraryIdentity] = (),
) -> dict[str, object]:
    """One page of games: `{games, total, next_offset}` (`next_offset` None at the end)."""
    with closing(_open(path)) as conn:
        marks = _library_marks(conn, identities)
        where, args = _where(query)
        total = cast(
            tuple[int],
            conn.execute(f"SELECT COUNT(*) FROM games{where}", args).fetchone(),
        )[0]
        rows = _rows_with_args(
            conn,
            f"SELECT {_CARD_COLUMNS} FROM games{where} ORDER BY {SORTS[query.sort]} "
            + "LIMIT ? OFFSET ?",
            [*args, query.limit, query.offset],
        )
    end = query.offset + len(rows)
    return {
        "games": [_card_dict(row, marks) for row in rows],
        "total": total,
        "next_offset": end if end < total else None,
    }


def _rows_with_args(
    conn: sqlite3.Connection, query: str, args: Iterable[object]
) -> list[dict[str, object]]:
    cursor = conn.execute(query, list(args))
    names = [
        str(column[0]) for column in cast(list[tuple[object, ...]], cursor.description)
    ]
    return [
        dict(zip(names, row, strict=True))
        for row in cast(list[tuple[object, ...]], cursor.fetchall())
    ]


def game_card(
    path: str | os.PathLike[str], key: str, identities: Iterable[LibraryIdentity] = ()
) -> dict[str, object] | None:
    """One game's card fields with its tag names and entry IDs, or None when the key is unknown."""
    with closing(_open(path)) as conn:
        marks = _library_marks(conn, identities)
        rows = _rows_with_args(
            conn, f"SELECT {_CARD_COLUMNS}, tags FROM games WHERE key = ?", [key]
        )
        if not rows:
            return None
        tags = [tag for tag in str(rows[0]["tags"]).split("|") if tag]
        names = dict(
            cast(
                list[tuple[str, str]],
                conn.execute(
                    f"SELECT key, name FROM tags WHERE key IN ({', '.join('?' * len(tags))})",
                    tags,
                ).fetchall(),
            )
        )
        ids = cast(
            list[tuple[str, str]],
            conn.execute(
                "SELECT kind, value FROM entry_ids WHERE key = ? ORDER BY kind, value",
                (key,),
            ).fetchall(),
        )
    card = _card_dict(rows[0], marks)
    card["tags"] = [names.get(tag, tag) for tag in tags]
    card["entry_ids"] = [{"kind": kind, "value": value} for kind, value in ids]
    return card


def filters(path: str | os.PathLike[str]) -> dict[str, object]:
    """The filter choices: tags, engines and statuses with game counts."""
    with closing(_open(path)) as conn:
        tags = cast(
            list[tuple[str, str, int]],
            conn.execute(
                "SELECT key, name, count FROM tags ORDER BY name, key"
            ).fetchall(),
        )
        engines = cast(
            list[tuple[str, int]],
            conn.execute(
                "SELECT engine, COUNT(*) FROM games WHERE engine != '' "
                + "GROUP BY engine ORDER BY engine COLLATE NOCASE"
            ).fetchall(),
        )
        statuses = dict(
            cast(
                list[tuple[str, int]],
                conn.execute(
                    "SELECT status, COUNT(*) FROM games WHERE status != '' GROUP BY status"
                ).fetchall(),
            )
        )
    return {
        "tags": [
            {"key": key, "name": name, "count": count} for key, name, count in tags
        ],
        "engines": [{"name": name, "count": count} for name, count in engines],
        "statuses": [
            {"key": status, "count": statuses.get(status, 0)} for status in STATUSES
        ],
    }


# --- Preview details ---------------------------------------------------------

SITE_NAMES = {
    "f95": "F95Zone",
    "lc": "LewdCorner",
    "dlsite": "DLsite",
    "vndb": "VNDB",
    "ryuugames": "Ryuugames",
}
_ENTRY_TABLES: dict[str, tuple[str, str]] = {
    "f95": ("f95_threads", "thread_id"),
    "lc": ("lc_threads", "thread_id"),
    "dlsite": ("dlsite_works", "product_id"),
    "vndb": ("vndb_vns", "vn_id"),
    "ryuugames": ("ryuu_posts", "post_id"),
}


def _one(
    conn: sqlite3.Connection, query: str, args: Iterable[object]
) -> dict[str, object]:
    rows = _rows_with_args(conn, query, args)
    return rows[0] if rows else {}


def _https_url(value: object) -> str:
    text = str(value or "").strip()
    try:
        scheme = urlsplit(text).scheme
    except ValueError:
        return ""
    return text if scheme in ("http", "https") else ""


def entry_details(
    catalog_path: str | os.PathLike[str], card: Mapping[str, object]
) -> dict[str, object]:
    """Preview details of a group's entries, read from the catalog.

    `entries` lists each entry in site order with its page URL; the overview
    comes from the F95 thread, else the LC thread. Values the catalog lacks
    are left out.
    """
    ids: dict[str, str] = {}
    for site, column in (
        ("f95", "f95_id"),
        ("lc", "lc_id"),
        ("dlsite", "dlsite_id"),
        ("vndb", "vndb_id"),
        ("ryuugames", "ryuu_id"),
    ):
        value = card.get(column)
        if value not in (None, ""):
            ids[site] = str(value)
    details: dict[str, object] = {}
    entries: list[dict[str, object]] = []
    with closing(_open(catalog_path)) as conn:
        tables = _tables(conn)
        for site in SITE_ORDER:
            if site not in ids or _ENTRY_TABLES[site][0] not in tables:
                continue
            table, key = _ENTRY_TABLES[site]
            identity: object = (
                int(ids[site]) if key != "product_id" and key != "vn_id" else ids[site]
            )
            row = _one(
                conn,
                f"SELECT title, url{', tier' if site == 'lc' else ''} FROM {table} WHERE {key} = ?",
                [identity],
            )
            if not row:
                continue
            entry: dict[str, object] = {
                "site": site,
                "name": SITE_NAMES[site],
                "id": ids[site],
                "title": str(row.get("title") or ""),
                "url": _https_url(row.get("url")),
            }
            if site == "lc":
                entry["tier"] = str(row.get("tier") or "")
            entries.append(entry)
        f95: dict[str, object] = {}
        if "f95" in ids and "f95_thread_details" in tables:
            columns = _columns(conn, "f95_thread_details")
            fields = ", ".join(
                _optional(columns, "d", name)
                for name in (
                    "overview",
                    "original_title",
                    "aliases",
                    "languages",
                    "language",
                )
            )
            f95 = _one(
                conn,
                f"SELECT {fields} FROM f95_thread_details d WHERE d.thread_id = ? AND d.status = 'ok'",
                [int(ids["f95"])],
            )
        lc: dict[str, object] = {}
        if "lc" in ids and "lc_thread_details" in tables:
            lc = _one(
                conn,
                "SELECT overview, languages, language, release_text FROM lc_thread_details "
                + "WHERE thread_id = ? AND status = 'ok'",
                [int(ids["lc"])],
            )
        dlsite: dict[str, object] = {}
        if "dlsite" in ids and "dlsite_works" in tables:
            columns = _columns(conn, "dlsite_works")
            dlsite = _one(
                conn,
                f"SELECT w.genres, {_optional(columns, 'w', 'genres_en')} FROM dlsite_works w "
                + "WHERE w.product_id = ?",
                [ids["dlsite"]],
            )
    overview = (
        str(f95.get("overview") or "").strip() or str(lc.get("overview") or "").strip()
    )
    if overview:
        details["overview"] = overview
    original = str(f95.get("original_title") or "").strip()
    if original:
        details["original_title"] = original
    aliases = _strings(f95.get("aliases"))
    if aliases:
        details["aliases"] = aliases
    languages = _strings(f95.get("languages")) or _strings(lc.get("languages"))
    if not languages:
        single = str(f95.get("language") or lc.get("language") or "").strip()
        languages = [single] if single else []
    if languages:
        details["languages"] = languages
    genres = _strings(dlsite.get("genres_en")) or _strings(dlsite.get("genres"))
    if genres:
        details["dlsite_genres"] = genres
    details["entries"] = entries
    return details
