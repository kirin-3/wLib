# SPDX-License-Identifier: GPL-3.0-or-later
import os

from core.host_platform import (
    get_cache_root,
    get_data_dir,
    get_platform_capabilities,
    get_playwright_browsers_path,
)


def test_windows_paths_use_local_app_data(tmp_path):
    local_app_data = tmp_path / "LocalAppData"
    env = {"LOCALAPPDATA": str(local_app_data)}

    assert get_data_dir(platform="win32", environ=env) == os.path.join(
        str(local_app_data), "wLib"
    )
    assert get_cache_root(platform="win32", environ=env) == os.path.join(
        str(local_app_data), "wLib", "cache"
    )
    assert get_playwright_browsers_path(
        platform="win32", environ=env
    ) == os.path.join(str(local_app_data), "wLib", "playwright")


def test_windows_paths_fall_back_to_user_home(tmp_path):
    assert get_data_dir(platform="win32", environ={}, home=str(tmp_path)) == os.path.join(
        str(tmp_path), "AppData", "Local", "wLib"
    )


def test_linux_paths_preserve_xdg_defaults(tmp_path):
    data_home = tmp_path / "data"
    cache_home = tmp_path / "cache"
    env = {"XDG_DATA_HOME": str(data_home), "XDG_CACHE_HOME": str(cache_home)}

    # Isolated home: a real ~/.local/share/wLib would win as the legacy library.
    assert get_data_dir(
        platform="linux", environ=env, home=str(tmp_path / "home")
    ) == os.path.join(str(data_home), "wLib")
    assert get_cache_root(platform="linux", environ=env) == str(cache_home)
    assert get_playwright_browsers_path(
        platform="linux", environ=env
    ) == os.path.join(str(cache_home), "ms-playwright")


def test_linux_paths_preserve_home_fallbacks(tmp_path):
    assert get_data_dir(platform="linux", environ={}, home=str(tmp_path)) == os.path.join(
        str(tmp_path), ".local", "share", "wLib"
    )
    assert get_playwright_browsers_path(
        platform="linux", environ={}, home=str(tmp_path)
    ) == os.path.join(str(tmp_path), ".cache", "ms-playwright")


def test_linux_xdg_data_home_keeps_existing_legacy_library(tmp_path):
    home = tmp_path / "home"
    legacy_dir = home / ".local" / "share" / "wLib"
    legacy_dir.mkdir(parents=True)
    xdg_home = tmp_path / "xdg-data"
    env = {"XDG_DATA_HOME": str(xdg_home)}

    assert get_data_dir(platform="linux", environ=env, home=str(home)) == str(
        legacy_dir
    )

    (xdg_home / "wLib").mkdir(parents=True)
    assert get_data_dir(platform="linux", environ=env, home=str(home)) == str(
        xdg_home / "wLib"
    )


def test_explicit_path_overrides_isolate_smoke_runtime(tmp_path):
    data_dir = tmp_path / "isolated-data"
    cache_dir = tmp_path / "isolated-cache"
    env = {"WLIB_DATA_DIR": str(data_dir), "WLIB_CACHE_DIR": str(cache_dir)}

    assert get_data_dir(platform="linux", environ=env) == str(data_dir)
    assert get_cache_root(platform="linux", environ=env) == str(cache_dir)


def test_platform_capabilities_hide_linux_mutations_on_windows(monkeypatch, tmp_path):
    monkeypatch.setenv("WLIB_DATA_DIR", str(tmp_path / "data"))
    capabilities = get_platform_capabilities("win32")

    assert capabilities["platform"] == "windows"
    assert capabilities["native_windows_launch"] is True
    assert capabilities["wine_proton"] is False
    assert capabilities["runtime_installers"] is False
    assert capabilities["rpgmaker_linux"] is False
    assert capabilities["launch_modes"] == ["auto"]
