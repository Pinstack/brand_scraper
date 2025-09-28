#!/usr/bin/env python3
"""
Simplified proxy scraper that bypasses complex session management.

This approach:
1. Launches browser with proxy (we know this works)
2. Navigates directly to target URL
3. Handles consent if needed
4. Returns the page for scraping
"""

import logging
import sys
import time
from playwright.sync_api import sync_playwright
from proxy_manager import create_default_proxy_manager


class SimpleProxyScraper:
    """Simplified scraper that works with proxies."""
    
    def __init__(self, headless: bool = False):
        self.headless = headless
        self.logger = logging.getLogger(__name__)
        
        # Configure logging
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
            self.logger.setLevel(logging.INFO)

    def get_page_with_proxy(self, target_url: str, proxy_manager=None):
        """
        Get a page with proxy support, simplified approach.
        
        Args:
            target_url: URL to navigate to
            proxy_manager: Optional proxy manager
            
        Returns:
            Page object ready for scraping
        """
        self.logger.info(f"Getting page for: {target_url}")
        
        # Get a working proxy
        proxy_config = None
        if proxy_manager:
            working_proxy = proxy_manager.get_working_proxy(max_attempts=1)
            if working_proxy:
                proxy_config = {
                    "server": f"http://{working_proxy['ip']}:{working_proxy['port']}",
                    "username": working_proxy.get("username"),
                    "password": working_proxy.get("password"),
                }
                self.logger.info(f"Using proxy: {working_proxy['ip']}:{working_proxy['port']}")
            else:
                self.logger.warning("No working proxy found, proceeding without proxy")
        
        with sync_playwright() as p:
            try:
                # Launch browser with or without proxy
                launch_kwargs = {"headless": self.headless}
                if proxy_config:
                    launch_kwargs["proxy"] = proxy_config
                
                self.logger.info("Launching browser...")
                browser = p.chromium.launch(**launch_kwargs)
                
                # Create context
                context = browser.new_context()
                page = context.new_page()
                
                # Navigate directly to target URL
                self.logger.info(f"Navigating to: {target_url}")
                start_time = time.time()
                
                page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
                elapsed = time.time() - start_time
                self.logger.info(f"Navigation completed in {elapsed:.1f}s")
                
                # Handle consent if needed
                if "consent.google.com" in page.url:
                    self.logger.info("Consent page detected, handling...")
                    self._handle_consent(page)
                
                # Wait for final page to load
                try:
                    page.wait_for_load_state("networkidle", timeout=10000)
                except:
                    pass  # Don't fail if networkidle times out
                
                self.logger.info(f"Final URL: {page.url}")
                self.logger.info(f"Page title: {page.title()}")
                
                return page
                
            except Exception as e:
                self.logger.error(f"Failed to get page: {e}")
                raise

    def _handle_consent(self, page):
        """Handle Google consent page."""
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

    def scrape_brands(self, url: str, use_proxies: bool = False):
        """
        Scrape brands from Google Maps URL.
        
        Args:
            url: Google Maps URL to scrape
            use_proxies: Whether to use proxy rotation
            
        Returns:
            List of brand names
        """
        self.logger.info(f"Starting brand scrape for: {url}")
        
        # Get proxy manager if requested
        proxy_manager = None
        if use_proxies:
            proxy_manager = create_default_proxy_manager()
            if not proxy_manager.proxies:
                self.logger.error("No proxies available")
                return []
        
        try:
            # Get page with proxy support
            page = self.get_page_with_proxy(url, proxy_manager)
            
            # Look for "View all" button
            self.logger.info("Looking for 'View all' button...")
            view_all_selectors = [
                'span:has-text("View all")',
                '[aria-label="View all"]',
                'button:has-text("View all")',
                '[jslog*="103597"]'
            ]
            
            found_view_all = False
            for selector in view_all_selectors:
                try:
                    button = page.locator(selector).first
                    if button.is_visible(timeout=5000):
                        button.click()
                        self.logger.info(f"Clicked View all button: {selector}")
                        found_view_all = True
                        break
                except:
                    continue
            
            if not found_view_all:
                self.logger.warning("Could not find 'View all' button")
                return []
            
            # Wait for directory to load
            page.wait_for_timeout(2000)
            
            # Extract brand names
            brands = set()
            brand_selectors = [
                '[role="button"]',
                'button',
                '[role="link"]',
                'a[href*="place"]'
            ]
            
            for selector in brand_selectors:
                try:
                    elements = page.locator(selector).all()
                    for element in elements:
                        try:
                            text = element.text_content().strip()
                            if self._is_brand_name(text):
                                brands.add(text)
                        except:
                            continue
                except:
                    continue
            
            brand_list = sorted(list(brands))
            self.logger.info(f"Found {len(brand_list)} brands")
            return brand_list
            
        except Exception as e:
            self.logger.error(f"Scraping failed: {e}")
            return []

    def _is_brand_name(self, text: str) -> bool:
        """Check if text is a brand name."""
        if not text or len(text) < 3:
            return False
        
        # Exclude common UI elements
        exclude_patterns = [
            'View all', 'More', 'Search', 'Directory', 'Back',
            'Menu', 'Saved', 'Recents', 'Get app', 'Sign in',
            'Directions', 'Save', 'Nearby', 'Share', 'Call',
            'Open ⋅ Closes', 'Opens soon', 'Closed',
            'Restaurants', 'Hotels', 'Things to do'
        ]
        
        if text in exclude_patterns:
            return False
        
        # Filter out icon-only elements
        if any(text.startswith(prefix) for prefix in ['', '']):
            return False
        
        return True


def main():
    """Main function for testing."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Simple proxy scraper')
    parser.add_argument('url', help='Google Maps URL to scrape')
    parser.add_argument('--use-proxies', action='store_true', help='Use proxy rotation')
    parser.add_argument('--headed', action='store_true', help='Run browser in headed mode')
    parser.add_argument('--verbose', '-v', action='store_true', help='Enable verbose logging')
    
    args = parser.parse_args()
    
    # Configure logging
    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)
    
    # Create scraper
    scraper = SimpleProxyScraper(headless=not args.headed)
    
    # Scrape brands
    brands = scraper.scrape_brands(args.url, use_proxies=args.use_proxies)
    
    # Print results
    print(f"\nFound {len(brands)} brands:")
    for brand in brands:
        print(f"  - {brand}")


if __name__ == "__main__":
    main()
