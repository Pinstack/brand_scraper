#!/usr/bin/env python3
"""
Test Playwright with a simple proxy configuration to isolate the issue.
"""

import logging
import sys
import time
from playwright.sync_api import sync_playwright


def test_simple_proxy():
    """Test Playwright with a simple proxy setup."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    logger = logging.getLogger(__name__)
    logger.info("Testing simple proxy configuration...")

    # Use one of our working proxies
    proxy_config = {
        "server": "http://45.38.107.97:6014",  # Our fastest proxy
        "username": "zpwhlpsh",
        "password": "f12nqx4tf9bl"
    }

    logger.info(f"Using proxy: {proxy_config['server']}")

    with sync_playwright() as p:
        try:
            # Launch browser with proxy
            logger.info("Launching browser with proxy...")
            browser = p.chromium.launch(
                headless=False,  # Run headed so we can see what happens
                proxy=proxy_config
            )

            # Create context
            context = browser.new_context()
            page = context.new_page()

            # Test simple navigation first
            logger.info("Testing simple navigation to httpbin.org...")
            start_time = time.time()
            
            try:
                page.goto("https://httpbin.org/ip", timeout=30000)
                elapsed = time.time() - start_time
                logger.info(f"✅ httpbin.org loaded in {elapsed:.1f}s")
                logger.info(f"Page content: {page.content()[:200]}...")
            except Exception as e:
                elapsed = time.time() - start_time
                logger.error(f"❌ httpbin.org failed after {elapsed:.1f}s: {e}")

            # Test Google Maps
            logger.info("Testing Google Maps navigation...")
            start_time = time.time()
            
            try:
                page.goto("https://www.google.com/maps", timeout=60000)
                elapsed = time.time() - start_time
                logger.info(f"✅ Google Maps loaded in {elapsed:.1f}s")
                logger.info(f"Current URL: {page.url}")
                logger.info(f"Page title: {page.title()}")
            except Exception as e:
                elapsed = time.time() - start_time
                logger.error(f"❌ Google Maps failed after {elapsed:.1f}s: {e}")

            # Wait for user to see what's happening
            logger.info("Browser is open - check what you see. Press Enter to continue...")
            input()

        except Exception as e:
            logger.error(f"Browser setup failed: {e}")
            import traceback
            traceback.print_exc()
        
        finally:
            try:
                browser.close()
            except:
                pass


def main():
    """Main test function."""
    print("Testing simple proxy configuration...")
    print("=" * 60)
    
    test_simple_proxy()
    
    print("=" * 60)
    print("Test completed")


if __name__ == "__main__":
    main()
