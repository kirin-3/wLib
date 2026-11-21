# SPDX-License-Identifier: GPL-3.0-or-later
import re


def normalize_version(version: object) -> str:
    """Mirror normalizeVersion() in the UI while preserving raw stored versions."""
    value = "".join(str(version or "").split()).lower()
    return re.sub(r"^v(?=\d)", "", value)


def actionable_remote_version(version: object) -> bool:
    return version is not None and str(version).strip().lower() not in (
        "",
        "unknown",
        "n/a",
        "na",
        "none",
        "null",
    )
