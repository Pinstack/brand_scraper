#!/usr/bin/env python3
"""
Debug script to see exactly what's happening during proxy navigation.
"""

import logging
import sys
import time
from proxy_manager import create_default_proxy_manager
from google_maps_session_manager import GoogleMapsSessionManager


def debug_proxy_navigation():
    """Debug what's happening during proxy navigation."""
    logging.basicConfig(
        level=logging.DEBUG,  # Enable debug logging
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    logger = logging.getLogger(__name__)
    logger.info("Debugging proxy navigation...")

    # Create proxy manager
    proxy_manager = create_default_proxy_manager()
    if not proxy_manager.proxies:
        logger.error("No proxies available")
        return

    # Use the fastest proxy we found
    best_proxy = proxy_manager.proxies[0]  # 84.247.60.125:6095
    logger.info(f"Using fastest proxy: {best_proxy['ip']}:{best_proxy['port']}")

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
    print("Debugging proxy navigation...")
    print("=" * 60)
    
    debug_proxy_navigation()
    
    print("=" * 60)
    print("Debug completed")


if __name__ == "__main__":
    main()
