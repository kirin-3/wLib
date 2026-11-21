# SPDX-License-Identifier: GPL-3.0-or-later
"""RPGMaker Linux runner detection, engine rules, and pinned installers."""

from __future__ import annotations

import hashlib
import importlib
import logging
import os
import platform
import shutil
import ssl
import stat
import subprocess
import tarfile
import tempfile
import urllib.request
import zipfile
from collections.abc import Callable
from contextlib import suppress
from http.client import HTTPResponse
from pathlib import PurePosixPath
from typing import Literal, TypedDict, cast

from core import game_sandbox
from core.host_platform import (
    RUNNER_INSTALL_CONFIG_REL as RUNNER_INSTALL_CONFIG_REL,
    get_data_subdir,
    host_command,
    host_which,
    is_linux,
    mkxpz_dir as mkxpz_dir,
    runner_install_config_path as runner_install_config_path,
    runner_install_dir as runner_install_dir,
)

_log = logging.getLogger("wlib.rpgmaker_runner")


RunnerEngine = Literal["mv", "mz", "mkxpz", ""]
AutoRuntimeKind = Literal["rpgmaker_linux", "default"]

RPGMAKER_LINUX_REPO_URL = "https://github.com/bakustarver/rpgmakermlinux-cicpoffs"
RUNNER_VERSION = "1.1.9"
RUNNER_TARBALL_URL_X86_64 = (
    "https://github.com/bakustarver/rpgmakermlinux-cicpoffs/releases/download/"
    + f"v{RUNNER_VERSION}/rpgmakerlinux-x86_64-v{RUNNER_VERSION}.tar.gz"
)
RUNNER_TARBALL_SHA256_X86_64 = (
    "d926a07e0c0f70bebe8671e7b0ac952a949ecb022e66992e2ccd64d372ecaf12"
)
MKXPZ_ZIP_URL_X86_64 = (
    "https://github.com/bakustarver/rpgmakermlinux-cicpoffs/releases/download/"
    + "libraries/mkxp-z.x86_64.zip"
)
MKXPZ_ZIP_SHA256_X86_64 = (
    "1791b5f6006a5df208112e4ced21fb473c5c8fdf95c65fd5d8d38fcafbc0fb04"
)
_DOWNLOAD_CHUNK_SIZE = 256 * 1024
_MAX_DOWNLOAD_BYTES = 1024 * 1024 * 1024  # 1 GiB upper bound


class MkxpzStatus(TypedDict):
    installed: bool
    path: str


class RunnerInstallability(TypedDict):
    supported: bool
    can_install: bool
    missing_tools: list[str]
    reason: str


class AutoRuntime(TypedDict):
    runtime: AutoRuntimeKind
    engine: RunnerEngine
    reason: str


ProgressCallback = Callable[[str, int, int], None]


def _noop_progress(_stage: str, _done: int, _total: int) -> None:
    return None


def mkxpz_status(
    *,
    install_dir: str | None = None,
    machine: str | None = None,
    home: str | None = None,
) -> MkxpzStatus:
    """Whether `<install_dir>/mkxp-z/mkxp-z.<machine>` exists and is executable."""
    arch = (platform.machine() if machine is None else machine).strip()
    if not arch:
        return {"installed": False, "path": ""}
    candidate = os.path.join(mkxpz_dir(install_dir, home=home), f"mkxp-z.{arch}")
    if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
        return {"installed": True, "path": candidate}
    return {"installed": False, "path": ""}


def normalize_runner_game_dir(game_dir: str) -> str:
    """Normalize `game_dir`, moving up from `www/` when `../package.json` exists."""
    if not game_dir:
        return ""
    resolved = os.path.abspath(os.path.expanduser(game_dir))
    if os.path.basename(resolved).lower() == "www":
        parent_dir = os.path.dirname(resolved)
        if os.path.isfile(os.path.join(parent_dir, "package.json")):
            return parent_dir
    return resolved


def runner_engine(game_dir: str) -> RunnerEngine:
    """Detect the RPG Maker engine using the upstream runner's `checkgamefilesfd` rules."""
    resolved = normalize_runner_game_dir(game_dir)
    if not resolved or not os.path.isdir(resolved):
        return ""
    if (
        os.path.isdir(os.path.join(resolved, "www"))
        and os.path.exists(os.path.join(resolved, "package.json"))
        and os.path.exists(os.path.join(resolved, "www", "js", "plugins.js"))
    ):
        return "mv"
    if (
        os.path.isdir(os.path.join(resolved, "data"))
        and os.path.exists(os.path.join(resolved, "package.json"))
        and os.path.exists(os.path.join(resolved, "js", "plugins.js"))
    ):
        return "mz"
    if os.path.exists(os.path.join(resolved, "Game.ini")) and os.path.exists(
        os.path.join(resolved, "Game.exe")
    ):
        return "mkxpz"
    return ""


def _get_launcher_runner_status() -> dict[str, object]:
    launcher_mod = importlib.import_module("core.launcher")
    launcher_cls = cast(type[object], getattr(launcher_mod, "Launcher"))
    launcher = launcher_cls()
    getter = cast(
        Callable[[], dict[str, object]],
        getattr(launcher, "get_rpgmaker_linux_runner_status"),
    )
    return dict(getter())


def _clean_host_env(env: dict[str, str]) -> dict[str, str]:
    launcher_mod = importlib.import_module("core.launcher")
    cleaner = cast(
        Callable[[dict[str, str]], dict[str, str]],
        getattr(launcher_mod, "without_packaged_runtime_env"),
    )
    return cleaner(env)


def _default_runner_available() -> bool:
    return bool(_get_launcher_runner_status().get("available"))


def resolve_auto_runtime(
    exe_path: str,
    *,
    auto_inject_ce: bool = False,
    custom_prefix: str = "",
    proton_version: str = "",
    sandboxed: bool = False,
    runner_available: bool | Callable[[], bool] | None = None,
    bwrap: str | None = None,
) -> AutoRuntime:
    """Decide whether Auto Detect launches `exe_path` via RPGMaker Linux or the default path."""
    if not is_linux():
        return {"runtime": "default", "engine": "", "reason": "non_linux"}

    cleaned_exe = (exe_path or "").strip()
    if not cleaned_exe:
        return {"runtime": "default", "engine": "", "reason": "not_exe"}

    expanded_exe = os.path.expanduser(cleaned_exe)
    if os.path.splitext(expanded_exe)[1].lower() != ".exe":
        return {"runtime": "default", "engine": "", "reason": "not_exe"}

    if (
        auto_inject_ce
        or bool((custom_prefix or "").strip())
        or bool((proton_version or "").strip())
    ):
        return {"runtime": "default", "engine": "", "reason": "wine_settings"}

    game_dir = os.path.dirname(os.path.abspath(expanded_exe))
    engine = runner_engine(game_dir)
    if not engine:
        return {"runtime": "default", "engine": "", "reason": "not_rpgmaker"}

    if runner_available is None:
        has_runner = _default_runner_available()
    elif callable(runner_available):
        has_runner = bool(runner_available())
    else:
        has_runner = bool(runner_available)

    if not has_runner:
        return {"runtime": "default", "engine": engine, "reason": "runner_missing"}

    if engine == "mkxpz":
        if not mkxpz_status()["installed"]:
            return {"runtime": "default", "engine": engine, "reason": "mkxpz_missing"}
        if sandboxed:
            if bwrap is None:
                sandbox_status = game_sandbox.get_sandbox_status()
                bwrap_path = (
                    sandbox_status["path"]
                    if sandbox_status["status"] == "ready"
                    else ""
                )
            else:
                bwrap_path = bwrap
            if not game_sandbox.runner_overlay_supported(bwrap_path, mkxpz_dir()):
                return {
                    "runtime": "default",
                    "engine": engine,
                    "reason": "mkxpz_sandbox_overlay_unsupported",
                }

    return {"runtime": "rpgmaker_linux", "engine": engine, "reason": ""}


def runner_installability(
    *, machine: str | None = None, check_tools: bool = True
) -> RunnerInstallability:
    """Whether this host supports the pinned runner/mkxp-z installers and has required tools.

    `check_tools=False` skips the host `bash`/`file` lookups (a host shell per
    lookup in Flatpak); only the runner installer needs them.
    """
    if not is_linux():
        return {
            "supported": False,
            "can_install": False,
            "missing_tools": [],
            "reason": "RPGMaker Linux runner is only available on Linux.",
        }
    arch = (platform.machine() if machine is None else machine).strip()
    if arch != "x86_64":
        return {
            "supported": False,
            "can_install": False,
            "missing_tools": [],
            "reason": f"Automatic installation is only available on x86_64 Linux (found {arch or 'unknown'}).",
        }
    missing = (
        [tool for tool in ("bash", "file") if not host_which(tool)]
        if check_tools
        else []
    )
    if missing:
        joined = ", ".join(missing)
        return {
            "supported": True,
            "can_install": False,
            "missing_tools": missing,
            "reason": f"Missing required host command: {joined}.",
        }
    return {
        "supported": True,
        "can_install": True,
        "missing_tools": [],
        "reason": "",
    }


def _download_ssl_context() -> ssl.SSLContext:
    cafile = (os.environ.get("SSL_CERT_FILE") or "").strip()
    if cafile and not os.path.isfile(cafile):
        cafile = ""
    return ssl.create_default_context(cafile=cafile or None)


def _download_verified(
    url: str,
    expected_sha256: str,
    *,
    dest_dir: str,
    prefix: str,
    stage: str,
    progress: ProgressCallback | None = None,
) -> str:
    """Stream `url` to a temporary file in `dest_dir` and verify its SHA-256 before returning."""
    os.makedirs(dest_dir, exist_ok=True)
    archive_path = ""
    notify: ProgressCallback = progress or _noop_progress
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "wLib"})
        context = _download_ssl_context()
        digest = hashlib.sha256()
        with tempfile.NamedTemporaryFile(
            prefix=prefix, dir=dest_dir, delete=False
        ) as output:
            archive_path = output.name
            notify(stage, 0, 0)
            with cast(
                HTTPResponse,
                urllib.request.urlopen(request, context=context, timeout=60),
            ) as response:
                total = int(response.headers.get("Content-Length", "0") or 0)
                done = 0
                while chunk := response.read(_DOWNLOAD_CHUNK_SIZE):
                    done += len(chunk)
                    if done > _MAX_DOWNLOAD_BYTES:
                        raise ValueError("Downloaded archive exceeded the size limit.")
                    _ = output.write(chunk)
                    digest.update(chunk)
                    notify(stage, done, total)
        notify("Verifying SHA-256 checksum", 0, 0)
        actual = digest.hexdigest().lower()
        if actual != expected_sha256.lower():
            raise ValueError(
                "Downloaded archive failed SHA-256 hash mismatch check "
                + f"(expected {expected_sha256[:16]}…, got {actual[:16]}…). "
                + f"Install manually from {RPGMAKER_LINUX_REPO_URL}."
            )
        return archive_path
    except Exception:
        if archive_path:
            with suppress(OSError):
                os.remove(archive_path)
        raise


def _find_extracted_install_script(scratch_dir: str) -> str:
    real_scratch = os.path.realpath(scratch_dir)
    candidates = [os.path.join(scratch_dir, "install.sh")]
    try:
        for entry in sorted(os.listdir(scratch_dir)):
            child = os.path.join(scratch_dir, entry)
            if os.path.isdir(child) and not os.path.islink(child):
                candidates.append(os.path.join(child, "install.sh"))
    except OSError:
        pass
    for candidate in candidates:
        if os.path.isfile(candidate) and not os.path.islink(candidate):
            real_candidate = os.path.realpath(candidate)
            if real_candidate.startswith(real_scratch + os.sep):
                return real_candidate
    raise RuntimeError("Runner archive does not contain install.sh.")


def install_runner(progress: ProgressCallback | None = None) -> dict[str, object]:
    """Download, verify, extract, and run the pinned upstream RPGMaker Linux runner installer."""
    notify: ProgressCallback = progress or _noop_progress
    if not is_linux():
        raise ValueError(
            "RPGMaker Linux runner installation is only available on Linux."
        )
    arch = platform.machine().strip()
    if arch != "x86_64":
        raise ValueError(
            f"Automatic runner installation is only supported on x86_64 Linux (found {arch or 'unknown'})."
        )
    if _default_runner_available():
        raise ValueError("RPGMaker Linux runner is already installed.")
    for tool in ("bash", "file"):
        if not host_which(tool):
            raise ValueError(
                f"Required host command '{tool}' was not found. Install '{tool}' and try again."
            )

    archive_path = ""
    scratch_dir = ""
    try:
        archive_path = _download_verified(
            RUNNER_TARBALL_URL_X86_64,
            RUNNER_TARBALL_SHA256_X86_64,
            dest_dir=get_data_subdir("downloads"),
            prefix="rpgm-runner-",
            stage=f"Downloading RPGMaker Linux runner v{RUNNER_VERSION}",
            progress=notify,
        )
        scratch_parent = get_data_subdir("install-scratch")
        os.makedirs(scratch_parent, exist_ok=True)
        scratch_dir = tempfile.mkdtemp(prefix="rpgm-runner-", dir=scratch_parent)
        notify("Extracting runner archive", 0, 0)
        with tarfile.open(archive_path, "r:*") as archive:
            archive.extractall(path=scratch_dir, filter="data")

        install_script = _find_extracted_install_script(scratch_dir)
        notify("Running upstream installer", 0, 0)
        clean_env = _clean_host_env(dict(os.environ))
        command = host_command(["bash", install_script], env=clean_env)
        _log.info("Running RPGMaker Linux runner installer: %s", command)
        result = subprocess.run(
            command,
            env=clean_env,
            capture_output=True,
            text=True,
            errors="replace",
            stdin=subprocess.DEVNULL,
            timeout=300,
            check=False,
        )
        if result.stdout:
            _log.info("RPGMaker runner install.sh stdout:\n%s", result.stdout.strip())
        if result.stderr:
            _log.info("RPGMaker runner install.sh stderr:\n%s", result.stderr.strip())
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip().splitlines()
            tail = detail[-1] if detail else f"exit code {result.returncode}"
            raise RuntimeError(
                f"Upstream install.sh failed (exit code {result.returncode}): {tail}"
            )

        notify("Checking runner status", 0, 0)
        status = _get_launcher_runner_status()
        if not status.get("available"):
            raise RuntimeError(
                str(status.get("error") or "")
                or "RPGMaker Linux runner was not detected after running install.sh."
            )
        return status
    finally:
        if archive_path:
            with suppress(OSError):
                os.remove(archive_path)
        if scratch_dir:
            shutil.rmtree(scratch_dir, ignore_errors=True)


def _safe_zip_member_path(dest_root: str, member_name: str) -> str:
    if not member_name or "\x00" in member_name or "\\" in member_name:
        raise ValueError(f"Unsafe path in mkxp-z archive: {member_name!r}")
    pure = PurePosixPath(member_name)
    if pure.is_absolute() or ".." in pure.parts:
        raise ValueError(f"Unsafe path in mkxp-z archive: {member_name!r}")
    rel_parts = [part for part in pure.parts if part not in ("", ".")]
    if not rel_parts:
        return dest_root
    target = os.path.join(dest_root, *rel_parts)
    real_root = os.path.realpath(dest_root)
    real_target = os.path.realpath(os.path.abspath(target))
    if real_target != real_root and not real_target.startswith(real_root + os.sep):
        raise ValueError(f"Path escapes destination in mkxp-z archive: {member_name!r}")
    return target


def install_mkxpz(progress: ProgressCallback | None = None) -> MkxpzStatus:
    """Download, verify, unpack, and configure mkxp-z in the runner's install folder."""
    notify: ProgressCallback = progress or _noop_progress
    if not is_linux():
        raise ValueError("mkxp-z installation is only available on Linux.")
    arch = platform.machine().strip()
    if arch != "x86_64":
        raise ValueError(
            f"Automatic mkxp-z installation is only supported on x86_64 Linux (found {arch or 'unknown'})."
        )
    if not _default_runner_available():
        raise ValueError("Install the RPGMaker Linux runner before installing mkxp-z.")
    install_dir = runner_install_dir()
    target_dir = mkxpz_dir(install_dir)
    if mkxpz_status(install_dir=install_dir, machine=arch)["installed"]:
        raise ValueError("mkxp-z is already installed.")

    archive_path = ""
    partial_dir = ""
    backup_dir = ""
    try:
        archive_path = _download_verified(
            MKXPZ_ZIP_URL_X86_64,
            MKXPZ_ZIP_SHA256_X86_64,
            dest_dir=get_data_subdir("downloads"),
            prefix="rpgm-mkxpz-",
            stage="Downloading mkxp-z",
            progress=notify,
        )
        os.makedirs(install_dir, exist_ok=True)
        partial_dir = tempfile.mkdtemp(prefix="mkxp-z.partial-", dir=install_dir)
        notify("Unpacking mkxp-z", 0, 0)
        with zipfile.ZipFile(archive_path, "r") as zf:
            for info in zf.infolist():
                dest_path = _safe_zip_member_path(partial_dir, info.filename)
                unix_mode = info.external_attr >> 16
                if stat.S_ISLNK(unix_mode):
                    raise ValueError(
                        f"Unsafe symlink entry in mkxp-z archive: {info.filename!r}"
                    )
                if info.is_dir():
                    os.makedirs(dest_path, exist_ok=True)
                    continue
                os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                with zf.open(info, "r") as src, open(dest_path, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                perm = stat.S_IMODE(unix_mode)
                if perm:
                    os.chmod(dest_path, perm & 0o777)

        mkxp_json = os.path.join(partial_dir, "mkxp.json")
        if os.path.isfile(mkxp_json):
            notify("Configuring mkxp.json", 0, 0)
            with open(mkxp_json, encoding="utf-8") as fh:
                content = fh.read()
            updated = content.replace('"RGSS', f'"{target_dir}/RGSS').replace(
                '"Kawariki-patches', f'"{target_dir}/Kawariki-patches'
            )
            with open(mkxp_json, "w", encoding="utf-8") as fh:
                _ = fh.write(updated)

        if os.path.lexists(target_dir):
            backup_dir = f"{partial_dir}.old"
            os.rename(target_dir, backup_dir)
        os.rename(partial_dir, target_dir)
        partial_dir = ""
        if backup_dir:
            shutil.rmtree(backup_dir, ignore_errors=True)
            backup_dir = ""

        notify("Checking mkxp-z status", 0, 0)
        status = mkxpz_status(install_dir=install_dir, machine=arch)
        if not status["installed"]:
            raise RuntimeError(
                f"mkxp-z executable was not found at {os.path.join(target_dir, f'mkxp-z.{arch}')} after unpacking."
            )
        return status
    except Exception:
        if backup_dir and not os.path.lexists(target_dir):
            with suppress(OSError):
                os.rename(backup_dir, target_dir)
        raise
    finally:
        if archive_path:
            with suppress(OSError):
                os.remove(archive_path)
        if partial_dir:
            shutil.rmtree(partial_dir, ignore_errors=True)
