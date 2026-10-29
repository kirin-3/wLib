# SPDX-License-Identifier: GPL-3.0-or-later
"""Per-game saves folder: detection, override validation, backup, merge import.

Everything here takes explicit paths so it can be tested against a temp dir.
`core.api` resolves a game's folder from the stored row and calls in.
"""

from __future__ import annotations

import glob
import os
import re
import shutil
import tempfile
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import TypedDict

from core.database import get_setting
from core.host_platform import get_default_wine_prefix, is_windows
from core.launcher import proton_compat_data_path

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

BACKUP_NAME_PATTERN = re.compile(
    r"^(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})(?:-(\d+))?$", re.ASCII
)
BACKUP_TIMESTAMP_FORMAT = "%Y-%m-%d_%H-%M-%S"


class GameSavesError(Exception):
    """A save operation failed; the message is safe to show the user."""


class NothingToBackUpError(GameSavesError):
    """The folder is missing or holds no regular files."""


class SaveLocation(TypedDict):
    path: str
    type: str
    description: str
    eligible: bool


@dataclass
class BackupInfo:
    name: str
    path: str
    files: int
    bytes: int
    skipped_links: int
    pruned: list[str] = field(default_factory=list)


@dataclass
class ImportInfo:
    files: int
    bytes: int
    skipped_links: int
    backup: BackupInfo | None


def _same_path(a: str, b: str) -> bool:
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(
        os.path.realpath(b)
    )


def is_within(path: str, parent: str) -> bool:
    """True when `path` is `parent` or lies under it (symlinks resolved)."""
    child = os.path.normcase(os.path.realpath(path))
    base = os.path.normcase(os.path.realpath(parent))
    try:
        return os.path.commonpath([child, base]) == base
    except ValueError:
        return False


def paths_overlap(a: str, b: str) -> bool:
    return is_within(a, b) or is_within(b, a)


# ── Detection ────────────────────────────────────────────────────────────


def _renpy_children(base: str, title_clean: str, game_name: str) -> list[str]:
    """Folders under a Ren'Py save root whose names match the game."""
    try:
        entries = os.listdir(base)
    except OSError:
        return []
    title_key = (
        title_clean.lower().replace(" ", "").replace("'", "") if title_clean else ""
    )
    matched: list[str] = []
    for entry in entries:
        entry_lower = entry.lower()
        if (
            title_key and title_key in entry_lower.replace("-", "").replace("_", "")
        ) or (game_name and game_name.lower() in entry_lower):
            candidate = os.path.join(base, entry)
            if os.path.isdir(candidate):
                matched.append(candidate)
    return matched


def _name_matches_game(entry: str, title_clean: str, game_name: str) -> bool:
    """The Windows/Wine folder-name match used for AppData and Documents."""
    entry_lower = entry.lower()
    if game_name and game_name.lower() in entry_lower:
        return True
    words = (
        [word for word in title_clean.lower().split() if len(word) >= 4]
        if title_clean
        else []
    )
    return any(word in entry_lower for word in words)


def _nested_game_folders(
    base: str, title_clean: str, game_name: str
) -> list[str]:
    """Game folders under a container folder (LocalLow/Dev/Game, Documents/My Games/Game).

    The match is stricter than for a top-level folder: a container can hold
    many games, so one shared title word or the executable's generic stem
    must not select every sibling.
    """

    def key(value: str) -> str:
        return (
            value.lower()
            .replace(" ", "")
            .replace("'", "")
            .replace("-", "")
            .replace("_", "")
        )

    try:
        entries = os.listdir(base)
    except OSError:
        return []
    title_key = key(title_clean)
    game_key = key(game_name)
    matched: list[str] = []
    for entry in entries:
        entry_key = key(entry)
        if (title_key and title_key in entry_key) or (
            game_key and entry_key == game_key
        ):
            candidate = os.path.join(base, entry)
            if os.path.isdir(candidate):
                matched.append(candidate)
    return matched


# AppData\LocalLow and Documents usually hold a container (developer or
# "My Games") one level above the game's own save folder.
_NESTED_SAVE_ROOTS = frozenset({"AppData LocalLow", "Documents", "My Documents"})


def detect_save_locations(
    exe_path: str,
    title: str = "",
    custom_prefix: str = "",
    proton_version: str = "",
    *,
    windows: bool | None = None,
    game_id: int | None = None,
    sandbox_enabled: bool = False,
    _profile_home: str | None = None,
) -> list[SaveLocation]:
    """Searches common save locations for a game.

    The executable's own folder is reported with `eligible: False`: it only
    qualifies as a lead (loose save files), never as an automatic target.
    """
    if windows is None:
        windows = is_windows()

    results: list[SaveLocation] = []
    profile_home = _profile_home or os.path.expanduser("~")
    if sandbox_enabled and game_id is not None:
        from core.game_sandbox import sandbox_folder

        root = sandbox_folder(game_id)
        profiles = [os.path.join(root, "home")]
        if windows:
            profiles = glob.glob(os.path.join(root, "user", "*"))
            profiles += glob.glob(os.path.join(root, "drive", "c", "users", "*"))
        for profile in profiles:
            if os.path.isdir(profile):
                results.extend(
                    detect_save_locations(
                        exe_path,
                        title,
                        custom_prefix,
                        proton_version,
                        windows=windows,
                        _profile_home=profile,
                    )
                )
    game_dir = os.path.dirname(exe_path) if exe_path else ""
    game_name = os.path.splitext(os.path.basename(exe_path))[0] if exe_path else ""
    title_clean = title.strip() if title else game_name

    def add(path: str, kind: str, description: str) -> None:
        eligible = not (game_dir and _same_path(path, game_dir))
        results.append(
            {
                "path": path,
                "type": kind,
                "description": description,
                "eligible": eligible,
            }
        )

    # ── 1. Check game directory for common save folders/files ──
    if game_dir and os.path.isdir(game_dir):
        for pattern in INFOLDER_SAVE_DIRS:
            candidate = os.path.join(game_dir, pattern)
            if os.path.isdir(candidate):
                is_www = pattern == os.path.join("www", "save")
                add(
                    candidate,
                    "RPGMaker MV/MZ" if is_www else "Game Folder",
                    "RPGMaker MV/MZ web save folder"
                    if is_www
                    else f'Found "{pattern}/" folder next to executable',
                )

        # Loose save files (.dat excluded: too many games ship data as .dat)
        for ext in LOOSE_SAVE_PATTERNS:
            found = glob.glob(os.path.join(game_dir, ext))
            if found:
                add(
                    game_dir,
                    "Game Folder",
                    f"Found {len(found)} {ext} file(s) in game directory",
                )
                break  # Don't duplicate the game_dir

    # ── 2. Check Ren'Py native save path (~/.renpy/) ──
    if windows:
        roaming_app_data = (
            os.path.join(profile_home, "AppData", "Roaming")
            if _profile_home
            else str(os.environ.get("APPDATA") or "").strip()
        )
        renpy_base = os.path.join(roaming_app_data, "RenPy") if roaming_app_data else ""
    else:
        renpy_base = os.path.join(profile_home, ".renpy")
    if os.path.isdir(renpy_base):
        for candidate in _renpy_children(renpy_base, title_clean, game_name):
            add(
                candidate,
                "Ren'Py",
                f"Ren'Py native save folder: {candidate}",
            )

    if windows:
        user_home = profile_home
        native_roots = [
            (
                os.path.join(user_home, "AppData", "Roaming")
                if _profile_home
                else str(os.environ.get("APPDATA") or ""),
                "AppData Roaming",
            ),
            (
                os.path.join(user_home, "AppData", "Local")
                if _profile_home
                else str(os.environ.get("LOCALAPPDATA") or ""),
                "AppData Local",
            ),
            (os.path.join(user_home, "AppData", "LocalLow"), "AppData LocalLow"),
            (os.path.join(user_home, "Documents"), "Documents"),
            (os.path.join(user_home, "Saved Games"), "Saved Games"),
        ]
        for root, label in native_roots:
            if not root or not os.path.isdir(root):
                continue
            try:
                for entry in os.listdir(root):
                    candidate = os.path.join(root, entry)
                    if not os.path.isdir(candidate):
                        continue
                    if label in _NESTED_SAVE_ROOTS:
                        # LocalLow/Dev/Game and Documents/My Games/Game hold
                        # the game folder one level down; prefer that folder.
                        children = _nested_game_folders(
                            candidate, title_clean, game_name
                        )
                        if children:
                            for child in children:
                                add(
                                    child,
                                    f"Windows ({label})",
                                    f"{label}/{entry}/{os.path.basename(child)}",
                                )
                            continue
                        if entry.casefold() == "my games":
                            # Never treat the shared container as one game's saves.
                            continue
                    if _name_matches_game(entry, title_clean, game_name):
                        add(candidate, f"Windows ({label})", f"{label}/{entry}")
            except PermissionError:
                continue
        return _dedupe(results)

    for base in (
        os.path.join(profile_home, ".config"),
        os.path.join(profile_home, ".local", "share"),
    ):
        if not os.path.isdir(base):
            continue
        try:
            for entry in os.listdir(base):
                words = [
                    word for word in title_clean.casefold().split() if len(word) >= 4
                ]
                if (game_name and game_name.casefold() in entry.casefold()) or any(
                    word in entry.casefold() for word in words
                ):
                    candidate = os.path.join(base, entry)
                    if os.path.isdir(candidate):
                        add(
                            candidate,
                            "Linux Profile",
                            f"Linux save/config folder: {candidate}",
                        )
        except OSError:
            continue

    # ── 3. Check Wine prefix AppData folders ──
    wine_prefix = (
        custom_prefix if custom_prefix else str(get_setting("wine_prefix_path") or "")
    )
    if not wine_prefix:
        wine_prefix = get_default_wine_prefix()

    proton_path = (
        proton_version if proton_version else str(get_setting("proton_path") or "")
    )
    is_proton = bool(proton_path and "proton" in os.path.basename(proton_path).lower())

    # Mirror the launcher's prefix resolution (core/launcher.py) so saves are
    # searched where the game actually ran.
    wine_prefix = os.path.expanduser(wine_prefix.strip())
    if is_proton:
        actual_prefix = os.path.join(proton_compat_data_path(wine_prefix), "pfx")
    elif os.path.isdir(os.path.join(wine_prefix, "pfx")):
        actual_prefix = os.path.join(wine_prefix, "pfx")
    else:
        actual_prefix = wine_prefix

    users_dir = os.path.join(actual_prefix, "drive_c", "users")
    if os.path.isdir(users_dir):
        appdata_variants = [
            ("AppData/Roaming", "AppData Roaming"),
            ("AppData/Local", "AppData Local"),
            ("AppData/LocalLow", "AppData LocalLow"),
            ("My Documents", "My Documents"),
            ("Documents", "Documents"),
            ("Saved Games", "Saved Games"),
        ]
        try:
            user_folders = os.listdir(users_dir)
        except OSError:
            user_folders = []
        for user_folder in user_folders:
            user_path = os.path.join(users_dir, user_folder)
            if not os.path.isdir(user_path) or user_folder in ("Public",):
                continue

            for sub_path, label in appdata_variants:
                appdata_dir = os.path.join(user_path, sub_path)
                if not os.path.isdir(appdata_dir):
                    continue

                # Fuzzy-match folders against the game title or exe name
                try:
                    for entry in os.listdir(appdata_dir):
                        candidate = os.path.join(appdata_dir, entry)
                        if not os.path.isdir(candidate):
                            continue
                        if (
                            label == "AppData Roaming"
                            and entry.casefold() == "renpy"
                        ):
                            # Ren'Py under Wine keeps its saves one level down,
                            # in %APPDATA%\RenPy\<game>-<hash>, like ~/.renpy.
                            for child in _renpy_children(
                                candidate, title_clean, game_name
                            ):
                                add(
                                    child,
                                    "Ren'Py",
                                    f"Ren'Py save folder in Wine prefix: {child}",
                                )
                            continue
                        if label in _NESTED_SAVE_ROOTS:
                            # LocalLow/Dev/Game and Documents/My Games/Game:
                            # prefer the game folder over its container.
                            children = _nested_game_folders(
                                candidate, title_clean, game_name
                            )
                            if children:
                                for child in children:
                                    add(
                                        child,
                                        f"Wine Prefix ({label})",
                                        f"{label}/{entry}/{os.path.basename(child)}",
                                    )
                                continue
                            if entry.casefold() == "my games":
                                continue
                        if _name_matches_game(entry, title_clean, game_name):
                            add(
                                candidate,
                                f"Wine Prefix ({label})",
                                f"{label}/{entry}",
                            )
                except PermissionError:
                    continue

    return _dedupe(results)


def _dedupe(results: Sequence[SaveLocation]) -> list[SaveLocation]:
    seen: set[str] = set()
    unique: list[SaveLocation] = []
    for result in results:
        if result["path"] not in seen:
            seen.add(result["path"])
            unique.append(result)
    return unique


def resolve_effective_folder(override: str, detected: Sequence[SaveLocation]) -> str:
    """The override, else the single eligible detected folder, else ''."""
    override = (override or "").strip()
    if override:
        return override
    eligible = [entry["path"] for entry in detected if entry["eligible"]]
    return eligible[0] if len(eligible) == 1 else ""


# ── Override validation ──────────────────────────────────────────────────


def validate_save_override(path: str, backups_root: str) -> str:
    """Normalized absolute override for `path`; '' clears it.

    The folder may not exist yet (a game may create it on first launch).
    """
    raw = (path or "").strip()
    if not raw:
        return ""
    expanded = os.path.expanduser(raw)
    if "\0" in expanded:
        # realpath() (inside is_within) raises ValueError for an embedded NUL;
        # report it as a bad path instead of letting it escape the API.
        raise GameSavesError("The saves folder path is not valid")
    if not os.path.isabs(expanded):
        raise GameSavesError("The saves folder must be an absolute path")
    normalized = os.path.normpath(expanded)
    if is_within(normalized, backups_root):
        raise GameSavesError(
            "The saves folder cannot be inside wLib's save backups folder"
        )
    if os.path.exists(normalized) and not os.path.isdir(normalized):
        raise GameSavesError("The saves folder path is a file, not a folder")
    return normalized


# ── Backups ──────────────────────────────────────────────────────────────


def game_backup_dir(backups_root: str, game_id: int) -> str:
    return os.path.join(backups_root, str(int(game_id)))


def _backup_sort_key(name: str) -> tuple[str, int]:
    match = BACKUP_NAME_PATTERN.match(name)
    if match is None:
        return ("", 0)
    return (match.group(1), int(match.group(2) or 0))


def list_backups(backup_dir: str) -> list[str]:
    """Backup folder names, newest first. Foreign and dot-folders are ignored."""
    try:
        entries = os.listdir(backup_dir)
    except OSError:
        return []
    names = [
        entry
        for entry in entries
        if BACKUP_NAME_PATTERN.match(entry)
        and os.path.isdir(os.path.join(backup_dir, entry))
        and not os.path.islink(os.path.join(backup_dir, entry))
    ]
    return sorted(names, key=_backup_sort_key, reverse=True)


def new_backup_name(backup_dir: str, now: datetime | None = None) -> str:
    """A fresh name for this second, always above the highest suffix in use.

    Retention can delete the base folder while a suffixed one stays, so picking
    the first free slot could return a name that sorts older than an existing
    backup (and a name a user already saw could later mean a different backup).
    """
    stamp = (now or datetime.now()).strftime(BACKUP_TIMESTAMP_FORMAT)
    highest = -1
    try:
        entries = os.listdir(backup_dir)
    except OSError:
        entries = []
    for entry in entries:
        match = BACKUP_NAME_PATTERN.match(entry)
        if match is not None and match.group(1) == stamp:
            highest = max(highest, int(match.group(2) or 0))
    return stamp if highest < 0 else f"{stamp}-{highest + 1}"


def prune_backups(
    backup_dir: str, keep: int | None, protect: Collection[str] = ()
) -> list[str]:
    """Delete backups beyond the newest `keep`, never one in `protect`."""
    if keep is None:
        return []
    deleted: list[str] = []
    for name in list_backups(backup_dir)[keep:]:
        if name in protect:
            continue
        try:
            shutil.rmtree(os.path.join(backup_dir, name))
        except OSError:
            continue
        deleted.append(name)
    return deleted


def resolve_backup(backups_root: str, game_id: int, backup_name: str) -> str:
    """Path of a named backup, rejecting anything outside the game's backups."""
    name = str(backup_name or "")
    if not BACKUP_NAME_PATTERN.match(name):
        raise GameSavesError("Unknown backup")
    backup_dir = game_backup_dir(backups_root, game_id)
    path = os.path.join(backup_dir, name)
    if (
        os.path.islink(path)
        or not os.path.isdir(path)
        or not is_within(path, backup_dir)
    ):
        raise GameSavesError("That backup no longer exists")
    return path


@dataclass
class _Tree:
    dirs: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    skipped_links: int = 0
    bytes: int = 0


def _scan_tree(root: str) -> _Tree:
    """Relative dirs and regular files under root; symlinks are skipped."""
    tree = _Tree()
    for current, dirnames, filenames in os.walk(root, followlinks=False):
        kept: list[str] = []
        for name in dirnames:
            if os.path.islink(os.path.join(current, name)):
                tree.skipped_links += 1
            else:
                kept.append(name)
        dirnames[:] = kept
        rel_dir = os.path.relpath(current, root)
        for name in kept:
            tree.dirs.append(name if rel_dir == "." else os.path.join(rel_dir, name))
        for name in filenames:
            full = os.path.join(current, name)
            if os.path.islink(full) or not os.path.isfile(full):
                tree.skipped_links += 1
                continue
            tree.files.append(name if rel_dir == "." else os.path.join(rel_dir, name))
            try:
                tree.bytes += os.path.getsize(full)
            except OSError:
                pass
    return tree


def _copy_tree(source: str, destination: str, tree: _Tree) -> None:
    """Copy the scanned tree, never writing through a link in `destination`."""
    os.makedirs(destination, exist_ok=True)
    root_real = os.path.realpath(destination)
    for rel in tree.dirs:
        target = os.path.join(destination, rel)
        if not is_within(os.path.realpath(target), root_real):
            raise OSError(
                f"'{rel}' is a symbolic link that points outside the saves folder"
            )
        os.makedirs(target, exist_ok=True)
    for rel in tree.files:
        target = os.path.join(destination, rel)
        parent = os.path.dirname(target)
        if not is_within(os.path.realpath(parent), root_real):
            raise OSError(
                f"'{rel}' is under a symbolic link that points outside the saves folder"
            )
        os.makedirs(parent, exist_ok=True)
        # copy2() would follow a symlink already at `target` and overwrite the
        # file it points at. Copy to a fresh file and rename over the entry so
        # only the saves folder is touched.
        descriptor, temporary = tempfile.mkstemp(prefix=".wlib-save-", dir=parent)
        os.close(descriptor)
        try:
            _ = shutil.copy2(os.path.join(source, rel), temporary)
            os.replace(temporary, target)
        finally:
            try:
                os.remove(temporary)
            except FileNotFoundError:
                pass


def copy_folder_contents(source: str, destination: str) -> int:
    """Copy regular files from `source` into `destination`, overwriting.

    Symlinks are skipped. Raises OSError on failure; returns the file count.
    """
    tree = _scan_tree(source)
    _copy_tree(source, destination, tree)
    return len(tree.files)


def has_content(folder: str) -> bool:
    try:
        return os.path.isdir(folder) and any(True for _ in os.scandir(folder))
    except OSError:
        return False


def backup_saves(
    saves_dir: str,
    backups_root: str,
    game_id: int,
    keep: int | None,
    *,
    protect: Collection[str] = (),
    now: datetime | None = None,
) -> BackupInfo:
    """Copy `saves_dir` into a new timestamped backup folder, then prune.

    The copy lands in a `.partial` sibling and is renamed into place, so an
    interrupted or failed backup never looks like a real one.
    """
    if not os.path.isdir(saves_dir):
        raise NothingToBackUpError(
            "The saves folder does not exist, so there is nothing to back up"
        )
    if is_within(backups_root, saves_dir):
        raise GameSavesError(
            "The saves folder contains wLib's save backups folder, so it cannot be backed up"
        )
    if is_within(saves_dir, backups_root):
        raise GameSavesError(
            "The saves folder is inside wLib's save backups folder, so it cannot be backed up"
        )

    tree = _scan_tree(saves_dir)
    if not tree.files:
        raise NothingToBackUpError(
            "The saves folder is empty, so there is nothing to back up"
        )

    backup_dir = game_backup_dir(backups_root, game_id)
    name = ""
    partial = ""
    try:
        os.makedirs(backup_dir, exist_ok=True)
        name = new_backup_name(backup_dir, now)
        partial = os.path.join(backup_dir, f".{name}.partial")
        if os.path.lexists(partial):
            shutil.rmtree(partial)
        _copy_tree(saves_dir, partial, tree)
        final = os.path.join(backup_dir, name)
        os.rename(partial, final)
    except (OSError, shutil.Error) as exc:
        if partial:
            shutil.rmtree(partial, ignore_errors=True)
        raise GameSavesError(f"Backup failed: {exc}") from exc

    pruned = prune_backups(backup_dir, keep, protect={name, *protect})
    return BackupInfo(
        name=name,
        path=final,
        files=len(tree.files),
        bytes=tree.bytes,
        skipped_links=tree.skipped_links,
        pruned=pruned,
    )


# ── Merge import ─────────────────────────────────────────────────────────


def import_saves(
    source: str,
    saves_dir: str,
    backups_root: str,
    game_id: int,
    keep: int | None,
    *,
    now: datetime | None = None,
) -> ImportInfo:
    """Merge `source` into `saves_dir`: same-named files are overwritten.

    A non-empty target is backed up first and the import stops if that
    fails. The merge itself is not atomic; the backup is the way back.
    """
    if not os.path.isdir(source):
        raise GameSavesError("The folder to import does not exist")
    if "\0" in saves_dir:
        # realpath() raises ValueError for an embedded NUL; keep it a clean error.
        raise GameSavesError("The saves folder path is not valid")
    if paths_overlap(source, saves_dir):
        raise GameSavesError(
            "The folder to import cannot be the saves folder, inside it, or contain it"
        )
    if os.path.lexists(saves_dir) and not os.path.isdir(saves_dir):
        raise GameSavesError("The saves folder path is a file, not a folder")

    tree = _scan_tree(source)
    if not tree.files:
        raise GameSavesError("The folder to import has no files")
    if is_within(saves_dir, backups_root):
        raise GameSavesError(
            "The saves folder cannot be inside wLib's save backups folder"
        )
    saves_real = os.path.realpath(saves_dir)
    for rel in tree.dirs:
        target = os.path.join(saves_dir, rel)
        if os.path.islink(target) and not is_within(
            os.path.realpath(target), saves_real
        ):
            raise GameSavesError(
                f"Cannot import: '{rel}' is a symbolic link outside the saves folder"
            )
        if os.path.lexists(target) and not os.path.isdir(target):
            raise GameSavesError(
                f"Cannot import: '{rel}' is a folder in the import but a file in the saves folder"
            )
    for rel in tree.files:
        if os.path.isdir(os.path.join(saves_dir, rel)):
            raise GameSavesError(
                f"Cannot import: '{rel}' is a file in the import but a folder in the saves folder"
            )

    backup: BackupInfo | None = None
    if has_content(saves_dir):
        protect: set[str] = set()
        game_dir = game_backup_dir(backups_root, game_id)
        if is_within(source, game_dir):
            # Restoring a backup: pruning must not delete the source.
            rel_source = os.path.relpath(
                os.path.realpath(source), os.path.realpath(game_dir)
            )
            protect.add(rel_source.split(os.sep)[0])
        try:
            backup = backup_saves(
                saves_dir, backups_root, game_id, keep, protect=protect, now=now
            )
        except NothingToBackUpError:
            backup = None
        except GameSavesError as exc:
            raise GameSavesError(
                f"Import stopped to protect the current saves: {exc}"
            ) from exc

    try:
        _copy_tree(source, saves_dir, tree)
    except (OSError, shutil.Error) as exc:
        hint = (
            f" Your previous saves were backed up as {backup.name}." if backup else ""
        )
        raise GameSavesError(f"Import failed part way: {exc}.{hint}") from exc

    return ImportInfo(
        files=len(tree.files),
        bytes=tree.bytes,
        skipped_links=tree.skipped_links,
        backup=backup,
    )
