# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from core import web_player

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def game(tmp_path: Path) -> Path:
    root = tmp_path / "game"
    (root / "sub").mkdir(parents=True)
    (root / "index.html").write_text("<html></html>")
    (root / "sub" / "a.wasm").write_bytes(b"\0asm")
    (root / "sub" / "my file.js").write_text("1")
    (tmp_path / "secret.txt").write_text("x")
    return root


def test_resolves_file_and_content_types(game: Path) -> None:
    assert web_player.resolve_game_file(str(game), "/index.html") == (
        os.path.realpath(game / "index.html"),
        "text/html",
    )
    resolved = web_player.resolve_game_file(str(game), "/sub/a.wasm")
    assert resolved is not None and resolved[1] == "application/wasm"
    assert web_player.resolve_game_file(str(game), "/sub/my%20file.js") is not None


@pytest.mark.parametrize("path", ["/../secret.txt", "/sub/../../secret.txt", "/%2e%2e/secret.txt", "/sub/..%2f..%2fsecret.txt", "/missing.html", "/sub", "/a\x00b"])
def test_rejects_traversal_and_missing(game: Path, path: str) -> None:
    assert web_player.resolve_game_file(str(game), path) is None


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks")
def test_rejects_symlink_escape(game: Path, tmp_path: Path) -> None:
    (game / "link.txt").symlink_to(tmp_path / "secret.txt")
    (game / "dirlink").symlink_to(tmp_path)
    assert web_player.resolve_game_file(str(game), "/link.txt") is None
    assert web_player.resolve_game_file(str(game), "/dirlink/secret.txt") is None


def test_parse_args_good(game: Path, tmp_path: Path) -> None:
    args = web_player.parse_args(
        ["--root", str(game), "--entry", "index.html", "--profile", str(tmp_path / "p"), "--title", "T", "--offline", "--links", "ignore"]
    )
    assert args.entry == "index.html" and args.offline and not args.flash
    assert args.links == "ignore" and args.title == "T"
    assert not args.devtools
    args = web_player.parse_args(
        ["--root", str(game), "--entry", "index.html", "--profile", str(tmp_path / "p"), "--devtools"]
    )
    assert args.devtools and args.links == "open"


@pytest.mark.parametrize(
    "extra",
    [
        [],
        ["--root"],
        ["--bogus"],
        ["--links", "maybe"],
    ],
)
def test_parse_args_bad(game: Path, tmp_path: Path, extra: list[str]) -> None:
    base = ["--root", str(game), "--entry", "index.html", "--profile", str(tmp_path / "p")]
    argv = extra if extra in ([], ["--root"]) else [*base, *extra]
    with pytest.raises(web_player.UsageError):
        web_player.parse_args(argv)


def test_parse_args_missing_entry(game: Path, tmp_path: Path) -> None:
    with pytest.raises(web_player.UsageError):
        web_player.parse_args(["--root", str(game), "--entry", "nope.html", "--profile", str(tmp_path)])


def test_main_dispatch_usage_error() -> None:
    result = subprocess.run(
        [sys.executable, str(REPO / "main.py"), "--play-web", "--root"],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 2 and "Usage: wlib --play-web" in result.stderr


@pytest.mark.parametrize(
    ("url", "links", "offline", "expected"),
    [
        ("wlib-game://game/index.html", "open", False, "allow"),
        ("wlib-game://ruffle/ruffle.js", "ignore", True, "allow"),
        ("about:blank", "ignore", False, "allow"),
        ("https://example.com/", "open", False, "open"),
        ("http://example.com/", "ignore", False, "drop"),
        ("https://example.com/", "open", True, "drop"),
        ("file:///etc/passwd", "open", False, "drop"),
        ("javascript:alert(1)", "open", False, "drop"),
        ("ftp://example.com/x", "open", False, "drop"),
    ],
)
def test_classify_navigation(url: str, links: str, offline: bool, expected: str) -> None:
    assert web_player.classify_navigation(url, links, offline) == expected  # type: ignore[arg-type]


def test_flash_page_and_bootstrap() -> None:
    page = web_player.flash_page("a b/<g>.swf").decode()
    assert "wlib-game://game/a%20b/%3Cg%3E.swf" in page and "<g>" not in page.split("<script")[0].replace("&lt;g&gt;", "")
    assert "wlib-game://ruffle/" in web_player.ruffle_bootstrap_source()


def test_host_open_env_drops_bundled_libs() -> None:
    env = web_player.host_open_env({"APPDIR": "/a", "LD_LIBRARY_PATH": "/a/lib", "LD_LIBRARY_PATH_ORIG": "/o", "QT_QPA_PLATFORM": "xcb", "HOME": "/h"})
    assert env == {"LD_LIBRARY_PATH": "/o", "HOME": "/h"}


def test_open_url_on_host_rejects_other_schemes() -> None:
    assert web_player.open_url_on_host("file:///etc/passwd") is False
