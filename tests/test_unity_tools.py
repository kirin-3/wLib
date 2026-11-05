# SPDX-License-Identifier: GPL-3.0-or-later
import struct

from core.unity_tools import detect_installed, detect_unity


def pe(path, machine):
    data = bytearray(134)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 60, 128)
    data[128:132] = b"PE\0\0"
    struct.pack_into("<H", data, 132, machine)
    path.write_bytes(data)


def test_detect_windows_and_old_unity(tmp_path):
    exe = tmp_path / "Game.exe"
    pe(exe, 0x14C)
    data = tmp_path / "Game_Data"
    (data / "Managed").mkdir(parents=True)
    (data / "Managed" / "Assembly-CSharp.dll").touch()
    (data / "mainData").write_bytes(b"Unity 2022.3.21f1")
    game = detect_unity(str(tmp_path), str(exe))
    assert game and (game.platform, game.backend, game.arch, game.version) == ("windows", "mono", "x86", "2022.3.21")
    pe(tmp_path / "UnityPlayer.dll", 0x8664)
    (tmp_path / "GameAssembly.dll").touch()
    game = detect_unity(str(tmp_path), str(exe))
    assert game and (game.backend, game.arch) == ("il2cpp", "x64")


def test_detect_linux_fallback_and_unknown(tmp_path):
    exe = tmp_path / "renamed.x86_64"
    data = tmp_path / "Original_Data"
    data.mkdir()
    (data / "globalgamemanagers").write_bytes(b"unknown")
    game = detect_unity(str(tmp_path), str(exe))
    assert game and game.platform == "linux" and game.version == "" and game.data_folder == str(data)
    (data / "globalgamemanagers").unlink()
    assert detect_unity(str(tmp_path), str(exe)) is None
    (tmp_path / "UnityPlayer.so").touch()
    assert detect_unity(str(tmp_path), str(exe)) is not None


def test_detect_installed(tmp_path):
    assert detect_installed(str(tmp_path)) is None
    core = tmp_path / "BepInEx" / "core"
    core.mkdir(parents=True)
    dll = core / "BepInEx.dll"
    dll.write_bytes(("ProductVersion\0\0" + "5.4.21.0\0").encode("utf-16-le"))
    (tmp_path / "winhttp.dll").touch()
    installed = detect_installed(str(tmp_path))
    assert installed and installed["version"] == "5.4.21.0" and installed["major"] == 5 and installed["complete"] and installed["backend"] == "mono"
    dll.unlink()
    (core / "BepInEx.Core.dll").touch()
    (core / "BepInEx.Unity.IL2CPP.dll").touch()
    (tmp_path / "winhttp.dll").unlink()
    installed = detect_installed(str(tmp_path))
    assert installed and installed["major"] == 6 and not installed["complete"] and installed["backend"] == "il2cpp"


def test_catalog_and_recommendation():
    from core.unity_tools import BLEEDING_EDGE, list_versions, recommended_build

    import json
    from pathlib import Path

    fixtures = Path(__file__).parent / "fixtures" / "unity"
    releases = json.loads((fixtures / "releases.json").read_text())
    feed = json.loads((fixtures / "bleeding-edge.json").read_text())
    builds, warnings = list_versions("x64", "il2cpp", lambda url: feed if url == BLEEDING_EDGE else releases)
    assert not warnings and len(builds) == 3
    assert recommended_build(builds, "il2cpp")["channel"] == "be"
    builds, _ = list_versions("x64", "mono", lambda url: feed if url == BLEEDING_EDGE else releases)
    assert len(builds) == 2 and recommended_build(builds, "mono")["id"] == "5.4.23.6"
    assert builds[0]["sha256"] == "abc"
    def failing(url):
        if url == BLEEDING_EDGE:
            raise OSError("offline")
        return releases
    builds, warnings = list_versions("x64", "mono", failing)
    assert builds and len(warnings) == 1


def test_download_cache(tmp_path):
    import hashlib
    import io
    import urllib.error

    import pytest

    from core.game_tools import GameToolError
    from core.unity_tools import download_cached

    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("a.txt", "data")
    payload = buffer.getvalue()

    class Response(io.BytesIO):
        headers = {"Content-Length": str(len(payload))}

    def opener(*args, **kwargs):
        return Response(payload)

    url = "https://example.test/file.zip"
    with pytest.raises(GameToolError, match="mismatch"):
        download_cached(url, "file.zip", str(tmp_path), "bad", opener=opener)
    assert not list(tmp_path.iterdir())
    with pytest.raises(GameToolError, match="cancelled"):
        download_cached(url, "file.zip", str(tmp_path), cancelled=lambda: True, opener=opener)
    assert not list(tmp_path.iterdir())
    path = download_cached(url, "file.zip", str(tmp_path), hashlib.sha256(payload).hexdigest(), opener=opener)
    assert path and path.read_bytes() == payload
    assert download_cached(url, "file.zip", str(tmp_path), opener=lambda *a, **k: pytest.fail("cache miss")) == path
    def missing(*args, **kwargs):
        raise urllib.error.HTTPError(url, 404, "missing", {}, None)
    assert download_cached(url, "other.zip", str(tmp_path), opener=missing) is None


def test_download_cache_rejects_truncated_files(tmp_path):
    import io
    import zipfile

    import pytest

    from core.game_tools import GameToolError
    from core.unity_tools import download_cached

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("a.txt", "data" * 100)
    payload = buffer.getvalue()

    class Truncated(io.BytesIO):
        headers = {"Content-Length": str(len(payload))}

    url = "https://example.test/be.zip"
    # The connection drops early; read() just returns b"" (no IncompleteRead).
    with pytest.raises(GameToolError, match="incomplete"):
        download_cached(url, "be.zip", str(tmp_path), opener=lambda *a, **k: Truncated(payload[:-20]))
    assert not list(tmp_path.iterdir())

    class NoLength(io.BytesIO):
        headers = {}

    with pytest.raises(GameToolError, match="not a valid zip"):
        download_cached(url, "be.zip", str(tmp_path), opener=lambda *a, **k: NoLength(payload[: len(payload) // 2]))
    assert not list(tmp_path.iterdir())

    # A truncated file cached by an older build is replaced, not reused.
    (tmp_path / "be.zip").write_bytes(payload[: len(payload) // 2])
    path = download_cached(url, "be.zip", str(tmp_path), opener=lambda *a, **k: NoLength(payload))
    assert path and path.read_bytes() == payload


def test_install_update_uninstall_restore(tmp_path):
    import zipfile

    from core.unity_tools import install_build, restore, uninstall

    archive = tmp_path / "package.zip"
    def package(value):
        with zipfile.ZipFile(archive, "w") as z:
            z.writestr("BepInEx/core/BepInEx.Core.dll", value)
            z.writestr("BepInEx/core/BepInEx.Unity.IL2CPP.dll", "il2cpp")
            z.writestr("BepInEx/plugins/", "")
            z.writestr("winhttp.dll", value)
            z.writestr("dotnet/runtime.dll", value)
    def install():
        return install_build(str(tmp_path), str(archive), None, lambda *a: None, lambda: False)
    package("old")
    install()
    assert not (tmp_path / "BepInEx.wlib-backup").exists()
    plugin = tmp_path / "BepInEx/plugins/mod.dll"
    plugin.write_text("mine")
    package("new")
    install()
    assert (tmp_path / "dotnet/runtime.dll").read_text() == "new"
    assert (tmp_path / "BepInEx.wlib-backup/dotnet/runtime.dll").read_text() == "old"
    assert plugin.read_text() == "mine"
    restore(str(tmp_path))
    assert (tmp_path / "dotnet/runtime.dll").read_text() == "old"
    uninstall(str(tmp_path), False)
    assert plugin.exists() and not (tmp_path / "winhttp.dll").exists()
    restore(str(tmp_path))
    assert (tmp_path / "winhttp.dll").exists()
    uninstall(str(tmp_path), True)
    assert not (tmp_path / "BepInEx").exists()
    restore(str(tmp_path))
    assert plugin.exists()


def test_install_rejects_unsafe_and_cancelled(tmp_path):
    import zipfile

    import pytest

    from core.unity_tools import install_build

    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("../escape", "x")
    with pytest.raises(Exception):
        install_build(str(tmp_path), str(archive), None, lambda *a: None, lambda: False)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["bad.zip"]
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("winhttp.dll", "x")
    with pytest.raises(Exception, match="cancelled"):
        install_build(str(tmp_path), str(archive), None, lambda *a: None, lambda: True)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["bad.zip"]


def test_foreign_uninstall_dotnet_only_for_il2cpp(tmp_path):
    from core.unity_tools import uninstall

    core = tmp_path / "BepInEx/core"
    core.mkdir(parents=True)
    (core / "BepInEx.Core.dll").touch()
    (tmp_path / "dotnet").mkdir()
    (tmp_path / "dotnet/runtime.dll").touch()
    uninstall(str(tmp_path), False)
    assert (tmp_path / "dotnet/runtime.dll").exists()


def test_foreign_il2cpp_uninstall_and_offline_libraries(tmp_path):
    import zipfile

    from core.unity_tools import install_build, restore, uninstall

    core = tmp_path / "BepInEx/core"
    core.mkdir(parents=True)
    (core / "BepInEx.Core.dll").touch()
    (core / "BepInEx.Unity.IL2CPP.dll").touch()
    (tmp_path / "dotnet").mkdir()
    (tmp_path / "dotnet/runtime.dll").touch()
    uninstall(str(tmp_path), False)
    assert not (tmp_path / "dotnet").exists()
    restore(str(tmp_path))
    archive = tmp_path / "package.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("BepInEx/core/BepInEx.Core.dll", "x")
    libs = tmp_path / "unity-libs-2022.3.21.zip"
    libs.write_bytes(b"libraries")
    install_build(str(tmp_path), str(archive), str(libs), lambda *a: None, lambda: False)
    assert (tmp_path / "BepInEx/unity-libs/2022.3.21.zip").read_bytes() == b"libraries"


def test_install_rejects_zip_symlink(tmp_path):
    import stat
    import zipfile

    import pytest

    from core.game_tools import GameToolError
    from core.unity_tools import install_build

    archive = tmp_path / "bad.zip"
    entry = zipfile.ZipInfo("link")
    entry.create_system = 3
    entry.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr(entry, "target")
    with pytest.raises(GameToolError, match="symbolic link"):
        install_build(str(tmp_path), str(archive), None, lambda *a: None, lambda: False)


def test_concurrent_downloads_share_the_cached_file(tmp_path):
    import io
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from core.unity_tools import download_cached

    started, release = threading.Event(), threading.Event()
    calls = []
    class Response(io.BytesIO):
        headers = {}
        def read(self, *args):
            started.set()
            assert release.wait(5)
            return super().read(*args)
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("a.txt", "data")

    def opener(*args, **kwargs):
        calls.append(1)
        return Response(buffer.getvalue())
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(download_cached, "https://example.test/a", "a.zip", str(tmp_path), opener=opener)
        assert started.wait(5)
        second = pool.submit(download_cached, "https://example.test/a", "a.zip", str(tmp_path), opener=opener)
        release.set()
        assert first.result() == second.result()
    assert len(calls) == 1


def test_layout_check_and_version_from_large_binary(tmp_path):
    from core.unity_tools import is_unity_folder

    exe = tmp_path / "Game.exe"
    assert not is_unity_folder(str(tmp_path), str(exe))
    player = tmp_path / "UnityPlayer.dll"
    pe(player, 0x8664)
    # The version resource sits past the first 16 MiB of a real UnityPlayer.dll.
    resource = "ProductVersion\0\0" + "2021.3.5f1 (abc)\0"
    with player.open("ab") as stream:
        _ = stream.write(b"\0" * (20 * 1024 * 1024))
        _ = stream.write(resource.encode("utf-16-le"))
    assert is_unity_folder(str(tmp_path), str(exe))
    game = detect_unity(str(tmp_path), str(exe))
    assert game and game.version == "2021.3.5"


def test_uninstall_leaves_no_empty_core_folder(tmp_path):
    import zipfile

    from core.unity_tools import install_build, uninstall

    archive = tmp_path / "package.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("BepInEx/core/BepInEx.dll", "core")
        z.writestr("BepInEx/plugins/", "")
        z.writestr("winhttp.dll", "loader")
        z.writestr("dotnet/sub/runtime.dll", "runtime")
    _ = install_build(str(tmp_path), str(archive), None, lambda *a: None, lambda: False)
    _ = uninstall(str(tmp_path), False)
    assert not (tmp_path / "BepInEx/core").exists()
    assert not (tmp_path / "dotnet").exists()
    assert (tmp_path / "BepInEx/plugins").is_dir()
