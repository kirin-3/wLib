# SPDX-License-Identifier: GPL-3.0-or-later
"""Torrent engine child process: JSON lines on stdin (commands) and stdout (events).

Commands: hello, add, pause, resume, remove, stop_seeding, set_limits, shutdown.
Events: ready, status, finished, error, saved, removed, bye.

The parent sends folder paths, never `.torrent` bytes or tracker URLs. Events
carry fixed messages only, so no passkey or announce URL leaves this process.
EOF on stdin means the parent is gone: resume data is saved and the helper exits.
"""

from __future__ import annotations

import json
import os
import queue
import re
import socket
import sys
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, TextIO, cast, final
from urllib.parse import urlsplit

STATUS_INTERVAL = 1.0
AUTOSAVE_INTERVAL = 300.0
SHUTDOWN_WAIT = 8.0
FLUSH_WAIT = 10.0
EXIT_ENGINE_MISSING = 3

MSG_UNAVAILABLE = "Torrent downloads are unavailable in this build"
MSG_UNSAFE = "The torrent has unsafe file paths"
MSG_BAD_TORRENT = "The torrent file is not valid"
MSG_BAD_COMMAND = "Bad command"
MSG_TORRENT_ERROR = "The torrent reported an error"

if TYPE_CHECKING:
    import libtorrent as lt_types

Emit = Callable[[dict[str, object]], None]


def redact_tracker(url: str) -> str:
    """Scheme and host of a tracker URL; the path and query carry the passkey."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    if not parts.scheme or not parts.hostname:
        return ""
    return f"{parts.scheme}://{parts.hostname}"


def _unsafe_component(part: str) -> bool:
    return part in ("..", ".") or "/" in part or "\\" in part or part == "" or re.match(r"^[A-Za-z]:", part) is not None


def _is_symlink(entry: Mapping[bytes, object]) -> bool:
    """BEP 47: an `l` attribute or a `symlink path` makes the file a symbolic link."""
    attr = entry.get(b"attr")
    return (isinstance(attr, bytes) and b"l" in attr) or b"symlink path" in entry


def _unsafe_file_tree(tree: Mapping[bytes, object], depth: int = 0) -> bool:
    """Walk a v2 `file tree`; an empty key holds a file's own properties."""
    if depth > 64:
        return True
    for key, value in tree.items():
        if not isinstance(value, dict):
            return True
        node = cast(dict[bytes, object], value)
        if key == b"":
            if _is_symlink(node):
                return True
            continue
        if _unsafe_component(key.decode("utf-8", "replace")) or _unsafe_file_tree(node, depth + 1):
            return True
    return False


def unsafe_paths(info: Mapping[bytes, object]) -> bool:
    """True when a decoded torrent `info` dictionary names a file outside its folder.

    Symbolic links are refused too: one could point outside the folder, and the
    install would then copy or follow it.
    """
    name = info.get(b"name")
    if isinstance(name, bytes) and _unsafe_component(name.decode("utf-8", "replace")):
        return True
    if _is_symlink(info):
        return True
    files = info.get(b"files")
    if isinstance(files, list):
        for entry in cast(list[object], files):
            if not isinstance(entry, dict):
                return True
            fields = cast(dict[bytes, object], entry)
            if _is_symlink(fields):
                return True
            path = fields.get(b"path")
            if not isinstance(path, list) or not path:
                return True
            for part in cast(list[object], path):
                if not isinstance(part, bytes) or _unsafe_component(part.decode("utf-8", "replace")):
                    return True
    tree = info.get(b"file tree")
    if tree is not None and (not isinstance(tree, dict) or _unsafe_file_tree(cast(dict[bytes, object], tree))):
        return True
    return False


def _free_port() -> int:
    """A TCP port that is free now, for libtorrent to listen on."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("", 0))
        address = cast(tuple[str, int], probe.getsockname())
        return address[1]


def _write_atomic(path: str, data: bytes) -> None:
    tmp = path + ".tmp"
    with open(tmp, "wb") as handle:
        _ = handle.write(data)
    os.replace(tmp, path)


@final
@dataclass
class _Entry:
    job_id: str
    folder: str
    handle: lt_types.torrent_handle
    finished_sent: bool = False
    # libtorrent marks a torrent finished when the last piece passes its hash
    # check, which can be before that piece is on disk. "finished" waits for
    # the disk cache flush (or FLUSH_WAIT) so an install never reads a short file.
    flush_requested_at: float | None = None
    flushed: bool = False


@final
class Engine:
    """One libtorrent session and the torrents added to it."""

    def __init__(self, emit: Emit, options: Mapping[str, object]) -> None:
        import libtorrent as lt

        self._lt = lt
        self._emit: Emit = emit
        self._entries: dict[str, _Entry] = {}
        self._pending_saves: int = 0
        self._last_autosave: float = time.monotonic()
        # With port 0 libtorrent opens a different port on each interface,
        # and listen_port() reports only the first one, which may not be the
        # one peers or the tracker reach. Use one free port everywhere.
        port = _free_port()
        settings: dict[str, object] = {
            "enable_dht": False,
            "enable_lsd": False,
            "enable_upnp": bool(options.get("port_mapping")),
            "enable_natpmp": bool(options.get("port_mapping")),
            "listen_interfaces": f"0.0.0.0:{port},[::]:{port}",
            "download_rate_limit": _limit(options.get("download_kib")),
            "upload_rate_limit": _limit(options.get("upload_kib")),
            "alert_mask": int(lt.alert.category_t.status_notification)
            | int(lt.alert.category_t.error_notification)
            | int(lt.alert.category_t.storage_notification),
        }
        # flags=0 leaves out the default plugins, so peer exchange stays off.
        self._session = lt.session(settings, 0)  # pyright: ignore[reportCallIssue, reportArgumentType]

    # --- commands ---------------------------------------------------------

    @property
    def port(self) -> int:
        return int(self._session.listen_port())

    def handle(self, command: Mapping[str, object]) -> None:
        name = command.get("cmd")
        job_id = str(command.get("id") or "")
        if name == "add":
            self._add(job_id, str(command.get("dir") or ""), bool(command.get("paused")))
        elif name == "set_limits":
            self._session.apply_settings({
                "download_rate_limit": _limit(command.get("download_kib")),
                "upload_rate_limit": _limit(command.get("upload_kib")),
            })
        elif name in ("pause", "resume", "remove", "stop_seeding"):
            entry = self._entries.get(job_id)
            if entry is None:
                self._error(job_id, MSG_BAD_COMMAND)
            elif name == "pause":
                self._pause(entry)
            elif name == "resume":
                entry.handle.resume()
            else:
                self._drop(entry, "stopped" if name == "stop_seeding" else "removed")
        else:
            self._error(job_id, MSG_BAD_COMMAND)

    def _error(self, job_id: str, message: str, code: str = "") -> None:
        event: dict[str, object] = {"event": "error", "message": message}
        if job_id:
            event["id"] = job_id
        if code:
            event["code"] = code
        self._emit(event)

    def _add(self, job_id: str, folder: str, paused: bool) -> None:
        lt = self._lt
        if not job_id or not folder or job_id in self._entries:
            self._error(job_id, MSG_BAD_COMMAND)
            return
        try:
            with open(os.path.join(folder, "meta.torrent"), "rb") as handle:
                meta = handle.read()
            decoded = cast(object, lt.bdecode(meta))  # pyright: ignore[reportUnknownMemberType]
            info = cast(dict[bytes, object], decoded).get(b"info") if isinstance(decoded, dict) else None
            if not isinstance(info, dict):
                raise ValueError("no info")
        except (OSError, ValueError, RuntimeError):
            self._error(job_id, MSG_BAD_TORRENT, "bad_torrent")
            return
        if unsafe_paths(cast(dict[bytes, object], info)):
            self._error(job_id, MSG_UNSAFE, "unsafe_paths")
            return
        try:
            torrent = lt.torrent_info(meta)
        except (RuntimeError, ValueError):
            self._error(job_id, MSG_BAD_TORRENT, "bad_torrent")
            return
        params = lt.add_torrent_params()
        resume = os.path.join(folder, "resume.dat")
        if os.path.isfile(resume):
            try:
                with open(resume, "rb") as handle:
                    params = lt.read_resume_data(handle.read())
            except (OSError, RuntimeError, ValueError):
                params = lt.add_torrent_params()
        params.ti = torrent
        params.save_path = os.path.join(folder, "data")
        params.flags &= ~int(lt.torrent_flags.auto_managed)
        if paused:
            params.flags |= int(lt.torrent_flags.paused)
        else:
            params.flags &= ~int(lt.torrent_flags.paused)
        try:
            handle_obj = self._session.add_torrent(params)
        except RuntimeError:
            self._error(job_id, MSG_BAD_TORRENT, "bad_torrent")
            return
        self._entries[job_id] = _Entry(job_id, folder, handle_obj)

    def _pause(self, entry: _Entry) -> None:
        entry.handle.pause()
        self._request_save(entry)

    def _drop(self, entry: _Entry, event: str) -> None:
        del self._entries[entry.job_id]
        self._session.remove_torrent(entry.handle)
        self._emit({"event": event, "id": entry.job_id})

    # --- periodic work ----------------------------------------------------

    def tick(self) -> None:
        self._handle_alerts()
        now = time.monotonic()
        if now - self._last_autosave >= AUTOSAVE_INTERVAL:
            self._last_autosave = now
            for entry in self._entries.values():
                self._request_save(entry)
        if not self._entries:
            return
        torrents: list[dict[str, object]] = []
        for entry in list(self._entries.values()):
            status = self._status(entry)
            torrents.append(status)
            if status["finished"] and not entry.finished_sent and self._flushed(entry, now):
                entry.finished_sent = True
                self._emit({"event": "finished", "id": entry.job_id})
                self._request_save(entry)
        self._emit({"event": "status", "torrents": torrents})

    def _flushed(self, entry: _Entry, now: float) -> bool:
        if entry.flushed:
            return True
        if entry.flush_requested_at is None:
            entry.flush_requested_at = now
            entry.handle.flush_cache()
            return False
        return now - entry.flush_requested_at >= FLUSH_WAIT

    def _status(self, entry: _Entry) -> dict[str, object]:
        lt = self._lt
        status = entry.handle.status()
        paused = bool(int(status.flags) & int(lt.torrent_flags.paused))
        state = "paused" if paused else _STATE_NAMES.get(int(status.state), "downloading")
        if status.errc.value() != 0 or status.error:
            state = "error"
        result: dict[str, object] = {
            "id": entry.job_id,
            "state": state,
            "paused": paused,
            "progress": float(status.progress),
            "size": int(status.total_wanted),
            "downloaded": int(status.total_wanted_done),
            "down": int(status.download_payload_rate),
            "up": int(status.upload_payload_rate),
            "peers": int(status.num_peers),
            "seeds": int(status.num_seeds),
            "uploaded": int(status.all_time_upload),
            "seeding_seconds": int(status.seeding_duration.total_seconds()),
            "finished": bool(status.is_finished),
            "tracker": redact_tracker(str(status.current_tracker)),
        }
        if state == "error":
            result["message"] = MSG_TORRENT_ERROR
        return result

    def _handle_alerts(self) -> None:
        lt = self._lt
        for alert in self._session.pop_alerts():
            if isinstance(alert, lt.save_resume_data_alert):
                self._pending_saves = max(0, self._pending_saves - 1)
                entry = self._entry_for(alert.handle)
                if entry is not None:
                    try:
                        _write_atomic(os.path.join(entry.folder, "resume.dat"), lt.write_resume_data_buf(alert.params))
                    except OSError:
                        continue
                    self._emit({"event": "saved", "id": entry.job_id})
            elif isinstance(alert, lt.save_resume_data_failed_alert):
                self._pending_saves = max(0, self._pending_saves - 1)
            elif isinstance(alert, lt.cache_flushed_alert):
                entry = self._entry_for(alert.handle)
                if entry is not None and entry.flush_requested_at is not None:
                    entry.flushed = True

    def _entry_for(self, handle: lt_types.torrent_handle) -> _Entry | None:
        for entry in self._entries.values():
            if entry.handle == handle:
                return entry
        return None

    def _request_save(self, entry: _Entry) -> None:
        handle = entry.handle
        if handle.is_valid():
            self._pending_saves += 1
            handle.save_resume_data()

    def shutdown(self, wait: float = SHUTDOWN_WAIT) -> None:
        """Save resume data for every torrent, then stop the session."""
        for entry in self._entries.values():
            self._request_save(entry)
        deadline = time.monotonic() + wait
        while self._pending_saves > 0 and time.monotonic() < deadline:
            self._handle_alerts()
            time.sleep(0.05)
        self._entries.clear()


# libtorrent's torrent_status.state values.
_STATE_NAMES = {
    0: "checking",
    1: "checking",
    2: "metadata",
    3: "downloading",
    4: "seeding",
    5: "seeding",
    6: "checking",
    7: "checking",
}


def _limit(kib: object) -> int:
    """Bytes per second for a KiB setting; 0 means unlimited."""
    if isinstance(kib, bool) or not isinstance(kib, (int, float)):
        return 0
    return max(0, int(kib)) * 1024


def _emit_to(out: TextIO) -> Emit:
    lock = threading.Lock()

    def emit(event: dict[str, object]) -> None:
        line = json.dumps(event, separators=(",", ":")) + "\n"
        with lock:
            _ = out.write(line)
            out.flush()

    return emit


def _protocol_streams() -> tuple[TextIO, TextIO]:
    """The pipes to the parent, bypassing the app log's stdout copy.

    `setup_app_log` replaces `sys.stdout` with a tee into `wlib.log`, which
    would log every event line. Events go to the original stdout instead, and
    stray prints go to stderr so they can never corrupt the protocol.
    """
    source = sys.__stdin__ or sys.stdin
    out = sys.__stdout__ or sys.stdout
    sys.stdout = sys.stderr
    return source, out


def run(stdin: TextIO | None = None, stdout: TextIO | None = None) -> int:
    """Serve commands until stdin closes (the parent is gone) or `shutdown` arrives."""
    if stdin is None or stdout is None:
        default_in, default_out = _protocol_streams()
        stdin = stdin if stdin is not None else default_in
        stdout = stdout if stdout is not None else default_out
    source = stdin
    emit = _emit_to(stdout)
    lines: queue.Queue[str | None] = queue.Queue()

    def read() -> None:
        for line in source:
            lines.put(line)
        lines.put(None)

    threading.Thread(target=read, daemon=True).start()
    engine: Engine | None = None
    next_tick = time.monotonic() + STATUS_INTERVAL
    while True:
        try:
            line = lines.get(timeout=0.2)
        except queue.Empty:
            line = ""
        if line is None:
            if engine is not None:
                engine.shutdown()
            return 0
        if time.monotonic() >= next_tick:
            next_tick = time.monotonic() + STATUS_INTERVAL
            if engine is not None:
                engine.tick()
        line = line.strip()
        if not line:
            continue
        try:
            parsed = cast(object, json.loads(line))
        except ValueError:
            emit({"event": "error", "message": MSG_BAD_COMMAND})
            continue
        if not isinstance(parsed, dict):
            emit({"event": "error", "message": MSG_BAD_COMMAND})
            continue
        command = cast(dict[str, object], parsed)
        name = command.get("cmd")
        if name == "hello":
            if engine is None:
                try:
                    engine = Engine(emit, command)
                except ImportError:
                    emit({"event": "error", "message": MSG_UNAVAILABLE, "code": "engine_missing"})
                    return EXIT_ENGINE_MISSING
            emit({"event": "ready", "port": engine.port})
        elif name == "shutdown":
            if engine is not None:
                engine.shutdown()
            emit({"event": "bye"})
            return 0
        elif engine is None:
            emit({"event": "error", "message": MSG_BAD_COMMAND})
        else:
            engine.handle(command)
