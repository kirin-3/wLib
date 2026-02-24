import os
from playwright.sync_api import sync_playwright
import re
import time

class Scraper:
    def __init__(self):
        self.playwright = None
        self.context = None
        # Store browser session in the user's data dir so login persists across installations
        self.user_data_dir = os.path.expanduser("~/.local/share/wLib/browser_session")

    def start_browser(self, headless=True):
        if self.playwright is None:
            self.playwright = sync_playwright().start()
        
        # We use launch_persistent_context to keep cookies/logins alive
        # This prevents F95Zone from redirecting guests away from adult threads
        print(f"Starting Playwright persistent context at: {self.user_data_dir}")
        self.context = self.playwright.chromium.launch_persistent_context(
            user_data_dir=self.user_data_dir,
            headless=headless,
            # Arguments to make headless scraping look less like a bot
            args=["--disable-blink-features=AutomationControlled"]
        )
        
    def close_browser(self):
        if self.context:
            self.context.close()
            self.context = None
        if self.playwright:
            self.playwright.stop()
            self.playwright = None

    def get_thread_version(self, url: str, headless=False):
        """
        Navigates to an F95Zone thread URL and extracts the version string from the title or tags.
        """
        self.start_browser(headless=headless) # headless=True for background update checks
        page = self.context.new_page()
        
        try:
            # Go to the url and wait until network is mostly idle
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            
            # Wait for either Cloudflare to finish, or the main thread title to appear
            # F95Zone usually has h1 with class p-title-value
            try:
                # We give the user 60 seconds to solve captcha or login on first run
                page.wait_for_selector("h1.p-title-value", timeout=60000)
            except Exception:
                # If we timeout waiting for the h1, we might be stuck on a CAPTCHA or Login page
                print("Could not find thread title. We might be blocked by Cloudflare or Login.")
                pass
            
            title = page.title()
            print(f"Scraped Page Title: {title}")
            
            # Example regex extraction F95Zone thread titles typically have tags like [v1.2.3]
            match = re.search(r'\[v([0-9\.]+[a-zA-Z]*)\]|\[([vV]ersion [^\]]+)\]|\[(v\d+[^\]]*)\]', title)
            
            version = None
            if match:
                # Get whichever group matched
                version = next((g for g in match.groups() if g is not None), "Unknown")
            else:
                version = "Unknown"
                
            return {
                "success": True,
                "title": title,
                "version": version
            }
            
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
        finally:
            page.close()
            self.close_browser()
