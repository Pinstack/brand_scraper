"""Tests for resilient View all button detection."""

import pytest


class FakeLocator:
    def __init__(self, visible=True, to_click=None, enabled=True, scroll_result=True, children=None):
        self.visible = visible
        self.enabled = enabled
        self.scroll_result = scroll_result
        self.scroll_calls = 0
        self.to_click = to_click or (lambda: None)
        self.children = children or {}

    def first(self):
        return self

    def is_visible(self, timeout=0):
        if isinstance(self.visible, BaseException):
            raise self.visible
        return self.visible

    def is_enabled(self):
        if isinstance(self.enabled, BaseException):
            raise self.enabled
        return self.enabled

    def scroll_into_view_if_needed(self, timeout=0):
        self.scroll_calls += 1
        if isinstance(self.scroll_result, BaseException):
            raise self.scroll_result
        return self.scroll_result

    def click(self):
        self.to_click()

    def locator(self, selector):
        if selector not in self.children:
            raise KeyError(selector)
        return self.children[selector]

    def count(self):
        if self.children:
            return len(self.children)
        return 1 if self.visible else 0


class FakePage:
    def __init__(self, locator_map):
        self.locator_map = locator_map
        self.clicks = []

    def locator(self, selector):
        if selector not in self.locator_map:
            raise KeyError(selector)
        return self.locator_map[selector]

    def get_by_role(self, role, name):
        key = f"ROLE::{role}::{name}"
        if key not in self.locator_map:
            raise KeyError(key)
        return self.locator_map[key]

    def wait_for_timeout(self, ms):
        self.clicks.append(f"sleep:{ms}")


def test_click_view_all_success_first_strategy(monkeypatch):
    from google_maps_brand_scraper import _click_view_all_button

    clicked = []

    def record_click():
        clicked.append("clicked")

    page = FakePage(
        {
            'ROLE::button::View all': FakeLocator(to_click=record_click),
        }
    )

    assert _click_view_all_button(page)
    assert clicked == ["clicked"]


def test_click_view_all_fallback_selector(monkeypatch):
    from google_maps_brand_scraper import _click_view_all_button

    clicked = []

    def record_click():
        clicked.append("clicked")

    page = FakePage(
        {
            'ROLE::button::View all': FakeLocator(visible=False),
            '[aria-label="View all"]': FakeLocator(to_click=record_click),
        }
    )

    assert _click_view_all_button(page)
    assert clicked == ["clicked"]


def test_click_view_all_failure(monkeypatch):
    from google_maps_brand_scraper import _click_view_all_button

    page = FakePage(
        {
            'ROLE::button::View all': FakeLocator(visible=False),
            '[aria-label="View all"]': FakeLocator(visible=False),
        }
    )

    assert _click_view_all_button(page) is False


def test_click_view_all_xpath_priority(monkeypatch):
    from google_maps_brand_scraper import _click_view_all_button

    clicked = []

    def record_click():
        clicked.append("clicked")

    xpath_selector = 'xpath=//h2[contains(normalize-space(.), "Directory")]/following::button[normalize-space(.)="View all"][1]'

    page = FakePage(
        {
            xpath_selector: FakeLocator(to_click=record_click),
            'ROLE::button::View all': FakeLocator(visible=False),
        }
    )

    assert _click_view_all_button(page)
    assert clicked == ["clicked"]
    assert page.locator_map[xpath_selector].scroll_calls == 1


def test_click_view_all_section_fallback(monkeypatch):
    from google_maps_brand_scraper import _click_view_all_button

    clicked = []

    def record_click():
        clicked.append("clicked")

    section_heading = FakeLocator(
        children={
            "xpath=ancestor::div[contains(@class,'m6QErb')][1]": FakeLocator(
                children={
                    "button:has-text(\"View all\")": FakeLocator(to_click=record_click)
                }
            )
        }
    )

    page = FakePage(
        {
            'ROLE::button::View all': FakeLocator(visible=False),
            "h2:has-text(\"Directory\")": section_heading,
        }
    )

    assert _click_view_all_button(page)
    assert clicked == ["clicked"]

