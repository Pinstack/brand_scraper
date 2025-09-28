#!/usr/bin/env python3
"""
Test script to verify navigation to Google Maps mall directory WITHOUT proxies.

This establishes a baseline to ensure the navigation logic works before testing proxies.
"""

import logging
import sys
from google_maps_session_manager import GoogleMapsSessionManager


def test_no_proxy_navigation(target_url: str = "https://maps.app.goo.gl/FsGevWWrjvab4tZ9A") -> bool:
    """
    Test navigation to target URL without proxies (baseline test).

    Args:
        target_url: Google Maps URL to navigate to

    Returns:
        True if successfully reached mall directory, False otherwise
    """
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    logger = logging.getLogger(__name__)
    logger.info(f"Testing baseline navigation (no proxy) to: {target_url}")

    # Create session manager WITHOUT proxy support
    session_manager = GoogleMapsSessionManager(
        headless=False,  # Run headed so user can observe
        proxy_manager=None,  # No proxy manager
        max_auth_attempts=2
    )

    success = False

    try:
        # Get authenticated page without proxy
        logger.info("Attempting to get authenticated page (no proxy)...")
        page = session_manager.get_authenticated_page(target_url)

        # Check if we're on the right page
        current_url = page.url
        logger.info(f"Current URL: {current_url}")

        # Verify we're not on consent or error pages
        if "consent.google.com" in current_url:
            logger.error("FAILED: Still on consent page")
            return False

        if "maps.app.goo.gl" not in current_url and "www.google.com/maps" not in current_url:
            logger.error(f"FAILED: Unexpected URL: {current_url}")
            return False

        # Check for mall directory indicators
        page_title = page.title()
        logger.info(f"Page title: {page_title}")

        # Look for directory-related elements
        directory_indicators = [
            page.locator('span:has-text("View all")'),
            page.locator('[aria-label="View all"]'),
            page.locator('button').filter(has_text="View all"),
        ]

        found_view_all = False
        for indicator in directory_indicators:
            try:
                if indicator.is_visible(timeout=5000):
                    found_view_all = True
                    logger.info("✓ Found 'View all' button - directory likely loaded")
                    break
            except:
                continue

        if not found_view_all:
            logger.warning("⚠ 'View all' button not found - may not be on directory page")

        # Check for mall/store indicators in page content
        page_text = page.locator('body').text_content().lower()
        mall_indicators = ['mall', 'shopping center', 'directory', 'stores', 'brands']

        found_mall_content = any(indicator in page_text for indicator in mall_indicators)

        if found_mall_content:
            logger.info("✓ Found mall-related content in page")
        else:
            logger.warning("⚠ No mall-related content detected")

        # Overall assessment
        if found_view_all or found_mall_content:
            logger.info("✓ SUCCESS: Successfully navigated to mall directory (no proxy)")
            success = True
        else:
            logger.error("FAILED: Could not verify mall directory access")
            success = False

        # Take a screenshot for verification
        try:
            page.screenshot(path="test_no_proxy_navigation.png")
            logger.info("Screenshot saved: test_no_proxy_navigation.png")
        except Exception as e:
            logger.warning(f"Could not save screenshot: {e}")

    except Exception as e:
        logger.error(f"FAILED: Exception during navigation test: {e}")
        success = False

    finally:
        # Always cleanup
        try:
            session_manager.cleanup()
            logger.info("Browser cleanup completed")
        except Exception as e:
            logger.warning(f"Cleanup error: {e}")

    return success


def main():
    """Main test function."""
    target_url = sys.argv[1] if len(sys.argv) > 1 else "https://maps.app.goo.gl/FsGevWWrjvab4tZ9A"

    print(f"Testing baseline navigation (no proxy) to: {target_url}")
    print("=" * 60)

    success = test_no_proxy_navigation(target_url)

    print("=" * 60)
    if success:
        print("✅ TEST PASSED: Baseline navigation successful")
        return 0
    else:
        print("❌ TEST FAILED: Baseline navigation failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
