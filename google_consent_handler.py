#!/usr/bin/env python3
"""
Google Consent Handler

A focused module for handling Google consent/privacy pages that appear before accessing
Google Maps and other Google services. This module provides reliable consent acceptance
across different Google consent page variations.

Usage:
    from google_consent_handler import GoogleConsentHandler

    handler = GoogleConsentHandler()
    page = handler.navigate_with_consent(browser, "https://maps.app.goo.gl/...")
"""

import logging
import time
from typing import Optional

from playwright.sync_api import Page, Browser, TimeoutError


class GoogleConsentHandler:
    """
    Handles Google consent/privacy pages that appear before accessing Google services.

    This class provides multiple strategies to automatically accept Google consent pages,
    handling various consent page layouts and languages.
    """

    def __init__(self, timeout: int = 10000):
        """
        Initialize the consent handler.

        Args:
            timeout: Timeout in milliseconds for consent page operations
        """
        self.timeout = timeout
        self.logger = logging.getLogger(__name__)

    def navigate_with_consent(self, browser: Browser, url: str) -> Page:
        """
        Navigate to a URL and automatically handle any Google consent pages.

        Args:
            browser: Playwright browser instance
            url: URL to navigate to

        Returns:
            Page object after consent handling
        """
        page = browser.new_page()

        try:
            # Navigate to the URL
            page.goto(url, wait_until='domcontentloaded')

            # Handle consent if redirected
            if self._is_consent_page(page):
                self.logger.info("Consent page detected, attempting to accept...")
                if self._accept_consent(page):
                    self.logger.info("Consent accepted successfully")
                else:
                    self.logger.warning("Could not automatically accept consent")

            # Wait for final page to load
            page.wait_for_load_state('networkidle')
            page.wait_for_timeout(2000)  # Extra time for dynamic content

            return page

        except Exception as e:
            self.logger.error(f"Error during navigation with consent: {e}")
            raise

    def _is_consent_page(self, page: Page) -> bool:
        """Check if the current page is a Google consent page."""
        return 'consent.google.com' in page.url

    def _accept_consent(self, page: Page) -> bool:
        """
        Attempt to accept consent on a Google consent page using locator handlers.

        Returns:
            True if consent was accepted, False otherwise
        """

        # Set up automatic locator handler so any initial rendering triggers a click
        try:
            page.add_locator_handler(
                page.get_by_role("heading", name="Before you continue to Google"),
                lambda: self._click_accept_all(page)
            )
            self.logger.debug("Locator handler registered for consent dialog")
        except Exception as e:
            self.logger.debug(f"Failed to register locator handler: {e}")

        # Strategy list that will be attempted sequentially
        accept_strategies = [
            self._try_click_selector,
            self._try_click_aria_label,
            self._try_javascript_click,
        ]

        for strategy in accept_strategies:
            try:
                if strategy(page):
                    self.logger.debug(f"Consent strategy {strategy.__name__} executed")
                    page.wait_for_timeout(2000)
                    if not self._is_consent_page(page):
                        return True
            except Exception as e:
                self.logger.debug(f"Consent strategy {strategy.__name__} failed: {e}")

        # Final check in case the locator handler succeeded asynchronously
        page.wait_for_timeout(1000)
        if not self._is_consent_page(page):
            self.logger.debug("Consent cleared by asynchronous handler")
            return True

        return False

    def _click_accept_all(self, page: Page):
        """Click the Accept all button when consent dialog appears."""
        target_url_before = page.url

        try:
            page.get_by_role("button", name="Accept all").click()
            self.logger.info("Automatically accepted Google consent via locator handler")
        except Exception as e:
            self.logger.debug(f"Failed to click Accept all via handler: {e}")
            try:
                page.locator('[aria-label*="Accept"]').first.click()
                self.logger.info("Accepted consent using aria-label via handler")
            except Exception as e2:
                self.logger.warning(f"Could not automatically accept consent via handler: {e2}")

        # Allow navigation to proceed asynchronously
        page.wait_for_timeout(1000)

        # Wait for redirect to complete
        try:
            page.wait_for_url(lambda url: "consent.google.com" not in url, timeout=self.timeout)
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(1000)
        except TimeoutError:
            self.logger.warning("Timeout waiting for consent redirect")

        if page.url != target_url_before and "consent.google.com" not in page.url:
            self.logger.debug(f"Redirected from consent to {page.url}")

    def _try_click_selector(self, page: Page) -> bool:
        """Try clicking accept button using CSS selectors."""
        selectors = [
            'button:has-text("Accept all")',
            'button:has-text("I agree")',
            'button:has-text("Accept")',
            'button:has-text("Agree")',
        ]

        for selector in selectors:
            try:
                button = page.locator(selector).first
                if button.is_visible(timeout=self.timeout):
                    button.click()
                    self.logger.debug(f"Clicked accept button with selector: {selector}")
                    return True
            except:
                continue

        return False

    def _try_click_aria_label(self, page: Page) -> bool:
        """Try clicking accept button using ARIA labels."""
        aria_patterns = [
            '[aria-label*="Accept"]',
            '[aria-label*="Agree"]',
            '[aria-label*="consent"]',
        ]

        for pattern in aria_patterns:
            try:
                button = page.locator(pattern).first
                if button.is_visible(timeout=self.timeout):
                    button.click()
                    self.logger.debug(f"Clicked accept button with aria-label: {pattern}")
                    return True
            except:
                continue

        return False

    def _try_javascript_click(self, page: Page) -> bool:
        """Try clicking accept button using JavaScript evaluation."""
        js_code = '''
        () => {
            // Look for buttons containing accept/agree text
            const buttons = document.querySelectorAll('button, [role="button"], input[type="submit"]');
            for (const btn of buttons) {
                const text = btn.textContent || btn.innerText || '';
                if (text.toLowerCase().includes('accept') ||
                    text.toLowerCase().includes('agree') ||
                    text.toLowerCase().includes('consent')) {
                    btn.click();
                    return true;
                }
            }

            // Look for links containing accept/agree text
            const links = document.querySelectorAll('a');
            for (const link of links) {
                const text = link.textContent || link.innerText || '';
                if (text.toLowerCase().includes('accept') ||
                    text.toLowerCase().includes('agree')) {
                    link.click();
                    return true;
                }
            }

            return false;
        }
        '''

        try:
            result = page.evaluate(js_code)
            if result:
                self.logger.debug("Clicked accept button using JavaScript evaluation")
                return True
        except Exception as e:
            self.logger.debug(f"JavaScript click failed: {e}")

        return False

    def wait_for_consent_completion(self, page: Page, max_wait: int = 10000) -> bool:
        """
        Wait for consent page to complete and redirect.

        Args:
            page: Playwright page object
            max_wait: Maximum time to wait in milliseconds

        Returns:
            True if consent was completed, False if still on consent page
        """
        start_time = time.time() * 1000

        while (time.time() * 1000) - start_time < max_wait:
            if not self._is_consent_page(page):
                return True
            page.wait_for_timeout(500)

        return False


# Convenience function for quick usage
def navigate_with_consent(browser: Browser, url: str, headless: bool = True) -> Page:
    """
    Convenience function to navigate to a URL with automatic consent handling.

    Args:
        browser: Playwright browser instance (will be created if None)
        url: URL to navigate to
        headless: Whether to run browser in headless mode

    Returns:
        Page object after consent handling
    """
    handler = GoogleConsentHandler()
    return handler.navigate_with_consent(browser, url)
