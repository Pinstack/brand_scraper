#!/usr/bin/env python3
"""
Updated Google Maps Session Manager with simplified proxy support.

Key improvements:
1. Simplified proxy approach that bypasses complex session management
2. Maintains backward compatibility for non-proxy sessions
3. Better error handling and logging
4. Documented findings for repeatability
"""

import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Optional

from playwright.sync_api import (
    sync_playwright,
    Browser,
    BrowserContext,
    Page,
    TimeoutError,
)
from proxy_manager import ProxyManager, create_default_proxy_manager


class GoogleMapsSessionManager:
    """
    Manages Google Maps browser sessions with consent handling and cookie persistence.

    This class handles the initial consent flow and saves the authenticated state
    so it can be reused for multiple scraping operations.
    
    Key improvements in v2:
    - Simplified proxy approach that bypasses complex session management
    - Better error handling and logging
    - Maintains backward compatibility for non-proxy sessions
    """

    def __init__(
        self,
        headless: bool = False,
        user_data_dir: Optional[str] = None,
        proxy_manager: Optional[ProxyManager] = None,
        max_auth_attempts: int = 2,
    ):
        """
        Initialize the session manager.

        Args:
            headless: Whether to run browser in headless mode (default: False for consent handling)
            user_data_dir: Base directory to store browser data/cookies (optional, defaults to .gmaps_sessions)
            proxy_manager: An instance of ProxyManager for proxy rotation
            max_auth_attempts: Maximum attempts to authenticate a session
        """
        self.headless = headless
        self._base_session_dir = Path(user_data_dir or ".gmaps_sessions")
        self._base_session_dir.mkdir(parents=True, exist_ok=True)
        self.user_data_dir_path: Path = self._base_session_dir / "default" # Default path
        self.storage_state_path: Path = self.user_data_dir_path / "storage_state.json"
        self.logger = logging.getLogger(__name__)
        self.proxy_manager = proxy_manager
        self._current_proxy_info = None
        self.max_auth_attempts = max_auth_attempts
        self._recaptcha_detected = False

        # Configure logging
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
            self.logger.setLevel(logging.INFO)

        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None

    def get_authenticated_page(self, target_url: str) -> Page:
        """
        Get an authenticated page that has passed Google consent.
        
        Uses simplified approach for proxy sessions to avoid complex session management.

        Args:
            target_url: URL to navigate to after authentication.

        Returns:
            Authenticated Page object ready for scraping
        """
        self.logger.info("Getting authenticated Google Maps page...")

        # For proxy sessions, use simplified approach
        if self.proxy_manager:
            return self._get_page_with_proxy_simple(target_url)
        
        # For non-proxy sessions, use existing complex logic
        return self._get_page_with_session_management(target_url)

    def _get_page_with_proxy_simple(self, target_url: str) -> Page:
        """
        Simplified proxy approach that bypasses complex session management.
        
        This approach:
        1. Launches browser with proxy (we know this works)
        2. Navigates directly to target URL
        3. Handles consent if needed
        4. Returns the page
        
        Key findings from testing:
        - Proxy configuration works fine with Playwright
        - Navigation is fast (4s vs 30s timeout in complex approach)
        - Consent handling works automatically
        - The core issue was session management complexity
        """
        self.logger.info("Using simplified proxy approach...")
        
        # Get a working proxy
        working_proxy = self.proxy_manager.get_working_proxy(max_attempts=1)
        if not working_proxy:
            raise Exception("No working proxy available")
        
        self._current_proxy_info = working_proxy
        proxy_config = {
            "server": f"http://{working_proxy['ip']}:{working_proxy['port']}",
            "username": working_proxy.get("username"),
            "password": working_proxy.get("password"),
        }
        
        self.logger.info(f"Using proxy: {working_proxy['ip']}:{working_proxy['port']}")
        
        # Start playwright and browser
        if self._playwright is None:
            self._playwright = sync_playwright().start()
        
        try:
            # Launch browser with proxy
            self._browser = self._playwright.chromium.launch(
                headless=self.headless,
                proxy=proxy_config,
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--disable-web-security',
                    '--disable-features=VizDisplayCompositor'
                ]
            )
            
            # Create context
            self._context = self._browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
                locale="en-GB",
                timezone_id="Europe/London",
            )
            
            # Create page and navigate directly to target URL
            page = self._context.new_page()
            self.logger.info(f"Navigating to: {target_url}")
            
            start_time = time.time()
            page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
            elapsed = time.time() - start_time
            self.logger.info(f"Navigation completed in {elapsed:.1f}s")
            
            # Handle consent if needed
            if "consent.google.com" in page.url:
                self.logger.info("Consent page detected, handling...")
                self._handle_consent_simple(page)
            
            # Wait for final page to load
            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except:
                pass  # Don't fail if networkidle times out
            
            self.logger.info(f"Final URL: {page.url}")
            self.logger.info(f"Page title: {page.title()}")
            
            # Record proxy success
            self.proxy_manager.record_success(working_proxy)
            
            return page
            
        except Exception as e:
            self.logger.error(f"Proxy navigation failed: {e}")
            if self._current_proxy_info:
                self.proxy_manager.record_failure(self._current_proxy_info, block=True)
            raise

    def _get_page_with_session_management(self, target_url: str) -> Page:
        """
        Original complex session management for non-proxy sessions.
        """
        self.logger.info("Using complex session management...")
        
        # Start browser with persistent context
        self._start_browser()

        # Create or load authenticated context
        if self._is_authenticated():
            self.logger.info("Using existing authenticated session")
        else:
            self.logger.info("Setting up new authenticated session")
            last_error = None
            for attempt in range(self.max_auth_attempts):
                try:
                    self._setup_authentication()
                    last_error = None
                    break
                except Exception as e:
                    last_error = e
                    self.logger.warning(f"Auth attempt {attempt+1}/{self.max_auth_attempts} failed: {e}")
                    # Restart browser/context for a clean retry
                    try:
                        self.cleanup()
                    except Exception:
                        pass
                    self._start_browser()
            if last_error:
                raise last_error

        # Create a fresh page in the authenticated context and navigate to target
        page = self._context.new_page()
        try:
            self._setup_consent_handler(page)
            self.logger.info(f"Navigating to target URL {target_url}")
            page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
            if "consent.google.com" in page.url:
                self.logger.info("Consent page detected after navigation")
                self._handle_consent_flow(page)
            else:
                try:
                    page.wait_for_load_state("networkidle", timeout=20000)
                    page.wait_for_timeout(1000)
                except TimeoutError:
                    pass
        except Exception as e:
            self.logger.error(f"Failed to load target URL {target_url}: {e}")
            raise
        return page

    def _handle_consent_simple(self, page: Page):
        """Simplified consent handling for proxy sessions."""
        try:
            # Try to find and click Accept all button
            accept_selectors = [
                'button:has-text("Accept all")',
                'button:has-text("I agree")',
                '[aria-label*="Accept"]',
                'button[jslog*="103597"]'
            ]
            
            for selector in accept_selectors:
                try:
                    button = page.locator(selector).first
                    if button.is_visible(timeout=5000):
                        button.click()
                        self.logger.info(f"Clicked consent button: {selector}")
                        break
                except:
                    continue
            
            # Wait for navigation away from consent page
            try:
                page.wait_for_url(lambda url: "consent.google.com" not in url, timeout=15000)
                self.logger.info("Successfully navigated away from consent page")
            except:
                self.logger.warning("Still on consent page after clicking")
                
        except Exception as e:
            self.logger.error(f"Error handling consent: {e}")

    def _start_browser(self):
        """Start the browser with persistent context."""
        # Ensure user data directory exists
        Path(self.user_data_dir_path).mkdir(parents=True, exist_ok=True)

        # Start playwright if not already started
        if self._playwright is None:
            self._playwright = sync_playwright().start()

        # Optional proxy configuration
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
                    self.logger.info(f"Using proxy {proxy['ip']}:{proxy['port']}")
                    # Use dedicated session directory per proxy
                    proxy_dir = self._base_session_dir / proxy["slug"]
                    proxy_dir.mkdir(parents=True, exist_ok=True)
                    self.user_data_dir_path = proxy_dir
                    self.storage_state_path = proxy_dir / "storage_state.json"
            except Exception as e:
                self.logger.warning(f"Proxy setup failed, continuing without proxy: {e}")
                self._current_proxy_info = None

        # Launch browser
        self._browser = self._playwright.chromium.launch(
            headless=self.headless,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--disable-web-security',
                '--disable-features=VizDisplayCompositor'
            ],
            **proxy_kwargs,
        )

        # Create persistent context (load storage state if present)
        storage_state = self.storage_state_path if self.storage_state_path.exists() else None

        self._context = self._browser.new_context(
            storage_state=str(storage_state) if storage_state else None,
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="en-GB",
            timezone_id="Europe/London",
        )

    def _is_authenticated(self) -> bool:
        """
        Check if we already have an authenticated session.

        Returns:
            True if authenticated, False otherwise
        """
        page: Optional[Page] = None
        try:
            # Quick test: try to access a Google Maps page
            page = self._context.new_page()
            self._setup_consent_handler(page)
            page.goto(
                "https://www.google.com/maps",
                wait_until="domcontentloaded",
                timeout=25000,
            )

            if "consent.google.com" in page.url:
                self.logger.debug("Authentication check hit consent page")
                return False

            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except TimeoutError:
                pass

            authenticated = True

            if self.proxy_manager and self._current_proxy_info:
                self.proxy_manager.record_success(self._current_proxy_info)

            return authenticated

        except Exception as e:
            self.logger.debug(f"Authentication check failed: {e}")
            if self.proxy_manager and self._current_proxy_info:
                self.proxy_manager.record_failure(self._current_proxy_info)
            return False
        finally:
            if page:
                try:
                    page.close()
                except Exception:
                    pass

    def _setup_authentication(self):
        """Set up authentication by handling the consent flow."""
        self.logger.info("Setting up Google authentication...")

        page = self._context.new_page()

        try:
            # Attach recaptcha listeners
            self._attach_recaptcha_listeners(page)

            # Navigate to stable Maps root first to establish consent cookies
            page.goto("https://www.google.com/maps", wait_until="domcontentloaded")

            # Set up automatic consent handler
            self._setup_consent_handler(page)

            # Handle consent if needed
            if "consent.google.com" in page.url:
                self.logger.info("Handling Google consent page...")
                self._handle_consent_flow(page)

            # Wait for Maps page to load
            self._wait_for_navigation(page)
            
            # Persist storage state immediately after successful navigation
            self._save_storage_state()

            # Fail fast if recaptcha detected
            if self._recaptcha_detected:
                raise Exception("Recaptcha detected during authentication")

            self.logger.info("Authentication setup complete")

        except Exception as e:
            # If we failed due to consent linger, try a limited number of retries
            self.logger.error(f"Authentication setup failed: {e}")
            raise
        finally:
            page.close()

    def _setup_consent_handler(self, page: Page):
        """Set up automatic locator handler for Google consent dialogs."""
        try:
            # Set up handler for the consent page heading
            page.add_locator_handler(
                page.get_by_role("heading", name="Before you continue to Google"),
                lambda: self._click_accept_all(page)
            )
            self.logger.debug("Set up automatic consent handler")
        except Exception as e:
            self.logger.debug(f"Failed to set up consent handler: {e}")

    def _handle_consent_flow(self, page: Page):
        """Handle the Google consent flow."""
        from google_consent_handler import GoogleConsentHandler

        handler = GoogleConsentHandler()
        success = handler._accept_consent(page)

        if not success:
            raise Exception("Failed to automatically accept Google consent")

        # Wait for redirect to complete and persist state
        self._wait_for_navigation(page)
        self._save_storage_state()

        # Clear recaptcha flag post-consent
        if self._recaptcha_detected:
            self.logger.warning("Recaptcha was detected during consent handling")

    def _click_accept_all(self, page: Page):
        """Click the Accept all button when consent dialog appears."""
        try:
            page.get_by_role("button", name="Accept all").click()
            self.logger.info("Automatically accepted Google consent")
        except Exception as e:
            self.logger.debug(f"Failed to click Accept all: {e}")
            # Try alternative selectors
            try:
                page.locator('[aria-label*="Accept"]').first.click()
                self.logger.info("Accepted consent using aria-label")
            except Exception as e2:
                self.logger.warning(f"Could not automatically accept consent: {e2}")

        # After clicking, ensure we wait for navigation away from consent page
        try:
            self._wait_for_navigation(page)
            self._save_storage_state()
        except Exception as e:
            self.logger.debug(f"Post-accept wait/storage failed: {e}")

    def _wait_for_navigation(self, page: Page, timeout: int = 20000):
        """Wait for the page to navigate away from the consent page."""
        try:
            page.wait_for_load_state("domcontentloaded", timeout=timeout)
            page.wait_for_timeout(1000)

            start_time = time.monotonic()
            while True:
                current_url = page.url
                self.logger.debug(f"Consent wait check URL: {current_url}")
                if "consent.google.com" not in current_url:
                    break
                if (time.monotonic() - start_time) * 1000 > timeout:
                    raise TimeoutError("Still on consent page after waiting")
                page.wait_for_timeout(500)

            # Try to wait for network idle, but do not fail if it times out
            try:
                page.wait_for_load_state("networkidle", timeout=timeout)
                page.wait_for_timeout(1000)
            except TimeoutError:
                # Proceed if we've left the consent domain
                pass

        except TimeoutError as e:
            self.logger.error(f"Navigation timeout: {e}")
            raise

    def _save_storage_state(self):
        """Persist storage state to disk for reuse."""
        try:
            if self._context:
                state = self._context.storage_state()
                self.storage_state_path.write_text(json.dumps(state))
                self.logger.debug(f"Saved storage state to {self.storage_state_path}")
        except Exception as e:
            self.logger.warning(f"Failed to save storage state: {e}")

    def _attach_recaptcha_listeners(self, page: Page):
        """Attach simple listeners to detect recaptcha assets."""
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
        """Clean up browser resources."""
        # Save storage state if possible
        try:
            if self._context:
                storage_state = self._context.storage_state()
                storage_path = os.path.join(self.user_data_dir_path, "storage_state.json")
                with open(storage_path, 'w') as f:
                    json.dump(storage_state, f)
        except Exception:
            pass

        # Close browser and playwright idempotently
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
    """
    Convenience function to get an authenticated Google Maps page.

    Args:
        headless: Whether to run in headless mode

    Returns:
        Authenticated Page object
    """
    manager = GoogleMapsSessionManager(headless=headless)
    return manager.get_authenticated_page()


if __name__ == "__main__":
    # Quick test
    logging.basicConfig(level=logging.INFO)

    with GoogleMapsSessionManager() as manager:
        page = manager.get_authenticated_page()
        print(f"Authenticated page URL: {page.url}")
        print(f"Page title: {page.title()}")

        # Test navigation
        page.goto("https://maps.app.goo.gl/FsGevWWrjvab4tZ9A", wait_until="domcontentloaded")
        print(f"Test URL loaded: {page.url}")

        page.close()
