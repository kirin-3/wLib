# SPDX-License-Identifier: GPL-3.0-or-later
"""Guess game details from an install folder and scan folders for games."""

from __future__ import annotations

import os
import re
from typing import TypedDict

from core.host_platform import is_linux
from core.launcher import is_elf


class GameGuess(TypedDict):
    title: str
    version: str
    engine: str
    exe_path: str


_PLATFORM_SUFFIX_RE = re.compile(
    r"[-_ .]+(pc|win(dows)?(64|32)?|linux|mac|market|android|x64|x86|64bit|32bit)$", re.I
)
_VERSION_SUFFIX_RE = re.compile(
    # A bare number needs a dot ("Game 2" is a sequel, "Game-0.2" a version).
    r"[-_ ]+v?((?:(?<=v)\d+|\d+[._]\d+)(?:[._]\d+)*[a-z]?"
    + r"(?:[-_ ]?(?:alpha|beta|public|final|fix(?:ed)?|patch\d*))?)$",
    re.I,
)
_SKIPPED_EXECUTABLES_RE = re.compile(
    r"^(unins\w*|unitycrashhandler\w*|notification_helper|crashpad_handler|crashreport\w*|"
    + r"dxsetup|dxwebsetup|vc_?redist\S*|ue4prereqsetup\S*|python\w*|zsync\w*)(?:\.exe)?$|"
    + r"^.*-32\.exe$",
    re.I,
)
_LAUNCHER_EXTENSIONS = (
    ".exe", ".bat", ".cmd", ".sh", ".x86", ".x86_64", ".html", ".htm", ".jar"
)


def guess_title_and_version(folder_name: str) -> tuple[str, str]:
    """'SummertimeSaga-0.20.16-pc' -> ('Summertime Saga', '0.20.16')."""
    name = folder_name.strip()
    version = ""
    for _ in range(3):
        stripped = _PLATFORM_SUFFIX_RE.sub("", name)
        match = _VERSION_SUFFIX_RE.search(stripped)
        if match and match.start() > 0:
            version = version or match.group(1).replace("_", ".")
            stripped = stripped[: match.start()]
        if stripped == name:
            break
        name = stripped
    name = name.replace("_", " ").strip(" -.")
    if " " not in name:
        name = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name)
    return name or folder_name, version


def _names(game_dir: str) -> set[str]:
    try:
        return {entry.lower() for entry in os.listdir(game_dir)}
    except OSError:
        return set()


def detect_engine(game_dir: str, exe_path: str = "") -> str:
    names = _names(game_dir)
    if "renpy" in names or ("game" in names and "lib" in names):
        return "Ren'Py"
    if (
        "www" in names
        or names & {"game.rgss3a", "game.rgss2a", "game.rgssad"}
        or os.path.isfile(os.path.join(game_dir, "js", "rmmz_core.js"))
        or os.path.isfile(os.path.join(game_dir, "js", "rpg_core.js"))
    ):
        return "RPGM"
    if "unityplayer.dll" in names or "unityplayer.so" in names:
        return "Unity"
    if "data.wolf" in names or os.path.isfile(os.path.join(game_dir, "Data", "Data.wolf")):
        return "Wolf RPG"
    if "engine" in names and os.path.isdir(os.path.join(game_dir, "Engine", "Binaries")):
        return "Unreal Engine"
    # Godot exports ship their data as a .pck beside the executable.
    if any(name.endswith(".pck") for name in names):
        return "Godot"
    if exe_path.lower().endswith((".html", ".htm")):
        return "HTML"
    return ""


def inspect_game_path(exe_path: str) -> GameGuess:
    game_dir = os.path.dirname(os.path.abspath(exe_path))
    title, version = guess_title_and_version(os.path.basename(game_dir))
    return {
        "title": title,
        "version": version,
        "engine": detect_engine(game_dir, exe_path),
        "exe_path": exe_path,
    }


def find_launcher(game_dir: str, depth: int = 2) -> str:
    """Best launch file in game_dir, looking one folder deeper when it holds none."""
    try:
        entries = list(os.scandir(game_dir))
    except OSError:
        return ""
    # Collect candidate launcher files without sorting entire directory upfront
    files: list[str] = []
    for entry in entries:
        try:
            if not entry.is_file():
                continue
            name = entry.name
            lower = name.lower()
            if _SKIPPED_EXECUTABLES_RE.match(name):
                continue
            if lower.endswith(_LAUNCHER_EXTENSIONS):
                files.append(name)
            elif not os.path.splitext(name)[1] and is_elf(entry.path):
                files.append(name)
        except OSError:
            continue
    files.sort(key=str.lower)

    # Native Ren'Py/NW.js scripts run without Wine on Linux; elsewhere prefer the .exe.
    native = [
        name for name in files
        if name.lower().endswith((".sh", ".x86", ".x86_64"))
        or is_elf(os.path.join(game_dir, name))
    ]
    if is_linux() and native:
        folder_stem = os.path.basename(game_dir).lower()
        title_stem, _ = guess_title_and_version(folder_stem)
        sh_files = [n for n in native if n.lower().endswith(".sh")]
        if sh_files:
            clean_title = re.sub(r"[^a-z0-9]", "", title_stem.lower())
            preferred_sh = next(
                (
                    n
                    for n in sh_files
                    if (clean_title and clean_title in re.sub(r"[^a-z0-9]", "", n.lower()))
                    or n.lower().startswith(("game", "start", "run"))
                ),
                sh_files[0],
            )
            return os.path.join(game_dir, preferred_sh)
        return os.path.join(game_dir, native[0])

    preferred = (".exe", ".bat", ".cmd", ".sh", ".x86", ".x86_64")
    folder_stem = os.path.basename(game_dir).lower()
    title_stem, _ = guess_title_and_version(folder_stem)
    clean_title = re.sub(r"[^a-z0-9]", "", title_stem.lower())

    for ext in (*preferred, ".jar"):
        matches = [name for name in files if name.lower().endswith(ext)]
        if matches:
            best = matches[0]
            # Prioritize executable whose name matches folder/title or standard game launchers over helper tools
            for m in matches:
                stem = os.path.splitext(m)[0].lower()
                clean_stem = re.sub(r"[^a-z0-9]", "", stem)
                if clean_title and (clean_stem == clean_title or clean_title in clean_stem):
                    best = m
                    break
                if stem in ("game", "start", "launcher", "main"):
                    best = m
                    break
            return os.path.join(game_dir, best)
    for name in ("index.html", "index.htm"):
        if name in (f.lower() for f in files):
            return os.path.join(game_dir, next(f for f in files if f.lower() == name))
    if files:
        return os.path.join(game_dir, files[0])
    if depth > 1:
        junk_dirs = {"__macosx", ".git", ".wlib-staging-"}
        subdirs = [
            entry.path
            for entry in entries
            if entry.is_dir() and entry.name.lower() not in junk_dirs
        ]
        if len(subdirs) == 1:
            return find_launcher(subdirs[0], depth - 1)
    return ""


def scan_games_folder(root: str) -> list[GameGuess]:
    """One game per direct subfolder of root that holds a launch file."""
    try:
        subdirs = sorted(
            (entry.path for entry in os.scandir(root) if entry.is_dir()),
            key=str.lower,
        )
    except OSError:
        return []
    games: list[GameGuess] = []
    for subdir in subdirs:
        exe_path = find_launcher(subdir)
        if not exe_path:
            continue
        title, version = guess_title_and_version(os.path.basename(subdir))
        games.append(
            {
                "title": title,
                "version": version,
                "engine": detect_engine(os.path.dirname(exe_path), exe_path),
                "exe_path": exe_path,
            }
        )
    return games
