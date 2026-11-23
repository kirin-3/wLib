# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

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


def test_resolves_paths_with_wrong_letter_case(game: Path) -> None:
    (game / "Img").mkdir()
    (game / "Img" / "Hero.PNG").write_bytes(b"png")
    expected = os.path.realpath(game / "Img" / "Hero.PNG")
    for path in ("/img/hero.png", "/IMG/HERO.PNG", "/Img/hero.png", "/img//./Hero.PNG"):
        resolved = web_player.resolve_game_file(str(game), path)
        assert resolved == (expected, "image/png"), path
    assert web_player.resolve_game_file(str(game), "/INDEX.HTML") == (
        os.path.realpath(game / "index.html"),
        "text/html",
    )
    assert web_player.resolve_game_file(str(game), "/img/missing.png") is None
    assert web_player.resolve_game_file(str(game), "/SUB") is None


def test_exact_name_wins_over_case_variant(game: Path) -> None:
    (game / "a.js").write_text("lower")
    (game / "A.js").write_text("upper")
    if (game / "a.js").read_text() != "lower":
        pytest.skip("file system ignores letter case")
    assert web_player.resolve_game_file(str(game), "/A.js") == (
        os.path.realpath(game / "A.js"),
        "text/javascript",
    )
    assert web_player.resolve_game_file(str(game), "/a.js") == (
        os.path.realpath(game / "a.js"),
        "text/javascript",
    )
    # Neither exact: the first name in sorted order wins.
    resolved = web_player.resolve_game_file(str(game), "/a.JS")
    assert resolved is not None and resolved[0] == os.path.realpath(game / "A.js")


def test_case_lookup_sees_new_files(game: Path) -> None:
    assert web_player.resolve_game_file(str(game), "/sub/NEW.js") is None
    (game / "sub" / "new.js").write_text("1")
    os.utime(game / "sub", ns=(0, os.stat(game / "sub").st_mtime_ns + 1_000_000_000))
    assert web_player.resolve_game_file(str(game), "/sub/NEW.js") is not None


def test_case_lookup_tries_the_next_folder_variant(game: Path) -> None:
    (game / "data").mkdir()
    (game / "data" / "a.txt").write_text("a")
    if (game / "Data").exists():
        pytest.skip("file system ignores letter case")
    (game / "Data").mkdir()
    (game / "Data" / "b.txt").write_text("b")
    resolved = web_player.resolve_game_file(str(game), "/data/B.TXT")
    assert resolved is not None and resolved[0] == os.path.realpath(
        game / "Data" / "b.txt"
    )
    resolved = web_player.resolve_game_file(str(game), "/DATA/A.TXT")
    assert resolved is not None and resolved[0] == os.path.realpath(
        game / "data" / "a.txt"
    )


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "",
        "/sub/",
        "/./",
        "/img/",
        "/STRASSE.png",
        "/C:/index.html",
        "/D:/index.html",
        "/c:index.html",
    ],
)
def test_case_lookup_misses(game: Path, path: str) -> None:
    (game / "Straße.png").write_bytes(b"png")
    assert web_player.resolve_game_file(str(game), path) is None


def test_case_lookup_is_bounded(game: Path) -> None:
    for depth in (60, 70):
        folder = game.joinpath(*[f"d{depth}"] * depth)
        folder.mkdir(parents=True)
        (folder / "f.txt").write_text("1")
    # Exact paths have no depth limit; the letter-case search stops after 64 parts.
    assert (
        web_player.resolve_game_file(str(game), "/" + "d70/" * 70 + "f.txt") is not None
    )
    if not (
        game / "D70"
    ).exists():  # a file system that ignores letter case finds it on the exact path
        assert (
            web_player.resolve_game_file(str(game), "/" + "D70/" * 70 + "f.txt") is None
        )
    assert (
        web_player.resolve_game_file(str(game), "/" + "D60/" * 60 + "F.TXT") is not None
    )


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks")
def test_case_lookup_stops_on_symlink_loops(game: Path) -> None:
    (game / "x").symlink_to(".")
    (game / "X").symlink_to(".")
    assert (
        web_player.resolve_game_file(str(game), "/" + "x/" * 40 + "missing.txt") is None
    )
    assert (
        web_player.resolve_game_file(str(game), "/" + "x/" * 40 + "INDEX.html")
        is not None
    )


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks")
def test_case_lookup_rejects_symlink_escape(game: Path, tmp_path: Path) -> None:
    (game / "DirLink").symlink_to(tmp_path)
    assert web_player.resolve_game_file(str(game), "/dirlink/SECRET.txt") is None
    assert web_player.resolve_game_file(str(game), "/SUB/../../secret.txt") is None


@pytest.mark.parametrize(
    "path",
    [
        "/../secret.txt",
        "/sub/../../secret.txt",
        "/%2e%2e/secret.txt",
        "/sub/..%2f..%2fsecret.txt",
        "/missing.html",
        "/sub",
        "/a\x00b",
    ],
)
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
        [
            "--root",
            str(game),
            "--entry",
            "index.html",
            "--profile",
            str(tmp_path / "p"),
            "--title",
            "T",
            "--offline",
            "--links",
            "ignore",
        ]
    )
    assert args.entry == "index.html" and args.offline and not args.flash
    assert args.links == "ignore" and args.title == "T"
    assert not args.devtools and not args.sandboxed
    args = web_player.parse_args(
        [
            "--root",
            str(game),
            "--entry",
            "index.html",
            "--profile",
            str(tmp_path / "p"),
            "--devtools",
            "--sandboxed",
        ]
    )
    assert args.devtools and args.sandboxed and args.links == "open"


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
    base = [
        "--root",
        str(game),
        "--entry",
        "index.html",
        "--profile",
        str(tmp_path / "p"),
    ]
    argv = extra if extra in ([], ["--root"]) else [*base, *extra]
    with pytest.raises(web_player.UsageError):
        web_player.parse_args(argv)


def test_parse_args_missing_entry(game: Path, tmp_path: Path) -> None:
    with pytest.raises(web_player.UsageError):
        web_player.parse_args(
            ["--root", str(game), "--entry", "nope.html", "--profile", str(tmp_path)]
        )


def test_main_dispatch_usage_error() -> None:
    result = subprocess.run(
        [sys.executable, str(REPO / "main.py"), "--play-web", "--root"],
        capture_output=True,
        text=True,
        timeout=60,
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
def test_classify_navigation(
    url: str, links: str, offline: bool, expected: str
) -> None:
    assert web_player.classify_navigation(url, links, offline) == expected  # type: ignore[arg-type]


def test_flash_page_and_bootstrap() -> None:
    page = web_player.flash_page("a b/<g>.swf").decode()
    assert "wlib-game://game/a%20b/%3Cg%3E.swf" in page and "<g>" not in page.split(
        "<script"
    )[0].replace("&lt;g&gt;", "")
    assert "wlib-game://ruffle/" in web_player.ruffle_bootstrap_source()


def test_host_open_env_drops_bundled_libs() -> None:
    env = web_player.host_open_env(
        {
            "APPDIR": "/a",
            "LD_LIBRARY_PATH": "/a/lib",
            "LD_LIBRARY_PATH_ORIG": "/o",
            "QT_PLUGIN_PATH": "/a/usr/plugins",
            "QT_QPA_PLATFORM": "xcb",
            "QTWEBENGINE_DISABLE_SANDBOX": "1",
            "WLIB_OWNED_ENV": "QT_QPA_PLATFORM,QTWEBENGINE_DISABLE_SANDBOX",
            "HOME": "/h",
        }
    )
    assert env == {"LD_LIBRARY_PATH": "/o", "HOME": "/h"}


def test_host_open_env_keeps_user_qt_platform() -> None:
    env = web_player.host_open_env({"QT_QPA_PLATFORM": "xcb", "HOME": "/h"})
    assert env == {"QT_QPA_PLATFORM": "xcb", "HOME": "/h"}


def test_open_url_on_host_rejects_other_schemes() -> None:
    with patch("subprocess.Popen") as popen:
        assert web_player.open_url_on_host("file:///x") is False
    popen.assert_not_called()


def test_open_folder_on_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPDIR", "/bundle")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/host/lib")
    with (
        patch("core.web_player.is_windows", return_value=False),
        patch("shutil.which", side_effect=[None, "/usr/bin/gio"]),
        patch("subprocess.Popen") as popen,
    ):
        popen.return_value.poll.return_value = 0
        assert web_player.open_folder_on_host(str(tmp_path))
    assert popen.call_args.args[0] == ["/usr/bin/gio", "open", str(tmp_path)]
    assert popen.call_args.kwargs["env"]["LD_LIBRARY_PATH"] == "/host/lib"
    assert "APPDIR" not in popen.call_args.kwargs["env"]


def test_open_folder_on_host_reports_failing_openers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import core.host_platform as host_platform

    monkeypatch.setattr(host_platform, "OPENER_START_WAIT_SECONDS", 0.05)
    with (
        patch("core.web_player.is_windows", return_value=False),
        patch("shutil.which", return_value="/usr/bin/opener"),
        patch("subprocess.Popen") as popen,
    ):
        popen.return_value.poll.return_value = 4
        assert web_player.open_folder_on_host(str(tmp_path)) is False
    assert popen.call_count == 5


def test_open_folder_on_windows(tmp_path: Path) -> None:
    with (
        patch("core.web_player.is_windows", return_value=True),
        patch(
            "core.host_platform.open_windows_system_target", return_value=(True, "")
        ) as opener,
    ):
        assert web_player.open_folder_on_host(str(tmp_path))
    opener.assert_called_once_with(str(tmp_path))


@pytest.mark.parametrize(
    ("suggested", "expected"),
    [
        ("save.save", "save.save"),
        (r"C:\\games\\save.save", "save.save"),
        ("../../evil.sh", "evil.sh"),
        ("sa\x00ve\n\x7f.save", "save.save"),
        (".save", "save"),
        ("...", "download"),
        ("", "download"),
        ("..", "download"),
    ],
)
def test_safe_export_name(suggested: str, expected: str) -> None:
    assert web_player.safe_export_name(suggested) == expected


@pytest.mark.parametrize(
    ("name", "stem", "extension"),
    [("save.save", "save", ".save"), ("save", "save", "")],
)
def test_unique_export_path(
    tmp_path: Path, name: str, stem: str, extension: str
) -> None:
    folder = str(tmp_path)
    first_copy = f"{stem} (1){extension}"
    (tmp_path / name).touch()
    assert web_player.unique_export_path(folder, name, set()) == str(
        tmp_path / first_copy
    )
    assert web_player.unique_export_path(folder, name, {first_copy}) == str(
        tmp_path / f"{stem} (2){extension}"
    )


def test_file_picker_filter_uses_extensions_only() -> None:
    assert (
        web_player.file_picker_filter([None, "image/*", ".save", ".json"])
        == "Accepted files (*.save *.json);;All files (*)"
    )
    assert web_player.file_picker_filter(["text/plain"]) == "All files (*)"


def test_literal_percent_names_decode_once(game: Path, tmp_path: Path) -> None:
    from urllib.parse import quote

    (game / "a%20b.html").write_text("x")
    # A URL path is decoded once: %2520 is the file "a%20b.html".
    assert (
        web_player.resolve_game_file(str(game), "/" + quote("a%20b.html")) is not None
    )
    args = web_player.parse_args(
        ["--root", str(game), "--entry", "a%20b.html", "--profile", str(tmp_path / "p")]
    )
    assert args.entry == "a%20b.html"


def test_open_in_background_reports_failure_from_worker_thread() -> None:
    import threading

    failures: list[tuple[str, bool]] = []

    def failing_opener(target: str) -> bool:
        return False

    thread = web_player.open_in_background(
        failing_opener,
        "/exports",
        lambda target: failures.append(
            (target, threading.current_thread() is threading.main_thread())
        ),
    )
    thread.join(timeout=2)
    assert thread.daemon is True
    assert failures == [("/exports", False)]

    ok = web_player.open_in_background(
        lambda _target: True, "/exports", failures.append
    )
    ok.join(timeout=2)
    assert len(failures) == 1


def test_gui_thread_relay_runs_callback_on_gui_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import threading
    import time

    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtCore import QCoreApplication
    from PyQt6.QtGui import QGuiApplication

    app = QCoreApplication.instance() or QGuiApplication([])
    assert app is not None
    received: list[tuple[str, bool]] = []
    relay = web_player.gui_thread_relay(
        lambda text: received.append(
            (text, threading.current_thread() is threading.main_thread())
        )
    )

    thread = web_player.open_in_background(lambda _target: False, "/exports", relay)
    thread.join(timeout=2)
    assert received == []  # Queued: nothing runs until the GUI loop does.
    deadline = time.monotonic() + 2
    while not received and time.monotonic() < deadline:
        app.processEvents()
    assert received == [("/exports", True)]
