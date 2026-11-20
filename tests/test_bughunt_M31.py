from core.download_links import parse_download_links


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


def test_downloads_spoiler_scope_ends_at_its_close():
    html = _post(
        _spoiler("Downloads", '<b>Win</b>: <a href="https://mega.nz/file/a#b">MEGA</a>')
        + 'Screenshots: <a href="https://pixeldrain.com/u/shots">Pixeldrain</a>'
    )
    builds = parse_download_links(html)["builds"]
    assert [(b["label"], b["platform"], b["kind"]) for b in builds] == [("Win", "windows", "full")]
    assert [m["host"] for m in builds[0]["mirrors"]] == ["mega"]


def test_bold_heading_before_downloads_spoiler_stays_active():
    html = _post(
        "<b>DOWNLOAD</b><br>"
        + _spoiler("Downloads", '<b>Win</b>: <a href="https://mega.nz/file/a#b">MEGA</a>')
        + '<b>Linux</b>: <a href="https://pixeldrain.com/u/lin">Pixeldrain</a>'
    )
    builds = parse_download_links(html)["builds"]
    assert [(b["label"], b["platform"]) for b in builds] == [("Win", "windows"), ("Linux", "linux")]
