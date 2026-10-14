# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_nfpm_declares_xcb_cursor_dependencies():
    nfpm_config = (PROJECT_ROOT / "packaging/nfpm.yaml").read_text(encoding="utf-8")

    assert "      - libxcb-cursor0" in nfpm_config
    assert "      - xcb-util-cursor" in nfpm_config


def test_aur_metadata_declares_xcb_cursor_dependency():
    pkgbuild_template = (PROJECT_ROOT / "packaging/aur/PKGBUILD.in").read_text(
        encoding="utf-8"
    )
    aur_generator = (PROJECT_ROOT / "scripts/generate-aur-package.sh").read_text(
        encoding="utf-8"
    )

    assert "  'xcb-util-cursor'" in pkgbuild_template
    assert "\tdepends = xcb-util-cursor" in aur_generator


def test_aur_metadata_installs_theme_icon_assets():
    pkgbuild_template = (PROJECT_ROOT / "packaging/aur/PKGBUILD.in").read_text(
        encoding="utf-8"
    )
    aur_generator = (PROJECT_ROOT / "scripts/generate-aur-package.sh").read_text(
        encoding="utf-8"
    )

    assert "  'hicolor-icon-theme'" in pkgbuild_template
    assert "\tdepends = hicolor-icon-theme" in aur_generator
    assert "/usr/share/icons/hicolor/scalable/apps/wlib.svg" in pkgbuild_template
    assert "/usr/share/icons/hicolor/256x256/apps/wlib.png" in pkgbuild_template
    assert "/usr/share/pixmaps/wlib.png" in pkgbuild_template


def test_native_package_metadata_installs_theme_icon_assets():
    nfpm_config = (PROJECT_ROOT / "packaging/nfpm.yaml").read_text(encoding="utf-8")

    assert "dst: /usr/share/icons/hicolor/scalable/apps/wlib.svg" in nfpm_config
    assert "dst: /usr/share/icons/hicolor/256x256/apps/wlib.png" in nfpm_config
    assert "dst: /usr/share/pixmaps/wlib.png" in nfpm_config


def test_appimage_build_stages_icon_assets_for_desktop_integration():
    build_script = (PROJECT_ROOT / "scripts/build.sh").read_text(encoding="utf-8")

    assert "$APPDIR/usr/share/icons/hicolor/scalable/apps/wlib.svg" in build_script
    assert "$APPDIR/usr/share/icons/hicolor/256x256/apps/wlib.png" in build_script
    assert 'cp "$PROJECT_DIR/wlib.png" "$APPDIR/wlib.png"' in build_script
    assert 'cp "$PROJECT_DIR/wlib.png" "$APPDIR/.DirIcon"' in build_script
    assert 'cp "$PROJECT_DIR/icon.svg" "$APPDIR/.DirIcon"' not in build_script


def test_appimage_build_keeps_qt_xcb_util_libs_and_catalog_name():
    build_script = (PROJECT_ROOT / "scripts/build.sh").read_text(encoding="utf-8")

    for lib in ("cursor", "icccm", "image", "keysyms", "render-util", "util"):
        assert f'! -name "libxcb-{lib}.so*"' in build_script
    assert 'APPIMAGE_NAME="${APP_NAME}-${VERSION}-x86_64.AppImage"' in build_script


def test_package_metadata_declares_gplv3_or_later_license():
    nfpm_config = (PROJECT_ROOT / "packaging/nfpm.yaml").read_text(encoding="utf-8")
    pkgbuild_template = (PROJECT_ROOT / "packaging/aur/PKGBUILD.in").read_text(
        encoding="utf-8"
    )
    aur_generator = (PROJECT_ROOT / "scripts/generate-aur-package.sh").read_text(
        encoding="utf-8"
    )

    assert "license: GPL-3.0-or-later" in nfpm_config
    assert "license=('GPL-3.0-or-later')" in pkgbuild_template
    assert "\tlicense = GPL-3.0-or-later" in aur_generator


def test_aur_package_release_can_be_overridden():
    pkgbuild_template = (PROJECT_ROOT / "packaging/aur/PKGBUILD.in").read_text(
        encoding="utf-8"
    )
    aur_generator = (PROJECT_ROOT / "scripts/generate-aur-package.sh").read_text(
        encoding="utf-8"
    )

    assert "pkgrel=@PKGREL@" in pkgbuild_template
    assert 'PKGREL="${WLIB_AUR_PKGREL:-1}"' in aur_generator
    assert "\tpkgrel = ${PKGREL}" in aur_generator


def test_release_notes_declare_gplv3_or_later_license_change():
    release_workflow = (PROJECT_ROOT / ".github/workflows/release.yml").read_text(
        encoding="utf-8"
    )

    assert (
        "Starting with v0.3.4, wLib is licensed under GPL-3.0-or-later."
        in release_workflow
    )


def _read_flatpak_manifest_text() -> str:
    manifest = PROJECT_ROOT / "packaging/flatpak/io.github.kirin_3.wLib.yml"
    return manifest.read_text(encoding="utf-8")


def test_flatpak_manifest_declares_app_id_and_runtime():
    text = _read_flatpak_manifest_text()

    assert "app-id: io.github.kirin_3.wLib" in text
    assert "runtime: org.freedesktop.Platform" in text
    assert "24.08" in text
    assert "buildsystem: simple" in text


def test_flatpak_manifest_requires_host_access_finish_args():
    text = _read_flatpak_manifest_text()

    assert "--talk-name=org.freedesktop.Flatpak" in text
    assert "--filesystem=host" in text
    assert "--device=dri" in text
    assert "QTWEBENGINE_DISABLE_SANDBOX=1" in text


def test_flatpak_metainfo_and_desktop_ids_match():
    manifest_text = _read_flatpak_manifest_text()
    metainfo = (
        PROJECT_ROOT / "packaging/flatpak/io.github.kirin_3.wLib.metainfo.xml"
    ).read_text(encoding="utf-8")

    assert "<id>io.github.kirin_3.wLib</id>" in metainfo
    assert "io.github.kirin_3.wLib.desktop" in metainfo
    # Metainfo states host execution.
    assert "host" in metainfo.lower()
    # Manifest installs desktop entry, icons and metainfo under the app ID.
    assert "io.github.kirin_3.wLib.metainfo.xml" in manifest_text
    assert "wlib.desktop" in manifest_text


def test_flatpak_build_reuses_staged_release_folder():
    build_script = (PROJECT_ROOT / "scripts/build.sh").read_text(encoding="utf-8")

    assert "build_flatpak()" in build_script
    assert 'WLIB_BUILD_FLATPAK="${WLIB_BUILD_FLATPAK:-auto}"' in build_script
    assert 'FLATPAK_NAME="${APP_NAME}-${VERSION}-x86_64.flatpak"' in build_script
    assert "flatpak-builder" in build_script
    assert "flatpak build-bundle" in build_script
