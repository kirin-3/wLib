#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Fail a release whose tag or extension version is wrong.

Usage: check-release-version.py <tag>

- The tag must be `v` + APP_VERSION from core/api.py, so the app reports the
  version it was released as.
- extension/manifest.json must have a version the previous release tag did
  not use: AMO accepts each extension version only once, so a reused version
  fails at signing after the build has already run.

Run from the repository root with the tags fetched (fetch-depth: 0).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_APP_VERSION_RE = re.compile(r'^APP_VERSION = "([^"]+)"$', re.MULTILINE)


def app_version(root: Path = ROOT) -> str:
    match = _APP_VERSION_RE.search((root / "core" / "api.py").read_text(encoding="utf-8"))
    if match is None:
        raise SystemExit("APP_VERSION was not found in core/api.py.")
    return match.group(1)


def manifest_version(text: str) -> str:
    return str(json.loads(text).get("version") or "")


def previous_tag(tag: str, root: Path = ROOT) -> str | None:
    """The newest `v*` tag before `tag` in version order, if any."""
    result = subprocess.run(
        ["git", "tag", "--list", "v*", "--sort=-v:refname"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    tags = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    older = tags[tags.index(tag) + 1 :] if tag in tags else tags
    return older[0] if older else None


def problems(tag: str, root: Path = ROOT) -> list[str]:
    found: list[str] = []
    version = app_version(root)
    if tag != f"v{version}":
        found.append(
            f"Tag {tag} does not match APP_VERSION {version} in core/api.py; "
            + f"tag v{version} or update APP_VERSION."
        )
    current = manifest_version(
        (root / "extension" / "manifest.json").read_text(encoding="utf-8")
    )
    previous = previous_tag(tag, root)
    if previous is not None:
        shown = subprocess.run(
            ["git", "show", f"{previous}:extension/manifest.json"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        if shown.returncode == 0 and manifest_version(shown.stdout) == current:
            found.append(
                f"extension/manifest.json still has version {current}, which "
                + f"{previous} already released; AMO signs each version once."
            )
    return found


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    found = problems(argv[1])
    for problem in found:
        print(f"::error::{problem}", file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
