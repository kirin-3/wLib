# SPDX-License-Identifier: GPL-3.0-or-later
"""Unity and BepInEx game-folder inspection."""

from __future__ import annotations

import json
import re
import struct
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import cast
from http.client import HTTPResponse
from pathlib import Path

_UNITY_VERSION = re.compile(rb"\b(\d{1,4}\.\d+\.\d+)[abfpx]\d+\b")
_PRODUCT_VERSION = re.compile(r"ProductVersion\x00+([^\x00]+)")
_FILE_VERSION = re.compile(r"FileVersion\x00+([^\x00]+)")


@dataclass(frozen=True)
class UnityGame:
    platform: str
    data_folder: str
    backend: str
    arch: str
    version: str


def _read(path: Path, limit: int) -> bytes:
    try:
        with path.open("rb") as stream:
            return stream.read(limit)
    except OSError:
        return b""


def _pe_arch(path: Path) -> str:
    try:
        with path.open("rb") as stream:
            header = stream.read(64)
            if len(header) < 64 or header[:2] != b"MZ":
                return ""
            offset = struct.unpack_from("<I", header, 60)[0]
            if offset > 16 * 1024 * 1024:
                return ""
            _ = stream.seek(offset)
            pe = stream.read(6)
            if len(pe) != 6 or pe[:4] != b"PE\0\0":
                return ""
            return {0x14C: "x86", 0x8664: "x64"}.get(struct.unpack_from("<H", pe, 4)[0], "")
    except OSError:
        return ""


def _version_string(path: Path) -> str:
    # The version resource sits in .rsrc near the end of the file, so read the
    # tail of a large binary such as UnityPlayer.dll.
    limit = 8 * 1024 * 1024
    try:
        with path.open("rb") as stream:
            size = stream.seek(0, 2)
            _ = stream.seek(max(0, size - limit))
            data = stream.read(limit)
    except OSError:
        return ""
    # PE version resources are UTF-16LE; allow either byte alignment.
    for pattern in (_PRODUCT_VERSION, _FILE_VERSION):
        for offset in (0, 1):
            text = data[offset:].decode("utf-16-le", errors="ignore")
            match = pattern.search(text)
            if match:
                return match.group(1).strip()
    return ""


_DATA_FILES = ("globalgamemanagers", "data.unity3d", "mainData")


def _unity_layout(folder: str, exe_path: str) -> tuple[Path, Path | None, Path | None, bool, Path] | None:
    """(root, exe, data folder, windows, player) when the folder is a Unity game."""
    root = Path(folder)
    if not root.is_dir():
        return None
    exe = Path(exe_path) if exe_path else None
    data = root / f"{exe.stem}_Data" if exe else None
    if data is None or not data.is_dir():
        candidates = [p for p in root.glob("*_Data") if p.is_dir()]
        data = candidates[0] if len(candidates) == 1 else None
    windows = bool(exe and exe.suffix.lower() == ".exe")
    player = root / ("UnityPlayer.dll" if windows else "UnityPlayer.so")
    if not player.is_file() and not (data and any((data / f).is_file() for f in _DATA_FILES)):
        return None
    return root, exe, data, windows, player


def is_unity_folder(folder: str, exe_path: str = "") -> bool:
    """Cheap file-layout check; reads no file contents."""
    return _unity_layout(folder, exe_path) is not None


def detect_unity(folder: str, exe_path: str = "") -> UnityGame | None:
    layout = _unity_layout(folder, exe_path)
    if layout is None:
        return None
    root, exe, data, windows, player = layout
    files = _DATA_FILES
    backend = ""
    if (root / "GameAssembly.dll").is_file() or (data and (data / "il2cpp_data").is_dir()):
        backend = "il2cpp"
    elif data and any((data / "Managed" / f).is_file() for f in ("Assembly-CSharp.dll", "UnityEngine.dll")):
        backend = "mono"
    binary = player if player.is_file() else exe
    arch = _pe_arch(binary) if windows and binary else ""
    version = ""
    if data:
        for name in files:
            match = _UNITY_VERSION.search(_read(data / name, 65536))
            if match:
                version = match.group(1).decode("ascii")
                break
    if not version and binary:
        match = _UNITY_VERSION.search(_version_string(binary).encode("ascii", errors="ignore"))
        if match:
            version = match.group(1).decode("ascii")
    return UnityGame("windows" if windows else "linux", str(data) if data else "", backend, arch, version)


def detect_installed(folder: str) -> dict[str, object] | None:
    root = Path(folder)
    core = root / "BepInEx" / "core"
    dll5, dll6 = core / "BepInEx.dll", core / "BepInEx.Core.dll"
    dll = dll6 if dll6.is_file() else dll5
    if not dll.is_file():
        return None
    marker = root / "BepInEx" / ".wlib-bepinex.json"
    managed = False
    version = _version_string(dll)
    try:
        value = cast(object, json.loads(marker.read_text(encoding="utf-8")))
        if isinstance(value, dict):
            managed = True
            metadata = cast(dict[str, object], value)
            if isinstance(metadata.get("version"), str) and metadata["version"]:
                version = str(metadata["version"])
    except (OSError, ValueError):
        pass
    return {
        "version": version,
        "major": 6 if dll == dll6 else 5,
        "backend": "il2cpp" if (core / "BepInEx.Unity.IL2CPP.dll").is_file() else "mono",
        "complete": (root / "winhttp.dll").is_file(),
        "managed_by_wlib": managed,
    }


# Official releases have used these names across the 5.x and 6.x eras.
_ASSET_PATTERNS = (
    re.compile(r"^BepInEx_(?:win_)?(?P<arch>x86|x64)_(?P<version>5\.[\w.+-]+)\.zip$", re.I),
    re.compile(r"^BepInEx-Unity\.(?P<backend>Mono|IL2CPP)-win-(?P<arch>x86|x64)-(?P<version>6\.[\w.+-]+)\.zip$", re.I),
    re.compile(r"^BepInEx_Unity(?P<backend>Mono|IL2CPP)_(?P<arch>x86|x64)_(?P<version>6\.[\w.+-]+)\.zip$", re.I),
)
GITHUB_RELEASES = "https://api.github.com/repos/BepInEx/BepInEx/releases?per_page=100"
BLEEDING_EDGE = "https://builds.bepinex.dev/api/projects/bepinex_be/artifacts/latest"


def _asset(name: str, arch: str, backend: str) -> tuple[str, int] | None:
    for pattern in _ASSET_PATTERNS:
        match = pattern.fullmatch(name)
        if match and match.group("arch").lower() == arch:
            major = int(match.group("version")[0])
            kind = (match.groupdict().get("backend") or "mono").lower()
            if kind == backend:
                return match.group("version"), major
    return None


def fetch_json(url: str) -> object:
    import urllib.request

    request = urllib.request.Request(url, headers={"User-Agent": "wLib", "Accept": "application/json"})
    with cast(HTTPResponse, urllib.request.urlopen(request, timeout=30)) as response:  # noqa: S310
        return cast(object, json.load(response))


def list_versions(arch: str, backend: str, fetcher: Callable[[str], object] | None = None) -> tuple[list[dict[str, object]], list[str]]:
    from urllib.parse import quote

    fetcher = fetcher or fetch_json
    builds: list[dict[str, object]] = []
    warnings: list[str] = []
    try:
        raw_releases = fetcher(GITHUB_RELEASES)
        if not isinstance(raw_releases, list):
            raise ValueError("Invalid release data")
        releases = cast(list[object], raw_releases)
        for raw_release in releases:
            if not isinstance(raw_release, dict):
                continue
            release = cast(dict[str, object], raw_release)
            raw_assets = release.get("assets")
            if not isinstance(raw_assets, list):
                continue
            for raw_item in cast(list[object], raw_assets):
                if not isinstance(raw_item, dict):
                    continue
                item = cast(dict[str, object], raw_item)
                name = str(item.get("name") or "")
                parsed = _asset(name, arch, backend)
                if parsed:
                    version, major = parsed
                    digest = str(item.get("digest") or "")
                    builds.append({"id": version, "major": major, "backend": backend,
                                   "channel": "pre" if release.get("prerelease") else "stable",
                                   "url": item.get("browser_download_url", ""), "file_name": name,
                                   "sha256": digest[7:] if digest.startswith("sha256:") else "",
                                   "date": release.get("published_at") or ""})
    except (OSError, ValueError, TypeError, AttributeError, KeyError) as exc:
        warnings.append(f"Could not load GitHub releases: {exc}")
    try:
        raw_feed = fetcher(BLEEDING_EDGE)
        if not isinstance(raw_feed, dict):
            raise ValueError("Invalid bleeding-edge data")
        feed = cast(dict[str, object], raw_feed)
        raw_artifacts = feed.get("artifacts")
        if not isinstance(raw_artifacts, list):
            raise ValueError("Invalid bleeding-edge artifacts")
        for raw_item in cast(list[object], raw_artifacts):
            if not isinstance(raw_item, dict):
                continue
            item = cast(dict[str, object], raw_item)
            name = str(item.get("file") or "")
            parsed = _asset(name, arch, backend)
            if parsed and parsed[1] == 6:
                version, major = parsed
                builds.append({"id": version, "major": major, "backend": backend, "channel": "be",
                               "url": f"https://builds.bepinex.dev/projects/bepinex_be/{quote(str(feed['id']), safe='')}/{quote(name, safe='')}",
                               "file_name": name, "sha256": "", "date": feed.get("date") or ""})
    except (OSError, ValueError, TypeError, AttributeError, KeyError) as exc:
        warnings.append(f"Could not load bleeding-edge builds: {exc}")
    return builds, warnings


def recommended_build(builds: list[dict[str, object]], backend: str, major: int | None = None) -> dict[str, object] | None:
    target = major or (6 if backend == "il2cpp" else 5)
    channels = ("stable",) if target == 5 else ("be", "pre")
    for channel in channels:
        choices = [b for b in builds if b["major"] == target and b["channel"] == channel]
        if choices:
            def version_key(build: dict[str, object]) -> tuple[int, ...]:
                return tuple(int(n) for n in cast(list[str], re.findall(r"\d+", str(build["id"]).split("+")[0])))
            return max(choices, key=version_key)
    return None


def _download_cached_unlocked(url: str, file_name: str, cache_dir: str, sha256: str = "", progress: Callable[[int, int, str], None] | None = None, cancelled: Callable[[], bool] | None = None, opener: Callable[..., HTTPResponse] | None = None, label: str = "Downloading BepInEx ...") -> Path | None:
    """Download to a cache atomically; return None for a missing resource."""
    import hashlib
    import urllib.error
    import urllib.request

    from core.game_tools import CHUNK_SIZE, GameToolError

    if not url.startswith("https://") or Path(file_name).name != file_name:
        raise GameToolError("Invalid download URL or filename")
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / file_name
    def digest(path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(CHUNK_SIZE), b""):
                h.update(chunk)
        return h.hexdigest()
    def complete(path: Path) -> bool:
        # Without a published hash, a zip's central directory (written last)
        # is the only sign that the whole file arrived.
        if sha256:
            return digest(path).lower() == sha256.lower()
        if file_name.lower().endswith(".zip"):
            import zipfile

            try:
                with zipfile.ZipFile(path) as archive:
                    return archive.testzip() is None
            except (OSError, zipfile.BadZipFile, EOFError):
                return False
        return True
    if target.is_file():
        if complete(target):
            return target
        target.unlink()
    partial = cache / (file_name + ".wlib-partial")
    opener = opener or cast(Callable[..., HTTPResponse], urllib.request.urlopen)
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "wLib"})
        try:
            response = opener(request, timeout=30)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise
        with response, partial.open("wb") as out:
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            while True:
                if cancelled and cancelled():
                    raise GameToolError("Download cancelled")
                chunk = response.read(CHUNK_SIZE)
                if not chunk:
                    break
                _ = out.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total, label)
        # HTTPResponse.read() returns b"" on an early EOF instead of raising.
        if total and done != total:
            raise GameToolError(
                f"Download incomplete ({done} of {total} bytes); try again"
            )
        if sha256 and digest(partial).lower() != sha256.lower():
            raise GameToolError("Downloaded file SHA-256 mismatch")
        if not sha256 and not complete(partial):
            raise GameToolError("Downloaded file is not a valid zip archive")
        _ = partial.replace(target)
        return target
    finally:
        partial.unlink(missing_ok=True)


_download_lock = threading.Lock()


def download_cached(url: str, file_name: str, cache_dir: str, sha256: str = "", progress: Callable[[int, int, str], None] | None = None, cancelled: Callable[[], bool] | None = None, opener: Callable[..., HTTPResponse] | None = None, label: str = "Downloading BepInEx ...") -> Path | None:
    """Serialize cache writes, including downloads shared by different games."""
    from core.game_tools import GameToolError

    while not _download_lock.acquire(timeout=0.25):
        if cancelled and cancelled():
            raise GameToolError("Download cancelled")
    try:
        return _download_cached_unlocked(url, file_name, cache_dir, sha256, progress, cancelled, opener, label)
    finally:
        _download_lock.release()


_PRESERVED = {"plugins", "config", "patchers", "unity-libs"}
_MARKER = "BepInEx/.wlib-bepinex.json"
_BACKUP = "BepInEx.wlib-backup"
_STAGING = ".wlib-bepinex-staging"


def _safe_relative(name: str) -> Path:
    from core.archive_install import check_archive_entry_safe

    check_archive_entry_safe(name)
    return Path(name.replace("\\", "/"))


def _safe_target(root: Path, name: str) -> Path:
    from core.game_tools import GameToolError

    rel = _safe_relative(name)
    target = root / rel
    for parent in (target, *target.parents):
        if parent == root:
            break
        if parent.is_symlink():
            raise GameToolError(f"Refusing symbolic link in game folder: {name}")
    return target


def _owned_manifest(root: Path) -> list[str] | None:
    try:
        value = cast(object, json.loads((root / _MARKER).read_text(encoding="utf-8")))
        if not isinstance(value, dict):
            return None
        paths = cast(dict[str, object], value).get("owned")
        if not isinstance(paths, list):
            return None
        values = cast(list[object], paths)
        if not all(isinstance(p, str) for p in values):
            return None
        result = cast(list[str], values)
        for path in result:
            _ = _safe_target(root, path)
        return result
    except (OSError, ValueError, TypeError, KeyError):
        return None


_DISCARD = ".wlib-bepinex-replaced"


def _read_backup_manifest(backup: Path) -> dict[str, object] | None:
    try:
        value = cast(object, json.loads((backup / "manifest.json").read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None
    return cast(dict[str, object], value) if isinstance(value, dict) else None


class _BackupChange:
    """Undo log for one install's or uninstall's moves into the backup."""

    def __init__(self) -> None:
        self.moves: list[tuple[Path, Path]] = []  # (now at, came from)
        self.discard: Path | None = None
        self.manifest_path: Path | None = None
        self.manifest_before: str = ""

    def rollback(self) -> None:
        import shutil

        for now_at, came_from in reversed(self.moves):
            came_from.parent.mkdir(parents=True, exist_ok=True)
            _ = shutil.move(str(now_at), str(came_from))
        if self.manifest_path is not None:
            _ = self.manifest_path.write_text(self.manifest_before, encoding="utf-8")
        self.commit()

    def commit(self) -> None:
        import shutil

        if self.discard is not None and self.discard.exists():
            shutil.rmtree(self.discard, ignore_errors=True)


def _merge_move(source: Path, destination: Path, change: _BackupChange) -> list[Path]:
    """Move source into destination, descending into folders both have."""
    import shutil

    from core.game_tools import GameToolError

    if not destination.exists() and not destination.is_symlink():
        destination.parent.mkdir(parents=True, exist_ok=True)
        _ = shutil.move(str(source), str(destination))
        change.moves.append((destination, source))
        return [destination]
    if source.is_dir() and not source.is_symlink() and destination.is_dir() and not destination.is_symlink():
        moved: list[Path] = []
        for child in sorted(source.iterdir()):
            moved.extend(_merge_move(child, destination / child.name, change))
        return moved
    raise GameToolError(f"The BepInEx backup already holds {destination.name}. Restore or remove the backup first.")


def _is_wlib_owned(name: str, owned: list[str] | None, installed: list[str]) -> bool:
    if owned is None:
        return False
    path = Path(name)
    owned_paths = [Path(p) for p in owned]
    # A folder the new build replaces whole (dotnet) counts when wLib put files in it.
    return any(path.is_relative_to(p) for p in owned_paths) or (
        name in installed and any(p.is_relative_to(path) for p in owned_paths)
    )


def _move_to_backup(root: Path, paths: list[str], installed: list[str], owned: list[str] | None = None) -> _BackupChange | None:
    """Move what the operation replaces into the backup; None when nothing moved.

    The backup normally holds the state before the last install or uninstall.
    When it holds a setup wLib did not install (the user's own BepInEx), it is
    the only copy of that setup: later operations keep it, delete wLib's own
    files instead of backing them up, and merge anything else into it.
    """
    import shutil

    from core.game_tools import GameToolError

    backup = root / _BACKUP
    # Collapse nested paths so a directory and its descendants are never moved twice.
    unique: list[str] = []
    for name in sorted(set(paths), key=lambda p: (len(Path(p).parts), p)):
        target = _safe_target(root, name)
        if any(Path(name).is_relative_to(parent) for parent in map(Path, unique)):
            continue
        if target.exists():
            unique.append(name)
    if backup.is_symlink():
        raise GameToolError("Backup path is a symbolic link")
    change = _BackupChange()
    existing = _read_backup_manifest(backup) if backup.is_dir() else None
    if existing is not None and existing.get("foreign") is True:
        moved_value = existing.get("moved")
        moved_list = [p for p in cast(list[object], moved_value) if isinstance(p, str)] if isinstance(moved_value, list) else []
        change.manifest_path = backup / "manifest.json"
        change.manifest_before = change.manifest_path.read_text(encoding="utf-8")
        try:
            for name in unique:
                if _is_wlib_owned(name, owned, installed):
                    # wLib's own files: set aside, deleted once the operation succeeds.
                    if change.discard is None:
                        change.discard = root / _DISCARD
                        if change.discard.is_symlink():
                            raise GameToolError("Staging path is a symbolic link")
                        if change.discard.exists():
                            shutil.rmtree(change.discard)
                    destination = change.discard / name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    _ = shutil.move(str(root / name), str(destination))
                    change.moves.append((destination, root / name))
                else:
                    for path in _merge_move(root / name, backup / name, change):
                        relative = path.relative_to(backup).as_posix()
                        if relative not in moved_list:
                            moved_list.append(relative)
            _ = change.manifest_path.write_text(
                json.dumps({"moved": moved_list, "installed": installed, "foreign": True}), encoding="utf-8"
            )
        except Exception:
            change.rollback()
            raise
        return change
    if not unique:
        return None
    if backup.exists():
        shutil.rmtree(backup)
    backup.mkdir()
    moved: list[str] = []
    # A setup wLib did not install is the user's original: keep it from now on.
    foreign = any(not _is_wlib_owned(name, owned, installed) for name in unique)
    try:
        for name in unique:
            destination = backup / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            _ = shutil.move(str(root / name), str(destination))
            moved.append(name)
            change.moves.append((destination, root / name))
        _ = (backup / "manifest.json").write_text(
            json.dumps({"moved": moved, "installed": installed, "foreign": foreign}), encoding="utf-8"
        )
    except Exception:
        for name in reversed(moved):
            destination = root / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            _ = shutil.move(str(backup / name), str(destination))
        shutil.rmtree(backup)
        raise
    return change


def install_build(folder: str, zip_path: str, unity_libs_zip: str | None, progress: Callable[[int, int, str], None], cancelled: Callable[[], bool]) -> dict[str, object]:
    import shutil
    import stat
    import zipfile

    from core.game_tools import GameToolError

    root = Path(folder)
    stage = root / _STAGING
    if stage.is_symlink():
        raise GameToolError("Staging path is a symbolic link")
    if stage.exists():
        shutil.rmtree(stage)
    try:
        with zipfile.ZipFile(zip_path) as archive:
            entries = archive.infolist()
            names: set[str] = set()
            for entry in entries:
                rel = _safe_relative(entry.filename)
                if stat.S_ISLNK(entry.external_attr >> 16):
                    raise GameToolError("Archive contains a symbolic link")
                normalized = rel.as_posix()
                if normalized in names:
                    raise GameToolError("Archive contains duplicate paths")
                names.add(normalized)
                _ = _safe_target(root, normalized)
            stage.mkdir()
            owned: list[str] = []
            for index, entry in enumerate(entries):
                if cancelled():
                    raise GameToolError("Installation cancelled")
                rel = _safe_relative(entry.filename)
                parts = rel.parts
                preserved = len(parts) > 1 and parts[0].lower() == "bepinex" and parts[1].lower() in _PRESERVED
                dest = stage / rel
                if entry.is_dir():
                    dest.mkdir(parents=True, exist_ok=True)
                elif not preserved:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(entry) as source, dest.open("wb") as output:
                        shutil.copyfileobj(source, output)
                    owned.append(rel.as_posix())
                progress(index + 1, len(entries), rel.as_posix())
        if unity_libs_zip:
            lib = Path(unity_libs_zip)
            # Cache uses a prefix; BepInEx expects the bare version filename.
            name = lib.name.removeprefix("unity-libs-")
            target = stage / "BepInEx" / "unity-libs" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if not (root / "BepInEx" / "unity-libs" / name).exists():
                _ = shutil.copyfile(lib, target)
        marker = stage / _MARKER
        marker.parent.mkdir(parents=True, exist_ok=True)
        version = ""
        for pattern in _ASSET_PATTERNS:
            match = pattern.fullmatch(Path(zip_path).name)
            if match:
                version = match.group("version")
                break
        _ = marker.write_text(json.dumps({"owned": owned, "version": version}), encoding="utf-8")
        previous = _owned_manifest(root)
        wlib_owned = None if previous is None else [*previous, _MARKER]
        if previous is None:
            existing = detect_installed(folder)
            previous = ["BepInEx/core"] if existing else []
            if existing and existing["backend"] == "il2cpp":
                previous.append("dotnet")
        dotnet = any(Path(name).parts[0].lower() == "dotnet" for name in owned)
        installed = [name for name in owned if not (dotnet and Path(name).parts[0].lower() == "dotnet")] + [_MARKER]
        if dotnet:
            installed.append("dotnet")
        staged_files = [p for p in stage.rglob("*") if p.is_file()]
        for path in staged_files:
            _ = _safe_target(root, path.relative_to(stage).as_posix())
        change = _move_to_backup(root, previous + installed, installed, wlib_owned)
        placed: list[Path] = []
        try:
            for path in sorted(stage.rglob("*"), key=lambda p: len(p.parts)):
                rel = path.relative_to(stage)
                target = root / rel
                if path.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                elif path.is_file():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    _ = shutil.move(str(path), str(target))
                    placed.append(target)
        except Exception:
            for path in reversed(placed):
                path.unlink(missing_ok=True)
            if change is not None:
                for name in installed:
                    path = root / name
                    if path.is_dir():
                        shutil.rmtree(path)
                change.rollback()
                if not (root / _BACKUP / "manifest.json").exists():
                    shutil.rmtree(root / _BACKUP, ignore_errors=True)
            raise
        if change is not None:
            change.commit()
        return {"files": len(owned)}
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def uninstall(folder: str, remove_user_data: bool) -> dict[str, object]:
    root = Path(folder)
    installed = detect_installed(folder)
    owned = _owned_manifest(root)
    wlib_owned = None if owned is None else [*owned, _MARKER]
    if owned is None:
        owned = ["BepInEx/core", "winhttp.dll", "doorstop_config.ini", ".doorstop_version", "changelog.txt"]
        if installed and installed["backend"] == "il2cpp":
            owned.append("dotnet")
    owned.append(_MARKER)
    if remove_user_data:
        owned.append("BepInEx")
    change = _move_to_backup(root, owned, [], wlib_owned)
    if change is not None:
        change.commit()
    _prune_empty_parents(root, owned)
    return {"backup": change is not None}


def _prune_empty_parents(root: Path, paths: list[str]) -> None:
    """Drop folders such as BepInEx/core that moving files out left empty."""
    folders = {parent for name in paths for parent in Path(name).parents if parent != Path(".")}
    for folder in sorted(folders, key=lambda p: len(p.parts), reverse=True):
        parts = folder.parts
        if parts[0].lower() == "bepinex" and (len(parts) == 1 or parts[1].lower() in _PRESERVED):
            continue
        target = root / folder
        if target.is_dir() and not target.is_symlink() and not any(target.iterdir()):
            target.rmdir()


def restore(folder: str) -> dict[str, object]:
    import shutil

    from core.game_tools import GameToolError

    root = Path(folder)
    backup = root / _BACKUP
    if backup.is_symlink():
        raise GameToolError("Backup path is a symbolic link")
    try:
        raw_manifest = cast(object, json.loads((backup / "manifest.json").read_text(encoding="utf-8")))
        if not isinstance(raw_manifest, dict):
            raise ValueError("Invalid backup manifest")
        manifest = cast(dict[str, object], raw_manifest)
        moved_value, installed_value = manifest["moved"], manifest["installed"]
        if not isinstance(moved_value, list) or not isinstance(installed_value, list):
            raise ValueError("Invalid backup manifest")
        moved_items, installed_items = cast(list[object], moved_value), cast(list[object], installed_value)
        if not all(isinstance(p, str) for p in moved_items + installed_items):
            raise ValueError("Invalid backup manifest")
        moved, installed = cast(list[str], moved_items), cast(list[str], installed_items)
        for name in moved + installed:
            _ = _safe_target(root, name)
            _ = _safe_target(backup, name)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise GameToolError("Invalid or missing BepInEx backup") from exc
    for name in installed:
        path = root / name
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
    for name in moved:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_dir() and not any(p.is_file() or p.is_symlink() for p in target.rglob("*")):
            shutil.rmtree(target)
        if target.exists():
            raise GameToolError(f"Cannot restore over existing path: {name}")
        _ = shutil.move(str(backup / name), str(target))
    shutil.rmtree(backup)
    return {"restored": True}
