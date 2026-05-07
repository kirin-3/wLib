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
