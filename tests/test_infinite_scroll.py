"""Tests for directory infinite scroll helper."""

import pytest


class FakeResponse:
    def __init__(self, url: str, *, status: int = 200, headers=None):
        self._url = url
        self._status = status
        self._headers = headers or {}

    @property
    def url(self):
        return self._url

    @property
    def status(self):
        return self._status

    def headers(self):
        return self._headers

    def header_value(self, name: str):
        return self._headers.get(name)


class FakeLocator:
    def __init__(self, counts, heights=None):
        self.counts = list(counts)
        self.index = 0
        self.scroll_calls = 0
        self.heights = list(heights or [])
        self.height_index = 0

    def evaluate(self, script):
        if script and "scrollTo" in script:
            self.scroll_calls += 1
            return None

        if script == "el => el.scrollHeight":
            if self.height_index < len(self.heights):
                value = self.heights[self.height_index]
                self.height_index += 1
                return value
            return self.heights[-1] if self.heights else 0

        if self.index < len(self.counts):
            value = self.counts[self.index]
            self.index += 1
        else:
            value = self.counts[-1]
        return value

    def locator(self, _selector):
        return self

    def count(self):
        return self.evaluate(None)

    def scroll(self):
        self.scroll_calls += 1


class FakePage:
    def __init__(self, container, responses=None):
        self.container = container
        self.sleep_calls = []
        self.events = {}
        self.off_calls = []
        self.responses_to_fire = list(responses or [])

    def locator(self, selector):
        if selector in {"#directory", "[aria-label~=\"Directory\"]", "div[role=\"list\"]", "div[jslog*=\"11886\"]"}:
            return self.container
        raise AssertionError(f"Unexpected selector {selector}")

    def wait_for_timeout(self, ms):
        self.sleep_calls.append(ms)
        if self.responses_to_fire:
            response = self.responses_to_fire.pop(0)
            for callback in self.events.get("response", []):
                callback(response)

    def on(self, event_name, callback):
        self.events.setdefault(event_name, []).append(callback)

    def off(self, event_name, callback):
        self.off_calls.append((event_name, callback))

    def remove_listener(self, event_name, callback):
        self.off(event_name, callback)


@pytest.fixture
def scroll_helper():
    from google_maps_brand_scraper import scroll_directory_until_complete
    return scroll_directory_until_complete


def test_scroll_stops_after_repeated_counts(scroll_helper):
    from google_maps_brand_scraper import ScrollTelemetry, DIRECTORY_CONTAINER_SELECTORS

    helper = scroll_helper
    container = FakeLocator([10, 18, 22, 22, 22], heights=[1000, 1200, 1400, 1400, 1400])
    page = FakePage(container)

    telemetry = helper(page, DIRECTORY_CONTAINER_SELECTORS, max_empty_scrolls=2, wait_between_scrolls_ms=100)

    assert isinstance(telemetry, ScrollTelemetry)
    assert telemetry.scrolls_performed == 3
    assert telemetry.final_card_count == 22
    assert telemetry.pb_sentinel_triggered is False
    assert page.off_calls, "Listener should be detached"


def test_scroll_stops_when_pb_sentinel_triggered(scroll_helper):
    from google_maps_brand_scraper import DIRECTORY_CONTAINER_SELECTORS

    helper = scroll_helper
    container = FakeLocator([8, 16, 20, 24], heights=[900, 1200, 1500, 1800])
    page = FakePage(container, responses=[FakeResponse("https://maps.google.com/preview/pb=?foo", status=204)])

    telemetry = helper(page, DIRECTORY_CONTAINER_SELECTORS, max_empty_scrolls=5, wait_between_scrolls_ms=10)

    assert telemetry.pb_sentinel_triggered is True
    assert telemetry.scrolls_performed == 1
    assert telemetry.responses_observed == 1


def test_scroll_caps_total_iterations(scroll_helper):
    from google_maps_brand_scraper import DIRECTORY_CONTAINER_SELECTORS

    helper = scroll_helper
    container = FakeLocator([5, 5, 5, 5, 5, 5], heights=[800, 900, 950, 950, 950, 950])
    page = FakePage(container)

    telemetry = helper(
        page,
        DIRECTORY_CONTAINER_SELECTORS,
        max_empty_scrolls=10,
        max_total_scrolls=3,
        wait_between_scrolls_ms=1,
    )

    assert telemetry.scrolls_performed == 3

