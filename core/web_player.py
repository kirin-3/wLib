# SPDX-License-Identifier: GPL-3.0-or-later
"""Built-in web game player: one QtWebEngine window per game, run as its own process.

The player takes everything from argv (the sandbox hides the database) and
imports nothing from `core.api`. Qt is imported only inside `run()`, so the
pure helpers stay importable and testable without a display.
"""
from __future__ import annotations

import html
import mimetypes
import os
import sys
from dataclasses import dataclass
from typing import Literal, override
from urllib.parse import quote, unquote, urlsplit

SCHEME = "wlib-game"
GAME_HOST = "game"
RUFFLE_HOST = "ruffle"
FLASH_PAGE = "__wlib_flash__.html"
USAGE = (
    "Usage: wlib --play-web --root <game dir> --entry <relative path> "
    "--profile <storage dir> --title <title> [--flash] [--offline] "
    "[--links open|ignore]"
)

EXTRA_CONTENT_TYPES = {
    ".wasm": "application/wasm",
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".webm": "video/webm",
    ".mp4": "video/mp4",
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".swf": "application/x-shockwave-flash",
    ".htm": "text/html",
    ".html": "text/html",
    ".css": "text/css",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".otf": "font/otf",
}

LinkMode = Literal["open", "ignore"]
NavigationAction = Literal["allow", "open", "drop"]


class UsageError(ValueError):
    """Raised when the player argv is malformed."""


@dataclass(frozen=True)
class PlayerArgs:
    root: str
    entry: str
    profile: str
    title: str
    flash: bool = False
    offline: bool = False
    links: LinkMode = "open"


def parse_args(argv: list[str]) -> PlayerArgs:
    """Parse the argv that follows `--play-web`, or raise UsageError."""
    values: dict[str, str] = {}
    flags: set[str] = set()
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg in ("--flash", "--offline"):
            flags.add(arg)
            index += 1
        elif arg in ("--root", "--entry", "--profile", "--title", "--links"):
            if index + 1 >= len(argv):
                raise UsageError(f"{arg} needs a value")
            values[arg] = argv[index + 1]
            index += 2
        else:
            raise UsageError(f"Unknown argument: {arg}")
    for required in ("--root", "--entry", "--profile"):
        if not values.get(required, "").strip():
            raise UsageError(f"{required} is required")
    links = values.get("--links", "open")
    if links not in ("open", "ignore"):
        raise UsageError("--links must be open or ignore")
    root = os.path.abspath(values["--root"])
    entry = values["--entry"].replace("\\", "/").lstrip("/")
    if resolve_game_file(root, entry) is None:
        raise UsageError(f"Entry file not found inside the game folder: {entry}")
    return PlayerArgs(
        root=root,
        entry=entry,
        profile=os.path.abspath(values["--profile"]),
        title=values.get("--title", "").strip() or "wLib Web Player",
        flash="--flash" in flags,
        offline="--offline" in flags,
        links=links,  # type: ignore[arg-type]
    )


def content_type(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in EXTRA_CONTENT_TYPES:
        return EXTRA_CONTENT_TYPES[ext]
    guessed, _ = mimetypes.guess_type(path)
    return guessed or "application/octet-stream"


def resolve_under_root(root: str, url_path: str) -> str | None:
    """Map a URL path onto a regular file inside root, or None when it escapes or is missing."""
    relative = unquote(url_path).replace("\\", "/").lstrip("/")
    if "\x00" in relative:
        return None
    if any(part == ".." for part in relative.split("/")):
        return None
    real_root = os.path.realpath(root)
    candidate = os.path.realpath(os.path.join(real_root, relative))
    try:
        inside = os.path.commonpath([real_root, candidate]) == real_root
    except ValueError:
        return None
    if not inside or not os.path.isfile(candidate):
        return None
    return candidate


def resolve_game_file(root: str, url_path: str) -> tuple[str, str] | None:
    path = resolve_under_root(root, url_path)
    return (path, content_type(path)) if path else None


def flash_page(swf: str) -> bytes:
    """Full-window Ruffle page for a .swf launch target."""
    src = quote(swf, safe="/")
    body = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{html.escape(swf)}</title>
<style>html,body{{margin:0;height:100%;background:#000;overflow:hidden}}
ruffle-player{{display:block;width:100%;height:100%}}</style>
<script src="{SCHEME}://{RUFFLE_HOST}/ruffle.js"></script></head>
<body><script>
window.RufflePlayer = window.RufflePlayer || {{}};
window.addEventListener("load", function () {{
  var ruffle = window.RufflePlayer.newest();
  var player = ruffle.createPlayer();
  document.body.appendChild(player);
  player.load({{url: "{SCHEME}://{GAME_HOST}/{src}", autoplay: "on", unmuteOverlay: "hidden", openUrlMode: "deny"}});
}});
</script></body></html>
"""
    return body.encode("utf-8")


def ruffle_bootstrap_source() -> str:
    """Injected into HTML pages: configure Ruffle's polyfill and load it."""
    return f"""(function () {{
  if (window.top !== window && window.location.protocol !== "{SCHEME}:") return;
  window.RufflePlayer = window.RufflePlayer || {{}};
  window.RufflePlayer.config = Object.assign({{
    publicPath: "{SCHEME}://{RUFFLE_HOST}/",
    polyfills: true,
    autoplay: "on",
    unmuteOverlay: "hidden",
    openUrlMode: "deny"
  }}, window.RufflePlayer.config || {{}});
  function load() {{
    var script = document.createElement("script");
    script.src = "{SCHEME}://{RUFFLE_HOST}/ruffle.js";
    document.documentElement.appendChild(script);
  }}
  if (document.documentElement) {{
    load();
  }} else {{
    // This runs at document creation, before the root element exists.
    var observer = new MutationObserver(function () {{
      if (document.documentElement) {{
        observer.disconnect();
        load();
      }}
    }});
    observer.observe(document, {{childList: true}});
  }}
}})();
"""


def classify_navigation(url: str, links: LinkMode, offline: bool = False) -> NavigationAction:
    """Decide what the player does with a navigation or new-window request."""
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme in (SCHEME, "about", "blob", "data", "devtools", "qrc"):
        return "allow"
    if scheme in ("http", "https", "mailto"):
        if offline or links == "ignore":
            return "drop"
        return "open"
    return "drop"


def is_network_request(url: str) -> bool:
    return urlsplit(url).scheme.lower() in ("http", "https", "ws", "wss", "ftp")


def host_open_env(env: dict[str, str]) -> dict[str, str]:
    """Environment for the system opener, without the AppImage's libraries."""
    clean = dict(env)
    for var in ("APPIMAGE", "APPDIR", "ARGV0", "APPIMAGE_SILENT_INSTALL", "OWD", "APPIMAGE_EXTRACT_AND_RUN"):
        _ = clean.pop(var, None)
    original = str(clean.pop("LD_LIBRARY_PATH_ORIG", "") or "").strip()
    if original:
        clean["LD_LIBRARY_PATH"] = original
    else:
        _ = clean.pop("LD_LIBRARY_PATH", None)
    # The player's own Qt platform and Chromium flags do not belong to a browser.
    for var in ("QT_QPA_PLATFORM", "QTWEBENGINE_CHROMIUM_FLAGS", "QTWEBENGINE_DISABLE_SANDBOX", "QT_PLUGIN_PATH"):
        _ = clean.pop(var, None)
    return clean


def open_url_on_host(url: str) -> bool:
    """Hand an http(s) URL to the system browser; return whether an opener started."""
    if urlsplit(url).scheme.lower() not in ("http", "https"):
        return False
    import shutil
    import subprocess

    from core.host_platform import is_windows

    if is_windows():
        from core.host_platform import open_windows_system_target

        opened, _error = open_windows_system_target(url)
        return bool(opened)
    env = host_open_env(dict(os.environ))
    for command in (["xdg-open", url], ["gio", "open", url], ["kde-open", url]):
        if not shutil.which(command[0], path=env.get("PATH")):
            continue
        try:
            _ = subprocess.Popen(
                command, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return True
        except OSError:
            continue
    return False


def ruffle_root() -> str:
    """Folder holding the bundled Ruffle build (ui/dist/ruffle)."""
    base = (
        str(getattr(sys, "_MEIPASS", ""))
        if getattr(sys, "frozen", False)
        else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    return os.path.join(base, "ui", "dist", "ruffle")


def run(args: PlayerArgs) -> int:
    """Open the player window and block until it closes."""
    # Qt types are imported here so the helpers above need no display or Qt.
    from PyQt6.QtCore import QByteArray, QBuffer, QCoreApplication, QEvent, QFile, QIODevice, QObject, Qt, QUrl
    from PyQt6.QtGui import QKeySequence, QShortcut
    from PyQt6.QtWebEngineCore import (
        QWebEngineProfile,
        QWebEngineScript,
        QWebEngineSettings,
        QWebEngineUrlRequestInfo,
        QWebEngineUrlRequestInterceptor,
        QWebEngineUrlRequestJob,
        QWebEngineUrlScheme,
        QWebEngineUrlSchemeHandler,
        QWebEngineFullScreenRequest,
        QWebEnginePage,
    )
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtWidgets import QApplication

    scheme = QWebEngineUrlScheme(QByteArray(SCHEME.encode()))
    scheme.setSyntax(QWebEngineUrlScheme.Syntax.Host)
    scheme.setFlags(
        QWebEngineUrlScheme.Flag.SecureScheme
        | QWebEngineUrlScheme.Flag.CorsEnabled
        | QWebEngineUrlScheme.Flag.FetchApiAllowed
    )
    QWebEngineUrlScheme.registerScheme(scheme)

    os.makedirs(args.profile, exist_ok=True)
    app = QApplication([sys.argv[0]])
    app.setApplicationName("wLib Web Player")

    ruffle_dir = ruffle_root()

    class SchemeHandler(QWebEngineUrlSchemeHandler):
        def __init__(self, parent: QObject) -> None:
            super().__init__(parent)

        @override
        def requestStarted(self, a0: QWebEngineUrlRequestJob | None) -> None:  # noqa: N802
            if a0 is None:
                return
            job = a0
            url = job.requestUrl()
            host = url.host().lower()
            path = url.path()
            if host == GAME_HOST and path.lstrip("/") == FLASH_PAGE and args.flash:
                self._reply_bytes(job, "text/html", flash_page(args.entry))
                return
            if host == GAME_HOST:
                resolved = resolve_game_file(args.root, path)
            elif host == RUFFLE_HOST:
                resolved = resolve_game_file(ruffle_dir, path)
            else:
                resolved = None
            if resolved is None:
                job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
                return
            file_path, mime = resolved
            handle = QFile(file_path, job)
            if not handle.open(QIODevice.OpenModeFlag.ReadOnly):
                job.fail(QWebEngineUrlRequestJob.Error.RequestFailed)
                return
            job.reply(QByteArray(mime.encode()), handle)

        @staticmethod
        def _reply_bytes(job: QWebEngineUrlRequestJob, mime: str, data: bytes) -> None:
            buffer = QBuffer(job)
            buffer.setData(QByteArray(data))
            _ = buffer.open(QIODevice.OpenModeFlag.ReadOnly)
            job.reply(QByteArray(mime.encode()), buffer)

    class Interceptor(QWebEngineUrlRequestInterceptor):
        @override
        def interceptRequest(self, info: QWebEngineUrlRequestInfo) -> None:  # noqa: N802
            if is_network_request(info.requestUrl().toString()):
                info.block(True)

    def open_externally(url: str) -> None:
        _ = open_url_on_host(url)

    class GamePage(QWebEnginePage):
        @override
        def acceptNavigationRequest(  # noqa: N802
            self, url: QUrl, type: QWebEnginePage.NavigationType, isMainFrame: bool  # noqa: A002, N803
        ) -> bool:
            action = classify_navigation(url.toString(), args.links, args.offline)
            if action == "allow":
                return True
            link_clicked = type == QWebEnginePage.NavigationType.NavigationTypeLinkClicked
            if (
                not isMainFrame
                and not link_clicked
                and not args.offline
                and is_network_request(url.toString())
            ):
                # A remote iframe the page embeds (a video, a widget) loads in
                # place; only links the player leaves go to the browser.
                return True
            if action == "open" and (
                isMainFrame
                or type == QWebEnginePage.NavigationType.NavigationTypeLinkClicked
            ):
                open_externally(url.toString())
            return False

        @override
        def createWindow(self, type: QWebEnginePage.WebWindowType) -> QWebEnginePage | None:  # noqa: A002, N802
            # New windows are routed through a throwaway page so the target URL
            # is classified like any other navigation instead of opening a window.
            probe = _LinkProbe(self.profile(), self)
            return probe

    class _LinkProbe(QWebEnginePage):
        @override
        def acceptNavigationRequest(  # noqa: N802
            self, url: QUrl, type: QWebEnginePage.NavigationType, isMainFrame: bool  # noqa: A002, N803
        ) -> bool:
            action = classify_navigation(url.toString(), args.links, args.offline)
            if action == "allow" and url.scheme() == SCHEME:
                view.setUrl(url)
            elif action == "open":
                open_externally(url.toString())
            self.deleteLater()
            return False

    # One profile per process; the storage path, not the name, keeps games apart.
    profile = QWebEngineProfile("wlib-web-player", app)
    profile.setPersistentStoragePath(os.path.join(args.profile, "data"))
    profile.setCachePath(os.path.join(args.profile, "cache"))
    profile.setPersistentCookiesPolicy(
        QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies
    )
    handler = SchemeHandler(app)
    profile.installUrlSchemeHandler(QByteArray(SCHEME.encode()), handler)
    interceptor: Interceptor | None = None
    if args.offline:
        interceptor = Interceptor(app)
        profile.setUrlRequestInterceptor(interceptor)

    settings = profile.settings()
    assert settings is not None
    for attr, enabled in (
        (QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True),
        (QWebEngineSettings.WebAttribute.AllowRunningInsecureContent, True),
        (QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False),
        (QWebEngineSettings.WebAttribute.FullScreenSupportEnabled, True),
        (QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, True),
        (QWebEngineSettings.WebAttribute.LocalStorageEnabled, True),
    ):
        settings.setAttribute(attr, enabled)

    if not args.flash:
        script = QWebEngineScript()
        script.setName("wlib-ruffle")
        script.setSourceCode(ruffle_bootstrap_source())
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        script.setRunsOnSubFrames(True)
        scripts = profile.scripts()
        assert scripts is not None
        scripts.insert(script)  # pyright: ignore[reportUnknownMemberType]

    view = QWebEngineView()
    page = GamePage(profile, view)
    view.setPage(page)
    view.setWindowTitle(args.title)
    view.resize(1280, 800)

    def on_permission(origin: QUrl, feature: QWebEnginePage.Feature) -> None:
        page.setFeaturePermission(
            origin, feature, QWebEnginePage.PermissionPolicy.PermissionDeniedByUser
        )

    _ = page.featurePermissionRequested.connect(on_permission)  # pyright: ignore[reportUnknownMemberType]

    def on_fullscreen(request: QWebEngineFullScreenRequest) -> None:
        request.accept()
        if request.toggleOn():
            view.showFullScreen()
        else:
            view.showNormal()

    _ = page.fullScreenRequested.connect(on_fullscreen)  # pyright: ignore[reportUnknownMemberType]

    def toggle_fullscreen() -> None:
        if view.isFullScreen():
            view.showNormal()
        else:
            view.showFullScreen()

    for sequence, action in (
        ("F11", toggle_fullscreen),
        ("Ctrl++", lambda: view.setZoomFactor(min(view.zoomFactor() + 0.1, 5.0))),
        ("Ctrl+=", lambda: view.setZoomFactor(min(view.zoomFactor() + 0.1, 5.0))),
        ("Ctrl+-", lambda: view.setZoomFactor(max(view.zoomFactor() - 0.1, 0.25))),
        ("Ctrl+0", lambda: view.setZoomFactor(1.0)),
    ):
        shortcut = QShortcut(QKeySequence(sequence), view)
        shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        _ = shortcut.activated.connect(action)  # pyright: ignore[reportUnknownMemberType]

    if os.environ.get("DEV_MODE") == "1":
        dev = QShortcut(QKeySequence("F12"), view)
        _ = dev.activated.connect(  # pyright: ignore[reportUnknownMemberType]
            lambda: page.triggerAction(QWebEnginePage.WebAction.InspectElement)
        )

    entry = FLASH_PAGE if args.flash else quote(args.entry, safe="/")
    view.setUrl(QUrl(f"{SCHEME}://{GAME_HOST}/{entry}"))
    view.show()
    app.setQuitOnLastWindowClosed(True)
    code = app.exec()
    # Delete the page before its profile, so storage is flushed and released
    # in order instead of at interpreter shutdown.
    page.deleteLater()
    view.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
    profile.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
    return int(code)
