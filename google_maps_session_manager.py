#!/usr/bin/env python3
"""
Google Maps Session Manager

Manages browser sessions with proper Google consent handling and cookie persistence.
This allows reusing authenticated sessions across multiple scraping operations.

Usage:
    from google_maps_session_manager import GoogleMapsSessionManager

    session_manager = GoogleMapsSessionManager()
    page = session_manager.get_authenticated_page()

"""

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from playwright.sync_api import (
    sync_playwright,
    Browser,
    BrowserContext,
    Page,
    TimeoutError,
)
from proxy_manager import ProxyManager


class GoogleMapsSessionManager:
    """
    Manages Google Maps browser sessions with consent handling and cookie persistence.

    Key features:
    - Simplified proxy support that bypasses complex session management
    - Backwards-compatible flow for non-proxy sessions
    - Automatic consent handling and optional storage-state persistence
    - Helper methods for recaptcha detection and graceful cleanup
    """

    def __init__(
        self,
        headless: bool = False,
        user_data_dir: Optional[str] = None,
        proxy_manager: Optional[ProxyManager] = None,
        max_auth_attempts: int = 2,
        record_har: bool = False,
        har_output_dir: Optional[str] = None,
    ):
        """Initialise the session manager."""
        self.headless = headless
        self._base_session_dir = Path(user_data_dir or ".gmaps_sessions")
        self._base_session_dir.mkdir(parents=True, exist_ok=True)
        self.user_data_dir_path: Path = self._base_session_dir / "default"
        self.storage_state_path: Path = self.user_data_dir_path / "storage_state.json"
        self.logger = logging.getLogger(__name__)
        self.proxy_manager = proxy_manager
        self._current_proxy_info = None
        self.max_auth_attempts = max_auth_attempts
        self._recaptcha_detected = False
        self.record_har = record_har
        if record_har:
            output_dir = Path(har_output_dir or "debug/har")
            output_dir.mkdir(parents=True, exist_ok=True)
            self.har_output_dir = output_dir
        else:
            self.har_output_dir = None

        if not self.logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
            self.logger.setLevel(logging.INFO)

        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._active_har_path: Optional[Path] = None

    def get_authenticated_page(self, target_url: Optional[str] = None) -> Page:
        """Return a page that is navigated to ``target_url`` with consent handled."""
        target_url = target_url or "https://www.google.com/maps"
        self.logger.info("Getting authenticated Google Maps page...")

        if self.proxy_manager:
            return self._get_page_with_proxy_simple(target_url)

        return self._get_page_with_session_management(target_url)

    def _get_page_with_proxy_simple(self, target_url: str) -> Page:
        """Simplified proxy flow that launches a fresh browser per attempt."""
        last_error: Optional[Exception] = None

        for attempt in range(1, self.max_auth_attempts + 1):
            proxy = self.proxy_manager.get_working_proxy(max_attempts=3)
            if not proxy:
                break

            self._current_proxy_info = proxy
            proxy_config = {
                "server": f"http://{proxy['ip']}:{proxy['port']}",
                "username": proxy.get("username"),
                "password": proxy.get("password"),
            }
            self.logger.info(
                "Proxy attempt %s/%s using %s:%s",
                attempt,
                self.max_auth_attempts,
                proxy["ip"],
                proxy["port"],
            )

            try:
                if self._playwright is None:
                    self._playwright = sync_playwright().start()

                self._browser = self._playwright.chromium.launch(
                    headless=self.headless,
                    proxy=proxy_config,
                    args=[
                        '--disable-blink-features=AutomationControlled',
                        '--disable-web-security',
                        '--disable-features=VizDisplayCompositor',
                    ],
                )

                context_kwargs = {
                    "user_agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                    ),
                    "locale": "en-GB",
                    "timezone_id": "Europe/London",
                }

                if self.record_har and self.har_output_dir:
                    timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
                    har_name = f"session_{timestamp}.har"
                    har_path = self.har_output_dir / har_name
                    context_kwargs["record_har_path"] = str(har_path)
                    context_kwargs["record_har_mode"] = "full"
                    self._active_har_path = har_path
                    self.logger.info("Recording HAR to %s", har_path)

                self._context = self._browser.new_context(**context_kwargs)

                page = self._context.new_page()
                self.logger.info("Navigating to: %s", target_url)
                start_time = time.time()
                page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
                elapsed = time.time() - start_time
                self.logger.info("Navigation completed in %.1fs", elapsed)

                if "consent.google.com" in page.url:
                    self.logger.info("Consent page detected, handling...")
                    self._handle_consent_simple(page)

                try:
                    page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    pass

                self.logger.info("Final URL: %s", page.url)
                self.logger.info("Page title: %s", page.title())
                self.proxy_manager.record_success(proxy)
                return page

            except Exception as exc:
                last_error = exc
                self.logger.error("Proxy navigation failed: %s", exc)
                if self._current_proxy_info:
                    self.proxy_manager.record_failure(self._current_proxy_info, block=True)
                self.cleanup()
                continue

        raise last_error or Exception("Unable to load target URL with available proxies")

    def _get_page_with_session_management(self, target_url: str) -> Page:
        """Original persistent-session flow used when no proxy manager is supplied."""
        self._start_browser()

        page: Optional[Page]
        page_reused = False

        storage_fresh = self._storage_state_is_fresh()
        page = None

        if storage_fresh:
            self.logger.info("Storage state still fresh; skipping auth probe")
        else:
            page = self._is_authenticated()

        if page:
            self.logger.info("Using existing authenticated session")
            page_reused = True
        elif not storage_fresh:
            self.logger.info("Setting up new authenticated session")
            last_error = None
            for attempt in range(self.max_auth_attempts):
                try:
                    page = self._setup_authentication()
                    page_reused = True
                    last_error = None
                    break
                except Exception as exc:
                    last_error = exc
                    self.logger.warning(
                        "Auth attempt %s/%s failed: %s",
                        attempt + 1,
                        self.max_auth_attempts,
                        exc,
                    )
                    try:
                        self.cleanup()
                    except Exception:
                        pass
                    self._start_browser()
            if last_error:
                raise last_error

        if page is None or page.is_closed():
            page = self._context.new_page()

        if page is None:
            page = self._context.new_page()
        reusable_page = page if page_reused else None

        try:
            self._setup_consent_handler(page)
        except Exception:
            pass

        try:
            current_url = ""
            try:
                current_url = page.url
            except Exception:
                current_url = ""

            if not self._urls_match(current_url, target_url):
                self.logger.info("Navigating to target URL %s", target_url)
                page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
            if "consent.google.com" in page.url:
                self.logger.info("Consent page detected after navigation")
                self._handle_consent_flow(page)
            else:
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=7000)
                except TimeoutError:
                    self.logger.debug("DOM content load wait timed out; continuing")
                try:
                    page.wait_for_load_state("load", timeout=5000)
                except TimeoutError:
                    self.logger.debug("Page load wait timed out; continuing")
                page.wait_for_timeout(800)
        except Exception as exc:
            self.logger.error("Failed to load target URL %s: %s", target_url, exc)
            if reusable_page:
                try:
                    page.close()
                except Exception:
                    pass
            raise

        return page

    def _handle_consent_simple(self, page: Page):
        """Handle Google consent page using a lightweight strategy."""
        selectors = [
            'button:has-text("Accept all")',
            'button:has-text("I agree")',
            '[aria-label*="Accept"]',
            'button[jslog*="103597"]',
        ]

        for selector in selectors:
            try:
                button = page.locator(selector).first
                if button.is_visible(timeout=5000):
                    button.click()
                    self.logger.info("Clicked consent button: %s", selector)
                    break
            except Exception:
                continue

        try:
            page.wait_for_url(lambda url: "consent.google.com" not in url, timeout=15000)
            self.logger.info("Successfully navigated away from consent page")
        except Exception:
            self.logger.warning("Still on consent page after attempting acceptance")

    def _start_browser(self):
        """Start the browser with persistent context."""
        Path(self.user_data_dir_path).mkdir(parents=True, exist_ok=True)

        if self._playwright is None:
            self._playwright = sync_playwright().start()

        proxy_kwargs = {}
        if self.proxy_manager:
            try:
                proxy = self.proxy_manager.get_working_proxy() or self.proxy_manager.get_current_proxy()
                if proxy:
                    self._current_proxy_info = proxy
                    proxy_kwargs = {
                        "proxy": {
                            "server": f"http://{proxy['ip']}:{proxy['port']}",
                            "username": proxy.get("username"),
                            "password": proxy.get("password"),
                        }
                    }
                    self.logger.info("Using proxy %s:%s", proxy["ip"], proxy["port"])
                    proxy_dir = self._base_session_dir / proxy["slug"]
                    proxy_dir.mkdir(parents=True, exist_ok=True)
                    self.user_data_dir_path = proxy_dir
                    self.storage_state_path = proxy_dir / "storage_state.json"
            except Exception as exc:
                self.logger.warning("Proxy setup failed, continuing without proxy: %s", exc)
                self._current_proxy_info = None

        self._browser = self._playwright.chromium.launch(
            headless=self.headless,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--disable-web-security',
                '--disable-features=VizDisplayCompositor',
            ],
            **proxy_kwargs,
        )

        storage_state = self.storage_state_path if self.storage_state_path.exists() else None
        context_kwargs = {
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "locale": "en-GB",
            "timezone_id": "Europe/London",
        }

        if storage_state:
            context_kwargs["storage_state"] = str(storage_state)

        if self.record_har and self.har_output_dir:
            timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
            har_name = f"session_{timestamp}.har"
            har_path = self.har_output_dir / har_name
            context_kwargs["record_har_path"] = str(har_path)
            context_kwargs["record_har_mode"] = "full"
            self._active_har_path = har_path
            self.logger.info("Recording HAR to %s", har_path)

        self._context = self._browser.new_context(**context_kwargs)

    def _storage_state_is_fresh(self, max_age_seconds: int = 3600) -> bool:
        try:
            if not self.storage_state_path.exists():
                return False
            age = time.time() - self.storage_state_path.stat().st_mtime
            return age < max_age_seconds
        except Exception:
            return False

    @staticmethod
    def _urls_match(current: str, target: str) -> bool:
        if not current or not target:
            return False

        def _normalize(url: str) -> str:
            base = url.split("#", 1)[0]
            if base.endswith("/"):
                base = base.rstrip("/")
            return base

        return _normalize(current) == _normalize(target)

    def _is_authenticated(self) -> Optional[Page]:
        page: Optional[Page] = None
        try:
            page = self._context.new_page()
            self._setup_consent_handler(page)
            page.goto(
                "https://www.google.com/maps",
                wait_until="domcontentloaded",
                timeout=15000,
            )

            if "consent.google.com" in page.url:
                self.logger.debug("Authentication check hit consent page")
                return False

            try:
                page.wait_for_load_state("load", timeout=3000)
            except TimeoutError:
                self.logger.debug("Auth probe load wait timed out; continuing")

            if self.proxy_manager and self._current_proxy_info:
                self.proxy_manager.record_success(self._current_proxy_info)

            return page

        except Exception as exc:
            self.logger.debug("Authentication check failed: %s", exc)
            if self.proxy_manager and self._current_proxy_info:
                self.proxy_manager.record_failure(self._current_proxy_info)
            if page:
                try:
                    page.close()
                except Exception:
                    pass
            return None
        finally:
            if page:
                if page.is_closed():
                    return None

    def _setup_authentication(self) -> Page:
        self.logger.info("Setting up Google authentication...")
        page = self._context.new_page()

        try:
            self._attach_recaptcha_listeners(page)
            page.goto("https://www.google.com/maps", wait_until="domcontentloaded")
            self._setup_consent_handler(page)

            if "consent.google.com" in page.url:
                self.logger.info("Handling Google consent page...")
                self._handle_consent_flow(page)

            self._wait_for_navigation(page)
            self._save_storage_state()

            if self._recaptcha_detected:
                raise Exception("Recaptcha detected during authentication")

            self.logger.info("Authentication setup complete")
            return page

        except Exception as exc:
            self.logger.error("Authentication setup failed: %s", exc)
            try:
                if not page.is_closed():
                    page.close()
            except Exception:
                pass
            raise

    def _setup_consent_handler(self, page: Page):
        try:
            page.add_locator_handler(
                page.get_by_role("heading", name="Before you continue to Google"),
                lambda: self._click_accept_all(page),
            )
            self.logger.debug("Set up automatic consent handler")
        except Exception as exc:
            self.logger.debug("Failed to set up consent handler: %s", exc)

    def _handle_consent_flow(self, page: Page):
        from google_consent_handler import GoogleConsentHandler

        handler = GoogleConsentHandler()
        success = handler._accept_consent(page)

        if not success:
            raise Exception("Failed to automatically accept Google consent")

        self._wait_for_navigation(page)
        self._save_storage_state()

        if self._recaptcha_detected:
            self.logger.warning("Recaptcha was detected during consent handling")

    def _click_accept_all(self, page: Page):
        try:
            page.get_by_role("button", name="Accept all").click()
            self.logger.info("Automatically accepted Google consent")
        except Exception as exc:
            self.logger.debug("Failed to click Accept all: %s", exc)
            try:
                page.locator('[aria-label*="Accept"]').first.click()
                self.logger.info("Accepted consent using aria-label")
            except Exception as exc2:
                self.logger.warning("Could not automatically accept consent: %s", exc2)

        try:
            self._wait_for_navigation(page)
            self._save_storage_state()
        except Exception as exc:
            self.logger.debug("Post-accept wait/storage failed: %s", exc)

    def _wait_for_navigation(self, page: Page, timeout: int = 20000):
        try:
            page.wait_for_load_state("domcontentloaded", timeout=timeout)
            page.wait_for_timeout(1000)

            start_time = time.monotonic()
            while True:
                current_url = page.url
                self.logger.debug("Consent wait check URL: %s", current_url)
                if "consent.google.com" not in current_url:
                    break
                if (time.monotonic() - start_time) * 1000 > timeout:
                    raise TimeoutError("Still on consent page after waiting")
                page.wait_for_timeout(500)

            try:
                page.wait_for_load_state("networkidle", timeout=timeout)
                page.wait_for_timeout(1000)
            except TimeoutError:
                pass

        except TimeoutError as exc:
            self.logger.error("Navigation timeout: %s", exc)
            raise

    def _save_storage_state(self):
        try:
            if self._context:
                state = self._context.storage_state()
                self.storage_state_path.write_text(json.dumps(state))
                self.logger.debug("Saved storage state to %s", self.storage_state_path)
        except Exception as exc:
            self.logger.warning("Failed to save storage state: %s", exc)

    def _attach_recaptcha_listeners(self, page: Page):
        self._recaptcha_detected = False

        def on_response(response):
            url = response.url
            if "recaptcha" in url or "gstatic.com/recaptcha" in url:
                self._recaptcha_detected = True

        try:
            page.on("response", on_response)
        except Exception:
            pass

    def cleanup(self):
        try:
            if self._context:
                storage_state = self._context.storage_state()
                storage_path = os.path.join(self.user_data_dir_path, "storage_state.json")
                with open(storage_path, 'w') as fh:
                    json.dump(storage_state, fh)
        except Exception:
            pass

        try:
            if self._browser:
                self._browser.close()
        except Exception:
            pass
        finally:
            self._browser = None

        try:
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass
        finally:
            self._playwright = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()


def get_authenticated_page(headless: bool = False) -> Page:
    manager = GoogleMapsSessionManager(headless=headless)
    return manager.get_authenticated_page()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    with GoogleMapsSessionManager() as manager:
        page = manager.get_authenticated_page()
        print(f"Authenticated page URL: {page.url}")
        print(f"Page title: {page.title()}")
        page.goto("https://maps.app.goo.gl/FsGevWWrjvab4tZ9A", wait_until="domcontentloaded")
        print(f"Test URL loaded: {page.url}")
        page.close()
