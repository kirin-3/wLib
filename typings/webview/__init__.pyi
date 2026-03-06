from typing import Any, Sequence

FOLDER_DIALOG: int
OPEN_DIALOG: int

class FileDialog:
    FOLDER: int
    OPEN: int

class Window:
    on_top: bool

    def evaluate_js(self, script: str) -> Any: ...
    def toggle_inspect(self) -> None: ...
    def create_file_dialog(
        self,
        dialog_type: int,
        allow_multiple: bool = ...,
        directory: str = ...,
        file_types: Sequence[str] = ...,
    ) -> list[str] | tuple[str, ...] | None: ...

def create_window(
    title: str,
    url: str,
    js_api: Any = ...,
    width: int = ...,
    height: int = ...,
    background_color: str = ...,
) -> Window: ...
def start(
    *,
    gui: str = ...,
    debug: bool = ...,
    http_server: bool = ...,
    icon: str | None = ...,
) -> None: ...
