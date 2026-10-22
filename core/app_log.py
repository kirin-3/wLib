# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import io
import logging
from logging.handlers import RotatingFileHandler
import os
import platform
import sys
import threading
from typing import TextIO, final, cast

MAX_BYTES = 2 * 1024 * 1024
BACKUP_COUNT = 3

# Set while a chained exception hook prints a traceback that was already logged.
_muted = threading.local()


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


def setup_app_log(data_dir: str) -> str | None:
    path = os.path.join(data_dir, "logs", "wlib.log")
    root = logging.getLogger()
    # Repeated initialization in one process must not stack tees or handlers.
    for existing in root.handlers:
        if isinstance(
            existing, RotatingFileHandler
        ) and existing.baseFilename == os.path.abspath(path):
            return path
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        handler = RotatingFileHandler(
            path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
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
    from core.api import APP_VERSION
    from core.diagnostics import packaging_label

    logger.info(
        "Starting wLib %s | %s | %s",
        APP_VERSION,
        platform.platform(),
        packaging_label(),
    )
    return path
