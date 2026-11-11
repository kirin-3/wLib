# SPDX-License-Identifier: GPL-3.0-or-later
"""TypedDicts and Protocols for the ``Api`` mixins."""
from __future__ import annotations

from typing import NotRequired, Protocol, TypedDict


class ProgressStatus(TypedDict):
    running: bool
    done: int
    total: int
    current: str
    error: str


class ExtensionSyncStatus(TypedDict):
    success: bool
    updated: NotRequired[bool]
    path: NotRequired[str]
    bundled_version: NotRequired[str]
    installed_version: NotRequired[str]
    reason: NotRequired[str]
    error: NotRequired[str]


class OpenPathResult(TypedDict):
    success: bool
    error: NotRequired[str]


class PickerCommandResult(TypedDict):
    success: bool
    cancelled: bool
    stderr: str
    returncode: int | None
    path: NotRequired[str]
    error: NotRequired[str]


class BrowseBackend(TypedDict):
    name: str
    command: list[str]
    env: dict[str, str]
    continue_on_cancel_error: NotRequired[bool]
    cancel_error_markers: NotRequired[tuple[str, ...]]


class ExtensionServiceStatus(TypedDict):
    success: bool
    reachable: bool
    error: NotRequired[str]
    bridge: NotRequired[dict[str, object]]


class ScraperResponse(TypedDict, total=False):
    success: bool
    error: str
    message: str
    code: str
    version: str
    engine: str
    cover_image: str
    tags: str | list[object]
    thread_main_post_last_edit_at: str
    thread_main_post_checked_at: str
    thread_completed: bool


class LaunchTargetRecord(TypedDict):
    id: int
    game_id: int
    label: str
    exe_path: str
    sort_order: int
    created_at: str
    updated_at: str


class GameRecord(TypedDict):
    id: int
    title: str
    version: str
    f95_url: str
    exe_path: NotRequired[str]
    latest_version: NotRequired[str]
    skipped_version: NotRequired[str]
    thread_completed: NotRequired[bool]
    launch_mode: NotRequired[str]
    launch_targets: NotRequired[list[LaunchTargetRecord]]
    thread_main_post_last_edit_at: NotRequired[str | None]
    thread_main_post_checked_at: NotRequired[str | None]


class ExecutableModifiedTimeResponse(TypedDict):
    success: bool
    modified_at: str | None
    error: NotRequired[str]


class RuntimeInstallTarget(TypedDict):
    base_prefix: str
    resolved_prefix: str
    proton_path: str
    is_proton: bool


class WebviewModule(Protocol):
    class FileDialog(Protocol):
        FOLDER: int
        OPEN: int

    FOLDER_DIALOG: int
    OPEN_DIALOG: int


class URLResponse(Protocol):
    status: int

    def read(self, size: int = -1) -> bytes: ...


class URLResponseContext(Protocol):
    def __enter__(self) -> URLResponse: ...

    def __exit__(
        self, exc_type: object, exc_value: object, traceback: object
    ) -> None: ...
