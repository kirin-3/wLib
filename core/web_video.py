# SPDX-License-Identifier: GPL-3.0-or-later
"""Media files the built-in web player cannot decode, and their in-place conversion.

The PyPI Qt WebEngine build has no H.264, H.265 or AAC decoders. This module
finds such files by reading MP4 headers in Python (no ffmpeg needed), keeps a
device-local per-game check cache, and converts files in place to AV1 (or VP9)
and Opus with the host's ffmpeg.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import struct
import subprocess
import threading
from collections.abc import Callable, Iterator
from typing import BinaryIO, TypedDict, cast

from core.host_platform import (
    get_data_dir,
    host_command,
    host_system_layout,
    host_which,
    is_flatpak,
    is_windows,
    no_console_window,
)

MEDIA_EXTENSIONS = (".mp4", ".m4v", ".m4a")
PARTIAL_SUFFIX = ".wlib-partial"
MAX_MOOV_BYTES = 64 * 1024 * 1024
FREE_SPACE_MARGIN = 64 * 1024 * 1024
SCAN_VERSION = 1

# Sample entry codes the player cannot decode. `mp4a` is checked apart,
# because MP3 in MP4 also uses it.
UNSUPPORTED_CODECS = frozenset({"avc1", "avc3", "hvc1", "hev1"})
MP3_OBJECT_TYPES = frozenset({0x69, 0x6B})
_CONTAINER_BOXES = frozenset({b"trak", b"mdia", b"minf", b"stbl"})

WEB_MEDIA_BUSY = "This game's videos are being converted. Wait for the conversion to finish."

# ── MP4 box reader ──


def _iter_boxes(data: bytes, start: int = 0, end: int | None = None) -> Iterator[tuple[bytes, int, int]]:
    """Yield (type, payload start, payload end) for each box in data[start:end]."""
    limit = len(data) if end is None else min(end, len(data))
    offset = start
    while offset + 8 <= limit:
        size, kind = struct.unpack(">I4s", data[offset : offset + 8])
        header = 8
        if size == 1:
            if offset + 16 > limit:
                return
            size = struct.unpack(">Q", data[offset + 8 : offset + 16])[0]
            header = 16
        elif size == 0:
            size = limit - offset
        if size < header or offset + size > limit:
            return
        yield kind, offset + header, offset + size
        offset += size


def _read_descriptor_length(data: bytes, offset: int, end: int) -> tuple[int, int] | None:
    length = 0
    for _ in range(4):
        if offset >= end:
            return None
        byte = data[offset]
        offset += 1
        length = (length << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return length, offset
    return length, offset


def _esds_object_type(data: bytes, start: int, end: int) -> int | None:
    """The DecoderConfigDescriptor objectTypeIndication of an esds payload."""
    offset = start + 4  # version and flags
    if offset >= end or data[offset] != 0x03:
        return None
    parsed = _read_descriptor_length(data, offset + 1, end)
    if parsed is None:
        return None
    _length, offset = parsed
    if offset + 3 > end:
        return None
    flags = data[offset + 2]
    offset += 3
    if flags & 0x80:
        offset += 2
    if flags & 0x40:
        if offset >= end:
            return None
        offset += 1 + data[offset]
    if flags & 0x20:
        offset += 2
    if offset >= end or data[offset] != 0x04:
        return None
    parsed = _read_descriptor_length(data, offset + 1, end)
    if parsed is None or parsed[1] >= end:
        return None
    return data[parsed[1]]


def _mp4a_object_type(data: bytes, start: int, end: int) -> int | None:
    # SampleEntry (8) + AudioSampleEntry (20); QuickTime sound description
    # version 1 adds 16 bytes, version 2 adds 36.
    if start + 28 > end:
        return None
    version = struct.unpack(">H", data[start + 8 : start + 10])[0]
    children = start + 28 + {1: 16, 2: 36}.get(version, 0)
    for kind, child_start, child_end in _iter_boxes(data, children, end):
        if kind == b"esds":
            return _esds_object_type(data, child_start, child_end)
        if kind == b"wave":
            for inner, inner_start, inner_end in _iter_boxes(data, child_start, child_end):
                if inner == b"esds":
                    return _esds_object_type(data, inner_start, inner_end)
    return None


def _sample_entries(moov: bytes) -> list[tuple[str, int | None]]:
    """(four-character code, esds object type for mp4a) of every track's sample entries."""
    entries: list[tuple[str, int | None]] = []

    def walk(start: int, end: int) -> None:
        for kind, child_start, child_end in _iter_boxes(moov, start, end):
            if kind in _CONTAINER_BOXES:
                walk(child_start, child_end)
            elif kind == b"stsd":
                # Full box header (4) and entry count (4).
                for entry, entry_start, entry_end in _iter_boxes(moov, child_start + 8, child_end):
                    code = entry.decode("latin-1")
                    object_type = _mp4a_object_type(moov, entry_start, entry_end) if code == "mp4a" else None
                    entries.append((code, object_type))

    walk(0, len(moov))
    return entries


def _read_moov(handle: BinaryIO, file_size: int) -> bytes | None:
    offset = 0
    while offset + 8 <= file_size:
        _ = handle.seek(offset)
        header = handle.read(16)
        if len(header) < 8:
            return None
        size, kind = struct.unpack(">I4s", header[:8])
        header_size = 8
        if size == 1:
            if len(header) < 16:
                return None
            size = struct.unpack(">Q", header[8:16])[0]
            header_size = 16
        elif size == 0:
            size = file_size - offset
        if size < header_size:
            return None
        if kind == b"moov":
            _ = handle.seek(offset + header_size)
            return handle.read(min(size - header_size, MAX_MOOV_BYTES))
        offset += size
    return None


def file_sample_entries(path: str) -> list[tuple[str, int | None]] | None:
    """The sample entries of an MP4 file, or None when its headers cannot be read."""
    try:
        with open(path, "rb") as handle:
            moov = _read_moov(handle, os.fstat(handle.fileno()).st_size)
    except OSError:
        return None
    if not moov:
        return None
    return _sample_entries(moov)


def entries_need_conversion(entries: list[tuple[str, int | None]]) -> bool:
    for code, object_type in entries:
        if code in UNSUPPORTED_CODECS:
            return True
        if code == "mp4a" and object_type not in MP3_OBJECT_TYPES:
            return True
    return False


def file_needs_conversion(path: str) -> bool:
    """True when the file holds H.264/H.265 video or AAC audio. Unreadable files are False."""
    entries = file_sample_entries(path)
    return entries is not None and entries_need_conversion(entries)


# ── Served-folder walk and device-local cache ──


def iter_media_files(root: str) -> Iterator[tuple[str, str]]:
    """Yield (relative path with "/", absolute path) of media files the player could serve."""
    real_root = os.path.realpath(root)
    seen: set[str] = set()

    def inside(path: str) -> bool:
        try:
            return os.path.commonpath([real_root, path]) == real_root
        except ValueError:
            return False

    for folder, dirs, files in os.walk(real_root, followlinks=True):
        real_folder = os.path.realpath(folder)
        if real_folder in seen or not inside(real_folder):
            dirs[:] = []
            continue
        seen.add(real_folder)
        dirs.sort()
        for name in sorted(files):
            if not name.lower().endswith(MEDIA_EXTENSIONS):
                continue
            path = os.path.join(folder, name)
            real = os.path.realpath(path)
            if not inside(real) or not os.path.isfile(real):
                continue
            relative = os.path.relpath(path, real_root).replace(os.sep, "/")
            yield relative, path


class CacheEntry(TypedDict):
    size: int
    mtime_ns: int
    needs: bool


class ScanCache(TypedDict):
    version: int
    root: str
    files: dict[str, CacheEntry]
    play_anyway: str | None


class ScanResult(TypedDict):
    root: str
    needs: list[str]
    fingerprint: str
    play_anyway: bool


_cache_lock = threading.Lock()


def scan_cache_dir() -> str:
    return os.path.join(get_data_dir(), "web-video-scan")


def scan_cache_path(game_id: int) -> str:
    if isinstance(game_id, bool) or game_id <= 0:
        raise ValueError("The check cache needs a valid library game ID.")
    return os.path.join(scan_cache_dir(), f"{game_id}.json")


def _empty_cache(root: str) -> ScanCache:
    return {"version": SCAN_VERSION, "root": root, "files": {}, "play_anyway": None}


def _load_cache(game_id: int) -> ScanCache | None:
    try:
        with open(scan_cache_path(game_id), encoding="utf-8") as handle:
            raw = cast(object, json.load(handle))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    data = cast(dict[str, object], raw)
    files_raw = data.get("files")
    root = data.get("root")
    if data.get("version") != SCAN_VERSION or not isinstance(root, str) or not isinstance(files_raw, dict):
        return None
    files: dict[str, CacheEntry] = {}
    for key, value in cast(dict[object, object], files_raw).items():
        if not isinstance(key, str) or not isinstance(value, dict):
            continue
        entry = cast(dict[str, object], value)
        size, mtime_ns, needs = entry.get("size"), entry.get("mtime_ns"), entry.get("needs")
        if isinstance(size, int) and isinstance(mtime_ns, int) and isinstance(needs, bool):
            files[key] = {"size": size, "mtime_ns": mtime_ns, "needs": needs}
    play_anyway = data.get("play_anyway")
    return {
        "version": SCAN_VERSION,
        "root": root,
        "files": files,
        "play_anyway": play_anyway if isinstance(play_anyway, str) else None,
    }


def _write_cache(game_id: int, cache: ScanCache) -> None:
    path = scan_cache_path(game_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(cache, handle)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def delete_scan_cache(game_id: int) -> bool:
    """Delete a game's check cache; True when a file was removed."""
    with _cache_lock:
        try:
            os.unlink(scan_cache_path(game_id))
        except FileNotFoundError:
            return False
    return True


def cached_root(game_id: int) -> str:
    """The folder of the last check, or "" when the game has no cache."""
    with _cache_lock:
        cache = _load_cache(game_id)
    return cache["root"] if cache else ""


def fingerprint(cache: ScanCache) -> str:
    """SHA-256 of the sorted (path, size, mtime_ns) of the files that need conversion."""
    rows = sorted(
        (path, entry["size"], entry["mtime_ns"]) for path, entry in cache["files"].items() if entry["needs"]
    )
    return hashlib.sha256(json.dumps(rows).encode("utf-8")).hexdigest()


def _refresh(game_id: int, root: str, reader: Callable[[str], bool]) -> ScanCache:
    """Bring the cache up to date with the folder; caller holds _cache_lock."""
    real_root = os.path.realpath(root)
    old = _load_cache(game_id)
    cache = old if old is not None and old["root"] == real_root else _empty_cache(real_root)
    files: dict[str, CacheEntry] = {}
    for relative, path in iter_media_files(real_root):
        try:
            info = os.stat(path)
        except OSError:
            continue
        previous = cache["files"].get(relative)
        if previous and previous["size"] == info.st_size and previous["mtime_ns"] == info.st_mtime_ns:
            files[relative] = previous
        else:
            files[relative] = {"size": info.st_size, "mtime_ns": info.st_mtime_ns, "needs": reader(path)}
    updated: ScanCache = {
        "version": SCAN_VERSION,
        "root": real_root,
        "files": files,
        "play_anyway": cache["play_anyway"],
    }
    if updated != old:
        _write_cache(game_id, updated)
    return updated


def _result(cache: ScanCache) -> ScanResult:
    current = fingerprint(cache)
    return {
        "root": cache["root"],
        "needs": sorted(path for path, entry in cache["files"].items() if entry["needs"]),
        "fingerprint": current,
        "play_anyway": cache["play_anyway"] == current,
    }


def scan_game(game_id: int, root: str, reader: Callable[[str], bool] | None = None) -> ScanResult:
    """Check the served folder, reading only new or changed files."""
    with _cache_lock:
        return _result(_refresh(game_id, root, reader or file_needs_conversion))


def set_play_anyway(game_id: int, root: str) -> ScanResult:
    """Remember "Play anyway" for the current set of files that need conversion."""
    with _cache_lock:
        cache = _refresh(game_id, root, lambda path: file_needs_conversion(path))
        cache["play_anyway"] = fingerprint(cache)
        _write_cache(game_id, cache)
        return _result(cache)


def mark_converted(game_id: int, relative: str, size: int, mtime_ns: int) -> None:
    """Record a replaced file so the next check does not read it again."""
    with _cache_lock:
        cache = _load_cache(game_id)
        if cache is None:
            return
        cache["files"][relative] = {"size": size, "mtime_ns": mtime_ns, "needs": False}
        _write_cache(game_id, cache)


# ── ffmpeg discovery ──


class InstallHint(TypedDict):
    command: str
    package_manager: str
    platform: str
    host: bool


class FfmpegInfo(TypedDict):
    available: bool
    ffmpeg: str
    ffprobe: str
    video_encoder: str
    audio_encoder: str
    reason: str
    install: InstallHint


VIDEO_ENCODERS = ("libsvtav1", "libvpx-vp9")
AUDIO_ENCODER = "libopus"
LINUX_INSTALL_COMMANDS: tuple[tuple[str, str], ...] = (
    ("rpm-ostree", "rpm-ostree install ffmpeg-free"),
    ("pacman", "sudo pacman -S ffmpeg"),
    ("apt", "sudo apt install ffmpeg"),
    ("dnf", "sudo dnf install ffmpeg-free"),
    ("zypper", "sudo zypper install ffmpeg"),
    ("xbps-install", "sudo xbps-install ffmpeg"),
)
NIXOS_INSTALL = "environment.systemPackages = [ pkgs.ffmpeg ];"
WINDOWS_INSTALL = "winget install Gyan.FFmpeg"


def parse_encoders(text: str) -> set[str]:
    """Encoder names from `ffmpeg -encoders` output."""
    names: set[str] = set()
    started = False
    for line in text.splitlines():
        stripped = line.strip()
        if not started:
            started = stripped.startswith("------")
            continue
        parts = stripped.split()
        if len(parts) >= 2 and len(parts[0]) == 6:
            names.add(parts[1])
    return names


def install_hint(
    which: Callable[[str], str | None] = host_which,
    *,
    windows: bool | None = None,
    flatpak: bool | None = None,
    nixos: bool | None = None,
) -> InstallHint:
    """How to install ffmpeg on this platform."""
    if is_windows() if windows is None else windows:
        return {"command": WINDOWS_INSTALL, "package_manager": "winget", "platform": "windows", "host": False}
    host = is_flatpak() if flatpak is None else flatpak
    if nixos if nixos is not None else host_system_layout()[0]:
        return {"command": NIXOS_INSTALL, "package_manager": "nixos", "platform": "linux", "host": host}
    for manager, command in LINUX_INSTALL_COMMANDS:
        if which(manager):
            return {"command": command, "package_manager": manager, "platform": "linux", "host": host}
    return {"command": "", "package_manager": "", "platform": "linux", "host": host}


_ffmpeg_lock = threading.Lock()
_ffmpeg_info: FfmpegInfo | None = None


def _run_text(cmd: list[str], timeout: float) -> str:
    completed = subprocess.run(
        host_command(cmd),
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
        timeout=timeout,
        creationflags=no_console_window(),
    )
    return completed.stdout or ""


def probe_ffmpeg(
    which: Callable[[str], str | None] = host_which,
    run: Callable[[list[str], float], str] = _run_text,
) -> FfmpegInfo:
    """Find ffmpeg and ffprobe and pick the encoders (no cache)."""
    ffmpeg = which("ffmpeg") or ""
    ffprobe = which("ffprobe") or ""
    info: FfmpegInfo = {
        "available": False,
        "ffmpeg": ffmpeg,
        "ffprobe": ffprobe,
        "video_encoder": "",
        "audio_encoder": "",
        "reason": "",
        "install": install_hint(which),
    }
    if not ffmpeg or not ffprobe:
        info["reason"] = "missing"
        return info
    try:
        encoders = parse_encoders(run([ffmpeg, "-hide_banner", "-encoders"], 20))
    except (OSError, subprocess.SubprocessError):
        encoders: set[str] = set()
    info["video_encoder"] = next((name for name in VIDEO_ENCODERS if name in encoders), "")
    info["audio_encoder"] = AUDIO_ENCODER if AUDIO_ENCODER in encoders else ""
    if not info["video_encoder"]:
        info["reason"] = "no_video_encoder"
    elif not info["audio_encoder"]:
        info["reason"] = "no_audio_encoder"
    else:
        info["available"] = True
    return info


def ffmpeg_info(refresh: bool = False) -> FfmpegInfo:
    """`probe_ffmpeg` once per app run, and again while no usable ffmpeg was found.

    A user who installs ffmpeg after the dialog told them to then sees
    "Convert videos" at the next launch, without restarting wLib.
    """
    global _ffmpeg_info
    with _ffmpeg_lock:
        if _ffmpeg_info is None or refresh or not _ffmpeg_info["available"]:
            _ffmpeg_info = probe_ffmpeg()
        return _ffmpeg_info


# ── Launch check ──


def launch_check(game_id: int, root: str) -> dict[str, object] | None:
    """The launch result that stops the player, or None when it may start."""
    result = scan_game(game_id, root)
    if not result["needs"] or result["play_anyway"]:
        return None
    count = len(result["needs"])
    noun = "video uses" if count == 1 else "videos use"
    return {
        "success": False,
        "code": "web_media_unsupported",
        "error": f"{count} {noun} a format the built-in player cannot play.",
        "media": {"count": count, "ffmpeg": ffmpeg_info()},
    }


# ── Per-file conversion ──


DOC_PORTAL_RE = re.compile(r"^/run/user/\d+/doc/")


class ConversionError(Exception):
    """A problem that stops the whole job."""


class LowDiskSpace(ConversionError):
    pass


def portal_path_error(root: str, flatpak: bool | None = None) -> str | None:
    if (is_flatpak() if flatpak is None else flatpak) and DOC_PORTAL_RE.match(root):
        return (
            "This game folder was opened through the Flatpak document portal, so ffmpeg on "
            "the host cannot find it. Give wLib access to the folder, then add the game again."
        )
    return None


def cleanup_partials(root: str) -> int:
    """Delete leftover partial files of an earlier job; return the count."""
    removed = 0
    for folder, _dirs, files in os.walk(root):
        for name in files:
            if name.endswith(PARTIAL_SUFFIX):
                try:
                    os.unlink(os.path.join(folder, name))
                    removed += 1
                except OSError:
                    pass
    return removed


def _mount_point(path: str) -> str:
    current = os.path.abspath(path)
    while not os.path.ismount(current):
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return current


class StreamInfo(TypedDict):
    video: list[str]
    audio: list[str]
    duration: float | None


SUPPORTED_VIDEO = ("av1", "vp9")
SUPPORTED_AUDIO = ("opus", "mp3", "flac")


def parse_probe(text: str) -> StreamInfo:
    info: StreamInfo = {"video": [], "audio": [], "duration": None}
    try:
        raw = cast(object, json.loads(text or "{}"))
    except ValueError:
        return info
    if not isinstance(raw, dict):
        return info
    data = cast(dict[str, object], raw)
    streams = data.get("streams")
    if isinstance(streams, list):
        for stream in cast(list[object], streams):
            if not isinstance(stream, dict):
                continue
            entry = cast(dict[str, object], stream)
            disposition = entry.get("disposition")
            if isinstance(disposition, dict) and cast(dict[str, object], disposition).get("attached_pic") == 1:
                # Cover art is a still image, not a video track; `-map 0:V?` drops it.
                continue
            kind, name = entry.get("codec_type"), str(entry.get("codec_name") or "")
            if kind == "video":
                info["video"].append(name)
            elif kind == "audio":
                info["audio"].append(name)
    container = data.get("format")
    if isinstance(container, dict):
        try:
            info["duration"] = float(str(cast(dict[str, object], container).get("duration")))
        except ValueError:
            info["duration"] = None
    return info


def verify_output(source: StreamInfo, output: StreamInfo) -> str | None:
    """Why the converted file is not usable, or None."""
    if bool(source["video"]) != bool(output["video"]) or bool(source["audio"]) != bool(output["audio"]):
        return "streams are missing"
    if any(name not in SUPPORTED_VIDEO for name in output["video"]):
        return "video codec is wrong"
    if any(name not in SUPPORTED_AUDIO for name in output["audio"]):
        return "audio codec is wrong"
    if source["duration"] is not None:
        if output["duration"] is None:
            return "duration is missing"
        if abs(output["duration"] - source["duration"]) > max(0.5, source["duration"] * 0.02):
            return "duration does not match"
    return None


def encode_command(info: FfmpegInfo, source: str, target: str, streams: StreamInfo) -> list[str]:
    cmd = [info["ffmpeg"], "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-i", source]
    # `0:V` (capital) leaves out cover art, which would otherwise become a
    # one-frame AV1 track or stop the mux with "received no packets".
    cmd += ["-map", "0:V?", "-map", "0:a?", "-sn", "-dn"]
    if streams["video"] and all(name in SUPPORTED_VIDEO for name in streams["video"]):
        cmd += ["-c:v", "copy"]
    elif info["video_encoder"] == "libsvtav1":
        cmd += ["-c:v", "libsvtav1", "-preset", "8", "-crf", "35"]
    else:
        cmd += ["-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8", "-row-mt", "1"]
        cmd += ["-b:v", "0", "-crf", "33"]
    if "-c:v" in cmd and cmd[cmd.index("-c:v") + 1] != "copy":
        cmd += ["-pix_fmt", "yuv420p", "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2"]
    if streams["audio"] and all(name in SUPPORTED_AUDIO for name in streams["audio"]):
        cmd += ["-c:a", "copy"]
    else:
        cmd += ["-c:a", AUDIO_ENCODER, "-b:a", "96k"]
    cmd += ["-movflags", "+faststart", "-f", "mp4", target]
    return cmd


class FileResult(TypedDict):
    ok: bool
    error: str
    size: int
    mtime_ns: int


class Converter:
    """Converts one file at a time; `kill` stops the running ffmpeg from another thread."""

    def __init__(self, info: FfmpegInfo) -> None:
        self.info: FfmpegInfo = info
        self._lock: threading.Lock = threading.Lock()
        self._process: subprocess.Popen[bytes] | None = None
        self._partial: str = ""
        self._killed: bool = False

    def _probe(self, path: str) -> StreamInfo:
        cmd = [
            self.info["ffprobe"], "-v", "error", "-show_entries",
            "stream=codec_type,codec_name:stream_disposition=attached_pic:format=duration",
            "-of", "json", path,
        ]
        return parse_probe(_run_text(cmd, 60))

    def _encode(self, cmd: list[str]) -> str | None:
        with self._lock:
            if self._killed:
                return "stopped"
            process = subprocess.Popen(
                host_command(cmd),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                creationflags=no_console_window(),
            )
            self._process = process
        try:
            _stdout, stderr = process.communicate()
        finally:
            with self._lock:
                self._process = None
        if process.returncode != 0:
            message = (stderr or b"").decode("utf-8", "replace").strip().splitlines()
            return message[-1] if message else f"ffmpeg exited with code {process.returncode}"
        return None

    def kill(self) -> None:
        """Stop ffmpeg and delete its partial file (wLib quits)."""
        with self._lock:
            self._killed = True
            process = self._process
            partial = self._partial
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                _ = process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        if partial:
            try:
                os.unlink(partial)
            except OSError:
                pass

    def convert(self, path: str) -> FileResult:
        """Replace `path` with a converted copy, or keep it. Raises LowDiskSpace."""
        failed: FileResult = {"ok": False, "error": "", "size": 0, "mtime_ns": 0}
        try:
            source_stat = os.stat(path)
        except OSError as exc:
            failed["error"] = str(exc)
            return failed
        folder = os.path.dirname(path)
        free = shutil.disk_usage(folder).free
        if free < source_stat.st_size + FREE_SPACE_MARGIN:
            raise LowDiskSpace(f"Not enough free space on {_mount_point(folder)} to convert the next video.")
        partial = path + PARTIAL_SUFFIX
        with self._lock:
            self._partial = partial
        try:
            source = self._probe(path)
            error = self._encode(encode_command(self.info, path, partial, source))
            if error is None:
                error = verify_output(source, self._probe(partial))
            if error is not None:
                failed["error"] = error
                return failed
            os.chmod(partial, stat.S_IMODE(source_stat.st_mode))
            os.replace(partial, path)
            new_stat = os.stat(path)
            return {"ok": True, "error": "", "size": new_stat.st_size, "mtime_ns": new_stat.st_mtime_ns}
        except (OSError, subprocess.SubprocessError) as exc:
            failed["error"] = str(exc)
            return failed
        finally:
            with self._lock:
                self._partial = ""
            if os.path.exists(partial):
                try:
                    os.unlink(partial)
                except OSError:
                    pass
