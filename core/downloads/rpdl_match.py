# SPDX-License-Identifier: GPL-3.0-or-later
"""Match RPDL torrent titles to a game: pure functions, no network.

RPDL titles are release names such as `Eternum-0.8-pc` or
`AeternumTheAwakening-2.2`: spaces are dropped, words may run together in
CamelCase, and a version and platform words follow a dash.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from difflib import SequenceMatcher
from typing import cast

from core.versions import normalize_version

HIDE_BELOW = 40
MAX_SHOWN = 8
PRESELECT_AT = 70
AMBIGUITY_GAP = 8
MARKER_PENALTY = 40
SEQUEL_PENALTY = 35

RELEASE_WORDS = frozenset({
    "pc", "win", "win32", "win64", "windows", "linux", "mac", "macos", "osx", "android", "apk",
    "x86", "x64", "final", "public", "patreon", "fix", "fixed", "hotfix", "beta", "alpha",
    "full", "uncensored", "uncen", "steam", "gog", "repack", "multi", "compressed", "extras",
    "unofficial", "ported", "port", "eng", "english",
})
MARKER_WORDS = frozenset({
    "ost", "soundtrack", "dlc", "demo", "bundle", "season", "episode", "trainer", "artbook",
    "walkthrough",
})
_ROMAN = {
    "ii": "2", "iii": "3", "iv": "4", "vi": "6", "vii": "7", "viii": "8", "ix": "9",
    "xi": "11", "xii": "12", "xiii": "13", "xiv": "14", "xv": "15", "xvi": "16",
    "xvii": "17", "xviii": "18", "xix": "19", "xx": "20",
}
_VERSION_TOKEN = re.compile(r"^v?\d+(?:\.\d+)+[a-z]*$|^v\d+[a-z]*$")
_CHAPTER_TOKEN = re.compile(r"^(?:ch|chap|chapter|ep|part|pt)\d+$")
_FIX_TOKEN = re.compile(r"^fix(?:ed)?\d*$|^hotfix\d*$")
_BRACKETS = re.compile(r"\[[^\]]*\]|\([^)]*\)|\{[^}]*\}")


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return text.replace("'", "").replace("’", "")


def _split_words(text: str) -> list[str]:
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", text)
    return re.findall(r"[A-Za-z0-9]+(?:\.[0-9]+)*", text)


def name_tokens(title: str) -> tuple[list[str], set[str]]:
    """Lower-case name words (release words, versions and markers removed) and the markers found."""
    tokens: list[str] = []
    markers: set[str] = set()
    versioned = False
    for raw in _split_words(_BRACKETS.sub(" ", _plain(title))):
        word = raw.lower()
        if _VERSION_TOKEN.match(word) and (versioned or len(tokens) > 0 or "." in word):
            versioned = True
            continue
        if word in RELEASE_WORDS or _FIX_TOKEN.match(word) or _CHAPTER_TOKEN.match(word):
            continue
        if versioned and not word.isdigit() and len(word) <= 3:
            continue
        if word in MARKER_WORDS:
            markers.add(word)
            continue
        tokens.append(_ROMAN.get(word, word))
    return tokens, markers


def _compact(tokens: Sequence[str]) -> str:
    return "".join(tokens)


def _token_match(a: str, b: str) -> bool:
    if a == b:
        return True
    if a.isdigit() or b.isdigit():
        return False
    return a[0] == b[0] and len(a) > 3 and len(b) > 3 and SequenceMatcher(None, a, b).ratio() >= 0.85


def _overlap(game: Sequence[str], other: Sequence[str]) -> int:
    free = list(other)
    count = 0
    for token in game:
        for index, candidate in enumerate(free):
            if _token_match(token, candidate):
                del free[index]
                count += 1
                break
    return count


def _numbers(tokens: Sequence[str]) -> list[str]:
    return sorted(token for token in tokens if token.isdigit())


def title_score(game_title: str, result_title: str) -> int:
    """0-100 similarity of a result title to the game title (no version or seeder bonus)."""
    game, game_markers = name_tokens(game_title)
    other, other_markers = name_tokens(result_title)
    if not game or not other:
        return 0
    if _compact(game) == _compact(other):
        score = 100.0
    else:
        shared = _overlap(game, other)
        score = 100.0 * shared * shared / (len(game) * len(other))
        if _numbers(game) != _numbers(other):
            score -= SEQUEL_PENALTY
    score -= MARKER_PENALTY * len(other_markers - game_markers)
    return max(0, min(100, round(score)))


# --- versions --------------------------------------------------------------

_VERSION_WORDS = re.compile(r"(?:^|[^a-z0-9])(?:hotfix|fixed|fix|final|beta|alpha|public|patreon)\d*(?![a-z])")
_VERSION_IN_TITLE = re.compile(
    r"(?<![\w.])v?(\d+(?:[._]\d+)*[a-z]?)(?:[-_ ]?(?:hotfix|fixed|fix|final|beta|alpha|public|patreon)\d*)*(?![\w.]*\d)",
    re.IGNORECASE,
)


def canonical_version(version: object) -> str:
    """A version without a leading v, release suffix words or trailing `.0` parts."""
    text = normalize_version(version).replace("_", ".")
    text = _VERSION_WORDS.sub("", text).strip(" .-")
    while re.search(r"\.0+$", text):
        text = re.sub(r"\.0+$", "", text)
    return text


def versions_in_title(title: str) -> list[str]:
    found: list[str] = []
    for match in _VERSION_IN_TITLE.finditer(_BRACKETS.sub(" ", title)):
        canonical = canonical_version(match.group(1))
        if canonical and canonical not in found:
            found.append(canonical)
    return found


def listing_has_version(title: str, version: object) -> bool:
    wanted = canonical_version(version)
    return bool(wanted) and wanted in versions_in_title(title)


# --- query planning --------------------------------------------------------

_VERSION_TEXT = re.compile(r"\bv?\d+(?:\.\d+)+[a-z]*\b|\bv\d+\b", re.IGNORECASE)


def _clean(title: str) -> str:
    return "".join(ch for ch in title if ch.isalnum()).lower()


def plan_queries(title: str, developer: str = "") -> list[str]:
    """Searches to try in order: full title, version-free title, title plus developer."""
    plain = " ".join(title.split())
    stripped = " ".join(_VERSION_TEXT.sub(" ", _BRACKETS.sub(" ", plain)).split())
    candidates = [plain, stripped]
    if developer.strip() and stripped:
        candidates.append(f"{stripped} {developer.strip()}")
    plan: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = _clean(candidate)
        if len(key) >= 2 and key not in seen:
            seen.add(key)
            plan.append(candidate)
    return plan


# --- ranking ---------------------------------------------------------------


def _int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def rank(
    results: Sequence[Mapping[str, object]],
    game_title: str,
    version: object = None,
) -> dict[str, object]:
    """Score, filter and order results; pick a preselected one only when it clearly wins."""
    want_version = bool(canonical_version(version))
    scored: list[dict[str, object]] = []
    for raw in results:
        title = str(raw.get("title") or "")
        seeders = _int(raw.get("seeders"))
        has_version = listing_has_version(title, version) if want_version else None
        score = title_score(game_title, title)
        if score:
            score += 4 if has_version else 0
            score += min(2, seeders // 10)
        score = max(0, min(100, score))
        if score < HIDE_BELOW:
            continue
        item: dict[str, object] = dict(raw)
        item.update(score=score, version_match=has_version, no_seeders=seeders == 0)
        scored.append(item)
    scored.sort(key=lambda r: (-_int(r["score"]), -_int(r.get("seeders")), -_int(r.get("date"))))
    shown = scored[:MAX_SHOWN]
    eligible = [
        r for r in shown
        if _int(r["seeders"]) > 0 and (r["version_match"] is not False) and _int(r["score"]) >= PRESELECT_AT
    ]
    preselected: int | None = None
    if eligible:
        top = eligible[0]
        close = [r for r in eligible[1:] if _int(top["score"]) - _int(r["score"]) <= AMBIGUITY_GAP]
        if not close:
            preselected = _int(top["id"])
    return {"results": shown, "preselected_id": preselected, "needs_choice": bool(shown) and preselected is None}


def find(
    search: Callable[[str], list[dict[str, object]]],
    game_title: str,
    developer: str = "",
    version: object = None,
) -> dict[str, object]:
    """Run the query plan through `search`, stopping at the first search with results."""
    for query in plan_queries(game_title, developer):
        found = search(query)
        if found:
            ranked = rank(cast(Sequence[Mapping[str, object]], found), game_title, version)
            ranked["query"] = query
            return ranked
    return {"results": [], "preselected_id": None, "needs_choice": False, "query": ""}
