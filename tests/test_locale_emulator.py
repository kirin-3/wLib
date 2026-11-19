# SPDX-License-Identifier: GPL-3.0-or-later
import contextlib
import subprocess
from types import SimpleNamespace

import pytest

from core import locale_emulator as le

HKCU = "HKCU"
HKLM = "HKLM"


def fake_winreg(values):
    """A winreg stand-in; ``values`` maps a root to the CodeBase it holds."""

    @contextlib.contextmanager
    def open_key(root, sub_key, reserved, access):
        assert sub_key == le._HANDLER_KEY
        assert access == 0x20019 | 0x0100
        if root not in values:
            raise FileNotFoundError(sub_key)
        yield root

    def query_value(key, name):
        assert name == "CodeBase"
        return values[key], 1

    return SimpleNamespace(
        HKEY_CURRENT_USER=HKCU,
        HKEY_LOCAL_MACHINE=HKLM,
        KEY_READ=0x20019,
        KEY_WOW64_64KEY=0x0100,
        OpenKey=open_key,
        QueryValueEx=query_value,
    )


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    le._cache.clear()
    monkeypatch.setattr(le, "get_setting", lambda _key: "")
    monkeypatch.setattr(le, "_load_winreg", lambda: None)
    monkeypatch.setenv("ProgramFiles", r"C:\Program Files")
    monkeypatch.setenv("ProgramFiles(x86)", r"C:\Program Files (x86)")
    yield
    le._cache.clear()


def existing(monkeypatch, *paths):
    seen = []

    def is_file(path):
        seen.append(path)
        return path in paths

    monkeypatch.setattr(le, "_is_file", is_file)
    return seen


def test_saved_path_is_used(monkeypatch):
    existing(monkeypatch, r"D:\Tools\LE\LEProc.exe")
    monkeypatch.setattr(le, "get_setting", lambda _key: r"D:\Tools\LE\LEProc.exe")
    result = le.get_status(platform="win32")
    assert result == {"status": "ready", "path": r"D:\Tools\LE\LEProc.exe", "reason": ""}


def test_missing_saved_path_has_no_fallback(monkeypatch):
    seen = existing(monkeypatch, r"C:\Program Files\Locale Emulator\LEProc.exe")
    monkeypatch.setattr(le, "_load_winreg", lambda: fake_winreg({HKCU: "file:///C:/LE/x.dll"}))
    result = le.get_status(r"D:\Gone\LEProc.exe", platform="win32")
    assert result["status"] == "missing"
    assert result["path"] == r"D:\Gone\LEProc.exe"
    assert r"D:\Gone\LEProc.exe" in result["reason"]
    assert seen == [r"D:\Gone\LEProc.exe"]


def test_wrong_file_name_is_unusable(monkeypatch):
    seen = existing(monkeypatch, r"D:\LE\LEGUI.exe")
    result = le.get_status(r"D:\LE\LEGUI.exe", platform="win32")
    assert result["status"] == "unusable"
    assert result["reason"] == "Choose the Locale Emulator LEProc.exe executable."
    assert seen == []


def test_file_name_check_ignores_case():
    assert le.is_leproc_path(r"C:\LE\leproc.EXE")
    assert not le.is_leproc_path(r"C:\LE\LEProc.exe.bak")


def test_registry_current_user_first(monkeypatch):
    existing(
        monkeypatch,
        r"D:\My Tools\LE\LEProc.exe",
        r"E:\AllUsers\LEProc.exe",
        r"C:\Program Files\Locale Emulator\LEProc.exe",
    )
    monkeypatch.setattr(
        le,
        "_load_winreg",
        lambda: fake_winreg(
            {
                HKCU: "file:///D:/My Tools/LE/LEContextMenuHandler.dll",
                HKLM: "file:///E:/AllUsers/LEContextMenuHandler.dll",
            }
        ),
    )
    assert le.get_status(platform="win32")["path"] == r"D:\My Tools\LE\LEProc.exe"


def test_registry_all_users(monkeypatch):
    existing(monkeypatch, r"E:\AllUsers\LEProc.exe")
    monkeypatch.setattr(
        le, "_load_winreg", lambda: fake_winreg({HKLM: "file:///E:/AllUsers/LEContextMenuHandler.dll"})
    )
    assert le.get_status(platform="win32")["path"] == r"E:\AllUsers\LEProc.exe"


def test_registry_folder_without_leproc_falls_back(monkeypatch):
    existing(monkeypatch, r"C:\Program Files (x86)\Locale Emulator\LEProc.exe")
    monkeypatch.setattr(
        le, "_load_winreg", lambda: fake_winreg({HKCU: "file:///D:/Moved/LEContextMenuHandler.dll"})
    )
    result = le.get_status(platform="win32")
    assert result["path"] == r"C:\Program Files (x86)\Locale Emulator\LEProc.exe"


@pytest.mark.parametrize("codebase", ["", "C:/LE/x.dll", "http://x/LE/x.dll", "file:///relative/x.dll"])
def test_bad_codebase_is_ignored(codebase):
    assert le._codebase_folder(codebase) == ""


def test_program_files(monkeypatch):
    existing(monkeypatch, r"C:\Program Files\Locale Emulator\LEProc.exe")
    result = le.get_status(platform="win32")
    assert result == {
        "status": "ready",
        "path": r"C:\Program Files\Locale Emulator\LEProc.exe",
        "reason": "",
    }


def test_not_found(monkeypatch):
    seen = existing(monkeypatch)
    monkeypatch.setattr(le, "_load_winreg", lambda: fake_winreg({}))
    result = le.get_status(platform="win32")
    assert result["status"] == "missing"
    assert result["path"] == ""
    assert "LEProc.exe" in result["reason"]
    assert seen == [
        r"C:\Program Files\Locale Emulator\LEProc.exe",
        r"C:\Program Files (x86)\Locale Emulator\LEProc.exe",
    ]


def test_only_ready_is_cached_and_recheck_clears(monkeypatch):
    seen = existing(monkeypatch)
    assert le.get_status(platform="win32")["status"] == "missing"
    assert le.get_status(platform="win32")["status"] == "missing"
    assert len(seen) == 4

    existing(monkeypatch, r"C:\Program Files\Locale Emulator\LEProc.exe")
    assert le.get_status(platform="win32")["status"] == "ready"
    gone = existing(monkeypatch)
    assert le.get_status(platform="win32")["status"] == "ready"
    assert gone == []
    assert le.get_status(platform="win32", recheck=True)["status"] == "missing"


def test_cached_result_is_a_copy(monkeypatch):
    existing(monkeypatch, r"C:\Program Files\Locale Emulator\LEProc.exe")
    first = le.get_status(platform="win32")
    first["path"] = "changed"
    assert le.get_status(platform="win32")["path"].endswith("LEProc.exe")


def test_new_saved_path_is_checked(monkeypatch):
    existing(monkeypatch, r"C:\Program Files\Locale Emulator\LEProc.exe")
    assert le.get_status("", platform="win32")["status"] == "ready"
    assert le.get_status(r"D:\Gone\LEProc.exe", platform="win32")["status"] == "missing"


def test_non_windows_host(monkeypatch):
    seen = existing(monkeypatch, r"C:\Program Files\Locale Emulator\LEProc.exe")
    for platform in ("linux", "darwin"):
        result = le.get_status(platform=platform)
        assert result["status"] == "unusable"
        assert result["path"] == ""
    assert seen == []


def test_wrap_command_keeps_argument_boundaries():
    args = ["a b", "plain", r"C:\Program Files\x y", 'q"t', "", "end\\", "tab\there"]
    wrapped = le.wrap_command(r"C:\LE\LEProc.exe", [r"C:\Games\My Game\game.exe", *args])
    assert wrapped[:2] == [r"C:\LE\LEProc.exe", r"C:\Games\My Game\game.exe"]
    assert wrapped[2:] == ['"a b"', "plain", r'"C:\Program Files\x y"', r'q\"t', '""', "end\\", '"tab\there"']
    # LEProc.exe joins its .NET argv with spaces; the game parses that line.
    joined = " ".join(wrapped[2:])
    assert _msvcrt_split(joined) == args


def test_wrap_command_without_arguments():
    assert le.wrap_command("LEProc.exe", ["game.exe"]) == ["LEProc.exe", "game.exe"]
    assert le.wrap_command("LEProc.exe", []) == ["LEProc.exe"]


def _msvcrt_split(line):
    """Parse a command line the way the Microsoft C runtime does."""
    args, current, in_arg, quoted, i = [], [], False, False, 0
    while i < len(line):
        char = line[i]
        if char == "\\":
            j = i
            while j < len(line) and line[j] == "\\":
                j += 1
            count = j - i
            if j < len(line) and line[j] == '"':
                current.append("\\" * (count // 2))
                if count % 2:
                    current.append('"')
                    i = j + 1
                else:
                    i = j
                in_arg = True
                continue
            current.append("\\" * count)
            in_arg, i = True, j
            continue
        if char == '"':
            quoted, in_arg = not quoted, True
        elif char in " \t" and not quoted:
            if in_arg:
                args.append("".join(current))
                current, in_arg = [], False
        else:
            current.append(char)
            in_arg = True
        i += 1
    if in_arg:
        args.append("".join(current))
    return args


def test_msvcrt_split_matches_list2cmdline():
    sample = ["a b", 'q"t', "", "x\\", 'p\\"q']
    assert _msvcrt_split(subprocess.list2cmdline(sample)) == sample
