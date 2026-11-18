# SPDX-License-Identifier: GPL-3.0-or-later
"""Measure the backend with a large generated library.

Builds N games with real cover files in a temporary data folder, starts the
local cover server on 127.0.0.1:8183, and times the game list, cover serving
and card copies. The real wLib data is never read or changed. See the "Library
scale benchmarks" section in docs/build.md.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from http.client import HTTPResponse
from pathlib import Path
from typing import TYPE_CHECKING, cast
from urllib.error import URLError
from urllib.request import urlopen

if TYPE_CHECKING:
    from core.api import Api

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVER_PORT = 8183

ENGINES = ("Ren'Py", "Unity", "RPGM", "HTML", "Unreal Engine", "Wolf RPG", "QSP", "Others")
STATUSES = (
    "Not Started", "Plan to Play", "Playing", "Waiting For Update",
    "On Hold", "Completed", "Abandoned",
)
WORDS = (
    "Summer", "Night", "Academy", "Secret", "Lust", "Island", "Dream", "Shadow",
    "Crimson", "Village", "Office", "Legacy", "Hearts", "Echoes", "Witch",
    "Kingdom", "Station", "Desire", "Lost", "Frontier", "Harem", "Campus",
    "Neon", "Tower", "Fallen", "Pact", "Garden", "Mansion", "Rogue", "Signal",
)
TAG_POOL = tuple(
    f"{a} {b}".strip()
    for a in ("", "male", "female", "3d", "2d", "big")
    for b in (
        "protagonist", "romance", "sandbox", "corruption", "harem", "humor",
        "animated", "fantasy", "sci-fi", "voyeurism", "milf", "teasing",
        "vaginal sex", "oral sex", "groping", "handjob", "dating sim", "rpg",
        "school setting", "incest", "graphic violence", "management", "mobile game",
        "point & click", "multiple endings",
    )
)
# Static covers: width x height, in the mix that F95 and DLsite covers use.
STATIC_SIZES = ((600, 338), (1280, 720), (1920, 1080))
ANIMATED_SIZE = (800, 450)
ANIMATED_FRAMES = 8
BASE_VARIANTS = 6
WARM_RUNS = 10
COVER_THREADS = 6


# --- Generated library -------------------------------------------------------


def make_game_specs(count: int, seed: int) -> list[dict[str, object]]:
    """The same count and seed always give the same games."""
    rng = random.Random(seed)
    now = datetime(2026, 11, 1, 12, tzinfo=UTC)
    specs: list[dict[str, object]] = []
    for index in range(count):
        version = f"0.{rng.randint(1, 30)}.{rng.randint(0, 9)}"
        has_update = rng.random() < 0.15
        played = rng.random() < 0.6
        title = " ".join(rng.sample(WORDS, rng.randint(2, 3)))
        specs.append({
            "title": f"{title} {index:04d}",
            "exe_path": f"/games/bench/{index:04d}/Game.exe",
            "f95_url": f"https://f95zone.to/threads/bench-{index}.{200000 + index}/",
            "version": version,
            "latest_version": f"0.{rng.randint(31, 40)}" if has_update else version,
            "engine": rng.choice(ENGINES),
            "developer": f"Studio {rng.randint(1, 150):03d}",
            "play_status": rng.choice(STATUSES),
            "tags": sorted(rng.sample(TAG_POOL, rng.randint(8, 20))),
            "rating": f"{rng.uniform(2.5, 5.0):.1f}",
            "is_favorite": rng.random() < 0.05,
            "overview": " ".join(rng.choices(WORDS, k=rng.randint(80, 160))).lower(),
            "screenshots": [
                f"https://attachments.f95zone.to/2026/0{rng.randint(1, 9)}/{200000 + index}_{shot}.jpg"
                for shot in range(rng.randint(4, 10))
            ],
            "playtime_seconds": rng.randint(600, 200_000) if played else 0,
            "last_played": (now - timedelta(hours=rng.randint(1, 8000))).isoformat()
            if played else None,
        })
    return specs


# --- Cover images --------------------------------------------------------------


def _noise_rgb(rng: random.Random, width: int, height: int) -> bytes:
    """Soft colored noise: a small random image, later scaled up smoothly."""
    return rng.randbytes(width * height * 3)


def make_static_covers(seed: int) -> list[tuple[str, bytes]]:
    """A few base JPEGs per size; ("WxH", bytes) pairs."""
    from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, Qt
    from PyQt6.QtGui import QImage

    rng = random.Random(seed + 1)
    out: list[tuple[str, bytes]] = []
    for width, height in STATIC_SIZES:
        for _ in range(BASE_VARIANTS):
            small_w, small_h = max(8, width // 12), max(8, height // 12)
            data = _noise_rgb(rng, small_w, small_h)
            small = QImage(data, small_w, small_h, small_w * 3, QImage.Format.Format_RGB888).copy()
            image = small.scaled(
                width, height,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            # The buffer writes into `encoded`, which must outlive it.
            encoded = QByteArray()
            buffer = QBuffer(encoded)
            _ = buffer.open(QIODevice.OpenModeFlag.WriteOnly)
            if not image.save(buffer, "JPEG", 85):
                raise RuntimeError("Qt could not write a JPEG cover")
            buffer.close()
            out.append((f"{width}x{height}", bytes(encoded.data())))
    return out


def _gif_image_data(pixels: bytes) -> bytes:
    """LZW data for 7-bit pixels without compression.

    Every code is a literal of 8 bits, and a clear code comes before the code
    table would need 9-bit codes, so any GIF decoder reads it."""
    clear, end = b"\x80", b"\x81"
    step = 120  # 130 + 120 stays below 256, so codes stay 8 bits wide
    codes = b"".join(clear + pixels[i:i + step] for i in range(0, len(pixels), step)) + clear + end
    blocks = b"".join(
        bytes((len(codes[i:i + 255]),)) + codes[i:i + 255] for i in range(0, len(codes), 255)
    )
    return b"\x07" + blocks + b"\x00"


def make_animated_gif(rng: random.Random, width: int, height: int, frames: int) -> bytes:
    """An animated GIF with a moving gradient and noise, 128 colors, looping."""
    palette = b"".join(
        bytes((min(255, i * 2), (i * 5) % 256, 255 - min(255, i * 2))) for i in range(128)
    )
    header = (
        b"GIF89a"
        + width.to_bytes(2, "little") + height.to_bytes(2, "little")
        + b"\xf6\x00\x00" + palette  # global table of 128 colors
        + b"!\xff\x0bNETSCAPE2.0\x03\x01\x00\x00\x00"  # loop forever
    )
    parts = [header]
    noise = rng.randbytes(width * height)
    for frame in range(frames):
        rows: list[bytes] = []
        for y in range(height):
            base = (y * 96 // height + frame * 12) % 128
            table = bytes((base + (i & 15)) % 128 for i in range(256))
            rows.append(noise[y * width:(y + 1) * width].translate(table))
        parts.append(b"!\xf9\x04\x04\x08\x00\x00\x00")  # 80 ms per frame
        parts.append(
            b"," + b"\x00\x00\x00\x00"
            + width.to_bytes(2, "little") + height.to_bytes(2, "little") + b"\x00"
        )
        parts.append(_gif_image_data(b"".join(rows)))
    parts.append(b";")
    return b"".join(parts)


# --- Setup ---------------------------------------------------------------------


def isolate_environment(temp_root: Path) -> Path:
    home = temp_root / "home"
    home.mkdir(parents=True, exist_ok=True)
    data_dir = home / ".local" / "share" / "wLib"
    os.environ["HOME"] = str(home)
    os.environ["WLIB_DATA_DIR"] = str(data_dir)
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        _ = os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    return data_dir


def port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        # Like the server: a closed connection still waiting (TIME_WAIT) from
        # an earlier run must not count as "in use".
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def seed_library(
    specs: Sequence[dict[str, object]], animated_share: float, seed: int
) -> dict[str, object]:
    """Add the games and their covers; returns cover statistics."""
    from core import database
    from core.game_covers import covers_dir

    rng = random.Random(seed + 2)
    statics = make_static_covers(seed)
    animated = [
        make_animated_gif(rng, *ANIMATED_SIZE, ANIMATED_FRAMES) for _ in range(BASE_VARIANTS)
    ]
    folder = covers_dir()
    folder.mkdir(parents=True, exist_ok=True)
    sizes: list[int] = []
    animated_count = 0
    for spec in specs:
        game_id = database.add_game(
            title=str(spec["title"]),
            exe_path=str(spec["exe_path"]),
            f95_url=str(spec["f95_url"]),
            version=str(spec["version"]),
            tags=[str(tag) for tag in cast(list[object], spec["tags"])],
            rating=str(spec["rating"]),
            developer=str(spec["developer"]),
            engine=str(spec["engine"]),
        )
        if game_id is None:
            raise RuntimeError(f"could not add {spec['title']}")
        _ = database.update_game(game_id, {
            "latest_version": spec["latest_version"],
            "play_status": spec["play_status"],
            "is_favorite": spec["is_favorite"],
            "overview": spec["overview"],
        })
        if rng.random() < animated_share:
            data, extension = rng.choice(animated), "gif"
            animated_count += 1
        else:
            data, extension = rng.choice(statics)[1], "jpg"
        name = f"{rng.randbytes(16).hex()}.{extension}"
        _ = (folder / name).write_bytes(data)
        sizes.append(len(data))
        _ = database.set_game_local_cover(game_id, name)
    # These columns are not in the update_game allowlist; a seed script may
    # set them straight, as the app sets them during play and import.
    with closing(database.get_connection()) as conn:
        _ = conn.executemany(
            "UPDATE games SET playtime_seconds = ?, last_played = ?, screenshots = ? WHERE title = ?",
            [
                (spec["playtime_seconds"], spec["last_played"], json.dumps(spec["screenshots"]), spec["title"])
                for spec in specs
            ],
        )
        conn.commit()
    return {
        "animated_covers": animated_count,
        "static_covers": len(specs) - animated_count,
        "cover_bytes_total": sum(sizes),
        "cover_bytes_mean": round(statistics.mean(sizes)) if sizes else 0,
        "static_sizes": [f"{w}x{h}" for w, h in STATIC_SIZES],
        "animated_size": f"{ANIMATED_SIZE[0]}x{ANIMATED_SIZE[1]}x{ANIMATED_FRAMES}",
    }


# --- Measurements ---------------------------------------------------------------


def time_calls(call: Callable[[], object], warm_runs: int) -> dict[str, float]:
    start = time.perf_counter()
    _ = call()
    cold = (time.perf_counter() - start) * 1000
    warm: list[float] = []
    for _ in range(warm_runs):
        start = time.perf_counter()
        _ = call()
        warm.append((time.perf_counter() - start) * 1000)
    return {
        "cold_ms": round(cold, 1),
        "median_ms": round(statistics.median(warm), 1),
        "max_ms": round(max(warm), 1),
    }


def measure_game_list(api: Api) -> dict[str, object]:
    from core.database import get_all_games

    games = api.get_games()
    with_cover_url = sum(1 for game in games if game.get("cover_local_url"))
    animated = sum(1 for game in games if game.get("cover_animated"))
    return {
        "get_all_games": time_calls(get_all_games, WARM_RUNS),
        "get_games": time_calls(api.get_games, WARM_RUNS),
        "json_bytes": len(json.dumps(games)),
        "games_with_cover_url": with_cover_url,
        "games_marked_animated": animated,
    }


def _fetch(url: str) -> int:
    try:
        with closing(cast(HTTPResponse, urlopen(url, timeout=30))) as response:
            return len(response.read())
    except (URLError, OSError):
        return -1


def measure_cover_serving(api: Api) -> dict[str, object]:
    urls = [str(game["cover_local_url"]) for game in api.get_games() if game.get("cover_local_url")]
    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=COVER_THREADS) as pool:
        sizes = list(pool.map(_fetch, urls))
    seconds = time.perf_counter() - start
    return {
        "requests": len(urls),
        "failed": sum(1 for size in sizes if size < 0),
        "total_ms": round(seconds * 1000, 1),
        "requests_per_second": round(len(urls) / seconds, 1) if seconds else 0.0,
        "megabytes": round(sum(size for size in sizes if size > 0) / 1_000_000, 1),
        "threads": COVER_THREADS,
    }


def measure_card_copies() -> dict[str, object]:
    from core.cover_cards import make_card_copies, pending_sources, webp_writer_available

    if not webp_writer_available():
        return {"skipped": "Qt has no WebP writer"}
    sources = pending_sources()
    made = 0
    per_cover: list[float] = []
    start = time.perf_counter()
    for source in sources:
        one = time.perf_counter()
        still, _anim = make_card_copies(source)
        per_cover.append((time.perf_counter() - one) * 1000)
        made += int(still)
    total = (time.perf_counter() - start) * 1000
    return {
        "sources": len(sources),
        "still_copies_made": made,
        "total_ms": round(total, 1),
        "median_ms_per_cover": round(statistics.median(per_cover), 1) if per_cover else 0.0,
        "max_ms_per_cover": round(max(per_cover), 1) if per_cover else 0.0,
    }


# --- Output ------------------------------------------------------------------------


def git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT, capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def print_table(report: dict[str, object]) -> None:
    results = cast(dict[str, object], report["results"])
    print(f"\nwLib backend benchmark: {report['games']} games, seed {report['seed']}, commit {report['commit'] or '?'}")
    print("-" * 72)
    for section, values in results.items():
        print(f"{section}")
        if not isinstance(values, dict):
            print(f"  {values}")
            continue
        for key, value in cast(dict[str, object], values).items():
            if isinstance(value, dict):
                inner = ", ".join(f"{k} {v}" for k, v in cast(dict[str, object], value).items())
                print(f"  {key:<24} {inner}")
            else:
                print(f"  {key:<24} {value}")
    print("-" * 72)


@dataclass(frozen=True)
class Options:
    games: int
    seed: int
    animated_share: float
    json: Path | None
    keep: bool


def parse_args(argv: Sequence[str] | None) -> Options:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    _ = parser.add_argument("--games", type=int, default=1000, help="library size (default 1000)")
    _ = parser.add_argument("--seed", type=int, default=1, help="random seed (default 1)")
    _ = parser.add_argument(
        "--animated-share", type=float, default=0.1,
        help="share of games with an animated GIF cover (default 0.1)",
    )
    _ = parser.add_argument("--json", type=Path, help="also write the results to this JSON file")
    _ = parser.add_argument(
        "--keep", action="store_true",
        help="keep the temporary library and print the command that opens it in wLib",
    )
    namespace = parser.parse_args(argv)
    args = Options(
        games=cast(int, namespace.games),
        seed=cast(int, namespace.seed),
        animated_share=cast(float, namespace.animated_share),
        json=cast(Path | None, namespace.json),
        keep=cast(bool, namespace.keep),
    )
    if args.games < 1:
        parser.error("--games must be 1 or more")
    if not 0 <= args.animated_share <= 1:
        parser.error("--animated-share must be between 0 and 1")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if not port_is_free(SERVER_PORT):
        print(
            f"Port {SERVER_PORT} is in use. Close wLib (or any other program on that port) "
            + "and run the benchmark again: cover URLs only work on that port.",
            file=sys.stderr,
        )
        return 2

    temp_root = Path(tempfile.mkdtemp(prefix="wlib-bench-"))
    server = None
    keep = args.keep
    try:
        data_dir = isolate_environment(temp_root)

        import main as wlib_main

        _ = wlib_main.configure_qt_runtime_environment()
        from PyQt6.QtGui import QGuiApplication

        _app = QGuiApplication.instance() or QGuiApplication([sys.argv[0]])

        from core import database
        from core.extension_server import bind_extension_server

        database.init_db()
        # The catalog check downloads data in the background; it would add
        # noise and is not part of the library list.
        database.update_setting("catalog_enabled", "0")

        print(f"Building {args.games} games in {data_dir} ...")
        start = time.perf_counter()
        specs = make_game_specs(args.games, args.seed)
        cover_stats = seed_library(specs, args.animated_share, args.seed)
        seed_seconds = time.perf_counter() - start

        server = bind_extension_server()
        if server is None:
            print(f"Could not bind 127.0.0.1:{SERVER_PORT}.", file=sys.stderr)
            return 2
        threading.Thread(target=server.serve_forever, daemon=True).start()

        from core.api import Api

        api = Api()
        api.library_sync.server_available = True

        print("Measuring ...")
        results: dict[str, object] = {
            "game_list": measure_game_list(api),
            "cover_serving": measure_cover_serving(api),
            "card_copies": measure_card_copies(),
            # After card copies exist, the list also checks for them.
            "game_list_with_card_copies": measure_game_list(api),
        }
        report: dict[str, object] = {
            "tool": "backend",
            "games": args.games,
            "seed": args.seed,
            "date": datetime.now(UTC).isoformat(timespec="seconds"),
            "commit": git_commit(),
            "covers": {**cover_stats, "animated_share": args.animated_share},
            "seed_seconds": round(seed_seconds, 1),
            "results": results,
        }
        print_table(report)
        if args.json is not None:
            _ = args.json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            print(f"Results written to {args.json}")
        if keep:
            home = temp_root / "home"
            # Without DEV_MODE the app loads the production build in ui/dist;
            # Vue dev mode is several times slower and would distort timings.
            print("\nThe library was kept. Build the UI, then open the library in wLib:")
            print("  (cd ui && npm run build)")
            print(f"  HOME={home} WLIB_DATA_DIR={data_dir} .venv/bin/python main.py")
            print(f"Delete it afterwards with: rm -rf {temp_root}")
        return 0
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
        if not keep:
            shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
