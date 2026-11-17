# SPDX-License-Identifier: GPL-3.0-or-later
import io
import logging
import os
import sys
import threading
import traceback

import pytest

from core import app_log


@pytest.fixture(autouse=True)
def restore_logging(monkeypatch):
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    for obj, name in (
        (sys, "stdout"),
        (sys, "stderr"),
        (sys, "excepthook"),
        (threading, "excepthook"),
        (logging, "raiseExceptions"),
    ):
        monkeypatch.setattr(obj, name, getattr(obj, name))
    yield
    for handler in root.handlers[:]:
        if handler not in handlers:
            root.removeHandler(handler)
            handler.close()
    root.setLevel(level)


def test_output_partial_lines_logging_and_hooks(tmp_path, monkeypatch):
    terminal = io.StringIO()
    monkeypatch.setattr(sys, "stdout", terminal)
    chained = []
    monkeypatch.setattr(sys, "excepthook", lambda *args: chained.append("main"))
    monkeypatch.setattr(threading, "excepthook", lambda args: chained.append("thread"))
    path = app_log.setup_app_log(str(tmp_path))
    print("status", end="")
    assert "status" not in (tmp_path / "logs/wlib.log").read_text()
    print(" complete")
    print("stderr entry", file=sys.stderr)
    logging.warning("logging entry")
    try:
        raise ValueError("traceback entry")
    except ValueError:
        traceback.print_exc()
        sys.excepthook(*sys.exc_info())

    def crash():
        raise RuntimeError("worker failure")

    thread = threading.Thread(target=crash)
    thread.start()
    thread.join()
    assert path == str(tmp_path / "logs/wlib.log")
    text = (tmp_path / "logs/wlib.log").read_text()
    for expected in (
        "Starting wLib",
        "status complete",
        "stderr entry",
        "logging entry",
        "ValueError",
        "worker failure",
        "Source",
    ):
        assert expected in text
    assert terminal.getvalue() == "status complete\n"
    assert chained == ["main", "thread"]
    assert app_log.setup_app_log(str(tmp_path)) == path


def test_no_console_and_flush(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    app_log.setup_app_log(str(tmp_path))
    print("no console", end="")
    sys.stdout.flush()
    assert not sys.stdout.isatty()
    assert sys.stdout.encoding == "utf-8"
    with pytest.raises(io.UnsupportedOperation):
        sys.stdout.fileno()
    assert "no console" in (tmp_path / "logs/wlib.log").read_text()


def test_worker_traceback_is_logged_once(tmp_path, monkeypatch):
    terminal = io.StringIO()
    monkeypatch.setattr(sys, "stderr", terminal)
    monkeypatch.setattr(threading, "excepthook", threading.__excepthook__)
    app_log.setup_app_log(str(tmp_path))

    def crash():
        raise ZeroDivisionError("worker crash")

    thread = threading.Thread(target=crash)
    thread.start()
    thread.join()
    print("after", file=sys.stderr)
    text = (tmp_path / "logs/wlib.log").read_text()
    assert text.count("ZeroDivisionError: worker crash") == 1
    assert "worker crash" in terminal.getvalue()
    assert "wlib.stderr: after" in text


def test_rotation(tmp_path, monkeypatch):
    monkeypatch.setattr(app_log, "MAX_BYTES", 200)
    app_log.setup_app_log(str(tmp_path))
    for index in range(30):
        print(f"line {index} " + "x" * 60)
    assert sorted(p.name for p in (tmp_path / "logs").iterdir()) == [
        "wlib.log",
        "wlib.log.1",
        "wlib.log.2",
        "wlib.log.3",
    ]


@pytest.mark.skipif(
    os.name == "nt",
    reason="Windows cannot rename an open file; the helper (Steam runner) is Linux-only",
)
def test_helper_log_never_rotates_and_follows_the_apps_rotation(tmp_path, monkeypatch):
    monkeypatch.setattr(app_log, "MAX_BYTES", 200)
    app_log.setup_app_log(str(tmp_path), rotate=False)
    for index in range(30):
        print(f"helper {index} " + "x" * 60)
    logs = tmp_path / "logs"
    assert [p.name for p in logs.iterdir()] == ["wlib.log"]
    # The main app rotates the shared file; the helper's next line must land
    # in the new wlib.log, not in the renamed backup.
    os.rename(logs / "wlib.log", logs / "wlib.log.1")
    print("after rotation")
    assert (logs / "wlib.log").read_text().strip().endswith("after rotation")
    assert "after rotation" not in (logs / "wlib.log.1").read_text()


def test_unwritable_folder_installs_nothing(tmp_path, monkeypatch, capsys):
    original = (sys.stdout, sys.stderr, sys.excepthook, threading.excepthook)
    handlers = logging.getLogger().handlers[:]

    def denied(*args, **kwargs):
        raise PermissionError("read-only folder")

    monkeypatch.setattr(app_log.os, "makedirs", denied)
    assert app_log.setup_app_log(str(tmp_path)) is None
    assert (sys.stdout, sys.stderr, sys.excepthook, threading.excepthook) == original
    assert logging.getLogger().handlers == handlers
    assert capsys.readouterr().err.count("App log unavailable") == 1


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            '"magnet:?xt=urn:btih:abc&tr=https://tracker.rpdl.net/secret-passkey" failed',
            '"magnet:<removed>" failed',
        ),
        (
            "Download https://example.com/file?token=abc failed",
            "Download https://example.com/file failed",
        ),
        ("See http://example.com/help#secret", "See http://example.com/help"),
        (
            "ws://localhost/a?token=1 wss://localhost/b#key FILE:///tmp/a?key=2#f",
            "ws://localhost/a wss://localhost/b FILE:///tmp/a",
        ),
        ("file:/tmp/a?token=abc", "file:/tmp/a"),
        (
            "at f (http://localhost:5173/src/App.vue?t=17:42:7)\n@http://h/a.js?v=1#x:3:9",
            "at f (http://localhost:5173/src/App.vue:42:7)\n@http://h/a.js:3:9",
        ),
        ("(see https://example.com/a?token=abc)", "(see https://example.com/a)"),
        ("https://example.com/a?token=abc)def", "https://example.com/a"),
        ("no URL here #section", "no URL here #section"),
    ],
)
def test_redact_ui_text(text, expected):
    assert app_log.redact_ui_text(text) == expected


def test_format_ui_error_caps_each_field():
    marker = "… [cut]"
    size = 9000
    formatted = app_log.format_ui_error(
        {
            field: char * size
            for field, char in zip(
                ("source", "where", "route", "count", "dropped", "message", "stack"),
                "swrcdmt",
                strict=True,
            )
        },
        "1.2.3",
    )

    def cut(char, limit):
        return char * (limit - len(marker)) + marker

    assert formatted.splitlines() == [
        f"UI error ({cut('s', 500)}) in {cut('w', 500)} on {cut('r', 500)} "
        f"[x{cut('c', 500)}]: {cut('m', 2000)}",
        "App version: 1.2.3",
        f"UI error reports dropped: {cut('d', 500)}",
        cut("t", 8000),
    ]


def test_format_ui_error_redacts_links_crossing_the_cut():
    prefix = "a" * 1970
    message = prefix + " magnet:?xt=urn:btih:abc&tr=https://rpdl.net/secret"
    formatted = app_log.format_ui_error({"message": message}, "1.2.3")
    assert formatted.splitlines()[0].endswith(prefix + " magnet:<removed>")


def test_ui_error_limiter_uses_a_sliding_window():
    now = 0.0
    limiter = app_log.UiErrorLimiter(lambda: now)
    assert limiter.allow()
    now = 20.0
    assert all(limiter.allow() for _ in range(19))
    assert not limiter.allow()
    now = 59.999
    assert not limiter.allow()
    now = 60.0
    assert limiter.allow()
    assert not limiter.allow()
    now = 80.001
    assert all(limiter.allow() for _ in range(19))


@pytest.fixture
def ui_log_api(monkeypatch):
    from core.api import Api
    from core.api_mixins import settings

    monkeypatch.setattr(settings, "_ui_error_limiter", app_log.UiErrorLimiter())
    return Api.__new__(Api)


def test_log_ui_error_records_context(ui_log_api, caplog):
    from core.app_version import APP_VERSION

    assert ui_log_api.log_ui_error(
        {
            "source": "vue",
            "message": "render failed",
            "stack": "Error\n  at render",
            "where": "GameCard / render function",
            "route": "/library",
            "count": 3,
            "dropped": 2,
        }
    ) == {"success": True}
    [record] = caplog.records
    assert (record.name, record.levelno) == ("wlib.ui", logging.ERROR)
    assert record.getMessage().splitlines() == [
        "UI error (vue) in GameCard / render function on /library [x3]: render failed",
        f"App version: {APP_VERSION}",
        "UI error reports dropped: 2",
        "Error",
        "  at render",
    ]


@pytest.mark.parametrize("report", [{}, None, "unexpected"])
def test_log_ui_error_accepts_missing_fields(ui_log_api, caplog, report):
    assert ui_log_api.log_ui_error(report) == {"success": True}
    assert (
        caplog.records[0]
        .getMessage()
        .startswith("UI error (unknown) in unknown on / [x1]:")
    )


def test_log_ui_error_redacts_every_field(ui_log_api, caplog):
    assert ui_log_api.log_ui_error(
        {
            "source": "promise",
            "message": 42,
            "where": "https://example.com/x?token=abc",
            "stack": "Error: magnet:?xt=urn:btih:abc&tr=https://rpdl.net/secret",
        }
    ) == {"success": True}
    message = caplog.records[0].getMessage()
    assert "Error: magnet:<removed>" in message
    assert "secret" not in message
    assert "https://example.com/x on / [x1]: 42" in message
    assert "token" not in message


def test_log_ui_error_swallows_logging_failure(ui_log_api, monkeypatch):
    def fail(*args):
        raise OSError("log unavailable")

    monkeypatch.setattr(logging.getLogger("wlib.ui"), "error", fail)
    assert ui_log_api.log_ui_error({}) == {"success": True}
