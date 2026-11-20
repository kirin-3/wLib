# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from collections import deque
from collections.abc import Callable
import io
import logging
from logging.handlers import RotatingFileHandler, WatchedFileHandler
import os
import platform
import re
import sys
import threading
import time
from typing import TextIO, final, cast

from core.app_version import APP_VERSION

MAX_BYTES = 2 * 1024 * 1024
BACKUP_COUNT = 3
# A stack frame ends a URL with ":line:column" and maybe ")" after the query
# (Vite dev URLs such as "App.vue?t=1:42:7)"). Keep that suffix; drop the query.
_UI_URL = re.compile(
    r"""magnet:\?[^\s"'<>]*"""
    + r"""|((?:(?:https?|wss?)://|file:/+)[^\s"'<>?#]*)"""
    + r"""(?:[?#][^\s"'<>]*?(?=(?::\d+:\d+)?\)?(?:[\s"'<>]|$)))?""",
    re.IGNORECASE,
)

# Set while a chained exception hook prints a traceback that was already logged.
_muted = threading.local()


def redact_ui_text(text: str) -> str:
    return _UI_URL.sub(lambda match: match[1] or "magnet:<removed>", text)


def _ui_log_field(value: object, limit: int, *, multiline: bool = False) -> str:
    text = redact_ui_text(str(value))
    if not multiline:
        text = " ".join(text.splitlines())
    marker = "… [cut]"
    if len(text) > limit:
        return text[: limit - len(marker)] + marker
    return text


def format_ui_error(report: object, version: str) -> str:
    fields = cast(dict[str, object], report) if isinstance(report, dict) else {}
    source = _ui_log_field(fields.get("source", "unknown"), 500)
    where = _ui_log_field(fields.get("where", "unknown"), 500)
    route = _ui_log_field(fields.get("route", "/"), 500)
    count = _ui_log_field(fields.get("count", 1), 500)
    dropped = _ui_log_field(fields.get("dropped", 0), 500)
    message = _ui_log_field(fields.get("message", ""), 2000)
    stack = _ui_log_field(fields.get("stack", ""), 8000, multiline=True)
    lines = [
        f"UI error ({source}) in {where} on {route} [x{count}]: {message}",
        f"App version: {version}",
    ]
    if dropped != "0":
        lines.append(f"UI error reports dropped: {dropped}")
    if stack:
        lines.append(stack)
    return "\n".join(lines)


@final
class UiErrorLimiter:
    def __init__(self, now: Callable[[], float] = time.monotonic) -> None:
        self._now = now
        self._entries: deque[float] = deque()
        self._lock = threading.Lock()

    def allow(self) -> bool:
        with self._lock:
            now = self._now()
            while self._entries and self._entries[0] <= now - 60:
                _ = self._entries.popleft()
            if len(self._entries) >= 20:
                return False
            self._entries.append(now)
            return True


@final
class RepeatLimiter:
    """Lets one log line per key through every ``window`` seconds.

    For problems that repeat on every retry, such as an extension that the
    server refuses every few seconds: the first one is logged, the repeats
    are counted and reported with the next line that gets through."""

    def __init__(
        self,
        window: float = 300.0,
        now: Callable[[], float] = time.monotonic,
        max_keys: int = 256,
    ) -> None:
        self._window = window
        self._now = now
        self._max_keys = max_keys
        self._seen: dict[str, tuple[float, int]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> int | None:
        """``None`` to skip this line; else how many repeats were skipped before it."""
        with self._lock:
            now = self._now()
            last = self._seen.get(key)
            if last is not None and now - last[0] < self._window:
                self._seen[key] = (last[0], last[1] + 1)
                return None
            if last is None and len(self._seen) >= self._max_keys:
                oldest = min(self._seen, key=lambda item: self._seen[item][0])
                del self._seen[oldest]
            self._seen[key] = (now, 0)
            return last[1] if last is not None else 0

    def log(
        self, logger: logging.Logger, level: int, key: str, message: str, *args: object
    ) -> None:
        skipped = self.allow(key)
        if skipped is None:
            return
        if skipped:
            message += " (%d more like this since the last report)"
            args = (*args, skipped)
        logger.log(level, message, *args)


@final
class OutputTee:
    def __init__(self, stream: TextIO | None, name: str, level: int) -> None:
        self.stream = stream
        self.logger = logging.getLogger(name)
        self.level = level
        self.pending = ""
        self.lock = threading.RLock()

    @property
    def encoding(self) -> str:
        return getattr(self.stream, "encoding", None) or "utf-8"

    def isatty(self) -> bool:
        return self.stream.isatty() if self.stream is not None else False

    def fileno(self) -> int:
        if self.stream is None:
            raise io.UnsupportedOperation("No console stream")
        return self.stream.fileno()

    def write(self, text: str) -> int:
        with self.lock:
            if self.stream is not None:
                _ = self.stream.write(text)
            if getattr(_muted, "active", False):
                return len(text)
            self.pending += text
            while "\n" in self.pending:
                line, self.pending = self.pending.split("\n", 1)
                self.logger.log(self.level, line.rstrip("\r"))
        return len(text)

    def flush(self) -> None:
        with self.lock:
            if self.stream is not None:
                self.stream.flush()
            if self.pending:
                self.logger.log(self.level, self.pending)
                self.pending = ""

    def __getattr__(self, name: str) -> object:
        if self.stream is None:
            raise AttributeError(name)
        return cast(object, getattr(self.stream, name))


def setup_app_log(data_dir: str, *, rotate: bool = True) -> str | None:
    """Send logging, stdout and stderr to ``logs/wlib.log``.

    Only the main app rotates the file. A helper process that shares it (the
    Steam launch runner) passes ``rotate=False``: two processes rotating one
    file rename each other's backups. Its handler reopens ``wlib.log`` after
    the app rotates it, so its lines keep landing in the current file.
    """
    path = os.path.join(data_dir, "logs", "wlib.log")
    root = logging.getLogger()
    # Repeated initialization in one process must not stack tees or handlers.
    for existing in root.handlers:
        if isinstance(
            existing, logging.FileHandler
        ) and existing.baseFilename == os.path.abspath(path):
            return path
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        handler: logging.FileHandler = (
            RotatingFileHandler(
                path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
            )
            if rotate
            else WatchedFileHandler(path, encoding="utf-8")
        )
    except OSError as exc:
        print(f"[wLib] App log unavailable: {exc}", file=sys.stderr or sys.__stderr__)
        return None
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    logging.raiseExceptions = False
    sys.stdout = OutputTee(sys.stdout, "wlib.stdout", logging.INFO)
    sys.stderr = OutputTee(sys.stderr, "wlib.stderr", logging.WARNING)
    previous_main = sys.excepthook
    previous_thread = threading.excepthook
    logger = logging.getLogger("wlib")

    def main_exception(
        exc_type: type[BaseException], exc: BaseException, tb: object
    ) -> None:
        from types import TracebackType
        from typing import cast

        logger.error(
            "Uncaught exception",
            exc_info=(exc_type, exc, cast(TracebackType | None, tb)),
        )
        _muted.active = True
        try:
            previous_main(exc_type, exc, cast(TracebackType | None, tb))
        finally:
            _muted.active = False

    def thread_exception(args: threading.ExceptHookArgs) -> None:
        if args.exc_value is not None:
            logger.error(
                "Uncaught worker exception",
                exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
            )
        _muted.active = True
        try:
            _ = previous_thread(args)
        finally:
            _muted.active = False

    sys.excepthook = main_exception
    threading.excepthook = thread_exception
    from core.diagnostics import packaging_label

    logger.info(
        "Starting wLib %s | %s | %s",
        APP_VERSION,
        platform.platform(),
        packaging_label(),
    )
    return path
