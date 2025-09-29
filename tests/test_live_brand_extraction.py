"""Live integration test for Google Maps brand extraction."""

import os
import pytest


@pytest.mark.live
def test_live_brand_extraction(pytestconfig):
    """Execute the scraper against a real Google Maps directory when enabled."""

    if not pytestconfig.getoption("--run-live"):
        pytest.skip("--run-live flag not provided")

    live_url = pytestconfig.getoption("--live-url") or os.getenv("LIVE_BRAND_URL")
    if not live_url:
        pytest.fail("Live brand extraction requested but no URL provided")

    use_proxies = (
        pytestconfig.getoption("--live-use-proxies")
        or os.getenv("LIVE_USE_PROXIES", "false").lower() in {"1", "true", "yes"}
    )

    proxy_manager = None
    if use_proxies:
        from proxy_manager import create_default_proxy_manager

        proxy_manager = create_default_proxy_manager()

    from google_maps_brand_scraper import GoogleMapsBrandScraper

    scraper = GoogleMapsBrandScraper(use_proxies=use_proxies, proxy_manager=proxy_manager)
    brands = scraper.scrape_brands(live_url)

    assert brands, "Expected at least one brand from live directory"

