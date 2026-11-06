from pathlib import Path

import pytest

from core.download_links import classify_build, parse_download_links

FIXTURES = Path(__file__).parent / "fixtures" / "f95_download_links"


@pytest.mark.parametrize(("label", "expected"), [
    ("Win - Update Only", ("windows", "patch")),
    ("Win x64", ("windows", "full")),
    ("PC - Android", ("android", "full")),
    ("Linux hotfix", ("linux", "patch")),
    ("Win/Linux", ("windows/linux", "full")),
])
def test_classify(label, expected):
    assert classify_build(label) == expected


def test_signed_in_first_post():
    result = parse_download_links((FIXTURES / "signed_in.html").read_text())
    assert result["signed_in"] is True
    assert result["version"] == "v0.5.8"
    builds = result["builds"]
    assert builds[0]["platform"] == "windows/linux"
    assert builds[0]["mirrors"][0]["host"] == "pixeldrain"
    assert "&k=fake" in builds[0]["mirrors"][0]["url"]
    assert any(b["section"] == "Season 2" and b["kind"] == "patch" for b in builds)
    assert {"mac", "android"} <= {b["platform"] for b in builds}
    mirrors = [m for b in builds for m in b["mirrors"]]
    assert len(mirrors) == 7
    assert [m["part"] for m in mirrors if m["split"]] == ["Part 1", "Part 2"]
    assert any(b["section"] == "Part 3" and not b["mirrors"][0]["split"] for b in builds)


def test_no_heading_and_guest():
    result = parse_download_links((FIXTURES / "no_heading.html").read_text())
    assert len(result["builds"][0]["mirrors"]) == 2
    assert not parse_download_links((FIXTURES / "guest.html").read_text())["signed_in"]


def test_links_inside_bold_rows_and_host_hints():
    result = parse_download_links((FIXTURES / "bold_rows.html").read_text())
    assert result["version"] == "Ch.2 v1.0"
    builds = {(b["label"], b["kind"]): b for b in result["builds"]}
    mirrors = [m for b in result["builds"] for m in b["mirrors"]]
    assert all("overview" not in m["url"] for m in mirrors)
    assert all("itch.io" not in m["url"] for m in mirrors)
    win_linux = builds[("Win/Linux", "full")]
    assert [m["host"] for m in win_linux["mirrors"]] == ["megaup.net", "mega"]
    assert win_linux["platform"] == "windows/linux"
    assert [m["host"] for m in builds[("Mac", "full")]["mirrors"]] == ["buzzheavier"]
    win = builds[("Win", "full")]
    assert [(m["host"], m["part"], m["split"]) for m in win["mirrors"][:2]] == [
        ("gofile", "Part 1", True), ("gofile", "Part 2", True)]
    assert any(m["host"] == "attachments.f95zone.to" for m in mirrors)
    extra = builds[("Win/Linux", "extra")]
    assert extra["mirrors"][0]["host"] == "pixeldrain"


def test_bold_separator_text_is_not_a_heading():
    html = ('<html data-logged-in="true"><article class="message-threadStarterPost"><div class="bbWrapper">'
            '<b>DOWNLOAD</b><br><b>Season 2</b><br><b>Linux: <a href="https://pixeldrain.com/u/a">PD</a> - '
            '<a href="https://mega.nz/file/b#c">MEGA</a></b></div></article></html>')
    builds = parse_download_links(html)["builds"]
    assert len(builds) == 1
    assert builds[0]["section"] == "Season 2" and builds[0]["label"] == "Linux"
    assert [m["host"] for m in builds[0]["mirrors"]] == ["pixeldrain", "mega"]


def test_mirror_url_is_decoded_once():
    html = (
        '<html data-logged-in="true"><article class="message-threadStarterPost">'
        '<div class="bbWrapper"><b>DOWNLOAD</b><br><b>Windows</b>: '
        '<a href="https://example.com/g.zip?dl=1&amp;param=2&amp;not=3&amp;amp=4">Mirror</a>'
        "</div></article></html>"
    )
    mirror = parse_download_links(html)["builds"][0]["mirrors"][0]
    assert mirror["url"] == "https://example.com/g.zip?dl=1&param=2&not=3&amp=4"
