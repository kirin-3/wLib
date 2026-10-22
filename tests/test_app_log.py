# SPDX-License-Identifier: GPL-3.0-or-later
import io
import logging
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
