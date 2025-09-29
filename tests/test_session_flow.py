"""Tests for session flow behaviour when proxies are enabled."""

from google_maps_brand_scraper import GoogleMapsBrandScraper


def test_scrape_brands_reuses_authenticated_page(monkeypatch):
    """Ensure scraper trusts session manager page when proxies are enabled."""

    target_url = "https://maps.app.goo.gl/ExampleTarget"

    class FakePage:
        def __init__(self):
            self.url = target_url
            self.goto_calls = []

        def goto(self, *args, **kwargs):
            self.goto_calls.append((args, kwargs))

        def wait_for_load_state(self, *args, **kwargs):
            return None

        def wait_for_timeout(self, *args, **kwargs):
            return None

        def title(self):
            return "Example Mall"

        def locator(self, selector):
            return self

        def first(self):
            return self

        def is_enabled(self):
            return True

        def is_visible(self, *args, **kwargs):
            return True

        def click(self):
            return None

        def content(self):
            return "<html></html>"

        _debug_dump = lambda *args, **kwargs: None

    fake_page = FakePage()

    class FakeSessionManager:
        instances = []

        def __init__(self, headless=False, proxy_manager=None, max_auth_attempts=0):
            self.headless = headless
            self.proxy_manager = proxy_manager
            self.max_auth_attempts = max_auth_attempts
            self.requested_urls = []
            self.cleaned_up = False
            FakeSessionManager.instances.append(self)

        def get_authenticated_page(self, target_url=None):
            self.requested_urls.append(target_url)
            fake_page.url = target_url
            return fake_page

        def cleanup(self):
            self.cleaned_up = True

    extracted_brands = ["Brand A", "Brand B"]

    def fake_extract(self, page):
        assert page.url == target_url
        return extracted_brands

    monkeypatch.setattr(
        "google_maps_brand_scraper.GoogleMapsSessionManager",
        FakeSessionManager,
    )
    monkeypatch.setattr(
        GoogleMapsBrandScraper,
        "_extract_brands_from_directory",
        fake_extract,
    )

    scraper = GoogleMapsBrandScraper(
        headless=True,
        use_proxies=True,
        proxy_manager="PROXY_SENTINEL",
    )
    scraper._debug_dump = lambda *args, **kwargs: None

    brands = scraper.scrape_brands(target_url)

    assert brands == extracted_brands
    assert fake_page.goto_calls == []

    assert FakeSessionManager.instances, "Session manager should be instantiated"
    session_instance = FakeSessionManager.instances[0]
    assert session_instance.proxy_manager == "PROXY_SENTINEL"
    assert session_instance.max_auth_attempts == 1
    assert session_instance.requested_urls == [target_url]
    assert session_instance.cleaned_up is True

