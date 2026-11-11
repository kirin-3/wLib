# SPDX-License-Identifier: GPL-3.0-or-later
import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "check_release_version", ROOT / "scripts" / "check-release-version.py"
)
assert _spec is not None and _spec.loader is not None
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)


def _repo(tmp_path: Path, app_version: str, manifest_version: str) -> Path:
    (tmp_path / "core").mkdir(exist_ok=True)
    (tmp_path / "extension").mkdir(exist_ok=True)
    (tmp_path / "core" / "app_version.py").write_text(f'APP_VERSION = "{app_version}"\n')
    (tmp_path / "extension" / "manifest.json").write_text(
        json.dumps({"version": manifest_version})
    )
    return tmp_path


def _git(root: Path, *args: str) -> None:
    _ = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_release_version_check(tmp_path):
    root = _repo(tmp_path, "1.0.0", "2.0.0")
    _git(root, "init", "-q")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "one")
    _git(root, "tag", "v1.0.0")
    assert check.problems("v1.0.0", root) == []

    # Next release forgot both bumps.
    _ = (root / "README").write_text("x")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "two")
    _git(root, "tag", "v1.0.1")
    found = check.problems("v1.0.1", root)
    assert len(found) == 2
    assert "APP_VERSION 1.0.0" in found[0]
    assert "2.0.0" in found[1] and "v1.0.0" in found[1]

    _ = _repo(root, "1.0.1", "2.0.1")
    assert check.problems("v1.0.1", root) == []


def _pins(path: Path) -> dict[str, str]:
    pins: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip().rstrip("\\").strip()
        if "==" in line and not line.startswith("-"):
            name, version = line.split("==", 1)
            pins[name.strip().lower().replace("_", "-")] = version.strip()
    return pins


@pytest.mark.parametrize(
    "lock",
    [
        "requirements.lock",
        "requirements-dev.lock",
        "requirements-windows.lock",
        "requirements-dev-windows.lock",
    ],
)
def test_lock_files_keep_the_exact_pins(lock):
    # Bumping a pin in requirements.txt without regenerating the locks (see
    # docs/build.md) would build releases with the old version.
    wanted = _pins(ROOT / "requirements.txt")
    locked = _pins(ROOT / lock)
    assert wanted, "requirements.txt has no exact pins"
    for name, version in wanted.items():
        assert locked.get(name) == version, f"{lock}: {name} {locked.get(name)} != {version}"
    assert all("--hash=" in text for text in [(ROOT / lock).read_text(encoding="utf-8")])
