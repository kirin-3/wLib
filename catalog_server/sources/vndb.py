# SPDX-License-Identifier: GPL-3.0-or-later
"""VNDB's kana API: visual novels with an 18+ release, and adult releases
that link to DLsite or Steam. VNDB data is under the Open Database License.

There is no incremental mode; the whole set is refreshed once a week.
"""

from datetime import datetime, timedelta
from typing import cast

from .base import SourceContext

API_URL = "https://api.vndb.org/kana/{endpoint}"
REFRESH_INTERVAL = timedelta(days=7)
# Tags at or above this score, and without spoilers, are stored by name.
MIN_TAG_RATING = 1.0

VN_FIELDS = "id,title,alttitle,released,olang,image.url,developers.name,tags.name,tags.rating,tags.spoiler"
RELEASE_FIELDS = "id,title,released,vns.id,extlinks.name,extlinks.id"
PHASES = ("vn", "release")


def _filters(phase: str, after: str | None) -> list[object]:
    if phase == "vn":
        base: list[object] = ["release", "=", ["minage", "=", 18]]
    else:
        base = [
            "and",
            ["minage", "=", 18],
            ["or", ["extlink", "=", "dlsite"], ["extlink", "=", "steam"]],
        ]
    if after is None:
        return base
    return ["and", base, ["id", ">", after]]


def _dicts(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [
        cast(dict[str, object], item) for item in cast(list[object], value) if isinstance(item, dict)
    ]


def parse_vn(item: dict[str, object]) -> dict[str, object]:
    vn_id = str(item["id"])
    image = item.get("image")
    tags = sorted(
        (
            tag
            for tag in _dicts(item.get("tags"))
            if tag.get("spoiler") == 0
            and isinstance(tag.get("rating"), (int, float))
            and cast(float, tag["rating"]) >= MIN_TAG_RATING
        ),
        key=lambda tag: -cast(float, tag["rating"]),
    )
    return {
        "vn_id": vn_id,
        "title": str(item.get("title") or ""),
        "alttitle": item.get("alttitle") if isinstance(item.get("alttitle"), str) else None,
        "developers": [str(dev.get("name")) for dev in _dicts(item.get("developers"))],
        "released": item.get("released") if isinstance(item.get("released"), str) else None,
        "olang": item.get("olang") if isinstance(item.get("olang"), str) else None,
        "tags": [str(tag.get("name")) for tag in tags],
        "cover_url": cast(dict[str, object], image).get("url")
        if isinstance(image, dict)
        else None,
        "url": f"https://vndb.org/{vn_id}",
    }


def parse_release(item: dict[str, object]) -> dict[str, object]:
    release_id = str(item["id"])
    links = _dicts(item.get("extlinks"))
    return {
        "release_id": release_id,
        "title": str(item.get("title") or ""),
        "released": item.get("released") if isinstance(item.get("released"), str) else None,
        "vn_ids": [str(vn.get("id")) for vn in _dicts(item.get("vns"))],
        "dlsite_ids": [str(link.get("id")) for link in links if link.get("name") == "dlsite"],
        "steam_ids": [str(link.get("id")) for link in links if link.get("name") == "steam"],
        "url": f"https://vndb.org/{release_id}",
    }


class VndbSource:
    name: str = "vndb"

    def incremental(self, ctx: SourceContext) -> None:
        del ctx

    def sweep_due(self, ctx: SourceContext) -> bool:
        if ctx.state.get(self.name, "sweep_cursor") is not None:
            return True
        completed = ctx.state.source_status(self.name)["sweep_completed_at"]
        if completed is None:
            return True
        now = datetime.fromisoformat(ctx.now().replace("Z", "+00:00"))
        return now - datetime.fromisoformat(completed.replace("Z", "+00:00")) >= REFRESH_INTERVAL

    def sweep_step(self, ctx: SourceContext) -> bool:
        cursor = ctx.state.get(self.name, "sweep_cursor")
        phase, after = cast(list[str | None], cursor) if isinstance(cursor, list) else ["vn", None]
        phase = phase if phase in PHASES else "vn"
        response = ctx.pacer.request(
            self.name,
            API_URL.format(endpoint=phase),
            method="POST",
            json_body={
                "filters": _filters(phase, after),
                "fields": VN_FIELDS if phase == "vn" else RELEASE_FIELDS,
                "sort": "id",
                "results": 100,
            },
        )
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("VNDB response is not an object")
        body = cast(dict[str, object], payload)
        items = _dicts(body.get("results"))
        if phase == "vn":
            ctx.upsert("vndb_vns", "vn_id", [parse_vn(item) for item in items])
        else:
            ctx.upsert("vndb_releases", "release_id", [parse_release(item) for item in items])
        if body.get("more") is True and items:
            ctx.state.put(self.name, "sweep_cursor", [phase, str(items[-1]["id"])])
            return False
        if phase == "vn":
            ctx.state.put(self.name, "sweep_cursor", ["release", None])
            return False
        return True

    def reset_sweep(self, ctx: SourceContext) -> None:
        ctx.state.put(self.name, "sweep_cursor", None)
