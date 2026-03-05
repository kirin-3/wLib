import os
import re
import shutil
import sys
import time
from urllib.parse import urlparse


class Scraper:
    def __init__(self):
        # Store browser session in the user's data dir so login persists across installations
        self.user_data_dir = os.path.expanduser("~/.local/share/wLib/browser_session")

    def _extract_version_from_title(self, title):
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

    def _extract_version_from_post(self, page):
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

    def _is_valid_thread_url(self, url):
        if not isinstance(url, str):
            return False

        normalized = url.strip()
        if not normalized:
            return False

        parsed = urlparse(normalized)
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)

    def _extract_title_text_from_page(self, page):
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
                if clean_text:
                    return clean_text
        except Exception as e:
            print(f"[wLib] Failed to extract title from h1 element: {e}")

        # Fallback to page title
        return page.title()

    def _extract_version_from_page(self, page):
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

    def _extract_metadata_from_page(self, page):
        """Extract thread metadata fields used by the app."""
        metadata = {
            "engine": "",
            "tags": [],
            "cover_image": "",
        }

        try:
            extracted = page.evaluate(
                """
                () => {
                    const result = {
                        engine: "",
                        tags: [],
                        cover_image: "",
                    };

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
                        document.querySelector("article.message--post .message-body .bbWrapper") ||
                        document.querySelector(".message-body .bbWrapper");

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
                metadata["engine"] = str(extracted.get("engine") or "").strip()
                raw_tags = extracted.get("tags") or []
                if isinstance(raw_tags, list):
                    metadata["tags"] = [
                        str(tag).strip() for tag in raw_tags if str(tag).strip()
                    ]
                metadata["cover_image"] = self._normalize_cover_image_url(
                    str(extracted.get("cover_image") or "").strip()
                )
        except Exception as e:
            print(f"[wLib] Failed to extract metadata from page: {e}")

        return metadata

    def _normalize_cover_image_url(self, url: str) -> str:
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

    def _error(self, code: str, message: str):
        return {"success": False, "code": code, "error": message}

    def _is_non_actionable_version(self, version) -> bool:
        if version is None:
            return True
        value = str(version).strip().lower()
        return value in ("", "unknown", "n/a", "na", "none", "null")

    def _classify_page_issue(self, page):
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

    def _load_sync_playwright(self):
        import importlib

        module = importlib.import_module("playwright.sync_api")
        return module.sync_playwright

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

    def open_login_session(self, login_url="https://f95zone.to/login/"):
        if not self._is_valid_thread_url(login_url):
            return self._error("invalid_url", "Invalid login URL")

        context = None
        page = None
        playwright_instance = None

        try:
            sync_playwright = self._load_sync_playwright()
        except ModuleNotFoundError:
            return self._error("dependency_missing", self._dependency_missing_message())

        try:
            playwright_instance = sync_playwright().start()
            context = playwright_instance.chromium.launch_persistent_context(
                user_data_dir=self.user_data_dir,
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
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

    def reset_browser_session(self):
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
        headless=False,
        timeout_ms=60000,
        hold_open_seconds=0,
        include_metadata=False,
    ):
        """
        Navigates to an F95Zone thread URL and extracts the version string from the title or tags.
        Spins up and down the browser safely within the calling thread.
        """
        if not self._is_valid_thread_url(url):
            return self._error("invalid_url", "Invalid thread URL")

        target_url = url.strip()
        context = None
        page = None
        playwright_instance = None

        try:
            sync_playwright = self._load_sync_playwright()
        except ModuleNotFoundError:
            return self._error("dependency_missing", self._dependency_missing_message())
        try:
            playwright_instance = sync_playwright().start()
            context = playwright_instance.chromium.launch_persistent_context(
                user_data_dir=self.user_data_dir,
                headless=headless,
                args=["--disable-blink-features=AutomationControlled"],
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

            result = {
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
        headless=True,
        timeout_ms=60000,
        hold_open_seconds=0,
    ):
        if not self._is_valid_thread_url(url):
            return self._error("invalid_url", "Invalid thread URL")

        target_url = url.strip()
        context = None
        page = None
        playwright_instance = None

        try:
            sync_playwright = self._load_sync_playwright()
        except ModuleNotFoundError:
            return self._error("dependency_missing", self._dependency_missing_message())

        try:
            playwright_instance = sync_playwright().start()
            context = playwright_instance.chromium.launch_persistent_context(
                user_data_dir=self.user_data_dir,
                headless=headless,
                args=["--disable-blink-features=AutomationControlled"],
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

    def get_multiple_thread_versions(self, urls, headless=True, delay=5, callback=None):
        """
        Checks multiple URLs reusing the same browser context to reduce overhead.
        """
        results = {}
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
                context = p.chromium.launch_persistent_context(
                    user_data_dir=self.user_data_dir,
                    headless=headless,
                    args=["--disable-blink-features=AutomationControlled"],
                )

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
                            result = {
                                "success": True,
                                "title": title,
                                "version": str(version).strip(),
                            }
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
