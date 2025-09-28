#!/usr/bin/env python3
"""
Simple proxy test to check if proxies work with basic URLs before testing Google Maps.
"""

import logging
import sys
import time
from proxy_manager import create_default_proxy_manager


def test_proxy_simple():
    """Test proxies with simple URLs first."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    logger = logging.getLogger(__name__)
    logger.info("Testing proxies with simple URLs...")

    # Create proxy manager
    proxy_manager = create_default_proxy_manager()
    if not proxy_manager.proxies:
        logger.error("No proxies available for testing")
        return

    logger.info(f"Testing {len(proxy_manager.proxies)} proxies...")

    # Test URLs in order of complexity
    test_urls = [
        "https://httpbin.org/ip",  # Simple IP check
        "https://www.google.com",  # Google homepage
        "https://www.google.com/maps",  # Google Maps
    ]

    for i, proxy in enumerate(proxy_manager.proxies):
        logger.info(f"\n--- Testing proxy {i+1}/{len(proxy_manager.proxies)}: {proxy['ip']}:{proxy['port']} ---")
        
        for url in test_urls:
            start_time = time.time()
            try:
                logger.info(f"Testing {url}...")
                success = proxy_manager.test_proxy(proxy, timeout=15)
                elapsed = time.time() - start_time
                
                if success:
                    logger.info(f"✅ {url} - SUCCESS ({elapsed:.1f}s)")
                else:
                    logger.error(f"❌ {url} - FAILED ({elapsed:.1f}s)")
                    break  # Stop testing this proxy if it fails
                    
            except Exception as e:
                elapsed = time.time() - start_time
                logger.error(f"❌ {url} - ERROR: {e} ({elapsed:.1f}s)")
                break  # Stop testing this proxy if it errors
        
        logger.info("-" * 50)


def main():
    """Main test function."""
    print("Testing proxies with simple URLs...")
    print("=" * 60)
    
    test_proxy_simple()
    
    print("=" * 60)
    print("Test completed")


if __name__ == "__main__":
    main()
