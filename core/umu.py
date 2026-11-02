# SPDX-License-Identifier: GPL-3.0-or-later
"""Discovery and preparation of the host's umu-launcher."""

from __future__ import annotations

import os
import hashlib
import glob
import json
import re
import signal
import ssl
import subprocess
import tarfile
import tempfile
import threading
import time
import urllib.request
from collections.abc import Callable
from http.client import HTTPResponse
from pathlib import PurePosixPath
from typing import TypedDict, cast, final

from core.host_platform import get_data_dir, host_command, host_which, proton_compat_data_path


class UmuStatus(TypedDict):
    source: str
    path: str
    version: str
    python3: bool


def locate_umu() -> tuple[str, str]:
    """Prefer the host package over the copy downloaded by wLib."""
    system = host_which("umu-run")
    if system:
        return system, "system"
    downloaded = os.path.join(get_data_dir(), "umu", "umu-run")
    if os.path.isfile(downloaded) and os.access(downloaded, os.X_OK):
        return downloaded, "wlib"
    return "", ""


def get_umu_status() -> UmuStatus:
    path, source = locate_umu()
    version = ""
    if path:
        try:
            result = subprocess.run(
                host_command([path, "--version"]),
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
                stdin=subprocess.DEVNULL,
            )
            if result.returncode == 0:
                version = (result.stdout or result.stderr).strip().splitlines()[0]
        except (OSError, subprocess.TimeoutExpired, IndexError):
            pass
    return {
        "path": path,
        "source": source,
        "version": version,
        "python3": bool(host_which("python3")),
    }


def umu_data_dir() -> str:
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(os.path.expanduser(base), "umu")


def required_runtime(runner: str) -> str:
    if not runner or runner in ("GE-Proton", "UMU-Proton"):
        return "steamrt4"
    folder = runner if os.path.isdir(runner) else os.path.dirname(runner)
    try:
        with open(
            os.path.join(folder, "toolmanifest.vdf"), encoding="utf-8"
        ) as manifest:
            content = manifest.read()
    except OSError:
        return "steamrt4"
    match = re.search(r'"require_tool_appid"\s*"(\d+)"', content)
    if match and match.group(1) == "1628350":
        return "sniper"
    return "steamrt4"


def runtime_status(runner: str) -> dict[str, object]:
    runtime = required_runtime(runner)
    root = os.path.join(umu_data_dir(), runtime)
    # umu writes .installed.ok after a complete install; older releases did
    # not, so fall back to the entry point that only a finished install has.
    ready = os.path.isfile(os.path.join(root, ".installed.ok")) or (
        os.path.isfile(os.path.join(root, "_v2-entry-point"))
        and any(
            os.path.isdir(path)
            for path in glob.glob(os.path.join(root, runtime + "_platform_*"))
        )
    )
    return {"runtime": runtime, "path": root, "ready": ready}


def proton_path_value(runner: str) -> str:
    """PROTONPATH for a runner; empty when umu should use its UMU-Proton default."""
    if runner == "GE-Proton":
        return runner
    if not runner or runner == "UMU-Proton":
        return ""
    if "proton" not in os.path.basename(runner.rstrip(os.sep)).lower():
        return ""
    return runner if os.path.isdir(runner) else os.path.dirname(runner)


def resolve_runner(runner: str) -> str:
    return runner or ("wine" if host_which("wine") else "UMU-Proton")


def resolve_codename(runner: str) -> str:
    if runner not in ("GE-Proton", "UMU-Proton"):
        return runner
    home = os.path.expanduser("~")
    roots = [
        os.path.join(get_data_dir(), "proton"),
        os.path.join(umu_data_dir(), "compatibilitytools"),
        *(
            os.path.join(home, root, "compatibilitytools.d")
            for root in (
                ".steam/root",
                ".steam/steam",
                ".local/share/Steam",
                ".var/app/com.valvesoftware.Steam/data/Steam",
                ".var/app/com.valvesoftware.Steam/.local/share/Steam",
            )
        ),
    ]
    prefix = "ge-proton" if runner == "GE-Proton" else "umu-"
    candidates: list[str] = []
    for root in roots:
        for candidate in glob.glob(os.path.join(root, "*", "proton")):
            if os.path.isfile(candidate) and os.path.basename(
                os.path.dirname(candidate)
            ).lower().startswith(prefix):
                candidates.append(candidate)

    def version_key(path: str) -> list[tuple[int, object]]:
        return [
            (1, int(part)) if part.isdigit() else (0, part.casefold())
            for part in re.split(r"(\d+)", os.path.basename(os.path.dirname(path)))
        ]

    if not candidates:
        raise ValueError(
            f"No installed {runner} build was found. Prepare it in Settings first."
        )
    return os.path.dirname(max(candidates, key=version_key))


def _wrapper_path() -> str:
    source = os.path.join(os.path.dirname(__file__), "umu_launch_wrapper.py")
    folder = os.path.join(get_data_dir(), "umu")
    destination = os.path.join(folder, "umu-run-wlib")
    os.makedirs(folder, exist_ok=True)
    with open(source, "rb") as handle:
        content = handle.read()
    try:
        with open(destination, "rb") as handle:
            matches = handle.read() == content
    except FileNotFoundError:
        matches = False
    if not matches:
        with tempfile.NamedTemporaryFile(
            prefix=".umu-wrapper-", dir=folder, delete=False
        ) as handle:
            temporary = handle.name
            try:
                _ = handle.write(content)
                os.chmod(temporary, 0o755)
                os.replace(temporary, destination)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
    else:
        os.chmod(destination, 0o755)
    return destination


def build_umu_invocation(
    executable: str,
    *,
    prefix: str,
    runner: str,
    game_id: int | None = None,
    game_dir: str = "",
    environment: dict[str, str] | None = None,
    sandboxed: bool = False,
    log_dir: str = "",
) -> tuple[list[str], dict[str, str]]:
    umu, _ = locate_umu()
    if not umu:
        raise ValueError("umu-launcher is missing. Install it in Settings.")
    compat = proton_compat_data_path(prefix)
    pfx = os.path.join(compat, "pfx")
    if os.path.islink(pfx) and os.path.realpath(pfx) != os.path.realpath(compat):
        raise ValueError(
            f"The pfx link in {compat} points elsewhere and umu would replace it."
        )
    os.makedirs(compat, exist_ok=True)
    selected = (
        resolve_codename(runner)
        if sandboxed and runner in ("GE-Proton", "UMU-Proton")
        else runner
    )
    env = dict(os.environ if environment is None else environment)
    env.update(
        WINEPREFIX=compat,
        GAMEID=f"umu-wlib{game_id}" if game_id is not None else "umu-default",
        PROTON_VERB="waitforexitandrun",
        UMU_RUNTIME_UPDATE="0",
        UMU_HTTP_TIMEOUT="3",
        UMU_HTTP_RETRIES="0",
        WLIB_UMU_RUN=umu,
    )
    for key in ("STEAM_COMPAT_CLIENT_INSTALL_PATH", "UMU_ID", "STEAM_COMPAT_DATA_PATH"):
        _ = env.pop(key, None)
    # Without a game folder umu derives the install path from the executable;
    # a placeholder or verb such as "winetricks" would otherwise point it at
    # wLib's working directory.
    if game_dir:
        env["STEAM_COMPAT_INSTALL_PATH"] = game_dir
    else:
        _ = env.pop("STEAM_COMPAT_INSTALL_PATH", None)
    proton_path = proton_path_value(selected)
    if proton_path:
        env["PROTONPATH"] = proton_path
    else:
        _ = env.pop("PROTONPATH", None)
    # Only a library game's own container can be joined: installers and
    # launches without a game id share umu-default and must not attach to it.
    if sandboxed or game_id is None:
        _ = env.pop("UMU_CONTAINER_NSENTER", None)
    else:
        env["UMU_CONTAINER_NSENTER"] = "1"
    if log_dir:
        env.update(UMU_LOG="1", PROTON_LOG="1", PROTON_LOG_DIR=log_dir)
    return ["python3", _wrapper_path(), executable], env


_MAX_ARCHIVE = 16 * 1024 * 1024
_MAX_EXECUTABLE = 8 * 1024 * 1024
_RELEASE_URL = (
    "https://api.github.com/repos/Open-Wine-Components/umu-launcher/releases/latest"
)


def download_release(progress: Callable[[str, int, int], None]) -> str:
    """Verify the release archive before installing its single executable."""
    context = ssl.create_default_context()

    def request(url: str) -> HTTPResponse:
        return cast(
            HTTPResponse,
            urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": "wLib"}),
                context=context,
                timeout=30,
            ),
        )

    progress("Fetching release info", 0, 0)
    with request(_RELEASE_URL) as response:
        release = cast(object, json.load(response))
    if not isinstance(release, dict):
        raise ValueError("Invalid release metadata.")
    release_data = cast(dict[str, object], release)
    raw_assets = release_data.get("assets")
    if not isinstance(raw_assets, list):
        raise ValueError("Invalid release assets.")
    assets: list[dict[str, str]] = []
    for item in cast(list[object], raw_assets):
        if isinstance(item, dict):
            raw = cast(dict[str, object], item)
            assets.append({k: v for k, v in raw.items() if isinstance(v, str)})
    archive = next(
        (a for a in assets if a.get("name", "").endswith("-zipapp.tar")), None
    )
    if not archive:
        raise ValueError("The release has no zipapp archive.")
    name = archive["name"]
    digest = archive.get("digest") or ""
    if not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
        raise ValueError("The release has no valid SHA-256 digest.")
    expected: dict[str, str] = {"sha256": digest[7:].lower()}
    for algorithm in ("sha512", "sha256"):
        checksum = next(
            (
                a
                for a in assets
                if a.get("name")
                in (
                    name + f".{algorithm}sum",
                    name.removesuffix(".tar") + f".{algorithm}sum",
                )
            ),
            None,
        )
        if checksum:
            with request(checksum["browser_download_url"]) as response:
                checksum_text = response.read(4097).decode("utf-8", "strict")
            match = re.fullmatch(
                rf"\s*([0-9a-fA-F]{{{128 if algorithm == 'sha512' else 64}}})(?:\s+\*?[^\s]+)?\s*",
                checksum_text,
            )
            if not match:
                raise ValueError(f"Invalid {algorithm} checksum file.")
            value = match.group(1).lower()
            if algorithm == "sha256" and value != expected["sha256"]:
                raise ValueError("Release SHA-256 checksums do not match.")
            expected[algorithm] = value

    folder = os.path.join(get_data_dir(), "umu")
    os.makedirs(folder, exist_ok=True)
    archive_path = ""
    executable_path = ""
    try:
        with tempfile.NamedTemporaryFile(
            prefix=".umu-archive-", dir=folder, delete=False
        ) as output:
            archive_path = output.name
            with request(archive["browser_download_url"]) as response:
                total = int(response.headers.get("Content-Length", "0") or 0)
                done = 0
                while chunk := response.read(1024 * 1024):
                    done += len(chunk)
                    if done > _MAX_ARCHIVE:
                        raise ValueError("The umu archive is too large.")
                    _ = output.write(chunk)
                    progress("Downloading umu", done, total)
        progress("Verifying download", 0, 0)
        for algorithm, value in expected.items():
            with open(archive_path, "rb") as source:
                actual = hashlib.file_digest(source, algorithm).hexdigest()
            if actual != value:
                raise ValueError(f"The umu archive failed its {algorithm} check.")
        progress("Extracting", 0, 0)
        with tarfile.open(archive_path, "r:*") as tar:
            selected = None
            for member in tar:
                path = PurePosixPath(member.name)
                if path.is_absolute() or ".." in path.parts:
                    raise ValueError("Unsafe path in umu archive.")
                if member.name == "umu/umu-run":
                    if (
                        selected is not None
                        or not member.isfile()
                        or member.size <= 0
                        or member.size > _MAX_EXECUTABLE
                    ):
                        raise ValueError("Invalid umu-run archive member.")
                    selected = member
            if selected is None:
                raise ValueError("The umu archive does not contain umu/umu-run.")
            source = tar.extractfile(selected)
            if source is None:
                raise ValueError("Cannot read umu-run from the archive.")
            with (
                source,
                tempfile.NamedTemporaryFile(
                    prefix=".umu-run-", dir=folder, delete=False
                ) as output,
            ):
                executable_path = output.name
                remaining = selected.size
                while remaining:
                    chunk = source.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise ValueError("Incomplete umu-run archive member.")
                    _ = output.write(chunk)
                    remaining -= len(chunk)
        os.chmod(executable_path, 0o755)
        destination = os.path.join(folder, "umu-run")
        os.replace(executable_path, destination)
        executable_path = ""
        return destination
    finally:
        for path in (archive_path, executable_path):
            if path and os.path.exists(path):
                os.unlink(path)


@final
class UmuDownload:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._status: dict[str, object] = {
            "running": False,
            "stage": "",
            "done": 0,
            "total": 0,
            "error": "",
        }

    def status(self) -> dict[str, object]:
        with self._lock:
            return self._status.copy()

    def start(self) -> dict[str, object]:
        with self._lock:
            if self._status["running"]:
                return {"success": False, "error": "umu is already downloading."}
            self._status.update(
                running=True, stage="Fetching release info", done=0, total=0, error=""
            )
        threading.Thread(target=self._run, daemon=True).start()
        return {"success": True}

    def _progress(self, stage: str, done: int, total: int) -> None:
        with self._lock:
            self._status.update(stage=stage, done=done, total=total)

    def _run(self) -> None:
        try:
            _ = download_release(self._progress)
        except Exception as exc:
            with self._lock:
                self._status.update(error=str(exc))
        finally:
            with self._lock:
                self._status.update(running=False, stage="")


def _folder_size(path: str) -> int:
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def _prepare_failure(returncode: int, output: str) -> str:
    text = output.lower()
    if "no space left" in text or "disk full" in text:
        return "disk"
    if (
        returncode == 127
        or "python3: not found" in text
        or "python3: no such file" in text
    ):
        return "python"
    if any(
        word in text
        for word in (
            "network",
            "connection",
            "timed out",
            "name resolution",
            "http error",
            "urlopen error",
        )
    ):
        return "network"
    return "other"


@final
class UmuRuntimePrepare:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._process: subprocess.Popen[bytes] | None = None
        self._status: dict[str, object] = {
            "running": False,
            "stage": "",
            "done": 0,
            "error": "",
            "error_class": "",
            "cancelled": False,
        }

    def status(self) -> dict[str, object]:
        with self._lock:
            return self._status.copy()

    def start(self, prefix: str, runner: str) -> dict[str, object]:
        path, _ = locate_umu()
        if not path:
            return {"success": False, "error": "umu-launcher is missing."}
        with self._lock:
            if self._status["running"]:
                return {
                    "success": False,
                    "error": "Runtime preparation is already running.",
                }
            self._cancel.clear()
            self._status.update(
                running=True,
                stage="Preparing runtime",
                done=0,
                error="",
                error_class="",
                cancelled=False,
            )
        threading.Thread(
            target=self._run, args=(path, prefix, runner), daemon=True
        ).start()
        return {"success": True}

    def cancel(self) -> dict[str, object]:
        with self._lock:
            if not self._status["running"]:
                return {
                    "success": False,
                    "error": "Runtime preparation is not running.",
                }
            self._cancel.set()
        return {"success": True}

    def _run(self, path: str, prefix: str, runner: str) -> None:
        environment = dict(os.environ)
        environment["WINEPREFIX"] = proton_compat_data_path(prefix)
        environment["GAMEID"] = "umu-default"
        _ = environment.pop("UMU_RUNTIME_UPDATE", None)
        _ = environment.pop("UMU_CONTAINER_NSENTER", None)
        # A plain Wine global runner prepares the runtime with UMU-Proton.
        proton_path = proton_path_value(runner)
        if proton_path:
            environment["PROTONPATH"] = proton_path
        else:
            _ = environment.pop("PROTONPATH", None)
        runtime_path = str(runtime_status(runner)["path"])
        try:
            with tempfile.TemporaryFile() as output:
                process = subprocess.Popen(
                    host_command(
                        [path, "createprefix"], env=environment, supervise=True
                    ),
                    env=environment,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                with self._lock:
                    self._process = process
                while process.poll() is None:
                    if self._cancel.is_set():
                        try:
                            os.killpg(process.pid, signal.SIGTERM)
                        except ProcessLookupError:
                            pass
                        try:
                            _ = process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            os.killpg(process.pid, signal.SIGKILL)
                            _ = process.wait()
                        break
                    with self._lock:
                        self._status["done"] = _folder_size(runtime_path)
                    time.sleep(0.5)
                _ = output.seek(0, os.SEEK_END)
                size = output.tell()
                _ = output.seek(max(0, size - 8192))
                tail = output.read().decode("utf-8", "replace")
                if self._cancel.is_set():
                    with self._lock:
                        self._status["cancelled"] = True
                elif process.returncode:
                    with self._lock:
                        self._status.update(
                            error=tail.strip() or "Runtime preparation failed.",
                            error_class=_prepare_failure(process.returncode, tail),
                        )
        except OSError as exc:
            with self._lock:
                self._status.update(
                    error=str(exc),
                    error_class="python"
                    if isinstance(exc, FileNotFoundError)
                    else _prepare_failure(1, str(exc)),
                )
        finally:
            with self._lock:
                self._process = None
                self._status.update(running=False, stage="")
