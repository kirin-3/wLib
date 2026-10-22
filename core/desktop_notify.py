# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import html
import json
import logging
import shutil
import subprocess
import sys
import threading
from collections.abc import Sequence

from core.host_platform import host_command, host_which

logger = logging.getLogger(__name__)


def build_update_summary(
    updated_titles: Sequence[str], completed_titles: Sequence[str]
) -> tuple[str, str] | None:
    counts: list[str] = []
    if updated_titles:
        counts.append(
            f"{len(updated_titles)} game update{'s' if len(updated_titles) != 1 else ''}"
        )
    if completed_titles:
        counts.append(
            f"{len(completed_titles)} wishlist game{'s' if len(completed_titles) != 1 else ''} completed"
        )
    if not counts:
        return None
    titles = [*updated_titles, *completed_titles]
    body = ", ".join(titles[:2])
    if len(titles) > 2:
        body += f" and {len(titles) - 2} more"
    return ", ".join(counts), body


def _send(title: str, body: str) -> None:
    try:
        notify_send = host_which("notify-send")
        if notify_send:
            try:
                result = subprocess.run(
                    host_command(
                        [notify_send, "--app-name=wLib", "--", title, html.escape(body)]
                    ),
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    timeout=5,
                    check=False,
                )
                if result.returncode == 0:
                    return
            except (OSError, subprocess.TimeoutExpired):
                logger.debug("notify-send failed", exc_info=True)
        # Prefer the runtime's gdbus: Flatpak grants direct session-bus access.
        gdbus = shutil.which("gdbus")
        host_gdbus = False
        if not gdbus:
            gdbus = host_which("gdbus")
            host_gdbus = True
        if not gdbus:
            return
        command = [
            gdbus,
            "call",
            "--session",
            "--dest",
            "org.freedesktop.Notifications",
            "--object-path",
            "/org/freedesktop/Notifications",
            "--method",
            "org.freedesktop.Notifications.Notify",
            "--",
            "wLib",
            "0",
            "",
            json.dumps(title, ensure_ascii=False),
            json.dumps(html.escape(body), ensure_ascii=False),
            "[]",
            "{}",
            "-1",
        ]
        result = subprocess.run(
            host_command(command) if host_gdbus else command,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=5,
            check=False,
        )
        if result.returncode != 0:
            logger.debug("Desktop notification service unavailable")
    except Exception:
        logger.debug("Desktop notification failed", exc_info=True)


def send_notification(title: str, body: str) -> None:
    if not sys.platform.startswith("linux"):
        return
    try:
        threading.Thread(target=_send, args=(title, body), daemon=True).start()
    except Exception:
        logger.debug("Could not start desktop notification sender", exc_info=True)
