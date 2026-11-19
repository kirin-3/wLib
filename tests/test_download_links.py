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


def test_domains_the_download_adapters_reject_are_not_named_as_in_app_hosts():
    html = ('<html data-logged-in="true"><article class="message-threadStarterPost"><div class="bbWrapper">'
            '<b>DOWNLOAD</b><br><b>Win: <a href="https://mega.nz/file/a#b">MEGA</a> - '
            '<a href="https://mega.io/file/c#d">MEGA</a> - <a href="https://bzzhr.co/e">BZZHR</a></b>'
            '</div></article></html>')
    mirrors = parse_download_links(html)["builds"][0]["mirrors"]
    assert [m["host"] for m in mirrors] == ["mega", "mega.io", "bzzhr.co"]


def test_lc_downloads():
    result = parse_download_links((FIXTURES.parent / "lc_thread_downloads.html").read_text(), "lc")
    assert result["signed_in"] and result["version"] == "1.20"
    assert [build["platform"] for build in result["builds"]] == ["windows", "linux"]
    for build in result["builds"]:
        assert [mirror["host"] for mirror in build["mirrors"]] == [
            "pixeldrain", "mega", "gofile", "buzzheavier", "mixdrop",
        ]
        assert all("proxy-leak.invalid" not in mirror["url"] for mirror in build["mirrors"])
        assert all("/threads/" not in mirror["url"] for mirror in build["mirrors"])
        assert build["mirrors"][0]["url"].startswith("https://lewdcorner.com/masked/out?")


def test_lc_title_fallback_and_hidden_links():
    html = (FIXTURES.parent / "lc_thread_downloads_guest.html").read_text().replace(
        'data-logged-in="false"', 'data-logged-in="true"'
    )
    result = parse_download_links(html, "lc")
    assert not result["signed_in"] and not result["builds"]
    assert parse_download_links(
        '<h1 class="p-title-value">Example Game [v1.2.0]</h1>', "lc"
    )["version"] == "v1.2.0"


@pytest.mark.parametrize(("status", "body", "code"), [
    (200, "lc_thread_downloads.html", ""),
    (200, "lc_thread_downloads_guest.html", "lc_login_required"),
    (200, '<html data-template="login" data-logged-in="false">', "lc_login_required"),
    (403, '<html data-template="error" data-logged-in="true">', "lc_plus_required"),
    (403, '<html><form id="challenge-form">', "blocked"),
    (404, "", "not_found"),
    (None, "", "lc_extension_unavailable"),
])
def test_fetch_lc_download_links(monkeypatch, status, body, code):
    from core import extension_bridge, scraper

    canonical = "https://lewdcorner.com/threads/25467/"

    def fake_fetch(url, *, site):
        assert site == "lc" and url == canonical
        if status is None:
            raise extension_bridge.BridgeUnavailable("no extension")
        html = (FIXTURES.parent / body).read_text() if body.endswith(".html") else body
        return extension_bridge.BridgeResponse(status, canonical, html)

    monkeypatch.setattr(extension_bridge, "fetch", fake_fetch)
    result = scraper.fetch_thread_download_links("https://lewdcorner.com/threads/example.25467/", "lc")
    assert result["success"] == (not code)
    if code:
        assert result["code"] == code and "builds" not in result
    else:
        assert len(result["builds"]) == 2 and result["version"] == "1.20"


def test_lc_download_links_parse_the_thread_page_once(monkeypatch):
    from core import extension_bridge, lewdcorner, scraper

    html = (FIXTURES.parent / "lc_thread_downloads.html").read_text()
    canonical = "https://lewdcorner.com/threads/25467/"
    monkeypatch.setattr(extension_bridge, "fetch", lambda url, *, site: extension_bridge.BridgeResponse(200, canonical, html))
    calls = []
    real = lewdcorner.parse_thread_page
    monkeypatch.setattr(lewdcorner, "parse_thread_page", lambda *args, **kwargs: calls.append(1) or real(*args, **kwargs))
    result = scraper.fetch_thread_download_links(canonical, "lc")
    assert result["success"] and result["version"] == "1.20" and len(calls) == 1
    # An empty version from the first parse falls back to the title.
    assert parse_download_links(
        '<h1 class="p-title-value">Example Game [v1.2.0]</h1>', "lc", page_version=""
    )["version"] == "v1.2.0"


def test_lc_downloads_from_real_page_markup():
    # Cut from a signed-in LC page: custom fields come before the post body,
    # and every masked link carries a proxy attribute that must stay unread.
    result = parse_download_links((FIXTURES.parent / "lc_thread_downloads_real.html").read_text(), "lc")
    assert result["signed_in"] and result["version"] == "v0.10.0"
    assert [(build["label"], build["platform"]) for build in result["builds"]] == [
        ("Win", "windows"), ("Linux", "linux"), ("Android", "android"),
    ]
    for build in result["builds"]:
        assert [mirror["host"] for mirror in build["mirrors"]] == [
            "pixeldrain", "buzzheavier", "vikingfile", "datanodes", "mixdrop",
        ]
        urls = [mirror["url"] for mirror in build["mirrors"]]
        assert all(url.startswith("https://lewdcorner.com/masked/out?") for url in urls[:4])
        assert urls[4].startswith("https://mxdrop.sx/f/")
        assert not any("proxy" in url or "attachments" in url for url in urls)
