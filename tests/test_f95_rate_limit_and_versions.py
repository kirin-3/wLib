"""F95 429 cooldown inside the shared slot, and version choice in post and list parsing."""

import threading
from collections.abc import Callable

import pytest

from core import extension_bridge, f95_lists
from core import scraper as scraper_module
from core.f95_lists import ListEntry, ListReadError, ListRow, entry_from_row
from core.scraper import Scraper


class _RecordingLock:
    """The shared F95 lock; notes the cooldown each time the slot is released."""

    def __init__(self) -> None:
        self._lock: threading.Lock = threading.Lock()
        self.cooldown_at_release: list[float] = []

    def acquire(self, blocking: bool = True) -> bool:
        return self._lock.acquire(blocking)

    def release(self) -> None:
        self.cooldown_at_release.append(scraper_module._f95_cooldown_until)  # pyright: ignore[reportPrivateUsage]
        self._lock.release()


@pytest.fixture
def recording_lock(monkeypatch: pytest.MonkeyPatch) -> _RecordingLock:
    lock = _RecordingLock()
    monkeypatch.setattr(scraper_module, "_F95_REQUEST_LOCK", lock)
    monkeypatch.setattr(scraper_module, "_f95_last_request_finished", None)
    monkeypatch.setattr(scraper_module, "_f95_cooldown_until", 0.0)
    return lock


def _fake_bridge_fetch(status: int) -> Callable[..., extension_bridge.BridgeResponse]:
    def fetch(
        url: str, *_args: object, **_kwargs: object
    ) -> extension_bridge.BridgeResponse:
        return extension_bridge.BridgeResponse(status=status, final_url=url, body="")

    return fetch


def test_plain_http_429_starts_cooldown_before_the_slot_is_released(
    monkeypatch: pytest.MonkeyPatch, recording_lock: _RecordingLock
) -> None:
    monkeypatch.setattr(scraper_module, "_urllib_f95_get", lambda _url: (429, ""))
    assert scraper_module._f95_get("https://f95zone.to/threads/1/") == (429, "")  # pyright: ignore[reportPrivateUsage]
    assert recording_lock.cooldown_at_release[-1] > 0


def test_extension_fetch_429_starts_cooldown_before_the_slot_is_released(
    monkeypatch: pytest.MonkeyPatch, recording_lock: _RecordingLock
) -> None:
    monkeypatch.setattr(extension_bridge, "fetch", _fake_bridge_fetch(429))
    monkeypatch.setattr(
        extension_bridge, "record_fetch_outcome", lambda *_args, **_kwargs: None
    )
    status, _ = scraper_module._f95_get_via_extension("https://f95zone.to/threads/1/")  # pyright: ignore[reportPrivateUsage]
    assert status == 429
    assert recording_lock.cooldown_at_release[-1] > 0


def test_list_page_429_starts_cooldown_before_the_slot_is_released(
    monkeypatch: pytest.MonkeyPatch, recording_lock: _RecordingLock
) -> None:
    monkeypatch.setattr(extension_bridge, "list_fetch_unavailable_reason", lambda: None)
    monkeypatch.setattr(extension_bridge, "fetch_list_page", _fake_bridge_fetch(429))
    status, _, _ = f95_lists.fetch_page_via_extension(
        "https://f95zone.to/watched/threads"
    )
    assert status == 429
    assert recording_lock.cooldown_at_release[-1] > 0
    # The next queued request is refused during the cooldown.
    with pytest.raises(ListReadError) as excinfo:
        _ = f95_lists.fetch_page_via_extension("https://f95zone.to/watched/threads")
    assert excinfo.value.code == "rate_limited"


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("<p>Release: 2024.</p>", "Unknown"),
        ("<p>Release: 2024!</p>", "Unknown"),
        ("<p>Release 2024</p>", "Unknown"),
        ("<p>Release: 2024.1</p>", "2024.1"),
        ("<p>Release: 0.5.</p>", "0.5"),
    ],
)
def test_release_year_with_sentence_punctuation_is_not_a_version(
    body: str, expected: str
) -> None:
    assert Scraper()._extract_version_from_post(body) == expected  # pyright: ignore[reportPrivateUsage]


def test_list_cell_version_wins_over_a_title_placeholder() -> None:
    entry = entry_from_row(
        ListRow("1", "Game [Unknown] [Dev]", list_version="v2.1"), "watched"
    )
    assert entry.version == "2.1"


def test_title_placeholder_is_kept_when_nothing_better_exists() -> None:
    entry = entry_from_row(ListRow("1", "Game [Unknown] [Dev]"), "watched")
    assert entry.version == "Unknown"


def test_catalog_placeholder_does_not_hide_the_list_version() -> None:
    entry = ListEntry(
        thread_id="1",
        raw_title="Game",
        title="Game",
        version="2.1",
        developer="",
        engine="",
        prefixes=[],
        lists=["watched"],
    )
    values = f95_lists._catalog_values(entry, {"version": "Unknown"})  # pyright: ignore[reportPrivateUsage]
    assert values["version"] == "2.1"
    values = f95_lists._catalog_values(entry, {"version": "3.0"})  # pyright: ignore[reportPrivateUsage]
    assert values["version"] == "3.0"
