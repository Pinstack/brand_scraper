#!/usr/bin/env python3
"""
Google Maps Brand Scraper

A focused module for scraping brand/store information from Google Maps business directory listings.
Works specifically with shopping centers, malls, and other multi-brand locations.

Usage:
    from google_maps_brand_scraper import GoogleMapsBrandScraper
    from google_consent_handler import GoogleConsentHandler

    consent_handler = GoogleConsentHandler()
    scraper = GoogleMapsBrandScraper()

    # Use together
    brands = scraper.scrape_brands("https://maps.app.goo.gl/FsGevWWrjvab4tZ9A")
"""

import json
import logging
import time
from typing import List, Optional

from playwright.sync_api import sync_playwright, Browser
from google_maps_session_manager import GoogleMapsSessionManager
from proxy_manager import create_default_proxy_manager, ProxyManager


class GoogleMapsBrandScraper:
    """
    Scrapes brand/store information from Google Maps business listings.

    This class focuses specifically on extracting brand names from directory listings
    in shopping centers and malls. It handles the "View all" button clicking and
    brand extraction logic.
    """

    def __init__(self, headless: bool = True, timeout: int = 30000, use_proxies: bool = False, proxy_manager: Optional[ProxyManager] = None):
        """
        Initialize the brand scraper.

        Args:
            headless: Whether to run browser in headless mode
            timeout: Default timeout for element operations in milliseconds
        """
        self.headless = headless
        self.timeout = timeout
        self.logger = logging.getLogger(__name__)
        self.use_proxies = use_proxies
        self.proxy_manager = proxy_manager

        # Configure logging
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
            self.logger.setLevel(logging.INFO)

    def scrape_brands(self, url: str) -> List[str]:
        """
        Scrape all brands from a Google Maps business listing URL.

        Args:
            url: Google Maps URL (supports both goo.gl short links and direct maps URLs)

        Returns:
            List of brand/store names found at the location
        """
        self.logger.info(f"Starting brand scrape for URL: {url}")

        # Use session manager for authenticated browsing
        session_manager = GoogleMapsSessionManager(
            headless=self.headless,
            proxy_manager=(self.proxy_manager if self.use_proxies else None),
            max_auth_attempts=(1 if self.use_proxies else 3),
        )

        try:
            # Get authenticated page
            page = session_manager.get_authenticated_page(target_url=url)

            # Navigate to target URL
            page.goto(url, wait_until="domcontentloaded")

            # Handle scenarios where navigation sends us back to consent page
            if "consent.google.com" in page.url:
                self.logger.info("Redirected to consent page after navigation; re-running consent handler")
                session_manager._handle_consent_flow(page)
                page.wait_for_load_state("networkidle")
                page.wait_for_timeout(2000)
                if "consent.google.com" in page.url:
                    raise RuntimeError("Unable to pass consent page after retry")
            else:
                page.wait_for_load_state("networkidle")
                page.wait_for_timeout(2000)

            # Extract brands from the directory
            brands = self._extract_brands_from_directory(page)

            self.logger.info(f"Successfully scraped {len(brands)} brands")
            return brands

        except Exception as e:
            self.logger.error(f"Error during scraping: {e}")
            return []

        finally:
            session_manager.cleanup()

    def _extract_brands_from_directory(self, page) -> List[str]:
        """Extract brand names from the Google Maps directory."""

        # Click "View all" to expand the directory
        if not self._click_view_all_button(page):
            self.logger.warning("Could not click View all button - may not have directory")
            return []

        # Extract brands from the expanded directory
        brands = self._extract_brands_from_page(page)

        return brands

    def _click_view_all_button(self, page) -> bool:
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

    def _extract_brands_from_page(self, page) -> List[str]:
        """Extract brand/store names from the loaded directory page."""

        self.logger.info("Extracting brands from directory...")

        brands = set()

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
            # Generate filename from URL or use default
            filename = 'google_maps_brands.json'

        result = {
            'url': url,
            'scraped_at': time.strftime('%Y-%m-%d %H:%M:%S'),
            'total_brands': len(brands),
            'brands': brands,
            'method': 'GoogleMapsBrandScraper v1.0 - Playwright-based extraction',
            'notes': 'Scraped using consent handler and directory expansion'
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
    parser.add_argument('--use-proxies', action='store_true', help='Enable proxy rotation via ProxyManager')

    args = parser.parse_args()

    # Configure logging
    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    # Create scraper
    proxy_mgr = create_default_proxy_manager() if args.use_proxies else None
    scraper = GoogleMapsBrandScraper(headless=not args.headed, use_proxies=args.use_proxies, proxy_manager=proxy_mgr)

    # Scrape brands
    brands = scraper.scrape_brands(args.url)

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
