# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash")


def test_source_launcher_venv_is_gitignored():
    ignored = (REPOSITORY_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()

    assert "venv/" in ignored


def test_source_launcher_installs_frontend_from_the_lockfile():
    script = (REPOSITORY_ROOT / "wlib.sh").read_text(encoding="utf-8")

    assert "npm ci" in script
    assert "npm install" not in script


@pytest.mark.skipif(sys.platform == "win32" or BASH is None, reason="bash launcher")
@pytest.mark.parametrize(
    ("data_dir_value", "expected_base"),
    [
        ("~/wlib-data", "home"),
        ("relative-wlib-data", "app"),
    ],
)
def test_packaged_launcher_resolves_data_dir_like_python(
    tmp_path, data_dir_value, expected_base
):
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    shutil.copy2(REPOSITORY_ROOT / "packaging" / "wlib-launcher", app_dir / "wlib")
    fake_binary = app_dir / "wlib-bin"
    fake_binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_binary.chmod(0o755)

    cwd = tmp_path / "cwd"
    cwd.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(home),
        "WLIB_DATA_DIR": data_dir_value,
        "WLIB_LAUNCHER_NAME": "test",
    }

    result = subprocess.run(
        [str(app_dir / "wlib")],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    base = home if expected_base == "home" else app_dir
    relative = data_dir_value.removeprefix("~/")
    assert (base / relative / "wlib-launch.log").is_file()
    assert not (cwd / "~").exists()
    assert not (cwd / "relative-wlib-data").exists()


@pytest.mark.skipif(sys.platform == "win32" or BASH is None, reason="bash launcher")
def test_wlib_sh_rejects_python_older_than_312(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    shutil.copy2(REPOSITORY_ROOT / "wlib.sh", project / "wlib.sh")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in ("dirname", "mkdir"):
        executable = shutil.which(tool)
        assert executable is not None
        (bin_dir / tool).symlink_to(executable)

    # This is what an unsupported interpreter looks like to the version probe.
    fake_python = bin_dir / "python3"
    fake_python.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    fake_python.chmod(0o755)

    env = {
        "PATH": str(bin_dir),
        "HOME": str(tmp_path / "home"),
    }
    result = subprocess.run(
        [BASH, str(project / "wlib.sh")],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "3.12" in output
    assert "Using Python" not in output
    assert not (project / "venv").exists()
    assert not (project / ".venv").exists()
