# SPDX-License-Identifier: GPL-3.0-or-later
"""Built-in web game player: one QtWebEngine window per game, run as its own process.

The player takes everything from argv (the sandbox hides the database) and
imports nothing from `core.api`. Qt is imported only inside `run()`, so the
pure helpers stay importable and testable without a display.
"""

from __future__ import annotations

import html
import mimetypes
import ntpath
import os
import sys
import threading
import unicodedata
from collections.abc import Callable, Iterable, Set
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal, final, override
from urllib.parse import quote, unquote, urlsplit

from core.host_platform import is_windows

SCHEME = "wlib-game"
GAME_HOST = "game"
RUFFLE_HOST = "ruffle"
FLASH_PAGE = "__wlib_flash__.html"
EXPORTS_DIR = "exports"
_EXTERNAL_LINK_SCHEMES = ("http", "https", "mailto")
USAGE = (
    "Usage: wlib --play-web --root <game dir> --entry <relative path> "
    "--profile <storage dir> --title <title> [--flash] [--offline] "
    "[--links open|ignore] [--devtools] [--sandboxed]"
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
    devtools: bool = False
    sandboxed: bool = False


def parse_args(argv: list[str]) -> PlayerArgs:
    """Parse the argv that follows `--play-web`, or raise UsageError."""
    values: dict[str, str] = {}
    flags: set[str] = set()
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg in ("--flash", "--offline", "--devtools", "--sandboxed"):
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
    # entry is a file path, not a URL path: quote it so a literal "%20" survives.
    if resolve_game_file(root, quote(entry, safe="/")) is None:
        raise UsageError(f"Entry file not found inside the game folder: {entry}")
    return PlayerArgs(
        root=root,
        entry=entry,
        profile=os.path.abspath(values["--profile"]),
        title=values.get("--title", "").strip() or "wLib Web Player",
        flash="--flash" in flags,
        offline="--offline" in flags,
        links=links,  # type: ignore[arg-type]
        devtools="--devtools" in flags,
        sandboxed="--sandboxed" in flags,
    )


def safe_export_name(suggested: str) -> str:
    name = ntpath.basename(suggested)
    name = "".join(char for char in name if unicodedata.category(char) != "Cc").strip()
    # A leading dot hides the file, and the detail modal does not count hidden files.
    name = name.lstrip(". ")
    if is_windows():
        # Windows also treats colons as stream separators and trims trailing dots.
        name = "".join(char for char in name if char not in '<>:"|?*').rstrip(". ")
        device = name.split(".", 1)[0].rstrip().upper()
        if device in {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} or (
            device[:3] in ("COM", "LPT")
            and len(device) == 4
            and device[-1] in "123456789¹²³"
        ):
            name = "_" + name
    return name if name not in ("", ".", "..") else "download"


def unique_export_path(folder: str, name: str, taken: Set[str]) -> str:
    """Choose a free basename, including names reserved by active downloads."""
    stem, ext = os.path.splitext(name)
    pending = {os.path.normcase(filename) for filename in taken}
    index = 0
    while os.path.normcase(name) in pending or os.path.lexists(
        os.path.join(folder, name)
    ):
        index += 1
        name = f"{stem} ({index}){ext}"
    return os.path.join(folder, name)


def file_picker_filter(accepted: Iterable[str | None]) -> str:
    patterns = [
        f"*{entry}"
        for entry in accepted
        if entry
        and entry.startswith(".")
        and len(entry) > 1
        and all(char.isalnum() or char in ".-_" for char in entry)
    ]
    all_files = "All files (*)"
    return (
        f"Accepted files ({' '.join(patterns)});;{all_files}" if patterns else all_files
    )


def content_type(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in EXTRA_CONTENT_TYPES:
        return EXTRA_CONTENT_TYPES[ext]
    guessed, _ = mimetypes.guess_type(path)
    return guessed or "application/octet-stream"


def _file_inside(real_root: str, path: str) -> str | None:
    candidate = os.path.realpath(path)
    try:
        inside = os.path.commonpath([real_root, candidate]) == real_root
    except ValueError:
        return None
    if not inside or not os.path.isfile(candidate):
        return None
    return candidate


@lru_cache(maxsize=256)
def _names_by_lower_case(directory: str, _mtime_ns: int) -> dict[str, tuple[str, ...]]:
    """Entry names of one folder keyed by lower(), each list sorted; the mtime in the key drops stale lists."""
    names: dict[str, list[str]] = {}
    try:
        entries = sorted(os.listdir(directory))
    except OSError:
        return {}
    for name in entries:
        names.setdefault(name.lower(), []).append(name)
    return {key: tuple(value) for key, value in names.items()}


def _case_variants(directory: str, part: str) -> tuple[str, ...]:
    try:
        mtime_ns = os.stat(directory).st_mtime_ns
    except OSError:
        return ()
    return _names_by_lower_case(directory, mtime_ns).get(part.lower(), ())


# Bounds for the letter-case search, so a crafted path cannot make it slow.
_CASE_MATCH_MAX_PARTS = 64
_CASE_MATCH_MAX_STEPS = 256


def _match_case(real_root: str, parts: list[str]) -> str | None:
    """Find a file under real_root, matching each name in parts without regard to letter case.

    Games made on Windows often ask for "Img/Hero.png" when the file is "img/hero.png".
    An exact name is tried first, then the case variants in sorted order. When a folder
    has no match, the search goes back and tries the next variant of the folders above.
    """
    parts = [part for part in parts if part not in ("", ".")]
    if not parts or len(parts) > _CASE_MATCH_MAX_PARTS:
        return None
    stack: list[tuple[str, int]] = [(real_root, 0)]
    steps = 0
    while stack and steps < _CASE_MATCH_MAX_STEPS:
        current, depth = stack.pop()
        steps += 1
        if depth == len(parts):
            found = _file_inside(real_root, current)
            if found is not None:
                return found
            continue
        part = parts[depth]
        variants = _case_variants(current, part)
        names = (
            [part]
            if part in variants or os.path.lexists(os.path.join(current, part))
            else []
        )
        names += [name for name in variants if name != part]
        # Reversed, so the stack pops the exact name first.
        stack.extend(
            (os.path.join(current, name), depth + 1) for name in reversed(names)
        )
    return None


def resolve_under_root(root: str, url_path: str) -> str | None:
    """Map a URL path onto a regular file inside root, or None when it escapes or is missing.

    When the exact path is missing, the file is looked up again without regard to letter case.
    """
    relative = unquote(url_path).replace("\\", "/").lstrip("/")
    if "\x00" in relative:
        return None
    parts = relative.split("/")
    if any(part == ".." for part in parts):
        return None
    # On Windows "D:" leaves the root, and "C:x" joins onto a root on drive C: as if the drive were not there.
    if any(os.path.splitdrive(part)[0] for part in parts):
        return None
    real_root = os.path.realpath(root)
    found = _file_inside(real_root, os.path.join(real_root, relative))
    if found is not None:
        return found
    return _match_case(real_root, parts)


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


def classify_navigation(
    url: str, links: LinkMode, offline: bool = False
) -> NavigationAction:
    """Decide what the player does with a navigation or new-window request."""
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme in (SCHEME, "about", "blob", "data", "devtools", "qrc"):
        return "allow"
    if scheme in _EXTERNAL_LINK_SCHEMES:
        if offline or links == "ignore":
            return "drop"
        return "open"
    return "drop"


def is_network_request(url: str) -> bool:
    return urlsplit(url).scheme.lower() in ("http", "https", "ws", "wss", "ftp")


def host_open_env(env: dict[str, str]) -> dict[str, str]:
    """Environment for the system opener, without the bundle's libraries and Qt settings."""
    from core import host_platform

    return host_platform.host_open_env(env)


def open_url_on_host(url: str) -> bool:
    """Hand a web or email URL to its system handler and report whether an opener started."""
    if urlsplit(url).scheme.lower() not in _EXTERNAL_LINK_SCHEMES:
        return False
    return _open_system_target(url)


def open_folder_on_host(path: str) -> bool:
    """Hand a local folder to the system file manager."""
    return _open_system_target(os.path.abspath(path))


def _open_system_target(target: str) -> bool:
    from core import host_platform

    if is_windows():
        opened, _error = host_platform.open_windows_system_target(target)
        return bool(opened)
    opened, _error = host_platform.open_on_host(target, "No opener found")
    return opened


def open_in_background(
    open_target: Callable[[str], bool],
    target: str,
    on_failure: Callable[[str], None] | None = None,
) -> threading.Thread:
    """Run a host opener away from the Qt GUI thread.

    The opener can wait up to host_platform.OPENER_START_WAIT_SECONDS, which
    would freeze the player window. `on_failure(target)` runs on the worker
    thread; use gui_thread_relay to reach Qt widgets from it.
    """

    def work() -> None:
        if not open_target(target) and on_failure is not None:
            on_failure(target)

    thread = threading.Thread(target=work, name="wlib-player-opener", daemon=True)
    thread.start()
    return thread


def gui_thread_relay(callback: Callable[[str], None]) -> Callable[[str], None]:
    """Return a function that any thread can call to run `callback` on the GUI thread.

    Call this on the GUI thread: the relay object lives there, and the queued
    connection delivers each call through that thread's event loop.
    """
    from PyQt6.QtCore import QObject, pyqtSignal

    @final
    class _Relay(QObject):
        called = pyqtSignal(str)

        def __init__(self) -> None:
            super().__init__()
            # The receiver is this object, which lives on the GUI thread, so an
            # emit from another thread is queued to the GUI thread.
            _ = self.called.connect(self.deliver)  # pyright: ignore[reportUnknownMemberType]

        def deliver(self, text: str) -> None:
            callback(text)

    relay = _Relay()

    def emit(text: str) -> None:
        relay.called.emit(text)

    return emit


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
    from PyQt6.QtCore import (
        QByteArray,
        QBuffer,
        QCoreApplication,
        QEvent,
        QFile,
        QIODevice,
        QObject,
        Qt,
        QUrl,
    )
    from PyQt6.QtGui import QAction, QContextMenuEvent, QKeySequence, QShortcut
    from PyQt6.QtWebEngineCore import (
        QWebEngineDownloadRequest,
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
    from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox, QSplitter

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
    exports = os.path.join(args.profile, EXPORTS_DIR)
    picker_options = QFileDialog.Option(0)
    if args.sandboxed:
        picker_options |= QFileDialog.Option.DontUseNativeDialog

    def ensure_exports_folder() -> None:
        if os.path.islink(exports):
            raise OSError("The exports folder is a symbolic link.")
        os.makedirs(exports, exist_ok=True)

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
            # Fully encoded, so resolve_game_file() decodes it exactly once
            # (the default form already turns %2520 into %20).
            path = url.path(QUrl.ComponentFormattingOption.FullyEncoded)
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
        _ = open_in_background(open_url_on_host, url)

    class GamePage(QWebEnginePage):
        last_pick_folder: str | None = None

        @override
        def chooseFiles(  # noqa: N802
            self,
            mode: QWebEnginePage.FileSelectionMode,
            oldFiles: Iterable[str | None],  # noqa: N803
            acceptedMimeTypes: Iterable[str | None],  # noqa: N803
        ) -> list[str]:
            try:
                ensure_exports_folder()
            except OSError as exc:
                show_export_message("Could not open exports folder", str(exc))
                return []

            start = self.last_pick_folder or exports
            name_filter = file_picker_filter(acceptedMimeTypes)
            selection = QWebEnginePage.FileSelectionMode
            if mode == selection.FileSelectUploadFolder:
                folder = QFileDialog.getExistingDirectory(
                    window,
                    "Choose folder",
                    start,
                    options=picker_options | QFileDialog.Option.ShowDirsOnly,
                )
                files = [folder] if folder else []
            elif mode == selection.FileSelectOpenMultiple:
                files, _ = QFileDialog.getOpenFileNames(
                    window,
                    "Choose files",
                    start,
                    name_filter,
                    options=picker_options,
                )
            elif mode == selection.FileSelectOpen:
                path, _ = QFileDialog.getOpenFileName(
                    window,
                    "Choose file",
                    start,
                    name_filter,
                    options=picker_options,
                )
                files = [path] if path else []
            else:
                return []
            if files and not args.sandboxed:
                self.last_pick_folder = (
                    files[0]
                    if mode == selection.FileSelectUploadFolder
                    else os.path.dirname(files[0])
                )
            return files

        @override
        def acceptNavigationRequest(  # noqa: N802
            self,
            url: QUrl,
            type: QWebEnginePage.NavigationType,
            isMainFrame: bool,  # noqa: A002, N803
        ) -> bool:
            action = classify_navigation(url.toString(), args.links, args.offline)
            if action == "allow":
                return True
            link_clicked = (
                type == QWebEnginePage.NavigationType.NavigationTypeLinkClicked
            )
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
        def createWindow(
            self, type: QWebEnginePage.WebWindowType
        ) -> QWebEnginePage | None:  # noqa: A002, N802
            # New windows are routed through a throwaway page so the target URL
            # is classified like any other navigation instead of opening a window.
            probe = _LinkProbe(self.profile(), self)
            return probe

    class DevToolsPage(QWebEnginePage):
        """The DevTools front end: only devtools:// loads here, other links leave the player."""

        @override
        def acceptNavigationRequest(  # noqa: N802
            self,
            url: QUrl,
            type: QWebEnginePage.NavigationType,
            isMainFrame: bool,  # noqa: A002, N803
        ) -> bool:
            if url.scheme().lower() == "devtools":
                return True
            if classify_navigation(
                url.toString(), args.links, args.offline
            ) == "open" and (
                isMainFrame
                or type == QWebEnginePage.NavigationType.NavigationTypeLinkClicked
            ):
                open_externally(url.toString())
            return False

        @override
        def createWindow(
            self, type: QWebEnginePage.WebWindowType
        ) -> QWebEnginePage | None:  # noqa: A002, N802
            return _LinkProbe(self.profile(), self)

    class GameView(QWebEngineView):
        @override
        def contextMenuEvent(self, a0: QContextMenuEvent | None) -> None:  # noqa: N802
            if a0 is None:
                return
            menu = self.createStandardContextMenu()
            if menu is None:
                return
            menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            inspect = page.action(QWebEnginePage.WebAction.InspectElement)
            listed = inspect is not None and inspect in menu.actions()
            if args.devtools:
                # Qt lists its own Inspect only after a DevTools page is
                # attached, and it cannot make the pane, so use ours: it shows
                # the pane first and then inspects.
                replacement = QAction(
                    inspect.text() if inspect is not None else "Inspect", menu
                )
                _ = replacement.triggered.connect(inspect_element)  # pyright: ignore[reportUnknownMemberType]
                if listed:
                    menu.insertAction(inspect, replacement)
                else:
                    _ = menu.addSeparator()
                    menu.addAction(replacement)  # pyright: ignore[reportUnknownMemberType]
            if listed:
                menu.removeAction(inspect)
            menu.popup(a0.globalPos())

    class _LinkProbe(QWebEnginePage):
        @override
        def acceptNavigationRequest(  # noqa: N802
            self,
            url: QUrl,
            type: QWebEnginePage.NavigationType,
            isMainFrame: bool,  # noqa: A002, N803
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

    view = GameView()
    page = GamePage(profile, view)
    view.setPage(page)
    # The splitter is the window, so fullscreen keeps the DevTools pane as it is.
    window = QSplitter(Qt.Orientation.Horizontal)
    window.setChildrenCollapsible(False)
    window.addWidget(view)
    window.setWindowTitle(args.title)
    window.resize(1280, 800)

    def show_export_message(
        title: str, text: str, saved_path: str | None = None
    ) -> None:
        message = QMessageBox(window)
        message.setWindowTitle(title)
        message.setIcon(
            QMessageBox.Icon.Information if saved_path else QMessageBox.Icon.Warning
        )
        message.setTextFormat(Qt.TextFormat.PlainText)
        message.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        message.setText(text)
        message.setStandardButtons(QMessageBox.StandardButton.Ok)
        message.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        if saved_path is not None:

            def use_saved_path() -> None:
                if args.sandboxed:
                    clipboard = app.clipboard()
                    if clipboard is not None:
                        clipboard.setText(saved_path)
                else:
                    _ = open_in_background(
                        open_folder_on_host,
                        os.path.dirname(saved_path),
                        report_folder_failure,
                    )

            action = message.addButton(
                "Copy path" if args.sandboxed else "Open folder",
                QMessageBox.ButtonRole.ActionRole,
            )
            if action is not None:
                _ = action.clicked.connect(use_saved_path)  # pyright: ignore[reportUnknownMemberType]
        message.setWindowModality(Qt.WindowModality.NonModal)
        message.show()

    report_folder_failure = gui_thread_relay(
        lambda folder: show_export_message("Could not open exports folder", folder)
    )

    taken: set[str] = set()

    def on_download(request: QWebEngineDownloadRequest) -> None:
        name = safe_export_name(request.suggestedFileName())
        try:
            ensure_exports_folder()
        except OSError as exc:
            request.cancel()
            show_export_message("Download failed", str(exc))
            return
        if args.sandboxed:
            path = unique_export_path(exports, name, taken)
            taken.add(os.path.basename(path))
        else:
            path, _ = QFileDialog.getSaveFileName(
                window,
                "Save as",
                os.path.join(exports, name),
                options=picker_options,
            )
            if not path:
                request.cancel()
                return
        folder, filename = os.path.split(path)
        request.setDownloadDirectory(folder)
        request.setDownloadFileName(filename)

        def on_finished() -> None:
            if not request.isFinished():
                return
            if args.sandboxed:
                taken.discard(filename)
            if (
                request.state()
                == QWebEngineDownloadRequest.DownloadState.DownloadCompleted
            ):
                saved_path = os.path.join(
                    request.downloadDirectory(), request.downloadFileName()
                )
                show_export_message("Download saved", saved_path, saved_path)
            else:
                show_export_message(
                    "Download failed",
                    request.interruptReasonString() or "The download did not finish.",
                )

        _ = request.isFinishedChanged.connect(on_finished)  # pyright: ignore[reportUnknownMemberType]
        request.accept()

    _ = profile.downloadRequested.connect(on_download)  # pyright: ignore[reportUnknownMemberType]

    if not args.flash:
        # On the game page only, so the bootstrap never runs in DevTools.
        script = QWebEngineScript()
        script.setName("wlib-ruffle")
        script.setSourceCode(ruffle_bootstrap_source())
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        script.setRunsOnSubFrames(True)
        scripts = page.scripts()
        assert scripts is not None
        scripts.insert(script)  # pyright: ignore[reportUnknownMemberType]

    # Made on the first F12 only, so a game nobody inspects starts no second renderer.
    devtools: list[tuple[QWebEngineView, DevToolsPage]] = []

    def show_devtools() -> None:
        if not devtools:
            dev_view = QWebEngineView()
            dev_page = DevToolsPage(profile, dev_view)
            dev_view.setPage(dev_page)
            page.setDevToolsPage(dev_page)
            # The DevTools close button hides the pane; the player stays open.
            _ = dev_page.windowCloseRequested.connect(dev_view.hide)  # pyright: ignore[reportUnknownMemberType]
            window.addWidget(dev_view)
            width = max(window.width(), 2)
            game_width = int(width * 0.6)
            window.setSizes([game_width, width - game_width])  # pyright: ignore[reportUnknownMemberType]
            devtools.append((dev_view, dev_page))
        devtools[0][0].show()

    def toggle_devtools() -> None:
        if devtools and devtools[0][0].isVisible():
            devtools[0][0].hide()
        else:
            show_devtools()

    def inspect_element() -> None:
        show_devtools()
        page.triggerAction(QWebEnginePage.WebAction.InspectElement)

    def on_permission(origin: QUrl, feature: QWebEnginePage.Feature) -> None:
        page.setFeaturePermission(
            origin, feature, QWebEnginePage.PermissionPolicy.PermissionDeniedByUser
        )

    _ = page.featurePermissionRequested.connect(on_permission)  # pyright: ignore[reportUnknownMemberType]

    def on_fullscreen(request: QWebEngineFullScreenRequest) -> None:
        request.accept()
        if request.toggleOn():
            window.showFullScreen()
        else:
            window.showNormal()

    _ = page.fullScreenRequested.connect(on_fullscreen)  # pyright: ignore[reportUnknownMemberType]

    def toggle_fullscreen() -> None:
        if window.isFullScreen():
            window.showNormal()
        else:
            window.showFullScreen()

    bindings: list[tuple[str, Callable[[], object]]] = [
        ("F11", toggle_fullscreen),
        ("Ctrl++", lambda: view.setZoomFactor(min(view.zoomFactor() + 0.1, 5.0))),
        ("Ctrl+=", lambda: view.setZoomFactor(min(view.zoomFactor() + 0.1, 5.0))),
        ("Ctrl+-", lambda: view.setZoomFactor(max(view.zoomFactor() - 0.1, 0.25))),
        ("Ctrl+0", lambda: view.setZoomFactor(1.0)),
    ]
    if args.devtools:
        # Without --devtools F12 is not bound, so the game receives it.
        bindings.append(("F12", toggle_devtools))
    for sequence, action in bindings:
        shortcut = QShortcut(QKeySequence(sequence), window)
        shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        _ = shortcut.activated.connect(action)  # pyright: ignore[reportUnknownMemberType]

    entry = FLASH_PAGE if args.flash else quote(args.entry, safe="/")
    view.setUrl(QUrl(f"{SCHEME}://{GAME_HOST}/{entry}"))
    window.show()
    app.setQuitOnLastWindowClosed(True)
    code = app.exec()
    # Delete the pages before their profile, so storage is flushed and released
    # in order instead of at interpreter shutdown.
    for _dev_view, dev_page in devtools:
        dev_page.deleteLater()
    page.deleteLater()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
    profile.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
    return int(code)
