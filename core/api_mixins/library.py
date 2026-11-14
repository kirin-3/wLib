# SPDX-License-Identifier: GPL-3.0-or-later
"""Games, folder scan, path checks, covers and collections."""
from __future__ import annotations

import json
import os
import sys
import threading
from collections.abc import Callable, Mapping, Sequence
from contextlib import closing
from typing import cast

from core import database
from core.database import (
    normalize_launch_mode,
)
from core.host_platform import (
    host_which,
    is_linux,
)
from core.api_mixins.base import ApiBase, normalize_thread_url
from core.api_mixins.types import (
    ExecutableModifiedTimeResponse,
)


class LibraryMixin(ApiBase):
    @staticmethod
    def _coerce_names_sequence(value: object) -> list[object] | None:
        """Validate a collection/target name list from the JS bridge."""
        if isinstance(value, str) or not isinstance(value, Sequence):
            return None
        return list(value)

    @staticmethod
    def _normalize_compare_path(value: object) -> str:
        raw = str(value or "")
        if not raw.strip():
            return ""
        expanded = os.path.expanduser(raw)
        try:
            resolved = os.path.realpath(os.path.abspath(expanded))
        except (OSError, ValueError):
            resolved = os.path.abspath(expanded)
        return os.path.normcase(resolved)

    def get_games(self) -> list[dict[str, object]]:
        from core.database import get_all_games

        games = get_all_games()
        with self._metadata_lock:
            pending = set(self._metadata_pending)
        for game in games:
            self._add_local_cover_url(game)
            if game.get("id") in pending:
                game["metadata_pending"] = True
        return games

    def get_game_screenshots(self, game_id: int) -> dict[str, object]:
        from core.database import get_game
        from core.f95zone import extract_thread_id
        from core.game_lookup import f95_thread_screenshots, screenshot_urls

        game = get_game(game_id)
        if game is None:
            return {"success": False, "error": "Game was not found"}
        thread_id = extract_thread_id(str(game.get("f95_url") or ""))
        catalog = f95_thread_screenshots(str(thread_id)) if thread_id else None
        try:
            stored = cast(object, json.loads(str(game.get("screenshots") or "[]")))
        except ValueError:
            stored = []
        library = [item for item in cast(list[object], stored) if isinstance(item, str)] if isinstance(stored, list) else []
        return {"success": True, "screenshots": [screenshot_urls(url) for url in (catalog or library)],
                "source": "catalog" if catalog else "library", "in_catalog": catalog is not None}

    def _add_local_cover_url(self, game: dict[str, object]) -> None:
        from pathlib import Path

        from core.cover_cards import card_paths, is_animated
        from core.game_covers import cover_path, cached_cover_path

        cached_url = game.pop("cover_cache_url", None)
        name = game.pop("cover_cache", None)
        cached = cached_cover_path(name)
        shown: Path | None = None
        if (
            self.library_sync.server_available
            and cached_url == game.get("cover_image_path")
            and cached is not None
            and cached.is_file()
        ):
            game["cover_cache_url"] = (
                f"http://127.0.0.1:8183/covers/cache/{cached.name}"
            )
            shown = cached
        path = cover_path(game.get("cover_local"))
        if self.library_sync.server_available and path is not None and path.is_file():
            game["cover_local_url"] = f"http://127.0.0.1:8183/covers/{path.name}"
            shown = path
        # Card copies belong to the source that is shown: local over cached.
        copies = card_paths(shown.name) if shown is not None else None
        if copies is not None and copies[0].is_file():
            base = "http://127.0.0.1:8183/covers/cards/"
            game["cover_card_still_url"] = base + copies[0].name
            if copies[1].is_file():
                game["cover_card_anim_url"] = base + copies[1].name
        if shown is not None and is_animated(shown):
            # Without a usable still copy (not made yet, Qt cannot make one,
            # or it failed to load) a view draws its own still frame, so the
            # animation setting still applies.
            game["cover_animated"] = True

    def get_cover_cache_status(self) -> dict[str, object]:
        try:
            return self.cover_cache.status()
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def purge_cover_cache(self) -> dict[str, object]:
        try:
            self.cover_cache.purge()
            self.cover_cards.sweep()
            self._refresh_synced_library()
            return self.cover_cache.status()
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def set_game_cover_file(
        self, game_id: object = None, path: object = ""
    ) -> dict[str, object]:
        import sqlite3

        from core.game_covers import set_cover

        coerced_id = self._coerce_db_id(game_id)
        if coerced_id is None or database.get_game(coerced_id) is None:
            return {
                "success": False,
                "error": "Game was not found",
                "error_code": "game_not_found",
            }
        if not isinstance(path, str) or not path.strip():
            return {"success": False, "error": "Choose an image file."}
        try:
            _ = set_cover(coerced_id, os.path.expanduser(path))
            self.cover_cards.sweep()
            self.cover_cards.wake()
            self._schedule_steam_sync()
            game = database.get_game(coerced_id)
            if game is not None:
                self._add_local_cover_url(game)
            return {"success": True, "game": game}
        except (OSError, ValueError, sqlite3.Error) as exc:
            return {"success": False, "error": str(exc)}

    def choose_game_cover(self, game_id: object = None) -> dict[str, object]:
        coerced_id = self._coerce_db_id(game_id)
        if coerced_id is None or database.get_game(coerced_id) is None:
            return {
                "success": False,
                "error": "Game was not found",
                "error_code": "game_not_found",
            }
        try:
            picker = (
                self._browse_linux_dialog
                if sys.platform.startswith("linux")
                else self._browse_qt_dialog
            )
            selected = picker(
                "file",
                file_types=(
                    "Images (*.jpg;*.jpeg;*.png;*.webp;*.gif;*.avif)",
                    "All files (*.*)",
                ),
            )
            if not selected:
                return {"success": True, "cancelled": True}
            return self.set_game_cover_file(coerced_id, selected)
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def clear_game_cover(self, game_id: object = None) -> dict[str, object]:
        import sqlite3

        from core.game_covers import clear_cover

        coerced_id = self._coerce_db_id(game_id)
        if coerced_id is None or database.get_game(coerced_id) is None:
            return {
                "success": False,
                "error": "Game was not found",
                "error_code": "game_not_found",
            }
        try:
            clear_cover(coerced_id)
            self.cover_cards.sweep()
            self._schedule_steam_sync()
            return {"success": True, "game": database.get_game(coerced_id)}
        except (OSError, ValueError, sqlite3.Error) as exc:
            return {"success": False, "error": str(exc)}

    def get_collections(self) -> list[str]:
        from core.database import get_collections as fetch_collections

        return fetch_collections()

    def create_collection(
        self, name: object = "", game_id: object = None
    ) -> dict[str, object]:
        import sqlite3

        from core.database import create_collection

        coerced_game_id: int | None = None
        if game_id is not None:
            coerced_game_id = self._coerce_db_id(game_id)
            if coerced_game_id is None:
                return {
                    "success": False,
                    "error": "Game not found",
                    "error_code": "game_not_found",
                }

        try:
            collections = create_collection(name, coerced_game_id)
        except ValueError as e:
            return {"success": False, "error": str(e)}
        except (sqlite3.Error, TypeError, OverflowError) as e:
            return {"success": False, "error": str(e) or "Could not create collection"}
        return {"success": True, "collections": collections}

    def rename_collection(
        self, old_name: object = "", new_name: object = ""
    ) -> dict[str, object]:
        import sqlite3

        from core.database import rename_collection

        try:
            collections = rename_collection(old_name, new_name)
        except ValueError as e:
            return {"success": False, "error": str(e)}
        except (sqlite3.Error, TypeError, OverflowError) as e:
            return {"success": False, "error": str(e) or "Could not rename collection"}
        return {"success": True, "collections": collections}

    def delete_collection(self, name: object = "") -> dict[str, object]:
        import sqlite3

        from core.database import delete_collection

        try:
            collections = delete_collection(name)
        except ValueError as e:
            return {"success": False, "error": str(e)}
        except (sqlite3.Error, TypeError, OverflowError) as e:
            return {"success": False, "error": str(e) or "Could not delete collection"}
        return {"success": True, "collections": collections}

    def reorder_collections(self, names: object = None) -> dict[str, object]:
        import sqlite3

        from core.database import reorder_collections

        coerced = self._coerce_names_sequence(names)
        if coerced is None:
            return {
                "success": False,
                "error": "Collection order must be a list of collection names",
            }

        try:
            collections = reorder_collections(coerced)
        except ValueError as e:
            return {"success": False, "error": str(e)}
        except (sqlite3.Error, TypeError, OverflowError) as e:
            return {
                "success": False,
                "error": str(e) or "Could not reorder collections",
            }
        return {"success": True, "collections": collections}

    def set_game_collections(
        self, game_id: object = None, names: object = None
    ) -> dict[str, object]:
        import sqlite3

        from core.database import set_game_collections

        coerced_game_id = self._coerce_db_id(game_id)
        if coerced_game_id is None:
            return {
                "success": False,
                "error": "Game not found",
                "error_code": "game_not_found",
            }
        coerced_names = self._coerce_names_sequence(names)
        if coerced_names is None:
            return {
                "success": False,
                "error": "Collections must be a list of collection names",
            }

        try:
            collections = set_game_collections(coerced_game_id, coerced_names)
        except ValueError as e:
            return {"success": False, "error": str(e)}
        except (sqlite3.Error, TypeError, OverflowError) as e:
            return {
                "success": False,
                "error": str(e) or "Could not update game collections",
            }
        return {"success": True, "collections": collections}

    def check_path(self, path: object = "", kind: object = "") -> dict[str, object]:
        """Report whether a typed path points at what its field expects.

        Purely informational: a not-found result never blocks saving.
        """
        if kind not in ("file", "dir", "parent", "runner"):
            return {"success": False, "error": f"Unknown path kind '{kind}'"}

        value = str(path or "").strip()
        if not value:
            return {"ok": False, "reason": "Enter a path"}
        expanded = os.path.expanduser(value)

        if kind == "runner":
            if is_linux() and expanded in ("GE-Proton", "UMU-Proton"):
                return {"ok": True, "reason": ""}
            has_separator = "/" in expanded or os.sep in expanded
            if not has_separator and os.altsep is not None:
                has_separator = os.altsep in expanded
            if not has_separator:
                if host_which(expanded) is not None:
                    return {"ok": True, "reason": ""}
                return {"ok": False, "reason": "Command not found on PATH"}
            if os.path.isfile(expanded):
                return {"ok": True, "reason": ""}
            if os.path.isdir(expanded):
                return {"ok": False, "reason": "Expected a file"}
            return {"ok": False, "reason": "File not found"}

        if kind == "file":
            if os.path.isfile(expanded):
                return {"ok": True, "reason": ""}
            if os.path.isdir(expanded):
                return {"ok": False, "reason": "Expected a file"}
            return {"ok": False, "reason": "File not found"}

        if kind == "dir":
            if os.path.isdir(expanded):
                return {"ok": True, "reason": ""}
            if os.path.isfile(expanded):
                return {"ok": False, "reason": "Expected a folder"}
            return {"ok": False, "reason": "Folder not found"}

        # parent: the file may not exist yet, but its folder must.
        if os.path.isdir(expanded) or expanded.endswith(("/", "\\")):
            return {"ok": False, "reason": "Expected a file"}
        parent = os.path.dirname(expanded) or "."
        if os.path.isdir(parent):
            return {"ok": True, "reason": ""}
        return {"ok": False, "reason": "Folder not found"}

    def inspect_game_path(self, exe_path: object = "") -> dict[str, object]:
        from core.game_scan import inspect_game_path

        exe_path_str = os.path.expanduser(str(exe_path or "").strip())
        if not exe_path_str or not os.path.isfile(exe_path_str):
            return {"success": False, "error": "File not found"}
        try:
            return {"success": True, **inspect_game_path(exe_path_str)}
        except (OSError, ValueError) as e:
            return {"success": False, "error": str(e) or "File not found"}

    def scan_games_folder(self, folder: object = "") -> dict[str, object]:
        from core.database import get_all_games
        from core.game_scan import scan_games_folder

        folder_str = os.path.expanduser(str(folder or "").strip())
        if not folder_str or not os.path.isdir(folder_str):
            return {"success": False, "error": "Folder not found"}
        try:
            known: set[str] = set()
            folded: dict[str, list[str]] = {}
            for game in get_all_games():
                normalized = self._normalize_compare_path(game.get("exe_path"))
                if not normalized:
                    continue
                known.add(normalized)
                folded.setdefault(normalized.casefold(), []).append(normalized)

            def in_library(exe_path: object) -> bool:
                normalized = self._normalize_compare_path(exe_path)
                if not normalized:
                    return False
                if normalized in known:
                    return True
                # A case-insensitive mount (ntfs3, exFAT) resolves a path whose
                # case differs from the stored row, but the strings never
                # compare equal. Confirm the case-folded candidates by inode.
                for candidate in folded.get(normalized.casefold(), []):
                    try:
                        if os.path.samefile(normalized, candidate):
                            return True
                    except (OSError, ValueError):
                        continue
                return False

            games = [
                {**game, "in_library": in_library(game["exe_path"])}
                for game in scan_games_folder(folder_str)
            ]
        except (OSError, ValueError) as e:
            return {"success": False, "error": str(e) or "Could not scan folder"}
        return {"success": True, "games": games}

    def add_game(
        self,
        title: object = "",
        exe_path: object = None,
        f95_url: str = "",
        version: str = "",
        cover_image: str = "",
        tags: str = "",
        rating: str = "",
        developer: str = "",
        engine: str = "",
        run_japanese_locale: bool = False,
        run_wayland: bool = False,
        auto_inject_ce: bool = False,
        custom_prefix: str = "",
        proton_version: str = "",
        launch_mode: str = "auto",
        command_line_args: str = "",
        links: object = None,
    ) -> dict[str, object]:
        import sqlite3

        from core.database import (
            add_game,
            find_game_by_f95_url,
            find_games_by_links,
            get_game,
            replace_game_links,
            update_game,
        )
        from core.store_links import coerce_store_links

        if not isinstance(title, str) or not title.strip():
            return {
                "success": False,
                "error": "Game title is required",
                "error_code": "invalid_title",
            }
        title = title.strip()

        if not isinstance(exe_path, str):
            return {
                "success": False,
                "error": "Game executable path must be a string",
                "error_code": "invalid_exe_path",
            }

        add_game_fn = cast(
            Callable[..., int | None],
            add_game,
        )

        normalized_url = normalize_thread_url(f95_url)
        normalized_launch_mode = normalize_launch_mode(launch_mode)
        incoming_links = coerce_store_links(links)
        linked_ids = find_games_by_links(incoming_links)
        existing_thread = (
            find_game_by_f95_url(normalized_url) if normalized_url else None
        )
        attach_id: int | None = None
        attach_fields: dict[str, object] = {"f95_url": normalized_url}
        if existing_thread or linked_ids:
            if not existing_thread and len(linked_ids) == 1 and normalized_url:
                linked_game = get_game(linked_ids[0])
                if linked_game and not linked_game.get("f95_url"):
                    attach_id = linked_ids[0]
                    # Adding with an executable installs a wishlisted match
                    # instead of dropping the path the user just chose.
                    if (
                        exe_path.strip()
                        and not str(linked_game.get("exe_path") or "").strip()
                    ):
                        attach_fields["exe_path"] = exe_path
                        if version.strip():
                            attach_fields["version"] = version
            if attach_id is None:
                return {
                    "success": False,
                    "error": "A game with this URL is already in your library",
                    "error_code": "duplicate_url",
                }

        try:
            if attach_id is not None:
                _ = update_game(attach_id, attach_fields)
                game_id = attach_id
            else:
                game_id = add_game_fn(
                    title,
                    exe_path,
                    normalized_url,
                    version=version,
                    cover_image=cover_image,
                    tags=tags,
                    rating=rating,
                    developer=developer,
                    engine=engine,
                    run_japanese_locale=run_japanese_locale,
                    run_wayland=run_wayland,
                    auto_inject_ce=auto_inject_ce,
                    custom_prefix=custom_prefix,
                    proton_version=proton_version,
                    launch_mode=normalized_launch_mode,
                    command_line_args=command_line_args,
                )
            if game_id is not None and incoming_links:
                replace_game_links(
                    game_id, "f95_post" if normalized_url else "page", incoming_links
                )
        except sqlite3.IntegrityError as exc:
            return self._integrity_error_response(exc)
        except (sqlite3.Error, TypeError, ValueError, OverflowError) as exc:
            return {"success": False, "error": str(exc) or "Could not add game"}

        self.cover_cache.wake()
        self._schedule_steam_sync()
        metadata_pending = bool(normalized_url) and game_id is not None
        if metadata_pending:
            assert game_id is not None
            needs_metadata_backfill = (
                attach_id is not None
                or self._is_missing_text(engine)
                or self._is_missing_text(cover_image)
                or not self._normalize_tags_csv(tags)
                or self._is_missing_text(developer)
            )
            # F95 requests are paced app-wide and can queue behind a folder import,
            # so the game is saved now and its card shows a loading state until this finishes.
            with self._metadata_lock:
                self._metadata_pending.add(game_id)
            threading.Thread(
                target=self._fetch_added_game_metadata,
                args=(game_id, normalized_url, needs_metadata_backfill),
                daemon=True,
            ).start()

        return {
            "success": True,
            "id": game_id,
            "title": title,
            "metadata_pending": metadata_pending,
            "attached": attach_id is not None,
        }

    @staticmethod
    def _integrity_error_response(exc: Exception) -> dict[str, object]:
        # Both the app-level guard ("duplicate f95_url") and the unique index
        # ("UNIQUE constraint failed: games.f95_url") name the column.
        if "f95_url" in str(exc):
            return {
                "success": False,
                "error": "A game with this URL is already in your library",
                "error_code": "duplicate_url",
            }
        return {"success": False, "error": str(exc)}

    def delete_game(self, game_id: object = None) -> dict[str, object]:
        import sqlite3

        from core.database import get_connection, get_game

        coerced_id = self._coerce_db_id(game_id)
        if coerced_id is None:
            return {
                "success": False,
                "error": "Game was not found",
                "error_code": "game_not_found",
            }

        try:
            previous_game = get_game(coerced_id)
            with closing(get_connection()) as conn:
                exists = cast(
                    object,
                    conn.execute(
                        "SELECT 1 FROM games WHERE id = ?", (coerced_id,)
                    ).fetchone(),
                )
            if exists is None:
                return {
                    "success": False,
                    "error": "Game was not found",
                    "error_code": "game_not_found",
                }
            # A running game would keep writing playtime to a deleted row, and
            # an install or mod tool would recreate files for it.
            if coerced_id in self.get_running_games():
                return {
                    "success": False,
                    "error": "Close the game before removing it.",
                    "error_code": "game_running",
                }
            with self._status_lock:
                busy = self._game_busy_reason(coerced_id)
            if busy:
                return {"success": False, "error": busy, "error_code": "game_busy"}
            self._remove_game_entry(coerced_id, previous_game)
        except (sqlite3.Error, TypeError, ValueError, OverflowError) as exc:
            return {"success": False, "error": str(exc) or "Could not delete game"}
        return {"success": True}

    def _remove_game_entry(
        self, coerced_id: int, previous_game: Mapping[str, object] | None
    ) -> None:
        """The "Remove from library" body, after the running and busy checks.

        Raises sqlite3.Error when the row cannot be deleted; the clean-up
        after that only logs its failures.
        """
        self.cover_cache.delete_game(coerced_id)
        try:
            from core.game_attachments import remove_game_attachments

            remove_game_attachments(coerced_id)
        except Exception as exc:
            print(f"[wLib] Failed to remove attachments for game {coerced_id}: {exc}")
        try:
            from core.game_covers import delete_cover_file

            if previous_game:
                delete_cover_file(previous_game.get("cover_local"))
            self.cover_cards.sweep()
        except Exception as exc:
            print(f"[wLib] Failed to remove cover for game {coerced_id}: {exc}")
        try:
            from datetime import datetime

            from core.game_saves import game_backup_dir

            # Save backups outlive the library entry: they move aside, out of
            # reach of a later game that gets the same ID.
            backup_dir = game_backup_dir(self._save_backups_root(), coerced_id)
            if os.path.isdir(backup_dir) and not os.path.islink(backup_dir):
                removed_root = os.path.join(self._save_backups_root(), "removed")
                os.makedirs(removed_root, exist_ok=True)
                stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                os.replace(
                    backup_dir, os.path.join(removed_root, f"{coerced_id}_{stamp}")
                )
        except Exception as exc:
            print(f"[wLib] Failed to keep save backups for game {coerced_id}: {exc}")
        try:
            self._download_queue.forget_game(coerced_id)
        except Exception as exc:
            print(f"[wLib] Failed to remove downloads of game {coerced_id}: {type(exc).__name__}")
        try:
            from core.downloads.torrents import supervisor as torrent_supervisor

            # Seeding torrents have no queue row; they go here.
            torrent_supervisor.forget_game(coerced_id)
        except Exception as exc:
            print(f"[wLib] Failed to remove torrents of game {coerced_id}: {type(exc).__name__}")
        try:
            from core.game_sandbox import retire_sandbox_folder

            _ = retire_sandbox_folder(coerced_id)
        except Exception as exc:
            print(f"[wLib] Failed to move the sandbox of game {coerced_id}: {exc}")
        try:
            from core.game_sandbox import retire_web_player_folder

            _ = retire_web_player_folder(coerced_id)
        except Exception as exc:
            print(f"[wLib] Failed to move the web player storage of game {coerced_id}: {exc}")
        try:
            from core.web_video import delete_scan_cache

            _ = delete_scan_cache(coerced_id)
        except Exception as exc:
            print(f"[wLib] Failed to delete the video check of game {coerced_id}: {exc}")
        with self._metadata_lock:
            self._metadata_pending.discard(coerced_id)
        self._schedule_steam_sync()

    # ── Uninstall and move ──

    @staticmethod
    def _coerce_id_list(value: object) -> list[int] | None:
        if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
            return None
        ids: list[int] = []
        for item in value:
            try:
                game_id = int(str(item))
            except (TypeError, ValueError):
                return None
            if game_id not in ids:
                ids.append(game_id)
        return ids

    def plan_game_uninstall(
        self, game_ids: object = None, root_overrides: object = None
    ) -> dict[str, object]:
        """Each game's root, guard result, saves folder and blocked reason."""
        from core.game_files import coerce_overrides, plan_uninstall

        ids = self._coerce_id_list(game_ids)
        if ids is None:
            return {"success": False, "error": "Choose the games to uninstall."}
        try:
            games = plan_uninstall(ids, coerce_overrides(root_overrides), self._game_file_hooks())
        except Exception as exc:
            return {"success": False, "error": str(exc) or "Could not plan the uninstall."}
        return {"success": True, "games": games}

    def _reserve_uninstall(self, game_id: int) -> str | None:
        from core.game_files import UNINSTALL_RUNNING

        with self._status_lock:
            if game_id in self.get_running_games():
                return UNINSTALL_RUNNING
            busy = self._game_busy_reason(game_id)
            if busy:
                return busy
            self._uninstalling.add(game_id)
        return None

    def _release_uninstall(self, game_id: int) -> None:
        with self._status_lock:
            self._uninstalling.discard(game_id)

    def _backup_saves_before_uninstall(self, game_id: int) -> str | None:
        from core.database import get_save_backup_keep
        from core.game_saves import GameSavesError, NothingToBackUpError, backup_saves

        effective = self._effective_saves_folder(game_id)
        if not effective:
            return None
        try:
            with self._save_operation(game_id):
                _ = backup_saves(
                    effective, self._save_backups_root(), game_id, get_save_backup_keep()
                )
        except NothingToBackUpError:
            return None
        except GameSavesError as exc:
            return str(exc) or "The save backup failed."
        return None

    def _remove_entry_after_uninstall(self, game_id: int) -> None:
        from core.database import get_game

        self._remove_game_entry(game_id, get_game(game_id))

    def uninstall_games(
        self, game_ids: object = None, options: object = None
    ) -> dict[str, object]:
        """Uninstall games after the Undo offer ended; one result per game.

        options: {backup_saves, remove_from_library, root_overrides}.
        """
        from core.game_files import UninstallHooks, coerce_overrides, run_uninstall

        ids = self._coerce_id_list(game_ids)
        if ids is None:
            return {"success": False, "error": "Choose the games to uninstall."}
        opts: Mapping[str, object] = (
            cast(Mapping[str, object], options) if isinstance(options, Mapping) else {}
        )
        base = self._game_file_hooks()
        hooks = UninstallHooks(
            running=base.running,
            busy=base.busy,
            saves_folder=base.saves_folder,
            reserve=self._reserve_uninstall,
            release=self._release_uninstall,
            backup_saves=self._backup_saves_before_uninstall,
            remove_entry=self._remove_entry_after_uninstall,
        )
        results = run_uninstall(
            ids,
            coerce_overrides(opts.get("root_overrides")),
            backup=bool(opts.get("backup_saves")),
            remove_from_library=bool(opts.get("remove_from_library")),
            hooks=hooks,
        )
        failed = [result for result in results if not result["success"]]
        if len(failed) < len(results):
            self._schedule_steam_sync()
        response: dict[str, object] = {
            "success": not failed,
            "results": results,
            "uninstalled": len(results) - len(failed),
            "failed": len(failed),
        }
        if failed:
            response["error"] = str(failed[0]["error"])
        return response

    def plan_game_move(
        self, game_ids: object = None, dest_parent: object = "", root_overrides: object = None
    ) -> dict[str, object]:
        """The uninstall checks plus the move checks and each game's size."""
        from core.game_files import coerce_overrides, plan_move

        ids = self._coerce_id_list(game_ids)
        if ids is None:
            return {"success": False, "error": "Choose the games to move."}
        dest = self._normalize_selected_path(dest_parent) if isinstance(dest_parent, str) else ""
        try:
            games = plan_move(
                ids, dest, coerce_overrides(root_overrides), self._game_file_hooks()
            )
        except Exception as exc:
            return {"success": False, "error": str(exc) or "Could not plan the move."}
        return {"success": True, "games": games}

    def start_game_move(
        self, game_ids: object = None, dest_parent: object = "", root_overrides: object = None
    ) -> dict[str, object]:
        """Add games to the end of the move queue. Each job checks its plan again."""
        from core.game_files import MoveJob, coerce_overrides

        ids = self._coerce_id_list(game_ids)
        if not ids:
            return {"success": False, "error": "Choose the games to move."}
        dest = self._normalize_selected_path(dest_parent) if isinstance(dest_parent, str) else ""
        if not dest or not os.path.isdir(dest):
            return {"success": False, "error": "The destination folder does not exist."}
        overrides = coerce_overrides(root_overrides)
        queued = self._game_moves.enqueue(
            [
                MoveJob(game_id=game_id, dest_parent=dest, root_override=overrides.get(game_id, ""))
                for game_id in ids
            ]
        )
        return {"success": True, "queued": queued}

    def get_move_status(self) -> dict[str, object]:
        return {"success": True, **self._game_moves.status()}

    def cancel_game_move(self, cancel_all: object = False) -> dict[str, object]:
        self._game_moves.cancel(bool(cancel_all))
        return {"success": True}

    def update_game(
        self, game_id: object = None, fields: object = None
    ) -> dict[str, object]:
        import sqlite3

        from core.database import get_connection, update_game

        coerced_id = self._coerce_db_id(game_id)
        if coerced_id is None:
            return {
                "success": False,
                "error": "Game was not found",
                "error_code": "game_not_found",
            }

        if fields is not None and not isinstance(fields, Mapping):
            return {
                "success": False,
                "error": "Fields must be an object",
                "error_code": "invalid_fields",
            }

        updated_fields: dict[str, object] = dict(
            cast(Mapping[str, object], fields or {})
        )
        title = updated_fields.get("title")
        if title is not None:
            # None is ignored by the database helper, like every other field.
            if not isinstance(title, str) or not title.strip():
                return {
                    "success": False,
                    "error": "Game title cannot be empty",
                    "error_code": "invalid_title",
                }
            updated_fields["title"] = title.strip()

        update_game_fn = cast(
            Callable[[int, dict[str, object]], dict[str, object]], update_game
        )

        try:
            with closing(get_connection()) as conn:
                exists = cast(
                    object,
                    conn.execute(
                        "SELECT 1 FROM games WHERE id = ?", (coerced_id,)
                    ).fetchone(),
                )
        except (sqlite3.Error, TypeError, ValueError, OverflowError) as exc:
            return {"success": False, "error": str(exc) or "Could not update game"}
        if exists is None:
            return {
                "success": False,
                "error": "Game was not found",
                "error_code": "game_not_found",
            }

        if "f95_url" in updated_fields:
            updated_fields["f95_url"] = normalize_thread_url(
                str(updated_fields["f95_url"] or "")
            )
        if "launch_mode" in updated_fields:
            updated_fields["launch_mode"] = normalize_launch_mode(
                updated_fields["launch_mode"]
            )
        if updated_fields.get("save_path_override") is not None:
            # Same rules as set_game_save_folder, so this path can't bypass them.
            from core.game_saves import GameSavesError, validate_save_override

            raw_override = updated_fields["save_path_override"]
            if not isinstance(raw_override, str):
                return {"success": False, "error": "The saves folder must be a path"}
            try:
                updated_fields["save_path_override"] = validate_save_override(
                    raw_override, self._save_backups_root()
                )
            except GameSavesError as exc:
                return {"success": False, "error": str(exc)}

        try:
            applied_fields = update_game_fn(coerced_id, updated_fields)
        except sqlite3.IntegrityError as exc:
            return self._integrity_error_response(exc)
        except (sqlite3.Error, TypeError, ValueError, OverflowError) as exc:
            return {"success": False, "error": str(exc) or "Could not update game"}

        response: dict[str, object] = {"success": True}
        if (
            "finished_version" in applied_fields
            and updated_fields.get("finished_version") is None
        ):
            response["finished_version"] = applied_fields["finished_version"]
        self.cover_cache.wake()
        self._schedule_steam_sync()
        return response

    def get_executable_modified_time(
        self, exe_path: str
    ) -> ExecutableModifiedTimeResponse:
        from datetime import datetime

        normalized_path = str(exe_path or "").strip()
        if not normalized_path:
            return {
                "success": False,
                "modified_at": None,
                "error": "Executable path is required",
            }

        try:
            stat_result = os.stat(normalized_path)
        except OSError as e:
            return {"success": False, "modified_at": None, "error": str(e)}

        try:
            modified_at = datetime.fromtimestamp(stat_result.st_mtime).isoformat()
        except (OSError, OverflowError, ValueError) as e:
            return {"success": False, "modified_at": None, "error": str(e)}

        return {"success": True, "modified_at": modified_at}

    def lookup_thread_details(self, url: str) -> dict[str, object]:
        """Read thread metadata for a new archive install through the paced scraper."""
        from core.f95zone import extract_thread_id, split_thread_title

        normalized = normalize_thread_url(url)
        if not extract_thread_id(normalized):
            return {
                "success": False,
                "error": "A valid F95Zone thread URL is required.",
            }
        try:
            metadata = self.scraper.get_thread_metadata(normalized)
            if not metadata.get("success"):
                return {
                    "success": False,
                    "error": metadata.get("error") or "Could not load thread details.",
                }
            title, version, creator = split_thread_title(
                str(metadata.get("title") or "")
            )
            # The scraper reports a missing version as "Unknown"; that is not a
            # version and must not replace the version guessed from the archive
            # name in the install dialog.
            scraped_version = metadata.get("version")
            if not version and self._is_actionable_remote_version(scraped_version):
                version = str(scraped_version).strip()
            return {
                "success": True,
                "title": title,
                "version": version,
                "creator": creator,
                "engine": metadata.get("engine") or "",
                "cover_image": metadata.get("cover_image") or "",
                "tags": metadata.get("tags") or [],
            }
        except Exception as exc:
            return {
                "success": False,
                "error": str(exc) or "Could not load thread details.",
            }
