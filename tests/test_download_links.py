from pathlib import Path

import pytest

from core.download_links import classify_build, parse_download_links

FIXTURES = Path(__file__).parent / "fixtures" / "f95_download_links"


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("Win - Update Only", ("windows", "patch")),
        ("Win x64", ("windows", "full")),
        ("PC - Android", ("android", "full")),
        ("Linux hotfix", ("linux", "patch")),
        ("Win/Linux", ("windows/linux", "full")),
        ("Android", ("android", "full")),
        ("APK", ("android", "full")),
        ("Patch - APK", ("android", "patch")),
        ("iOS (IPA)", ("ios", "full")),
        ("iPhone", ("ios", "full")),
        ("iPad", ("ios", "full")),
    ],
)
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
    assert any(
        b["section"] == "Part 3" and not b["mirrors"][0]["split"] for b in builds
    )


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
        ("gofile", "Part 1", True),
        ("gofile", "Part 2", True),
    ]
    assert any(m["host"] == "attachments.f95zone.to" for m in mirrors)
    extra = builds[("Win/Linux", "extra")]
    assert extra["mirrors"][0]["host"] == "pixeldrain"


def test_bold_separator_text_is_not_a_heading():
    html = (
        '<html data-logged-in="true"><article class="message-threadStarterPost"><div class="bbWrapper">'
        '<b>DOWNLOAD</b><br><b>Season 2</b><br><b>Linux: <a href="https://pixeldrain.com/u/a">PD</a> - '
        '<a href="https://mega.nz/file/b#c">MEGA</a></b></div></article></html>'
    )
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
    from core import update_downloads

    html = (
        '<html data-logged-in="true"><article class="message-threadStarterPost"><div class="bbWrapper">'
        '<b>DOWNLOAD</b><br><b>Win: <a href="https://mega.nz/file/a#b">MEGA</a> - '
        '<a href="https://mega.io/file/c#d">MEGA</a> - <a href="https://bzzhr.co/e">BZZHR</a> - '
        '<a href="https://1fichier.com/?abc">1fichier</a> - '
        '<a href="https://krakenfiles.com/view/abc/file.html">KrakenFiles</a></b>'
        "</div></article></html>"
    )
    mirrors = parse_download_links(html)["builds"][0]["mirrors"]
    assert [m["host"] for m in mirrors] == [
        "mega",
        "mega.io",
        "bzzhr.co",
        "1fichier",
        "krakenfiles",
    ]
    assert [update_downloads._in_app(m["host"], m["url"]) for m in mirrors] == [
        True,
        False,
        False,
        False,
        False,
    ]


def test_lc_downloads():
    result = parse_download_links(
        (FIXTURES.parent / "lc_thread_downloads.html").read_text(), "lc"
    )
    assert result["signed_in"] and result["version"] == "1.20"
    assert [build["platform"] for build in result["builds"]] == ["windows", "linux"]
    for build in result["builds"]:
        assert [mirror["host"] for mirror in build["mirrors"]] == [
            "pixeldrain",
            "mega",
            "gofile",
            "buzzheavier",
            "mixdrop",
        ]
        assert all(
            "proxy-leak.invalid" not in mirror["url"] for mirror in build["mirrors"]
        )
        assert all("/threads/" not in mirror["url"] for mirror in build["mirrors"])
        assert build["mirrors"][0]["url"].startswith(
            "https://lewdcorner.com/masked/out?"
        )


def test_lc_title_fallback_and_hidden_links():
    html = (
        (FIXTURES.parent / "lc_thread_downloads_guest.html")
        .read_text()
        .replace('data-logged-in="false"', 'data-logged-in="true"')
    )
    result = parse_download_links(html, "lc")
    assert not result["signed_in"] and not result["builds"]
    assert (
        parse_download_links(
            '<h1 class="p-title-value">Example Game [v1.2.0]</h1>', "lc"
        )["version"]
        == "v1.2.0"
    )


@pytest.mark.parametrize(
    ("status", "body", "code"),
    [
        (200, "lc_thread_downloads.html", ""),
        (200, "lc_thread_downloads_guest.html", "lc_login_required"),
        (
            200,
            '<html data-template="login" data-logged-in="false">',
            "lc_login_required",
        ),
        (403, '<html data-template="error" data-logged-in="true">', "lc_plus_required"),
        (403, '<html><form id="challenge-form">', "blocked"),
        (404, "", "not_found"),
        (None, "", "lc_extension_unavailable"),
    ],
)
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
    result = scraper.fetch_thread_download_links(
        "https://lewdcorner.com/threads/example.25467/", "lc"
    )
    assert result["success"] == (not code)
    if code:
        assert result["code"] == code and "builds" not in result
    else:
        assert len(result["builds"]) == 2 and result["version"] == "1.20"


def test_lc_download_links_parse_the_thread_page_once(monkeypatch):
    from core import extension_bridge, lewdcorner, scraper

    html = (FIXTURES.parent / "lc_thread_downloads.html").read_text()
    canonical = "https://lewdcorner.com/threads/25467/"
    monkeypatch.setattr(
        extension_bridge,
        "fetch",
        lambda url, *, site: extension_bridge.BridgeResponse(200, canonical, html),
    )
    calls = []
    real = lewdcorner.parse_thread_page
    monkeypatch.setattr(
        lewdcorner,
        "parse_thread_page",
        lambda *args, **kwargs: calls.append(1) or real(*args, **kwargs),
    )
    result = scraper.fetch_thread_download_links(canonical, "lc")
    assert result["success"] and result["version"] == "1.20" and len(calls) == 1
    # An empty version from the first parse falls back to the title.
    assert (
        parse_download_links(
            '<h1 class="p-title-value">Example Game [v1.2.0]</h1>',
            "lc",
            page_version="",
        )["version"]
        == "v1.2.0"
    )


def test_lc_downloads_from_real_page_markup():
    # Cut from a signed-in LC page: custom fields come before the post body,
    # and every masked link carries a proxy attribute that must stay unread.
    result = parse_download_links(
        (FIXTURES.parent / "lc_thread_downloads_real.html").read_text(), "lc"
    )
    assert result["signed_in"] and result["version"] == "v0.10.0"
    assert [(build["label"], build["platform"]) for build in result["builds"]] == [
        ("Win", "windows"),
        ("Linux", "linux"),
        ("Android", "android"),
    ]
    for build in result["builds"]:
        assert [mirror["host"] for mirror in build["mirrors"]] == [
            "pixeldrain",
            "buzzheavier",
            "vikingfile",
            "datanodes",
            "mixdrop",
        ]
        urls = [mirror["url"] for mirror in build["mirrors"]]
        assert all(
            url.startswith("https://lewdcorner.com/masked/out?") for url in urls[:4]
        )
        assert urls[4].startswith("https://mxdrop.sx/f/")
        assert not any("proxy" in url or "attachments" in url for url in urls)


def _spoiler(title, body):
    return (
        '<div class="bbCodeSpoiler"><button type="button" class="button bbCodeSpoiler-button">'
        f'<span class="button-text"><span>Spoiler: <span class="bbCodeSpoiler-button-title">{title}</span>'
        f'</span></span></button><div class="bbCodeSpoiler-content">{body}</div></div>'
    )


def _post(body, version="v0.21"):
    return (
        f'<html data-logged-in="true"><h1 class="p-title-value">Game [{version}] [Dev]</h1>'
        f'<article class="message-threadStarterPost"><div class="bbWrapper">{body}</div></article></html>'
    )


@pytest.mark.parametrize(
    ("title", "version", "expected"),
    [
        ("Part 7", "v0.21", "part"),
        ("Before the Tech Update (v0.20.16)", "v0.21", "old"),
        ("Episode 2 (v0.21)", "v0.21", "ignore"),
        ("Episode 2 (v0.21)", "0.21", "ignore"),
        ("Old Versions", "v0.21", "old"),
        ("Previous builds", "v0.21", "old"),
        ("Legacy", "v0.21", "old"),
        ("2024-05-01 build", "v0.21", "old"),
        ("01.05.2024 build", "v0.21", "old"),
        ("Downloads v0.5", "v0.21", "download"),
        ("Changelog", "v0.21", "ignore"),
        ("Installation", "v0.21", "ignore"),
        ("Compressed (1.5 GB)", "v0.21", "ignore"),
        ("Win (2.25 GiB)", "v0.21", "ignore"),
        ("", "v0.21", "ignore"),
    ],
)
def test_spoiler_title_kind(title, version, expected):
    from core.download_links import spoiler_title_kind

    assert spoiler_title_kind(title, version) == expected


def test_installation_spoiler_names_no_build():
    html = _post(
        _spoiler("Installation", "Extract and run.")
        + '<b>DOWNLOAD</b><br><b>Win</b>: <a href="https://mega.nz/file/a#b">MEGA</a>'
    )
    builds = parse_download_links(html)["builds"]
    assert [(b["label"], b["section"], b["kind"]) for b in builds] == [
        ("Win", "", "full")
    ]


def test_current_build_after_old_version_spoiler():
    html = _post(
        "<b>DOWNLOAD</b><br>"
        + _spoiler(
            "Before the Tech Update (v0.20.16)",
            'Win: <a href="https://mega.nz/file/old#k">MEGA</a>',
        )
        + '<b>Win</b>: <a href="https://mega.nz/file/new#k">MEGA</a>'
    )
    builds = parse_download_links(html)["builds"]
    assert [(b["label"], b["section"], b["kind"]) for b in builds] == [
        ("Win", "Before the Tech Update (v0.20.16)", "old"),
        ("Win", "", "full"),
    ]
    full = builds[1]
    # The old MEGA link listed first no longer joins the current build.
    assert [m["url"] for m in full["mirrors"]] == ["https://mega.nz/file/new#k"]


def test_part_spoilers_give_two_builds():
    html = _post(
        "<b>DOWNLOAD</b><br>"
        + _spoiler("Part 7", '<b>Win</b>: <a href="https://mega.nz/file/p7#k">MEGA</a>')
        + _spoiler("Part 8", '<b>Win</b>: <a href="https://mega.nz/file/p8#k">MEGA</a>')
    )
    builds = parse_download_links(html)["builds"]
    assert [(b["section"], b["platform"], b["kind"]) for b in builds] == [
        ("Part 7", "windows", "full"),
        ("Part 8", "windows", "full"),
    ]


def test_same_platform_inside_and_outside_old_spoiler():
    html = _post(
        "<b>DOWNLOAD</b><br>"
        + _spoiler(
            "Old Versions", '<b>Win</b>: <a href="https://mega.nz/file/old#k">MEGA</a>'
        )
        + '<b>Win</b>: <a href="https://mega.nz/file/new#k">MEGA</a>'
    )
    builds = parse_download_links(html)["builds"]
    assert [(b["kind"], b["platform"], len(b["mirrors"])) for b in builds] == [
        ("old", "windows", 1),
        ("full", "windows", 1),
    ]


def test_patch_inside_old_spoiler_is_old():
    html = _post(
        "<b>DOWNLOAD</b><br>"
        + _spoiler(
            "Old Versions",
            '<b>Win - Update Only</b>: <a href="https://mega.nz/file/old#k">MEGA</a>',
        )
    )
    assert [b["kind"] for b in parse_download_links(html)["builds"]] == ["old"]


def test_spoiler_with_current_version_is_not_old():
    html = _post(
        "<b>DOWNLOAD</b><br>"
        + _spoiler(
            "Episode 2 (v0.21)",
            '<b>Win</b>: <a href="https://mega.nz/file/e2#k">MEGA</a>',
        ),
        version="0.21",
    )
    assert [b["kind"] for b in parse_download_links(html)["builds"]] == ["full"]


def test_lc_reads_spoilers_the_same_way():
    body = (
        "<b>DOWNLOAD</b><br>"
        + _spoiler(
            "Old Versions", '<b>Win</b>: <a href="https://mega.nz/file/old#k">MEGA</a>'
        )
        + '<b>Win</b>: <a href="https://mega.nz/file/new#k">MEGA</a>'
    )
    html = _post(body).replace(
        '<article class="message-threadStarterPost">',
        '<article class="message message--post">',
    )
    f95 = parse_download_links(_post(body))["builds"]
    lc = parse_download_links(html, "lc", page_version="v0.21")["builds"]
    assert [
        (b["kind"], b["section"], [m["url"] for m in b["mirrors"]]) for b in lc
    ] == [(b["kind"], b["section"], [m["url"] for m in b["mirrors"]]) for b in f95]


def test_spoiler_in_hidden_quote_copy_is_not_read():
    html = _post(
        "<b>DOWNLOAD</b><br>"
        + '<span class="u-hidden">'
        + _spoiler("Old Versions", "Win: ")
        + "</span>"
        + '<b>Win</b>: <a href="https://mega.nz/file/new#k">MEGA</a>'
    )
    builds = parse_download_links(html)["builds"]
    assert [(b["label"], b["section"], b["kind"]) for b in builds] == [
        ("Win", "", "full")
    ]


def test_hidden_regions_and_trap_links_are_skipped():
    html = _post(
        "<b>DOWNLOAD</b><br><b>Win</b>: "
        '<div aria-hidden="true"><a href="https://mega.nz/file/aria#k">MEGA</a></div>'
        '<div class="u-hidden"><a href="https://gofile.io/d/uhid">GoFile</a></div>'
        '<a href="https://pixeldrain.com/u/trap" tabindex="-1">Pixeldrain</a>'
        '<a href="https://pixeldrain.com/u/real">'
        '<img aria-hidden="true" src="icon.png">'
        '<i class="fa" aria-hidden="true">icon</i>Pixeldrain</a>'
    )
    builds = parse_download_links(html)["builds"]
    assert len(builds) == 1
    assert [(m["url"], m["host"], m["label"]) for m in builds[0]["mirrors"]] == [
        ("https://pixeldrain.com/u/real", "pixeldrain", "Pixeldrain")
    ]


@pytest.mark.parametrize(
    ("label", "expected_host"),
    [
        ("1fichier", "1fichier"),
        ("Krakenfiles", "krakenfiles"),
        ("Files.fm", "filesfm"),
        ("MEGA", "mega"),
        ("Google Drive", "gdrive"),
        ("Download", "unknown"),
        ("Download files", "unknown"),
    ],
)
def test_masked_link_without_favicon_names_host_from_label(label, expected_host):
    html = _post(
        "<b>DOWNLOAD</b><br><b>Win</b>: "
        f'<a href="https://f95zone.to/dl/1/?k=abc">{label}</a>'
    )
    mirror = parse_download_links(html)["builds"][0]["mirrors"][0]
    assert mirror["host"] == expected_host


def test_headingless_post_starts_at_newly_known_host():
    html = _post(
        '<a href="https://example.com/blog">Blog</a><br>'
        '<b>APK</b>: <a href="https://1fichier.com/?xyz">1fichier</a>'
    )
    builds = parse_download_links(html)["builds"]
    assert len(builds) == 1
    assert builds[0]["platform"] == "android"
    assert [m["host"] for m in builds[0]["mirrors"]] == ["1fichier"]


def test_elements_collects_plain_text_lines():
    from core.download_links import _Elements

    html = _post(
        "<b>DOWNLOAD</b><br>"
        '<b>Win</b>: <a href="https://mega.nz/file/a#b">MEGA</a><br>'
        '<span class="u-hidden">Password: hidden-secret</span>'
        "<p>Password:</p>"
        + _spoiler(
            "Password",
            '<div class="bbCodeBlock-title">Code:</div>'
            "<pre><code>mysecret\nline2</code></pre>",
        )
    )
    parser = _Elements()
    parser.feed(html)
    assert parser.lines == [
        "DOWNLOAD",
        "Win: MEGA",
        "Password:",
        "Code:",
        "mysecret",
        "line2",
    ]


@pytest.mark.parametrize(
    ("lines", "expected"),
    [
        (["DOWNLOAD", "Password: f95zone"], "f95zone"),
        (["DOWNLOAD", "Password:", "Spoiler: Password", "gamepass123"], "gamepass123"),
        (
            ["DOWNLOAD", "Password:", "Code:", "Click to expand...", "secret42"],
            "secret42",
        ),
        (["DOWNLOAD", "Пароль на архив: abc123"], "abc123"),
        (["DOWNLOAD", "Pass - dashpass"], "dashpass"),
        (["DOWNLOAD", "pwd=eqpass"], "eqpass"),
        (["DOWNLOAD", "Archive password: archpass"], "archpass"),
        (["DOWNLOAD", "Password: none", "Win: MEGA"], ""),
        (["DOWNLOAD", "Password: N/A"], ""),
        (["DOWNLOAD", "Password: not required"], ""),
        (["DOWNLOAD", "Password: see below", "Win: MEGA"], ""),
        (["DOWNLOAD", "Password: in spoiler", "Win: MEGA"], ""),
        (["DOWNLOAD", 'Password: "my game 2"'], "my game 2"),
        (["DOWNLOAD", "Pass: xyz789 (case sensitive)"], "xyz789"),
        (["DOWNLOAD", "Password: f95zone."], "f95zone"),
        (["DOWNLOAD", "Win: MEGA"], ""),
        (["DOWNLOAD", "Password: https://example.com/pass"], ""),
        (["DOWNLOAD", "Password: " + "a" * 129], ""),
        (["DOWNLOAD", "Password: " + "a" * 128], "a" * 128),
        (["Password: before", "DOWNLOAD", "Win: MEGA"], "before"),
        (["Password: before", "DOWNLOAD", "Password: after"], "after"),
        (["Season Pass: included", "DOWNLOAD", "Win: MEGA"], ""),
        (["DOWNLOAD", "Battle pass - free"], ""),
        (["DOWNLOAD", "Rar pass: rarpw"], "rarpw"),
        (["DOWNLOAD", "• PW: bulletpw"], "bulletpw"),
        (["DOWNLOAD", "Password:", "Win: MEGA - PIXELDRAIN"], ""),
        (["DOWNLOAD", "Password:", "Pass: inner"], "inner"),
        (["Password:", "DOWNLOAD", "Win: MEGA"], ""),
        (
            ["DOWNLOAD", "Password:", "Spoiler", "Code:", "Click to expand...", "deep"],
            "deep",
        ),
    ],
)
def test_find_archive_password(lines, expected):
    from core.download_links import find_archive_password

    assert find_archive_password(lines) == expected


def test_parse_download_links_extracts_archive_password_for_f95_and_lc():
    f95_html = _post(
        '<b>DOWNLOAD</b><br><b>Win</b>: <a href="https://mega.nz/file/a#b">MEGA</a><br>'
        "<b>Password:</b><br>" + _spoiler("Password", "gamepass123")
    )
    f95 = parse_download_links(f95_html)
    assert f95["archive_password"] == "gamepass123"

    lc_html = _post(
        '<b>DOWNLOAD</b><br><b>APK</b>: <a href="https://mega.nz/file/a#b">MEGA</a><br>'
        "Password: lewdcorner"
    ).replace(
        '<article class="message-threadStarterPost">',
        '<article class="message message--post">',
    )
    lc = parse_download_links(lc_html, "lc", page_version="v0.21")
    assert lc["archive_password"] == "lewdcorner"
    assert lc["builds"][0]["platform"] == "android"

    assert (
        parse_download_links((FIXTURES / "signed_in.html").read_text())[
            "archive_password"
        ]
        == ""
    )
    assert (
        parse_download_links(
            (FIXTURES.parent / "lc_thread_downloads.html").read_text(), "lc"
        )["archive_password"]
        == ""
    )


def test_fetch_thread_download_links_passes_password_without_logging_it(
    monkeypatch, caplog
):
    import logging

    from core import extension_bridge, scraper

    secret = "SuperSecretArchivePass99"
    f95_html = _post(
        '<b>DOWNLOAD</b><br><b>Win</b>: <a href="https://mega.nz/file/a#b">MEGA</a><br>'
        f"Password: {secret}"
    )
    monkeypatch.setattr(extension_bridge, "is_available", lambda: True)
    monkeypatch.setattr(scraper, "_f95_get_via_extension", lambda _url: (200, f95_html))
    with caplog.at_level(logging.DEBUG):
        f95_res = scraper.fetch_thread_download_links(
            "https://f95zone.to/threads/example.12345/"
        )
    assert f95_res["success"] is True
    assert f95_res["archive_password"] == secret
    assert any("found 1 download links" in r.getMessage() for r in caplog.records)
    assert all(secret not in r.getMessage() for r in caplog.records)

    lc_html = (
        (FIXTURES.parent / "lc_thread_downloads.html")
        .read_text()
        .replace("<b>DOWNLOAD</b><br>", f"<b>DOWNLOAD</b><br>Password: {secret}<br>")
    )
    canonical = "https://lewdcorner.com/threads/25467/"
    monkeypatch.setattr(
        extension_bridge,
        "fetch",
        lambda _url, *, site: extension_bridge.BridgeResponse(200, canonical, lc_html),
    )
    caplog.clear()
    with caplog.at_level(logging.DEBUG):
        lc_res = scraper.fetch_thread_download_links(canonical, "lc")
    assert lc_res["success"] is True
    assert lc_res["archive_password"] == secret
    assert all(secret not in r.getMessage() for r in caplog.records)
