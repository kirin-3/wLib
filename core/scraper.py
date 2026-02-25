import os
from playwright.sync_api import sync_playwright
import re


class Scraper:
    def __init__(self):
        # Store browser session in the user's data dir so login persists across installations
        self.user_data_dir = os.path.expanduser("~/.local/share/wLib/browser_session")

    def _extract_version_from_title(self, title):
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
                    version = m.group(1).strip().split()[0]
                    if version and re.match(r"^\d", version):
                        return version

            # Fallback: plain text search
            text_content = first_post.text_content()
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

    def get_thread_version(self, url: str, headless=False):
        """
        Navigates to an F95Zone thread URL and extracts the version string from the title or tags.
        Spins up and down the browser safely within the calling thread.
        """
        try:
            with sync_playwright() as p:
                context = p.chromium.launch_persistent_context(
                    user_data_dir=self.user_data_dir,
                    headless=headless,
                    args=["--disable-blink-features=AutomationControlled"],
                )
                page = context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=60000)

                try:
                    page.wait_for_selector("h1.p-title-value", timeout=60000)
                except Exception:
                    print(
                        "Could not find thread title. We might be blocked by Cloudflare or Login."
                    )

                title, version = self._extract_version_from_page(page)

                page.close()
                context.close()

                return {"success": True, "title": title, "version": version}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_multiple_thread_versions(
        self, urls, headless=True, delay=15, callback=None
    ):
        """
        Checks multiple URLs reusing the same browser context to reduce overhead.
        """
        results = {}
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
                        page.goto(url, wait_until="domcontentloaded", timeout=60000)
                        try:
                            page.wait_for_selector("h1.p-title-value", timeout=60000)
                        except Exception:
                            pass

                        title, version = self._extract_version_from_page(page)

                        result = {"success": True, "title": title, "version": version}
                    except Exception as e:
                        result = {"success": False, "error": str(e)}

                    results[url] = result
                    page.close()

                    if callback:
                        if not callback(url, result):
                            break  # cancel if callback returns False

                    if i < len(urls) - 1:
                        import time

                        time.sleep(delay)

                context.close()
        except Exception as e:
            print(f"Playwright batch check failed: {e}")

        return results
