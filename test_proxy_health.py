#!/usr/bin/env python3
"""
Test script to diagnose proxy health and performance.

This script tests each proxy individually to identify which ones are working
and which are too slow for Google Maps navigation.
"""

import logging
import sys
import time
from proxy_manager import create_default_proxy_manager


def test_proxy_health():
    """Test all proxies for health and speed."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    logger = logging.getLogger(__name__)
    logger.info("Testing proxy health and performance...")

    # Create proxy manager
    proxy_manager = create_default_proxy_manager()
    if not proxy_manager.proxies:
        logger.error("No proxies available for testing")
        return

    logger.info(f"Testing {len(proxy_manager.proxies)} proxies...")

    working_proxies = []
    slow_proxies = []
    failed_proxies = []

    for i, proxy in enumerate(proxy_manager.proxies):
        logger.info(f"\n--- Testing proxy {i+1}/{len(proxy_manager.proxies)}: {proxy['ip']}:{proxy['port']} ---")
        
        start_time = time.time()
        
        try:
            # Test with a simple health check first
            logger.info("Testing basic connectivity...")
            basic_health = proxy_manager.test_proxy(proxy, timeout=10)
            
            if not basic_health:
                failed_proxies.append(proxy)
                logger.error(f"❌ Basic connectivity failed")
                continue
            
            # Test with Google Maps (more realistic test)
            logger.info("Testing Google Maps navigation...")
            from google_maps_session_manager import GoogleMapsSessionManager
            
            session_manager = GoogleMapsSessionManager(
                headless=True,
                proxy_manager=proxy_manager,
                max_auth_attempts=1  # Single attempt for testing
            )
            
            # Force this specific proxy
            session_manager._current_proxy_info = proxy
            
            try:
                page = session_manager.get_authenticated_page("https://www.google.com/maps")
                elapsed = time.time() - start_time
                
                if "consent.google.com" in page.url:
                    logger.warning(f"⚠ Consent page detected (took {elapsed:.1f}s)")
                    slow_proxies.append((proxy, elapsed))
                elif "google.com/maps" in page.url:
                    logger.info(f"✅ Successfully reached Google Maps (took {elapsed:.1f}s)")
                    working_proxies.append((proxy, elapsed))
                else:
                    logger.warning(f"⚠ Unexpected URL: {page.url} (took {elapsed:.1f}s)")
                    slow_proxies.append((proxy, elapsed))
                
                page.close()
                
            except Exception as e:
                elapsed = time.time() - start_time
                logger.error(f"❌ Google Maps test failed: {e} (took {elapsed:.1f}s)")
                if elapsed > 30:
                    slow_proxies.append((proxy, elapsed))
                else:
                    failed_proxies.append(proxy)
            
            finally:
                try:
                    session_manager.cleanup()
                except:
                    pass
                
        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"❌ Proxy test failed: {e} (took {elapsed:.1f}s)")
            failed_proxies.append(proxy)

    # Summary
    logger.info("\n" + "="*60)
    logger.info("PROXY HEALTH SUMMARY")
    logger.info("="*60)
    
    logger.info(f"✅ Working proxies ({len(working_proxies)}):")
    for proxy, elapsed in sorted(working_proxies, key=lambda x: x[1]):
        logger.info(f"  - {proxy['ip']}:{proxy['port']} ({elapsed:.1f}s)")
    
    logger.info(f"\n⚠ Slow proxies ({len(slow_proxies)}):")
    for proxy, elapsed in sorted(slow_proxies, key=lambda x: x[1]):
        logger.info(f"  - {proxy['ip']}:{proxy['port']} ({elapsed:.1f}s)")
    
    logger.info(f"\n❌ Failed proxies ({len(failed_proxies)}):")
    for proxy in failed_proxies:
        logger.info(f"  - {proxy['ip']}:{proxy['port']}")
    
    logger.info(f"\nTotal: {len(working_proxies)} working, {len(slow_proxies)} slow, {len(failed_proxies)} failed")
    
    if working_proxies:
        best_proxy, best_time = min(working_proxies, key=lambda x: x[1])
        logger.info(f"\n🏆 Best proxy: {best_proxy['ip']}:{best_proxy['port']} ({best_time:.1f}s)")
        return True
    else:
        logger.error("\n❌ No working proxies found!")
        return False


def main():
    """Main test function."""
    print("Diagnosing proxy health and performance...")
    print("=" * 60)
    
    success = test_proxy_health()
    
    print("=" * 60)
    if success:
        print("✅ Found working proxies")
        return 0
    else:
        print("❌ No working proxies found")
        return 1


if __name__ == "__main__":
    sys.exit(main())
