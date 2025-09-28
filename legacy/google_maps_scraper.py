#!/usr/bin/env python3
"""
Google Maps Brand Scraper

A Playwright-based scraper for extracting brand/store information from Google Maps business listings.
Specifically designed to scrape all brands from shopping centers and malls.

Usage:
    from google_maps_scraper import GoogleMapsScraper

    scraper = GoogleMapsScraper()
    brands = scraper.scrape_brands_from_url("https://maps.app.goo.gl/FsGevWWrjvab4tZ9A")
    print(f"Found {len(brands)} brands: {brands}")
"""

import json
import time
import logging
from typing import List, Dict, Optional, Set
from urllib.parse import urlparse, parse_qs

from playwright.sync_api import sync_playwright, Page, Browser, Playwright


class GoogleMapsScraper:
    """
    A scraper for extracting brand/store information from Google Maps business listings.

    This class handles the complexities of Google Maps' dynamic loading, consent pages,
    and infinite scroll functionality to extract all brands from a business location.
    """

    def __init__(self, headless: bool = True, timeout: int = 30000):
        """
        Initialize the scraper.

        Args:
            headless: Whether to run browser in headless mode (default: True)
            timeout: Default timeout for element operations in milliseconds (default: 30000)
        """
        self.headless = headless
        self.timeout = timeout
        self.logger = logging.getLogger(__name__)

        # Configure logging
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
            self.logger.setLevel(logging.INFO)

    def scrape_brands_from_url(self, url: str) -> List[str]:
        """
        Scrape all brands from a Google Maps business listing URL.

        Args:
            url: Google Maps URL (supports both goo.gl short links and direct maps URLs)

        Returns:
            List of brand/store names found at the location
        """
        self.logger.info(f"Starting brand scrape for URL: {url}")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless)
            page = browser.new_page()

            try:
                # Navigate to the URL and handle consent
                brands = self._scrape_with_browser(page, url)
                self.logger.info(f"Successfully scraped {len(brands)} brands")
                return brands

            except Exception as e:
                self.logger.error(f"Error during scraping: {e}")
                return []

            finally:
                browser.close()

    def _scrape_with_browser(self, page: Page, url: str) -> List[str]:
        """Internal method to handle the scraping logic with an open browser page."""

        # Navigate to the URL
        page.goto(url, wait_until='domcontentloaded')

        # Handle Google consent page if redirected
        self._handle_consent_page(page)

        # Wait for Maps page to load
        page.wait_for_load_state('networkidle')
        page.wait_for_timeout(3000)  # Extra time for dynamic content

        self.logger.info(f"Successfully loaded Maps page: {page.url}")

        # Click "View all" to open the directory
        if not self._click_view_all_button(page):
            self.logger.warning("Could not click View all button")
            return []

        # Extract brands from the directory
        brands = self._extract_brands_from_page(page)

        return brands

    def _handle_consent_page(self, page: Page) -> None:
        """Handle Google consent/privacy pages that may appear."""

        if 'consent.google.com' not in page.url:
            return

        self.logger.info("Handling Google consent page...")

        # Try multiple strategies to accept consent
        accept_strategies = [
            lambda: page.locator('button:has-text("Accept all")').first.click(),
            lambda: page.locator('[aria-label*="Accept"]').first.click(),
            lambda: page.locator('button[data-value="accept"]').first.click(),
            lambda: page.evaluate('''
                const buttons = document.querySelectorAll('button');
                for (const btn of buttons) {
                    if (btn.textContent && btn.textContent.includes('Accept')) {
                        btn.click();
                        return true;
                    }
                }
                return false;
            ''')
        ]

        for strategy in accept_strategies:
            try:
                strategy()
                page.wait_for_timeout(2000)
                if 'consent.google.com' not in page.url:
                    self.logger.info("Successfully accepted consent")
                    return
            except Exception as e:
                self.logger.debug(f"Consent strategy failed: {e}")
                continue

        self.logger.warning("Could not automatically accept consent - manual intervention may be required")

    def _click_view_all_button(self, page: Page) -> bool:
        """Click the 'View all' button to expand the directory."""

        self.logger.info("Looking for View all button...")

        # Multiple strategies to find and click View all
        click_strategies = [
            # Strategy 1: Direct span text
            lambda: page.locator('span:has-text("View all")').first.click(),

            # Strategy 2: ARIA label
            lambda: page.locator('[aria-label="View all"]').first.click(),

            # Strategy 3: jslog attribute (analytics tracking)
            lambda: page.locator('[jslog*="103597"]').first.click(),

            # Strategy 4: Find clickable parent of View all text
            lambda: page.locator('xpath=//span[contains(text(), "View all")]/ancestor::button').first.click(),
            lambda: page.locator('xpath=//span[contains(text(), "View all")]/ancestor::div[@role="button"]').first.click(),
            lambda: page.locator('xpath=//span[contains(text(), "View all")]/ancestor::a').first.click(),
        ]

        for i, strategy in enumerate(click_strategies):
            try:
                strategy()
                page.wait_for_timeout(2000)  # Wait for directory to load
                self.logger.info(f"Successfully clicked View all button (strategy {i+1})")
                return True
            except Exception as e:
                self.logger.debug(f"View all click strategy {i+1} failed: {e}")
                continue

        return False

    def _extract_brands_from_page(self, page: Page) -> List[str]:
        """Extract brand/store names from the loaded directory page."""

        self.logger.info("Extracting brands from page...")

        brands: Set[str] = set()

        # Multiple selectors to find brand elements
        selectors = [
            '[role="button"]',
            'button',
            '[role="link"]',
            'a[href*="place"]',
            'div[role="button"]',
            'span[role="button"]'
        ]

        for selector in selectors:
            try:
                elements = page.locator(selector).all()
                for element in elements:
                    try:
                        text = element.text_content().strip()
                        if self._is_brand_name(text):
                            brands.add(text)
                    except:
                        continue
            except Exception as e:
                self.logger.debug(f"Error with selector {selector}: {e}")
                continue

        brand_list = sorted(list(brands))
        self.logger.info(f"Extracted {len(brand_list)} unique brands")
        return brand_list

    def _is_brand_name(self, text: str) -> bool:
        """
        Determine if a text string represents a brand/store name.

        This function filters out UI elements, navigation items, and other
        non-brand text that commonly appears on Google Maps pages.
        """

        if not text or len(text) < 3:
            return False

        # Common UI and navigation text to exclude
        exclude_patterns = [
            # Basic UI elements
            'View all', 'More', 'Search', 'Directory', 'Back',

            # Category headers
            'Department stores', 'Food & Drink', 'Clothing', 'Shoes',
            'Health & Beauty', 'Home & Kitchen', 'Jewellery', 'Electronics',
            'Toys & Sports', 'Other',

            # Navigation and actions
            'Menu', 'Saved', 'Recents', 'Get app', 'Google apps', 'Sign in',
            'Show Your Location', 'Zoom', 'Browse Street View', 'Street View',
            'Layers', 'Collapse side panel',

            # Business actions
            'Directions', 'Save', 'Nearby', 'Send to phone', 'Share',
            'See photos', 'Suggest an edit', 'Write a review', 'Call phone number',
            'Copy address', 'Copy phone number', 'Copy website', 'Copy Plus Code',
            'Reserve a table', 'Order online', 'Like',

            # Information sections
            'Popular times', 'Photos and videos', 'Add photos and videos',
            'Questions and answers', 'More questions', 'Ask the community',
            'Review summary', 'Updates from customers', 'People also search for',
            'Web results', 'About this data',

            # Status and metadata
            'Open ⋅ Closes', 'Opens soon', 'Closes soon', 'Closed',
            'Learn more', 'Show opening hours', 'Information about Popular Times',
            'Local Guide', 'reviews', 'photos', 'New', 'a week ago', '3 weeks ago',
            'a month ago', 'Photo of', 'Sundays', 'Go to the previous day',
            'Go to the next day',

            # Maps features
            'Interactive map', '20 m', 'Browse Street View images',

            # Consent page
            'Reject all', 'Accept all', 'Language:', 'Privacy Policy',
            'Terms of Service', 'Before you continue',

            # Transport and services
            'Restaurants', 'Hotels', 'Things to do', 'Transport', 'Parking',
            'Chemists', 'ATMs', 'Next page'
        ]

        if text in exclude_patterns:
            return False

        # Filter out icon-only elements (starting with special Unicode characters)
        icon_prefixes = [
            '', '', '', '', '', '', '', '', '', '', '', '',
            '', '', '', '', '', '', '', '', '', '', '', '',
            '', '', '', '', '', '', '', '', '', '', '', ''
        ]

        if any(text.startswith(prefix) for prefix in icon_prefixes):
            return False

        # Additional filters for numbers and ratings
        if text.isdigit() or 'stars' in text or text.startswith(''):
            return False

        return True

    def save_results(self, brands: List[str], url: str, filename: Optional[str] = None) -> str:
        """
        Save scraping results to a JSON file.

        Args:
            brands: List of brand names
            url: Original URL that was scraped
            filename: Optional filename (default: auto-generated)

        Returns:
            Path to the saved file
        """

        if not filename:
            # Generate filename from URL
            parsed_url = urlparse(url)
            if 'goo.gl' in parsed_url.netloc:
                # For goo.gl links, use a generic name
                filename = 'google_maps_brands.json'
            else:
                # For direct maps URLs, try to extract place name
                path_parts = parsed_url.path.split('/')
                place_name = 'google_maps_brands.json'
                for part in path_parts:
                    if part and not part.startswith('@') and not part.startswith('data'):
                        place_name = f"{part.replace('+', '_').replace('%2B', '_')}_brands.json"
                        break
                filename = place_name

        result = {
            'url': url,
            'scraped_at': time.strftime('%Y-%m-%d %H:%M:%S'),
            'total_brands': len(brands),
            'brands': brands,
            'method': 'GoogleMapsScraper v1.0 - Playwright-based extraction',
            'notes': 'Scraped using automated browser with consent handling and View all button clicking'
        }

        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=2, ensure_ascii=False)

        self.logger.info(f"Results saved to {filename}")
        return filename


def main():
    """Command-line interface for the scraper."""

    import argparse

    parser = argparse.ArgumentParser(description='Scrape brands from Google Maps business listings')
    parser.add_argument('url', help='Google Maps URL to scrape')
    parser.add_argument('--output', '-o', help='Output JSON file (optional)')
    parser.add_argument('--verbose', '-v', action='store_true', help='Enable verbose logging')
    parser.add_argument('--headed', action='store_true', help='Run browser in headed mode (visible)')

    args = parser.parse_args()

    # Configure logging
    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    # Create scraper
    scraper = GoogleMapsScraper(headless=not args.headed)

    # Scrape brands
    brands = scraper.scrape_brands_from_url(args.url)

    # Save results
    filename = scraper.save_results(brands, args.url, args.output)

    # Print summary
    print(f"\nScraping completed!")
    print(f"Found {len(brands)} brands at {args.url}")
    print(f"Results saved to: {filename}")

    if brands:
        print("\nBrands found:")
        for brand in brands:
            print(f"  - {brand}")


if __name__ == '__main__':
    main()
