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
