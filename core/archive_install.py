# SPDX-License-Identifier: GPL-3.0-or-later
"""Install games from downloaded archives (.zip/.7z/.rar)."""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Callable

from core.database import DEFAULT_LIBRARY_FOLDER_PATTERN

_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

_INVALID_SEGMENT_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

ARCHIVE_EXTENSIONS = (".zip", ".7z", ".rar")
STAGING_PREFIX = ".wlib-staging-"


class ArchiveInstallError(Exception):
    """Base failure for archive installs (message is user-facing)."""


class ArchiveSafetyError(ArchiveInstallError):
    """Archive tried to write outside the destination."""


def sanitize_path_segment(segment: str) -> str:
    """Sanitize one path segment for Windows + Linux."""
    cleaned = _INVALID_SEGMENT_CHARS_RE.sub("_", segment)
    cleaned = cleaned.strip().strip(". ")
    cleaned = cleaned.strip()
    if not cleaned:
        return ""
    base = cleaned.split(".")[0].upper()
    if base in _WINDOWS_RESERVED_NAMES:
        cleaned = f"_{cleaned}"
    cleaned = re.sub(r"_+", "_", cleaned)
    return cleaned.strip()


def build_destination(
    library_folder: str,
    pattern: str,
    creator: str = "",
    title: str = "",
    version: str = "",
    engine: str = "",
) -> str:
    """Compute `<library>/<pattern>` with token substitution + sanitizing."""
    base = os.path.abspath(os.path.expanduser((library_folder or "").strip()))
    raw_pattern = (pattern or "").strip() or DEFAULT_LIBRARY_FOLDER_PATTERN
    parts = re.split(r"[/\\]+", raw_pattern)
    tokens = {
        "creator": (creator or "").strip(),
        "title": (title or "").strip(),
        "version": (version or "").strip(),
        "engine": (engine or "").strip(),
    }
    segments: list[str] = []
    for part in parts:
        substituted = part
        for key, value in tokens.items():
            substituted = substituted.replace("{" + key + "}", value)
        substituted = re.sub(r"\{[^{}]*\}", "", substituted)
        for sub in re.split(r"[/\\]+", substituted):
            cleaned = sanitize_path_segment(sub)
            if cleaned:
                segments.append(cleaned)
    if not segments:
        fallback = sanitize_path_segment(tokens["title"])
        segments.append(fallback or "game")
    if base:
        return os.path.join(base, *segments)
    return os.path.join(*segments)


def archive_format(archive_path: str) -> str:
    """'zip' | '7z' | 'rar' | '' based on the file extension."""
    lower = (archive_path or "").lower()
    if lower.endswith(".zip"):
        return "zip"
    if lower.endswith(".7z"):
        return "7z"
    if lower.endswith(".rar"):
        return "rar"
    return ""


def _is_windows(platform: str | None = None) -> bool:
    if platform is not None:
        return platform.lower().startswith("win")
    return sys.platform.startswith("win")


def detect_archive_tool(
    archive_path: str, *, platform: str | None = None
) -> tuple[str | None, str | None]:
    """Return (tool, error) for an archive.

    `.zip` needs no tool → (None, None). For `.7z`/`.rar`, search
    `7zz`/`7z`/`7za`, then `bsdtar`, then `unrar` (rar only), plus the
    Windows `Program Files\\7-Zip` paths. Uses `shutil.which` and
    `os.path.isfile` so tests can monkeypatch them.
    """
    fmt = archive_format(archive_path)
    if fmt == "zip":
        return None, None
    if fmt not in ("7z", "rar"):
        return None, f"Unsupported archive format: {archive_path or '(none)'}"

    for candidate in ("7zz", "7z", "7za"):
        found = shutil.which(candidate)
        if found:
            return found, None
    found = shutil.which("bsdtar")
    if found:
        return found, None
    if fmt == "rar":
        found = shutil.which("unrar")
        if found:
            return found, None

    if _is_windows(platform):
        program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
        program_files_x86 = os.environ.get(
            "ProgramFiles(x86)", r"C:\Program Files (x86)"
        )
        for base in (program_files, program_files_x86):
            candidate = os.path.join(base, "7-Zip", "7z.exe")
            try:
                if os.path.isfile(candidate):
                    return candidate, None
            except OSError:
                continue
        return None, "Install 7-Zip to extract .7z/.rar archives."
    if fmt == "rar":
        return None, "Install 7-Zip (7z), bsdtar, or unrar to extract .rar archives."
    return None, "Install 7-Zip (7z) or bsdtar to extract .7z archives."


def _is_absolute_entry(entry: str) -> bool:
    normalized = entry.replace("\\", "/")
    if os.path.isabs(entry) or os.path.isabs(normalized):
        return True
    if normalized.startswith("/"):
        return True
    # Windows drive-absolute (C:/, C:\) or UNC (//server).
    if re.match(r"^[a-zA-Z]:[\\/]", entry) or re.match(r"^[a-zA-Z]:[\\/]", normalized):
        return True
    return False


def check_archive_entry_safe(entry: str) -> None:
    """Raise ArchiveSafetyError for absolute paths or `..` components."""
    if not entry or entry.strip() == "":
        raise ArchiveSafetyError(f"Archive entry has an empty name: {entry!r}")
    normalized = entry.replace("\\", "/")
    if _is_absolute_entry(entry) or _is_absolute_entry(normalized):
        raise ArchiveSafetyError(f"Archive entry is unsafe (absolute path): {entry!r}")
    parts = [p for p in normalized.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise ArchiveSafetyError(f"Archive entry is unsafe: {entry!r}")


def _target_inside_staging(staging: str, entry: str) -> str:
    normalized = entry.replace("\\", "/")
    # Strip a trailing slash for directories; join handles the rest.
    target = os.path.abspath(os.path.join(staging, normalized))
    staging_real = os.path.abspath(staging)
    if target != staging_real and not target.startswith(staging_real + os.sep):
        raise ArchiveSafetyError(f"Archive entry is unsafe: {entry!r}")
    return target


def build_host_open_env() -> dict[str, str]:
    """Env for archive subprocesses (no AppImage library paths)."""
    clean_env: dict[str, str] = os.environ.copy()
    for var in (
        "APPIMAGE",
        "APPDIR",
        "ARGV0",
        "APPIMAGE_SILENT_INSTALL",
        "OWD",
        "APPIMAGE_EXTRACT_AND_RUN",
    ):
        clean_env.pop(var, None)
    original_library_path = str(clean_env.get("LD_LIBRARY_PATH_ORIG") or "").strip()
    if original_library_path:
        clean_env["LD_LIBRARY_PATH"] = original_library_path
    else:
        clean_env.pop("LD_LIBRARY_PATH", None)
    return clean_env


def extract_zip(archive_path: str, staging_dir: str) -> int:
    """Extract a zip into staging with per-entry validation.

    Returns the number of entries extracted. Raises ArchiveSafetyError for
    traversal entries and ArchiveInstallError for corrupt archives.
    """
    import zipfile

    os.makedirs(staging_dir, exist_ok=True)
    count = 0
    try:
        with zipfile.ZipFile(archive_path, "r") as zf:
            infos = zf.infolist()
            # Pre-validate every entry before writing anything.
            for info in infos:
                name = info.filename.replace("\\", "/")
                if name == "" or name.endswith("/") and name.strip("/") == "":
                    raise ArchiveSafetyError(f"Archive entry is unsafe: {name!r}")
                check_archive_entry_safe(name)
                _target_inside_staging(staging_dir, name)
            for info in infos:
                name = info.filename.replace("\\", "/")
                target = _target_inside_staging(staging_dir, name)
                if name.endswith("/"):
                    os.makedirs(target, exist_ok=True)
                    count += 1
                    continue
                os.makedirs(os.path.dirname(target) or staging_dir, exist_ok=True)
                # Symlink entries are written as plain files holding the link
                # text, so a zip can't plant links (deliberate, see design).
                mode = (info.external_attr >> 16) & 0o7777
                with zf.open(info, "r") as src, open(target, "wb") as out:
                    _ = shutil.copyfileobj(src, out)
                if mode and not _is_windows():
                    try:
                        os.chmod(target, mode)
                    except OSError:
                        pass
                count += 1
    except zipfile.BadZipFile as exc:
        raise ArchiveInstallError(f"Archive is corrupt or incomplete: {exc}") from exc
    check_symlinks_inside_staging(staging_dir)
    return count


def _tool_basename(tool: str) -> str:
    return os.path.basename(tool).lower()


def list_tool_entries(tool: str, archive_path: str) -> list[str]:
    """List archive entries via the external tool (for the path pre-check)."""
    base = _tool_basename(tool)
    env = build_host_open_env()
    if "unrar" in base:
        completed = subprocess.run(
            [tool, "lb", archive_path],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        if completed.returncode != 0:
            raise ArchiveInstallError(
                f"Could not list archive contents: {(completed.stderr or '').strip() or f'exit {completed.returncode}'}"
            )
        return [
            line.strip()
            for line in (completed.stdout or "").splitlines()
            if line.strip()
        ]
    if "bsdtar" in base or base == "tar":
        completed = subprocess.run(
            [tool, "-tf", archive_path],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        if completed.returncode != 0:
            raise ArchiveInstallError(
                f"Could not list archive contents: {(completed.stderr or '').strip() or f'exit {completed.returncode}'}"
            )
        entries = []
        for line in (completed.stdout or "").splitlines():
            line = line.strip()
            if line.startswith("./"):
                line = line[2:]
            if line:
                entries.append(line)
        return entries
    # 7z/7zz/7za (also a raw 7z.exe path on Windows).
    completed = subprocess.run(
        [tool, "l", "-slt", archive_path],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    if completed.returncode != 0:
        raise ArchiveInstallError(
            f"Could not list archive contents: {(completed.stderr or '').strip() or f'exit {completed.returncode}'}"
        )
    entries = []
    archive_real = os.path.abspath(archive_path)
    for line in (completed.stdout or "").splitlines():
        if line.startswith("Path = "):
            path = line[len("Path = ") :].strip()
            # 7z lists the archive itself as the first entry; it is never a member.
            if os.path.abspath(path) == archive_real:
                continue
            entries.append(path)
    return entries


def check_symlinks_inside_staging(staging_dir: str) -> None:
    """Reject symlinks whose resolved target escapes staging."""
    staging_real = os.path.realpath(os.path.abspath(staging_dir))
    for root, _dirs, files in os.walk(staging_dir, followlinks=False):
        candidates: list[str] = []
        try:
            with os.scandir(root) as it:
                for entry in it:
                    try:
                        if entry.is_symlink():
                            candidates.append(entry.path)
                    except OSError:
                        continue
        except OSError:
            continue
        # os.walk already yields files; also check dir symlinks via scandir above.
        for path in candidates:
            target_real = os.path.realpath(path)
            if target_real != staging_real and not target_real.startswith(
                staging_real + os.sep
            ):
                raise ArchiveSafetyError(
                    f"Archive link points outside the destination: {os.path.relpath(path, staging_dir)!r}"
                )
        # Belt and braces: files list from walk for platforms where scandir missed.
        for name in files:
            path = os.path.join(root, name)
            try:
                if os.path.islink(path):
                    target_real = os.path.realpath(path)
                    if target_real != staging_real and not target_real.startswith(
                        staging_real + os.sep
                    ):
                        raise ArchiveSafetyError(
                            f"Archive link points outside the destination: {os.path.relpath(path, staging_dir)!r}"
                        )
            except OSError:
                continue


def extract_with_tool(tool: str, archive_path: str, staging_dir: str) -> int:
    """Extract via an external tool after a listing-based safety pre-check."""
    os.makedirs(staging_dir, exist_ok=True)
    entries = list_tool_entries(tool, archive_path)
    for entry in entries:
        # 7z lists the archive itself as "." or empty in some outputs; skip those.
        if entry.strip() in ("", ".", "./"):
            continue
        check_archive_entry_safe(entry)
    base = _tool_basename(tool)
    env = build_host_open_env()
    if "unrar" in base:
        cmd = [tool, "x", "-o+", archive_path, staging_dir + os.sep]
    elif "bsdtar" in base or base == "tar":
        cmd = [tool, "-xf", archive_path, "-C", staging_dir]
    else:
        cmd = [tool, "x", archive_path, f"-o{staging_dir}", "-y"]
    try:
        completed = subprocess.run(
            cmd, capture_output=True, text=True, check=False, env=env
        )
    except OSError as exc:
        raise ArchiveInstallError(f"Extraction failed: {exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or "").strip() or f"exit {completed.returncode}"
        raise ArchiveInstallError(f"Extraction failed ({detail})")
    check_symlinks_inside_staging(staging_dir)
    return len(entries)


def apply_execute_bits(root: str) -> int:
    """Linux execute-bit pass: .sh/.x86/.x86_64 or ELF files gain +x.

    Returns the number of files made executable. Uses `is_elf` so zips made
    on Windows (no mode data) still launch.
    """
    from core.launcher import is_elf

    changed = 0
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            path = os.path.join(dirpath, name)
            try:
                if os.path.islink(path):
                    continue
                mode = os.stat(path).st_mode
            except OSError:
                continue
            if mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
                continue
            lower = name.lower()
            needs_exec = lower.endswith((".sh", ".x86", ".x86_64")) or is_elf(path)
            if not needs_exec:
                continue
            new_mode = mode | stat.S_IXUSR
            if mode & stat.S_IRGRP:
                new_mode |= stat.S_IXGRP
            if mode & stat.S_IROTH:
                new_mode |= stat.S_IXOTH
            try:
                os.chmod(path, new_mode)
                changed += 1
            except OSError:
                continue
    return changed


def flatten_single_wrapper(staging_dir: str) -> bool:
    """Move a single top-level wrapper folder's contents up one level."""
    try:
        entries = os.listdir(staging_dir)
    except OSError:
        return False
    if len(entries) != 1:
        return False
    wrapper = os.path.join(staging_dir, entries[0])
    if not os.path.isdir(wrapper) or os.path.islink(wrapper):
        return False
    try:
        inner = os.listdir(wrapper)
    except OSError:
        return False
    for name in inner:
        dst = os.path.join(staging_dir, name)
        if os.path.lexists(dst):
            return False
    try:
        for name in inner:
            shutil.move(os.path.join(wrapper, name), os.path.join(staging_dir, name))
        os.rmdir(wrapper)
    except OSError:
        return False
    return True


def create_staging_dir(library_folder: str) -> str:
    """Create `<library>/.wlib-staging-<random>/` (same filesystem for rename)."""
    base = os.path.abspath(os.path.expanduser(library_folder))
    os.makedirs(base, exist_ok=True)
    path = tempfile.mkdtemp(prefix=STAGING_PREFIX, dir=base)
    if not _is_windows():
        # mkdtemp is 0o700 and staging is renamed into the game folder.
        os.chmod(path, 0o755)
    return path


def cleanup_staging_dir(path: str) -> None:
    try:
        if path and os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path, ignore_errors=True)
    except OSError:
        pass


def cleanup_stale_staging(library_folder: str) -> int:
    """Remove leftover `.wlib-staging-*` folders from crashed installs."""
    removed = 0
    try:
        base = os.path.abspath(os.path.expanduser(library_folder))
        if not os.path.isdir(base):
            return 0
        for name in os.listdir(base):
            if not name.startswith(STAGING_PREFIX):
                continue
            candidate = os.path.join(base, name)
            try:
                if os.path.isdir(candidate) and not os.path.islink(candidate):
                    shutil.rmtree(candidate, ignore_errors=True)
                    removed += 1
            except OSError:
                continue
    except OSError:
        return removed
    return removed


def ensure_free_space(library_folder: str, required_bytes: int | None) -> None:
    """Fail early when the library drive cannot hold the uncompressed size."""
    if not required_bytes or required_bytes <= 0:
        return
    try:
        base = os.path.abspath(os.path.expanduser(library_folder))
        os.makedirs(base, exist_ok=True)
        free = shutil.disk_usage(base).free
    except OSError:
        return
    if free < required_bytes:
        required_gb = required_bytes / (1024**3)
        free_gb = free / (1024**3)
        raise ArchiveInstallError(
            f"Not enough free space: need {required_gb:.1f} GB, have {free_gb:.1f} GB."
        )


def uncompressed_size(archive_path: str, tool: str | None = None) -> int | None:
    """Best-effort uncompressed size in bytes (None when unknown)."""
    import zipfile

    fmt = archive_format(archive_path)
    if fmt == "zip":
        try:
            with zipfile.ZipFile(archive_path, "r") as zf:
                return sum(info.file_size for info in zf.infolist())
        except OSError:
            return None
    # 7z listing carries sizes, but parsing is tool-specific; skip for now and
    # let ensure_free_space no-op when unknown.
    return None


INFOLDER_SAVE_DIRS = (
    os.path.join("game", "saves"),
    os.path.join("www", "save"),
    "save",
    "saves",
    "Save",
    "Saves",
    "SaveData",
    "savedata",
)
LOOSE_SAVE_PATTERNS = ("*.sav", "*.rpgsave", "*.rmmzsave", "*.save")


def infolder_save_relpaths(root: str) -> list[str]:
    """Relative in-folder save paths under root (dirs + loose save files)."""
    import glob

    found: list[str] = []
    for rel in INFOLDER_SAVE_DIRS:
        if os.path.isdir(os.path.join(root, rel)):
            found.append(rel)
    for pattern in LOOSE_SAVE_PATTERNS:
        for path in glob.glob(os.path.join(root, pattern)):
            try:
                if os.path.isfile(path) and not os.path.islink(path):
                    found.append(os.path.relpath(path, root))
            except OSError:
                continue
    return found


def copy_infolder_saves(old_root: str, new_root: str) -> list[str]:
    """Copy in-folder saves from old_root into new_root, overwriting.

    Returns the copied relative paths. Raises ArchiveInstallError on failure
    so the caller can roll back before touching the library.
    """
    copied: list[str] = []
    for rel in infolder_save_relpaths(old_root):
        src = os.path.join(old_root, rel)
        dst = os.path.join(new_root, rel)
        try:
            if os.path.isdir(src) and not os.path.islink(src):
                os.makedirs(os.path.dirname(dst) or new_root, exist_ok=True)
                shutil.copytree(
                    src, dst, dirs_exist_ok=True, copy_function=shutil.copy2
                )
                copied.append(rel)
            elif os.path.isfile(src):
                os.makedirs(os.path.dirname(dst) or new_root, exist_ok=True)
                shutil.copy2(src, dst)
                copied.append(rel)
        except OSError as exc:
            raise ArchiveInstallError(
                f"Could not carry saves into the new version: {exc}"
            ) from exc
    return copied


def _real(path: str) -> str:
    try:
        return os.path.realpath(os.path.abspath(os.path.expanduser(path)))
    except OSError:
        return os.path.abspath(os.path.expanduser(path))


def _is_root(path_real: str) -> bool:
    # Filesystem/drive root: parent == itself (posix "/") or drive root (nt "C:\\").
    parent = os.path.dirname(path_real.rstrip(os.sep))
    if parent == path_real.rstrip(os.sep):
        return True
    drive, _tail = os.path.splitdrive(path_real)
    if drive and path_real.rstrip(os.sep).lower() == drive.lower():
        return True
    return False


def _is_within(child_real: str, parent_real: str) -> bool:
    if child_real == parent_real:
        return True
    return child_real.startswith(parent_real.rstrip(os.sep) + os.sep)


def can_remove_old_folder(
    candidate: str,
    new_destination: str,
    library_folder: str = "",
    downloads_folder: str = "",
    other_paths: list[str] | tuple[str, ...] | None = None,
    old_exe: str = "",
) -> dict[str, object]:
    """Guard for deleting the previous install folder.

    Returns {"removable": bool, "reason": str}; reason is "" when removable.
    """
    cand_real = _real(candidate) if candidate else ""
    if not candidate or not cand_real:
        return {"removable": False, "reason": "The previous folder is unknown."}
    if _is_root(cand_real):
        return {"removable": False, "reason": "Refusing to remove a drive root."}
    home_real = _real("~")
    if cand_real == home_real:
        return {"removable": False, "reason": "Refusing to remove your home folder."}
    if library_folder and cand_real == _real(library_folder):
        return {"removable": False, "reason": "Refusing to remove the library folder."}
    if downloads_folder and cand_real == _real(downloads_folder):
        return {
            "removable": False,
            "reason": "Refusing to remove the Downloads folder.",
        }
    if new_destination:
        new_real = _real(new_destination)
        # Equal folders = in-place replace: what gets removed is the renamed
        # .wlib-old-* backup, not the destination itself.
        if cand_real != new_real and _is_within(new_real, cand_real):
            return {
                "removable": False,
                "reason": "Refusing to remove a parent of the new install.",
            }
    if old_exe:
        old_exe_real = _real(old_exe)
        try:
            exe_exists = os.path.isfile(old_exe)
        except OSError:
            exe_exists = False
        if not exe_exists or not _is_within(old_exe_real, cand_real):
            return {
                "removable": False,
                "reason": "The old folder no longer contains the game's executable.",
            }
    else:
        # Without an exe to anchor on, refuse empty/missing candidates only.
        if not os.path.isdir(candidate):
            return {"removable": False, "reason": "The previous folder is missing."}
    for other in other_paths or []:
        if not other:
            continue
        other_real = _real(str(other))
        if _is_within(other_real, cand_real):
            return {
                "removable": False,
                "reason": "The folder contains another library game.",
            }
    return {"removable": True, "reason": ""}


def _relative_inside(path: str, root: str) -> str | None:
    """Relative path of `path` inside `root`, or None when outside."""
    path_real = _real(path)
    root_real = _real(root)
    if path_real == root_real:
        return ""
    prefix = root_real.rstrip(os.sep) + os.sep
    if path_real.startswith(prefix):
        return os.path.relpath(path_real, root_real)
    return None


def relocate_launch_targets(
    old_root: str,
    new_root: str,
    targets: list[dict[str, object]],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Repoint targets inside old_root to the same relative path in new_root.

    Returns (updated, unmoved): updated holds the new target dicts (with
    exe_path rewritten when the file exists in the new install); unmoved
    holds the targets left unchanged because the file is missing there.
    Targets outside old_root are left unchanged and not reported.
    """
    updated: list[dict[str, object]] = []
    unmoved: list[dict[str, object]] = []
    for target in targets:
        exe = str(target.get("exe_path") or "")
        rel = _relative_inside(exe, old_root) if old_root and exe else None
        if rel is None or rel == "":
            updated.append(dict(target))
            continue
        candidate = os.path.join(new_root, rel)
        if os.path.isfile(candidate):
            next_target = dict(target)
            next_target["exe_path"] = candidate
            updated.append(next_target)
        else:
            updated.append(dict(target))
            unmoved.append(dict(target))
    return updated, unmoved


def remove_tree_collect(path: str) -> list[str]:
    """Remove a tree, collecting errors instead of aborting halfway."""
    errors: list[str] = []

    def _onerror(_func: object, p: str, _exc: object) -> None:
        errors.append(str(p))

    try:
        shutil.rmtree(path, onexc=_onerror)
    except OSError as exc:
        errors.append(str(exc))
    return errors


def other_game_paths(exclude_id: int | None = None) -> list[str]:
    from core.database import get_all_games, list_launch_targets_for_games

    paths: list[str] = []
    try:
        games = get_all_games()
    except OSError:
        return []
    ids: list[int] = []
    for game in games:
        try:
            gid = int(str(game.get("id", 0)))
        except (TypeError, ValueError):
            continue
        if exclude_id is not None and gid == exclude_id:
            continue
        ids.append(gid)
        exe = str(game.get("exe_path") or "")
        if exe:
            paths.append(exe)
    if ids:
        try:
            by_game = list_launch_targets_for_games(ids)
            for targets in by_game.values():
                for target in targets:
                    exe = str(target.get("exe_path") or "")
                    if exe:
                        paths.append(exe)
        except OSError:
            pass
    return paths


def _pick_exe(destination: str, old_exe: str = "", old_root: str = "") -> str:
    from core.game_scan import find_launcher

    if old_exe and old_root:
        rel = _relative_inside(old_exe, old_root)
        if rel:
            candidate = os.path.join(destination, rel)
            if os.path.isfile(candidate):
                return candidate
    return find_launcher(destination)


def install_archive(
    archive_path: str,
    *,
    library_folder: str,
    pattern: str,
    title: str,
    version: str = "",
    creator: str = "",
    engine: str = "",
    game_id: int | None = None,
    remove_old: bool = True,
    delete_archive: bool = False,
    progress_cb: Callable[[str], None] | None = None,
) -> dict[str, object]:
    """Full install orchestration per design §7 (filesystem + DB).

    Returns {"game_id", "exe_path", "destination", "unmoved_targets",
    "kept_old_folder_reason", "removed_old", "old_folder"}.
    Raises ArchiveInstallError on failure; staging is always cleaned and the
    previous install is untouched until the new tree is in place.
    """
    import time

    from core.database import (
        get_all_games,
        list_game_launch_targets,
        update_game_launch_target,
    )

    def _progress(stage: str) -> None:
        if progress_cb is not None:
            try:
                progress_cb(stage)
            except Exception:
                pass

    if not archive_path or not os.path.isfile(archive_path):
        raise ArchiveInstallError("Archive file was not found.")
    fmt = archive_format(archive_path)
    if fmt not in ("zip", "7z", "rar"):
        raise ArchiveInstallError(
            "Unsupported archive format. Choose a .zip, .7z, or .rar file."
        )
    if not title.strip():
        raise ArchiveInstallError("A title is required.")
    if not library_folder.strip():
        raise ArchiveInstallError("Choose a library folder first.")

    destination = build_destination(
        library_folder, pattern, creator, title, version, engine
    )

    old_exe = ""
    old_root = ""
    if game_id is not None:
        for game in get_all_games():
            try:
                if int(str(game.get("id"))) == int(game_id):
                    old_exe = str(game.get("exe_path") or "")
                    break
            except (TypeError, ValueError):
                continue
        if old_exe:
            old_root = os.path.dirname(os.path.abspath(old_exe))

    from core.database import get_setting

    downloads_folder = ""
    try:
        downloads_folder = str(get_setting("downloads_folder") or "")
    except OSError:
        downloads_folder = ""
    guard = (
        can_remove_old_folder(
            old_root,
            destination,
            library_folder,
            downloads_folder,
            other_game_paths(game_id),
            old_exe,
        )
        if old_root
        else {"removable": False, "reason": ""}
    )

    dest_real = _real(destination)
    old_real = _real(old_root) if old_root else ""
    dest_exists = os.path.lexists(destination)
    if dest_exists and dest_real != old_real:
        raise ArchiveInstallError(
            "The destination folder already exists. Change the version or destination."
        )

    _progress("preparing")
    _ = cleanup_stale_staging(library_folder)
    tool, tool_error = detect_archive_tool(archive_path)
    if tool_error:
        raise ArchiveInstallError(tool_error)
    ensure_free_space(library_folder, uncompressed_size(archive_path, tool))
    staging = create_staging_dir(library_folder)
    try:
        _progress("extracting")
        if fmt == "zip":
            _ = extract_zip(archive_path, staging)
        else:
            assert tool is not None
            _ = extract_with_tool(tool, archive_path, staging)
        _progress("fixing permissions")
        try:
            _ = apply_execute_bits(staging)
        except OSError:
            pass
        _ = flatten_single_wrapper(staging)

        if old_root and os.path.isdir(old_root):
            _progress("carrying saves")
            _ = copy_infolder_saves(old_root, staging)

        _progress("moving into place")
        old_backup = ""
        try:
            os.makedirs(os.path.dirname(destination) or destination, exist_ok=True)
        except OSError:
            pass
        if old_root and dest_real == old_real:
            # Pattern without {version}: swap via .wlib-old-<ts>.
            old_backup = f"{destination}.wlib-old-{int(time.time())}"
            try:
                os.rename(destination, old_backup)
            except OSError as exc:
                raise ArchiveInstallError(
                    f"Could not replace the current folder: {exc}"
                ) from exc
            old_root_for_targets = old_backup
            try:
                os.rename(staging, destination)
            except OSError as exc:
                try:
                    os.rename(old_backup, destination)
                except OSError:
                    pass
                raise ArchiveInstallError(
                    f"Could not move the new version into place: {exc}"
                ) from exc
            staging = ""  # moved
        else:
            try:
                os.rename(staging, destination)
            except OSError as exc:
                raise ArchiveInstallError(
                    f"Could not move the new version into place: {exc}"
                ) from exc
            staging = ""
            old_root_for_targets = old_root
            old_backup = ""
    except Exception:
        if staging:
            cleanup_staging_dir(staging)
        raise

    # Destination is in place from here on; failures below keep the update.
    kept_reason = ""
    unmoved: list[dict[str, object]] = []
    try:
        _progress("updating library")
        from core.game_scan import detect_engine as _detect_engine

        exe_path = _pick_exe(destination, old_exe, old_backup or old_root)
        if not exe_path:
            raise ArchiveInstallError(
                "No launchable file was found. The extracted folder was kept; choose the executable manually."
            )
        detected_engine = ""
        try:
            detected_engine = _detect_engine(os.path.dirname(exe_path), exe_path)
        except OSError:
            detected_engine = ""

        from core import database as _db

        if game_id is None:
            new_id = _db.add_game(
                title.strip(),
                exe_path,
                version=version.strip(),
                developer=creator.strip(),
                engine=engine.strip() or detected_engine,
            )
            game_id = int(str(new_id)) if new_id is not None else None
        else:
            fields: dict[str, object] = {
                "exe_path": exe_path,
                "version": version.strip(),
            }
            if not engine.strip() and detected_engine:
                # Only fill engine when the row has none (don't clobber manual edits).
                for game in get_all_games():
                    try:
                        if int(str(game.get("id"))) == int(game_id):
                            if not str(game.get("engine") or "").strip():
                                fields["engine"] = detected_engine
                            break
                    except (TypeError, ValueError):
                        continue
            _db.update_game(int(game_id), fields)
            if old_root_for_targets and os.path.isdir(destination):
                try:
                    targets = list_game_launch_targets(int(game_id))
                except OSError:
                    targets = []
                target_dicts: list[dict[str, object]] = [
                    {"label": t["label"], "exe_path": t["exe_path"], "id": t["id"]}
                    for t in targets
                ]
                updated_targets, unmoved = relocate_launch_targets(
                    old_backup or old_root, destination, target_dicts
                )
                for before, after in zip(targets, updated_targets):
                    if str(before.get("exe_path") or "") != str(
                        after.get("exe_path") or ""
                    ):
                        try:
                            _ = update_game_launch_target(
                                int(before["id"]), {"exe_path": str(after["exe_path"])}
                            )
                        except (OSError, ValueError):
                            unmoved.append(dict(before))

        _progress("cleaning up")
        removed_old = False
        if game_id is not None and old_root and remove_old:
            if guard.get("removable"):
                cleanup_root = old_backup or old_root
                if _real(cleanup_root) != dest_real:
                    errors = remove_tree_collect(cleanup_root)
                    if errors:
                        kept_reason = (
                            f"Could not fully remove {cleanup_root}: {errors[0]}"
                        )
                    else:
                        removed_old = True
                else:
                    kept_reason = "The old and new folders are the same."
            else:
                kept_reason = str(guard.get("reason") or "")
        elif old_backup:
            # Same-folder update with remove unticked: the old contents were
            # already replaced, so report where the renamed backup was kept.
            kept_reason = f"Previous contents kept at {old_backup}."

        # Archive deletion is always last and only on success.
        if delete_archive:
            try:
                os.remove(archive_path)
            except OSError as exc:
                kept_reason = (
                    f"{kept_reason}; could not delete archive: {exc}"
                    if kept_reason
                    else f"Could not delete archive: {exc}"
                )

        return {
            "success": True,
            "game_id": game_id,
            "exe_path": exe_path,
            "destination": destination,
            "unmoved_targets": unmoved,
            "kept_old_folder_reason": kept_reason,
            "removed_old": removed_old,
            "old_folder": old_root,
        }
    except Exception:
        # Step 5/6 failure after the move: the new tree stands (per design a
        # step-6 failure still counts as success), except the no-launcher and
        # DB-update cases which must not leave a half-pointing row. For
        # no-launcher the folder is intentionally kept; re-raise.
        raise


def _normalize_version(version: object) -> str:
    """Treat v0.8, 0.8 and "V 0.8 " as equal; mirrors normalizeVersion() in the UI."""
    value = "".join(str(version or "").split()).lower()
    return re.sub(r"^v(?=\d)", "", value)


def _squash_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def _dismissal_key(path: str, size: int, mtime: float) -> str:
    return f"{path}|{size}|{mtime}"


def _load_dismissals() -> list[str]:
    import json

    from core.database import get_setting

    try:
        raw = get_setting("dismissed_download_archives") or "[]"
    except OSError:
        return []
    try:
        raw_items: object = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(raw_items, list):
        return []
    return [str(item) for item in raw_items if isinstance(item, (str, int, float))]


def dismiss_download_offer(key: str) -> None:
    """Persist a dismissal key, capped at the 200 most recent."""
    import json

    from core.database import update_setting

    key = (key or "").strip()
    if not key:
        return
    items = [k for k in _load_dismissals() if k != key]
    items.insert(0, key)
    try:
        update_setting("dismissed_download_archives", json.dumps(items[:200]))
    except OSError:
        pass


def find_download_offers(*, now: float | None = None) -> list[dict[str, object]]:
    """Scan the Downloads folder for archives matching pending updates."""
    import time as _time

    from core.database import get_all_games, get_setting
    from core.game_scan import guess_title_and_version

    try:
        enabled = (get_setting("download_detection_enabled") or "true") == "true"
    except OSError:
        enabled = True
    if not enabled:
        return []
    try:
        folder = (get_setting("downloads_folder") or "").strip()
    except OSError:
        return []
    if not folder or not os.path.isdir(folder):
        return []

    try:
        names = os.listdir(folder)
    except OSError:
        return []
    dismissals = set(_load_dismissals())
    current = now if now is not None else _time.time()

    pending: list[dict[str, object]] = []
    try:
        for game in get_all_games():
            installed = str(game.get("version") or "")
            latest = str(game.get("latest_version") or "")
            if not latest.strip():
                continue
            if _normalize_version(latest) == _normalize_version(installed):
                continue
            try:
                gid = int(str(game.get("id", 0)))
            except (TypeError, ValueError):
                continue
            pending.append(
                {
                    "id": gid,
                    "title": str(game.get("title") or ""),
                    "key": _squash_key(str(game.get("title") or "")),
                    "version": installed,
                    "latest_version": latest,
                }
            )
    except OSError:
        return []
    if not pending:
        return []
    by_key: dict[str, list[dict[str, object]]] = {}
    for game in pending:
        by_key.setdefault(str(game["key"]), []).append(game)

    offers: list[dict[str, object]] = []
    for name in sorted(names):
        lower = name.lower()
        if not lower.endswith((".zip", ".7z", ".rar")):
            continue
        # Multi-part rar: offer only through the first part.
        part_match = re.search(r"\.part(\d+)\.rar$", lower)
        if part_match and int(part_match.group(1)) > 1:
            continue
        path = os.path.join(folder, name)
        try:
            if not os.path.isfile(path) or os.path.islink(path):
                continue
            stat_result = os.stat(path)
        except OSError:
            continue
        if current - stat_result.st_mtime < 15:
            continue
        key = _dismissal_key(path, stat_result.st_size, stat_result.st_mtime)
        if key in dismissals:
            continue
        stem = name
        if lower.endswith(".zip"):
            stem = name[: -len(".zip")]
        elif lower.endswith(".7z"):
            stem = name[: -len(".7z")]
        else:
            stem = name[: -len(".rar")]
        stem = re.sub(r"\.part\d+$", "", stem, flags=re.IGNORECASE)
        guessed_title, guessed_version = guess_title_and_version(stem)
        squashed = _squash_key(guessed_title)
        games = by_key.get(squashed, [])
        if not games:
            continue
        for game in games:
            if guessed_version and _normalize_version(
                guessed_version
            ) == _normalize_version(str(game["version"])):
                continue
            offers.append(
                {
                    "game_id": game["id"],
                    "game_title": game["title"],
                    "installed_version": game["version"],
                    "latest_version": game["latest_version"],
                    "archive_path": path,
                    "archive_version": guessed_version,
                    "guessed_title": guessed_title,
                    "key": key,
                }
            )
    return offers
