#!/usr/bin/env python3
"""
Debug script to test just one proxy and see exactly what's happening.
"""

import logging
import sys
import time
from proxy_manager import create_default_proxy_manager
from google_maps_session_manager import GoogleMapsSessionManager


def debug_single_proxy():
    """Debug what's happening with a single proxy."""
    logging.basicConfig(
        level=logging.DEBUG,  # Enable debug logging
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    logger = logging.getLogger(__name__)
    logger.info("Debugging single proxy navigation...")

    # Create proxy manager
    proxy_manager = create_default_proxy_manager()
    if not proxy_manager.proxies:
        logger.error("No proxies available")
        return

    # Use just the first proxy
    test_proxy = proxy_manager.proxies[0]
    logger.info(f"Testing single proxy: {test_proxy['ip']}:{test_proxy['port']}")

    # Create session manager
    session_manager = GoogleMapsSessionManager(
        headless=False,  # Run headed so we can see what's happening
        proxy_manager=proxy_manager,
        max_auth_attempts=1
    )

    try:
        logger.info("Starting browser...")
        start_time = time.time()
        
        # Get authenticated page
        page = session_manager.get_authenticated_page("https://www.google.com/maps")
        
        elapsed = time.time() - start_time
        logger.info(f"Browser setup took {elapsed:.1f}s")
        logger.info(f"Current URL: {page.url}")
        logger.info(f"Page title: {page.title()}")
        
        # Check if we're on consent page
        if "consent.google.com" in page.url:
            logger.warning("Still on consent page - this is the problem!")
        else:
            logger.info("Successfully past consent page")
        
        # Wait for user to see what's happening
        logger.info("Browser is open - check what you see. Press Enter to continue...")
        input()
        
    except Exception as e:
        elapsed = time.time() - start_time
        logger.error(f"Error after {elapsed:.1f}s: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        try:
            session_manager.cleanup()
        except:
            pass


def main():
    """Main debug function."""
    print("Debugging single proxy navigation...")
    print("=" * 60)
    
    debug_single_proxy()
    
    print("=" * 60)
    print("Debug completed")


if __name__ == "__main__":
    main()
