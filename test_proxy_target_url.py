#!/usr/bin/env python3
"""
Test proxies specifically with the target Google Maps URL.
"""

import logging
import sys
import time
from proxy_manager import create_default_proxy_manager


def test_proxy_target_url():
    """Test proxies with the specific target URL."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    logger = logging.getLogger(__name__)
    target_url = "https://maps.app.goo.gl/FsGevWWrjvab4tZ9A"
    logger.info(f"Testing proxies with target URL: {target_url}")

    # Create proxy manager
    proxy_manager = create_default_proxy_manager()
    if not proxy_manager.proxies:
        logger.error("No proxies available for testing")
        return

    logger.info(f"Testing {len(proxy_manager.proxies)} proxies...")

    working_proxies = []
    failed_proxies = []

    for i, proxy in enumerate(proxy_manager.proxies):
        logger.info(f"\n--- Testing proxy {i+1}/{len(proxy_manager.proxies)}: {proxy['ip']}:{proxy['port']} ---")
        
        start_time = time.time()
        try:
            logger.info(f"Testing {target_url}...")
            success = proxy_manager.test_proxy(proxy, timeout=20)
            elapsed = time.time() - start_time
            
            if success:
                logger.info(f"✅ SUCCESS ({elapsed:.1f}s)")
                working_proxies.append((proxy, elapsed))
            else:
                logger.error(f"❌ FAILED ({elapsed:.1f}s)")
                failed_proxies.append(proxy)
                
        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"❌ ERROR: {e} ({elapsed:.1f}s)")
            failed_proxies.append(proxy)

    # Summary
    logger.info("\n" + "="*60)
    logger.info("TARGET URL PROXY TEST SUMMARY")
    logger.info("="*60)
    
    logger.info(f"✅ Working proxies ({len(working_proxies)}):")
    for proxy, elapsed in sorted(working_proxies, key=lambda x: x[1]):
        logger.info(f"  - {proxy['ip']}:{proxy['port']} ({elapsed:.1f}s)")
    
    logger.info(f"\n❌ Failed proxies ({len(failed_proxies)}):")
    for proxy in failed_proxies:
        logger.info(f"  - {proxy['ip']}:{proxy['port']}")
    
    logger.info(f"\nTotal: {len(working_proxies)} working, {len(failed_proxies)} failed")
    
    if working_proxies:
        best_proxy, best_time = min(working_proxies, key=lambda x: x[1])
        logger.info(f"\n🏆 Best proxy for target URL: {best_proxy['ip']}:{best_proxy['port']} ({best_time:.1f}s)")
        return True
    else:
        logger.error("\n❌ No proxies work with target URL!")
        return False


def main():
    """Main test function."""
    print("Testing proxies with target Google Maps URL...")
    print("=" * 60)
    
    success = test_proxy_target_url()
    
    print("=" * 60)
    if success:
        print("✅ Found working proxies for target URL")
        return 0
    else:
        print("❌ No proxies work with target URL")
        return 1


if __name__ == "__main__":
    sys.exit(main())
