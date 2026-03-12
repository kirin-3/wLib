import os
import re
import shutil
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from types import TracebackType
from typing import Protocol, TypeAlias, cast
from urllib.parse import urlparse


ScraperResultValue: TypeAlias = bool | str | list[str]
ScraperResult: TypeAlias = Mapping[str, ScraperResultValue]
ScraperResultDict: TypeAlias = dict[str, ScraperResultValue]
ThreadMetadata: TypeAlias = dict[str, str | list[str]]


class PostElementLike(Protocol):
    def inner_html(self) -> str: ...

    def text_content(self) -> str | None: ...


class PageLike(Protocol):
    def query_selector(self, selector: str) -> PostElementLike | None: ...

    def evaluate(
        self, expression: str, arg: PostElementLike | None = None
    ) -> object: ...

    def title(self) -> str: ...

    def content(self) -> str: ...

    def goto(self, url: str, *, wait_until: str, timeout: int) -> None: ...

    def bring_to_front(self) -> None: ...

    def wait_for_event(self, event: str, *, timeout: int) -> None: ...

    def wait_for_selector(self, selector: str, *, timeout: int) -> None: ...

    def close(self) -> None: ...


class BrowserContextLike(Protocol):
    pages: Sequence[PageLike]

    def new_page(self) -> PageLike: ...

    def close(self) -> None: ...


class ChromiumLike(Protocol):
    def launch_persistent_context(
        self,
        *,
        user_data_dir: str,
        headless: bool,
        args: list[str],
        env: dict[str, str],
    ) -> BrowserContextLike: ...


class PlaywrightInstanceLike(Protocol):
    chromium: ChromiumLike

    def stop(self) -> None: ...


class PlaywrightContextManagerLike(Protocol):
    def start(self) -> PlaywrightInstanceLike: ...

    def __enter__(self) -> PlaywrightInstanceLike: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...


class PlaywrightModuleLike(Protocol):
    sync_playwright: Callable[[], PlaywrightContextManagerLike]


class BatchResultCallback(Protocol):
    def __call__(self, url: str, result: ScraperResult) -> bool: ...


class Scraper:
    def __init__(self) -> None:
        # Store browser session in the user's data dir so login persists across installations
        self.user_data_dir: str = os.path.expanduser(
            "~/.local/share/wLib/browser_session"
        )

    def _build_browser_launch_env(self) -> dict[str, str]:
        clean_env = os.environ.copy()

        for var in (
            "APPIMAGE",
            "APPDIR",
            "ARGV0",
            "APPIMAGE_SILENT_INSTALL",
            "OWD",
            "APPIMAGE_EXTRACT_AND_RUN",
        ):
            _ = clean_env.pop(var, None)

        original_library_path = str(clean_env.get("LD_LIBRARY_PATH_ORIG") or "").strip()
        if original_library_path:
            clean_env["LD_LIBRARY_PATH"] = original_library_path
        else:
            _ = clean_env.pop("LD_LIBRARY_PATH", None)

        return clean_env

    def _launch_persistent_browser_context(
        self, playwright_instance: PlaywrightInstanceLike, headless: bool
    ) -> BrowserContextLike:
        return playwright_instance.chromium.launch_persistent_context(
            user_data_dir=self.user_data_dir,
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
            env=self._build_browser_launch_env(),
        )

    def _extract_version_from_title(self, title: object) -> str:
        if not isinstance(title, str):
            return "Unknown"

        # Multi-pass version extraction from F95Zone titles
        # Pass 1: Bracketed version [v1.0], [1.0.2], [Version 2.1]
        match = re.search(
            r"\[(?:v|version\s*)?((\d+[\d.]*[a-zA-Z]?)(?:\s*(?:beta|alpha|final|fix\d*|hotfix))?)\]",
            title,
            re.I,
        )
        if not match:
            # Pass 2: Chapter/Episode/Season formats [Ch.3], [Ep.5], [S2 E3], [Part 3]
            match = re.search(
                r"\[(Ch(?:apter|\.)?\s*\d+(?:\.\d+)?(?:\s*v[\d.]+[a-zA-Z]?)?|Ep(?:isode|\.)??\s*\d+(?:\.\d+)?|S\d+\s*E\d+|Part\s*\d+)\]",
                title,
                re.I,
            )
        if not match:
            # Pass 3: Bare version not in brackets "Game Name v1.0.2"
            match = re.search(r"\bv(\d+[\d.]*[a-zA-Z]?)\b", title, re.I)

        if match:
            return next((g for g in match.groups() if g is not None), "Unknown")
        return "Unknown"

    def _extract_version_from_post(self, page: PageLike) -> str:
        """
        Fallback: extract version from the first post body.
        Looks for patterns like <b>Version</b>: 1.07 or plain text "Version: 1.07".
        """
        try:
            first_post = page.query_selector(".message-body .bbWrapper")
            if not first_post:
                return "Unknown"

            # Try innerHTML first for <b>Version</b>: value patterns
            html_content = first_post.inner_html()
            html_patterns = [
                r"<b>\s*(?:Current\s+)?Version\s*</b>\s*:?\s*v?([^<\n]+)",
                r"<b>\s*Ver(?:\.|sion)?\s*</b>\s*:?\s*v?([^<\n]+)",
            ]
            for pattern in html_patterns:
                m = re.search(pattern, html_content, re.I)
                if m:
                    raw_value = (m.group(1) or "").strip()
                    version = raw_value.split()[0] if raw_value else ""
                    if version and re.match(r"^\d", version):
                        return version

            # Fallback: plain text search
            text_content = first_post.text_content() or ""
            text_patterns = [
                r"(?:Current\s+)?Version\s*:?\s*v?([^\s,;\n]+)",
                r"Ver(?:\.|sion)?\s*:?\s*v?([^\s,;\n]+)",
                r"Release\s*:?\s*v?(\d[\d.]*[a-zA-Z]?)",
            ]
            for pattern in text_patterns:
                m = re.search(pattern, text_content, re.I)
                if m:
                    version = m.group(1).strip()
                    if version and re.match(r"^\d", version):
                        return version
        except Exception as e:
            print(f"[wLib] Failed to extract version from post body: {e}")

        return "Unknown"

    def _is_valid_thread_url(self, url: object) -> bool:
        if not isinstance(url, str):
            return False

        normalized = url.strip()
        if not normalized:
            return False

        parsed = urlparse(normalized)
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)

    def _extract_title_text_from_page(self, page: PageLike) -> str:
        """
        Extract the clean title text from the h1.p-title-value DOM element,
        stripping out label/badge links (engine tags, status tags).
        Falls back to page.title() if the element is not found.
        """
        try:
            h1 = page.query_selector("h1.p-title-value")
            if h1:
                # Remove label links and label-append spans via JS to get clean text
                clean_text = page.evaluate(
                    """
                    (el) => {
                        const clone = el.cloneNode(true);
                        clone.querySelectorAll('a.labelLink, span.label-append').forEach(e => e.remove());
                        return clone.textContent.trim();
                    }
                """,
                    h1,
                )
                if isinstance(clean_text, str):
                    normalized_text = clean_text.strip()
                    if normalized_text:
                        return normalized_text
        except Exception as e:
            print(f"[wLib] Failed to extract title from h1 element: {e}")

        # Fallback to page title
        return page.title()

    def _extract_version_from_page(self, page: PageLike) -> tuple[str, str]:
        """
        Full version extraction pipeline:
        1. Try extracting from the h1.p-title-value element text
        2. Fall back to first post body (Version: X.X)
        """
        title_text = self._extract_title_text_from_page(page)
        version = self._extract_version_from_title(title_text)

        if version == "Unknown":
            version = self._extract_version_from_post(page)

        return title_text, version

    def _extract_metadata_from_page(self, page: PageLike) -> ThreadMetadata:
        """Extract thread metadata fields used by the app."""
        metadata: ThreadMetadata = {
            "engine": "",
            "tags": [],
            "cover_image": "",
            "thread_main_post_last_edit_at": "",
            "thread_main_post_checked_at": "",
        }
        metadata_extracted = False

        try:
            extracted = page.evaluate(
                """
                () => {
                    const result = {
                        engine: "",
                        tags: [],
                        cover_image: "",
                        thread_main_post_last_edit_at: "",
                    };

                    const starterPost =
                        document.querySelector(
                            "article.message-threadStarterPost.message--post"
                        ) || document.querySelector("article.message--post");

                    const h1 = document.querySelector("h1.p-title-value");
                    if (h1) {
                        const firstLabel = h1.querySelector("a.labelLink span");
                        if (firstLabel) {
                            result.engine = (firstLabel.textContent || "").trim();
                        }
                    }

                    const tagSet = new Set();
                    document.querySelectorAll(".tagItem").forEach((tag) => {
                        const value = (tag.textContent || "").trim();
                        if (value) tagSet.add(value);
                    });
                    result.tags = Array.from(tagSet);

                    const firstPostWrapper =
                        starterPost?.querySelector(".message-body .bbWrapper") ||
                        document.querySelector("article.message--post .message-body .bbWrapper") ||
                        document.querySelector(".message-body .bbWrapper");

                    const lastEditTime =
                        starterPost?.querySelector(".message-lastEdit time[datetime]") ||
                        starterPost?.querySelector(".message-lastEdit time");
                    if (lastEditTime) {
                        result.thread_main_post_last_edit_at =
                            lastEditTime.getAttribute("datetime") || "";
                    }

                    if (firstPostWrapper) {
                        const zoomer = firstPostWrapper.querySelector(
                            ".lbContainer-zoomer[data-src]"
                        );
                        if (zoomer) {
                            result.cover_image = zoomer.getAttribute("data-src") || "";
                        }

                        if (!result.cover_image) {
                            const firstImg = firstPostWrapper.querySelector(
                                "img.bbImage[data-src], img.bbImage[src]"
                            );
                            if (firstImg) {
                                result.cover_image =
                                    firstImg.getAttribute("data-src") ||
                                    firstImg.getAttribute("src") ||
                                    "";
                            }
                        }
                    }

                    if (!result.cover_image) {
                        const fallbackZoomer = document.querySelector(
                            ".message .lbContainer-zoomer[data-src]"
                        );
                        if (fallbackZoomer) {
                            result.cover_image = fallbackZoomer.getAttribute("data-src") || "";
                        }
                    }

                    return result;
                }
                """
            )
            if isinstance(extracted, dict):
                metadata_extracted = True
                extracted_dict = cast(dict[str, object], extracted)
                metadata["engine"] = str(extracted_dict.get("engine") or "").strip()
                raw_tags_obj = extracted_dict.get("tags")
                if isinstance(raw_tags_obj, list):
                    tags: list[str] = []
                    for raw_tag in cast(list[object], raw_tags_obj):
                        normalized_tag = str(raw_tag).strip()
                        if normalized_tag:
                            tags.append(normalized_tag)
                    metadata["tags"] = tags
                metadata["cover_image"] = self._normalize_cover_image_url(
                    extracted_dict.get("cover_image")
                )
                metadata["thread_main_post_last_edit_at"] = str(
                    extracted_dict.get("thread_main_post_last_edit_at") or ""
                ).strip()
        except Exception as e:
            print(f"[wLib] Failed to extract metadata from page: {e}")

        if metadata_extracted:
            metadata["thread_main_post_checked_at"] = datetime.now().isoformat()

        return metadata

    def _normalize_cover_image_url(self, url: object) -> str:
        if not isinstance(url, str):
            return ""

        normalized = url.strip()
        if not normalized:
            return ""

        # F95 attachments may expose thumbnail URLs under /thumb/.
        # Prefer the original asset path when possible.
        if "attachments.f95zone.to/" in normalized and "/thumb/" in normalized:
            while "/thumb/" in normalized:
                normalized = normalized.replace("/thumb/", "/", 1)

        return normalized

    def _error(self, code: str, message: str) -> ScraperResult:
        return {"success": False, "code": code, "error": message}

    def _is_non_actionable_version(self, version: object) -> bool:
        if version is None:
            return True
        value = str(version).strip().lower()
        return value in ("", "unknown", "n/a", "na", "none", "null")

    def _classify_page_issue(self, page: PageLike) -> dict[str, str] | None:
        try:
            page_title = (page.title() or "").lower()
        except Exception:
            page_title = ""

        try:
            page_content = (page.content() or "").lower()
        except Exception:
            page_content = ""

        combined = f"{page_title}\n{page_content}"

        blocked_markers = (
            "just a moment",
            "checking your browser",
            "verify you are human",
            "attention required",
            "cf-challenge",
            "challenge-platform",
        )
        if any(marker in combined for marker in blocked_markers):
            return {
                "code": "blocked",
                "error": "Blocked by anti-bot challenge while loading thread",
            }

        login_markers = (
            "log in",
            "login",
            "sign in",
            "you must be logged in",
        )
        if any(marker in combined for marker in login_markers):
            return {
                "code": "login_required",
                "error": "Login is required to access this thread",
            }

        return None

    def _load_sync_playwright(self) -> Callable[[], PlaywrightContextManagerLike]:
        import importlib

        module = importlib.import_module("playwright.sync_api")
        typed_module = cast(PlaywrightModuleLike, cast(object, module))
        return typed_module.sync_playwright

    def _dependency_missing_message(self) -> str:
        if getattr(sys, "frozen", False):
            return (
                "Playwright runtime is missing from this build. "
                "Please reinstall/update wLib and try again."
            )
        return (
            "Playwright is not installed in this Python environment. "
            "Run: python -m pip install playwright && python -m playwright install chromium"
        )

    def open_login_session(
        self, login_url: str = "https://f95zone.to/login/"
    ) -> ScraperResult:
        if not self._is_valid_thread_url(login_url):
            return self._error("invalid_url", "Invalid login URL")

        context: BrowserContextLike | None = None
        page: PageLike | None = None
        playwright_instance: PlaywrightInstanceLike | None = None

        try:
            sync_playwright = self._load_sync_playwright()
        except ModuleNotFoundError:
            return self._error("dependency_missing", self._dependency_missing_message())

        try:
            playwright_instance = sync_playwright().start()
            context = self._launch_persistent_browser_context(
                playwright_instance, headless=False
            )

            if context.pages:
                page = context.pages[0]
            else:
                page = context.new_page()

            page.goto(login_url.strip(), wait_until="domcontentloaded", timeout=60000)
            try:
                page.bring_to_front()
            except Exception:
                pass

            # Keep session browser open until user closes the login tab/window.
            page.wait_for_event("close", timeout=0)
            return {"success": True, "message": "Login session closed"}
        except Exception as e:
            return self._error("scraper_error", str(e))
        finally:
            try:
                if context is not None:
                    context.close()
            except Exception:
                pass
            try:
                if playwright_instance is not None:
                    playwright_instance.stop()
            except Exception:
                pass

    def reset_browser_session(self) -> ScraperResult:
        try:
            if os.path.exists(self.user_data_dir):
                shutil.rmtree(self.user_data_dir)
            os.makedirs(self.user_data_dir, exist_ok=True)
            return {"success": True, "message": "Browser session reset"}
        except Exception as e:
            return self._error("session_reset_failed", str(e))

    def get_thread_version(
        self,
        url: str,
        headless: bool = False,
        timeout_ms: int = 60000,
        hold_open_seconds: int = 0,
        include_metadata: bool = False,
    ) -> ScraperResult:
        """
        Navigates to an F95Zone thread URL and extracts the version string from the title or tags.
        Spins up and down the browser safely within the calling thread.
        """
        if not self._is_valid_thread_url(url):
            return self._error("invalid_url", "Invalid thread URL")

        target_url = url.strip()
        context: BrowserContextLike | None = None
        page: PageLike | None = None
        playwright_instance: PlaywrightInstanceLike | None = None

        try:
            sync_playwright = self._load_sync_playwright()
        except ModuleNotFoundError:
            return self._error("dependency_missing", self._dependency_missing_message())
        try:
            playwright_instance = sync_playwright().start()
            context = self._launch_persistent_browser_context(
                playwright_instance, headless=headless
            )
            page = context.new_page()
            try:
                page.goto(
                    target_url,
                    wait_until="domcontentloaded",
                    timeout=timeout_ms,
                )
            except Exception:
                return self._error(
                    "navigation_timeout",
                    "Timed out while loading thread page",
                )

            try:
                page.wait_for_selector("h1.p-title-value", timeout=timeout_ms)
            except Exception:
                page_issue = self._classify_page_issue(page)
                if page_issue:
                    return self._error(page_issue["code"], page_issue["error"])
                return self._error(
                    "title_not_found",
                    "Could not find thread title on page",
                )

            title, version = self._extract_version_from_page(page)

            if self._is_non_actionable_version(version):
                return self._error(
                    "extract_failed",
                    "Could not extract a usable version from thread",
                )

            result: ScraperResultDict = {
                "success": True,
                "title": title,
                "version": str(version).strip(),
            }
            if include_metadata:
                result.update(self._extract_metadata_from_page(page))
            return result
        except Exception as e:
            return self._error("scraper_error", str(e))
        finally:
            try:
                hold_seconds = int(hold_open_seconds)
            except (TypeError, ValueError):
                hold_seconds = 0
            if not headless and hold_seconds > 0:
                time.sleep(max(0, min(hold_seconds, 300)))

            try:
                if page is not None:
                    page.close()
            except Exception:
                pass
            try:
                if context is not None:
                    context.close()
            except Exception:
                pass
            try:
                if playwright_instance is not None:
                    playwright_instance.stop()
            except Exception:
                pass

    def get_thread_metadata(
        self,
        url: str,
        headless: bool = True,
        timeout_ms: int = 60000,
        hold_open_seconds: int = 0,
    ) -> ScraperResult:
        if not self._is_valid_thread_url(url):
            return self._error("invalid_url", "Invalid thread URL")

        target_url = url.strip()
        context: BrowserContextLike | None = None
        page: PageLike | None = None
        playwright_instance: PlaywrightInstanceLike | None = None

        try:
            sync_playwright = self._load_sync_playwright()
        except ModuleNotFoundError:
            return self._error("dependency_missing", self._dependency_missing_message())

        try:
            playwright_instance = sync_playwright().start()
            context = self._launch_persistent_browser_context(
                playwright_instance, headless=headless
            )
            page = context.new_page()
            try:
                page.goto(
                    target_url,
                    wait_until="domcontentloaded",
                    timeout=timeout_ms,
                )
            except Exception:
                return self._error(
                    "navigation_timeout",
                    "Timed out while loading thread page",
                )

            try:
                page.wait_for_selector("h1.p-title-value", timeout=timeout_ms)
            except Exception:
                page_issue = self._classify_page_issue(page)
                if page_issue:
                    return self._error(page_issue["code"], page_issue["error"])
                return self._error(
                    "title_not_found",
                    "Could not find thread title on page",
                )

            metadata = self._extract_metadata_from_page(page)
            return {"success": True, **metadata}
        except Exception as e:
            return self._error("scraper_error", str(e))
        finally:
            try:
                hold_seconds = int(hold_open_seconds)
            except (TypeError, ValueError):
                hold_seconds = 0
            if not headless and hold_seconds > 0:
                time.sleep(max(0, min(hold_seconds, 300)))

            try:
                if page is not None:
                    page.close()
            except Exception:
                pass
            try:
                if context is not None:
                    context.close()
            except Exception:
                pass
            try:
                if playwright_instance is not None:
                    playwright_instance.stop()
            except Exception:
                pass

    def get_multiple_thread_versions(
        self,
        urls: Sequence[str],
        headless: bool = True,
        delay: int = 5,
        include_metadata: bool = False,
        callback: BatchResultCallback | None = None,
    ) -> dict[str, ScraperResult]:
        """
        Checks multiple URLs reusing the same browser context to reduce overhead.
        """
        results: dict[str, ScraperResult] = {}
        try:
            delay_seconds = int(delay)
        except (TypeError, ValueError):
            delay_seconds = 5
        delay_seconds = max(2, min(delay_seconds, 30))

        try:
            sync_playwright = self._load_sync_playwright()
        except ModuleNotFoundError:
            results["__batch_error__"] = self._error(
                "dependency_missing", self._dependency_missing_message()
            )
            return results

        try:
            with sync_playwright() as p:
                context = self._launch_persistent_browser_context(p, headless=headless)

                for i, url in enumerate(urls):
                    page = context.new_page()
                    try:
                        if not self._is_valid_thread_url(url):
                            result = self._error("invalid_url", "Invalid thread URL")
                            results[url] = result
                            page.close()
                            if callback:
                                if not callback(url, result):
                                    break
                            continue

                        try:
                            page.goto(
                                url.strip(),
                                wait_until="domcontentloaded",
                                timeout=60000,
                            )
                        except Exception:
                            result = self._error(
                                "navigation_timeout",
                                "Timed out while loading thread page",
                            )
                            results[url] = result
                            page.close()
                            if callback and not callback(url, result):
                                break
                            if i < len(urls) - 1:
                                import time

                                time.sleep(delay_seconds)
                            continue

                        try:
                            page.wait_for_selector("h1.p-title-value", timeout=60000)
                        except Exception:
                            page_issue = self._classify_page_issue(page)
                            if page_issue:
                                result = self._error(
                                    page_issue["code"], page_issue["error"]
                                )
                            else:
                                result = self._error(
                                    "title_not_found",
                                    "Could not find thread title on page",
                                )
                            results[url] = result
                            page.close()
                            if callback and not callback(url, result):
                                break
                            if i < len(urls) - 1:
                                import time

                                time.sleep(delay_seconds)
                            continue

                        title, version = self._extract_version_from_page(page)

                        if self._is_non_actionable_version(version):
                            result = self._error(
                                "extract_failed",
                                "Could not extract a usable version from thread",
                            )
                        else:
                            result = cast(
                                ScraperResultDict,
                                {
                                    "success": True,
                                    "title": title,
                                    "version": str(version).strip(),
                                },
                            )
                            if include_metadata:
                                result.update(self._extract_metadata_from_page(page))
                    except Exception as e:
                        result = self._error("scraper_error", str(e))

                    results[url] = result
                    page.close()

                    if callback:
                        if not callback(url, result):
                            break  # cancel if callback returns False

                    if i < len(urls) - 1:
                        import time

                        time.sleep(delay_seconds)

                context.close()
        except Exception as e:
            results["__batch_error__"] = self._error(
                "batch_failed", f"Playwright batch check failed: {e}"
            )

        return results
