#!/usr/bin/env python3
"""
Google Maps Brand Scraper

A focused module for scraping brand/store information from Google Maps business directory listings.
Works specifically with shopping centers, malls, and other multi-brand locations.

Usage:
    from google_maps_brand_scraper import GoogleMapsBrandScraper
    from google_consent_handler import GoogleConsentHandler

    consent_handler = GoogleConsentHandler()
    scraper = GoogleMapsBrandScraper()

    # Use together
    brands = scraper.scrape_brands("https://maps.app.goo.gl/FsGevWWrjvab4tZ9A")
"""

import logging
import os
import re
import time
import json
import base64
from typing import Dict, List, Optional, Sequence, Tuple

from playwright.sync_api import sync_playwright, Browser, TimeoutError as PlaywrightTimeoutError
from google_maps_session_manager import GoogleMapsSessionManager
from proxy_manager import create_default_proxy_manager, ProxyManager
from dataclasses import dataclass
from bs4 import BeautifulSoup


VIEW_ALL_LOCATOR_PRIORITIES: Sequence[str] = (
    'xpath=//h2[contains(normalize-space(.), "Directory")]/following::button[normalize-space(.)="View all"][1]',
    'ROLE::button::View all',
    'span:has-text("View all")',
    '[aria-label="View all"]',
    '[jslog^="103597"]',
    'xpath=//span[contains(text(), "View all")]/ancestor::button',
    'xpath=//span[contains(text(), "View all")]/ancestor::div[@role="button"]',
    'xpath=//span[contains(text(), "View all")]/ancestor::a',
)


CARD_SELECTOR_PRIORITIES: Sequence[str] = (
    '[role="listitem"]',
    'div:has(a[href*="/maps/place/"])',
    'div[jslog*="11886"]',
    'div:has([data-value*="/maps/place/"])',
    'div.Nv2PK',
)

DIRECTORY_CONTAINER_SELECTORS: Sequence[str] = (
    '#directory',
    '[aria-label~="Directory"]',
    'div[role="list"]',
    'div[jslog*="11886"]',
    'div.k7jAl.miFGmb.lJ3Kh.PLbyfe',
    'div.m6QErb.DxyBCb.kA9KIf.dS8AEf.XiKgde',
)

DIRECTORY_TAB_SELECTORS: Sequence[str] = (
    '#directory-tab',
    '[aria-label^="Directory"]',
    'button:has-text("Directory")',
    'div[role="tab"]:has-text("Directory")',
)

CTA_EXCLUSION_NAMES = {
    "order online",
    "reserve a table",
    "book online",
    "call",
    "directions",
}


def activate_directory_tab(
    page,
    *,
    logger=None,
    selectors: Sequence[str] = DIRECTORY_TAB_SELECTORS,
    max_attempts: int = 3,
    wait_between_attempts_ms: int = 500,
) -> bool:
    logger = logger or logging.getLogger(__name__)

    logger.info(f"[DIRECTORY_TAB] Starting activation with {len(selectors)} selectors")

    for attempt in range(1, max_attempts + 1):
        logger.debug(f"[DIRECTORY_TAB] Attempt {attempt}/{max_attempts}")
        for selector in selectors:
            logger.debug(f"[DIRECTORY_TAB] Trying selector: {selector}")
            try:
                locator = page.locator(selector)
                count = locator.count()
                logger.debug(f"[DIRECTORY_TAB] Selector {selector} found {count} elements")

                if count == 0:
                    continue

                candidate = getattr(locator, "first", locator)
                if callable(candidate):
                    candidate = candidate()

                if hasattr(candidate, "is_enabled"):
                    try:
                        enabled = candidate.is_enabled()
                        logger.debug(f"[DIRECTORY_TAB] Selector {selector} enabled: {enabled}")
                        if not enabled:
                            continue
                    except Exception as e:
                        logger.debug(f"[DIRECTORY_TAB] Selector {selector} enabled check failed: {e}")
                        continue

                scroll_heading = getattr(candidate, "scroll_into_view_if_needed", None)
                if callable(scroll_heading):
                    try:
                        scroll_heading(timeout=wait_between_attempts_ms)
                    except Exception as e:
                        logger.debug(f"[DIRECTORY_TAB] Selector {selector} scroll failed: {e}")

                if not hasattr(candidate, "is_visible"):
                    logger.debug(f"[DIRECTORY_TAB] Selector {selector} has no is_visible method")
                    continue

                try:
                    visible = candidate.is_visible(timeout=wait_between_attempts_ms)
                    logger.debug(f"[DIRECTORY_TAB] Selector {selector} visible: {visible}")
                    if not visible:
                        continue
                except PlaywrightTimeoutError:
                    logger.debug(f"[DIRECTORY_TAB] Selector {selector} visibility timeout")
                    continue

                logger.info(f"[DIRECTORY_TAB] Clicking selector: {selector}")
                candidate.click()
                page.wait_for_timeout(wait_between_attempts_ms)
                logger.info("Activated directory tab via selector %s", selector)
                return True
            except Exception as exc:
                logger.debug(
                    "Directory tab selector %s failed on attempt %s: %s",
                    selector,
                    attempt,
                    exc,
                )
                continue

        page.wait_for_timeout(wait_between_attempts_ms)

    return False


@dataclass
class ScrollTelemetry:
    scrolls_performed: int
    final_card_count: int
    pb_sentinel_triggered: bool
    responses_observed: int

    cards_collected: Optional[int] = None


class PbDirectoryCollector:
    def __init__(self, *, logger=None, min_payload_bytes: int = 200):
        self.logger = logger or logging.getLogger(__name__)
        self._payloads: List[str] = []
        self.min_payload_bytes = min_payload_bytes
        self.total_seen = 0
        self.total_stored = 0

    def on_response(self, response):
        self.total_seen += 1
        url = getattr(response, "url", "")
        if "pb=" not in url:
            return

        status = getattr(response, "status", None)
        if status not in (200, 204):
            return

        text: Optional[str] = None
        try:
            body = response.body()
            if isinstance(body, bytes):
                text = body.decode("utf-8", errors="ignore")
            else:
                text = body or ""
        except Exception:
            try:
                text = response.text()
            except Exception as exc:
                self.logger.debug("Failed to read pb payload: %s", exc)
                return

        if not text:
            return

        if status == 204 or len(text) < self.min_payload_bytes:
            return

        self._payloads.append(text)
        self.total_stored += 1
        self.logger.debug("Captured pb payload from %s (len=%s)", url, len(text))

    def extract_cards(self) -> List[Dict[str, Optional[str]]]:
        cards: List[Dict[str, Optional[str]]] = []

        for text in self._payloads:
            if text.startswith(")]}'"):
                text = text[4:]

            try:
                data = json.loads(text)
            except Exception as exc:
                self.logger.debug("Failed to parse pb JSON: %s", exc)
                continue

            cards.extend(self._extract_cards_from_payload(data))

        return cards


def _load_pb_payloads_from_har(context, *, logger=None) -> List[Dict[str, Optional[str]]]:
    logger = logger or logging.getLogger(__name__)
    payloads: List[str] = []

    try:
        har_traces = getattr(context, "_har_traces", None)
        if not har_traces:
            return []

        for har_entry in har_traces.values():
            try:
                for record in har_entry.get("entries", []):
                    request = record.get("request", {})
                    response = record.get("response", {})
                    url = request.get("url")
                    if not url or "pb=" not in url:
                        continue
                    status = response.get("status")
                    if status not in (200, 204):
                        continue
                    content = response.get("content", {})
                    text = content.get("text")
                    if not text:
                        continue
                    if content.get("encoding") == "base64":
                        try:
                            decoded = base64.b64decode(text).decode("utf-8", errors="ignore")
                        except Exception:
                            continue
                        payloads.append(decoded)
                    else:
                        payloads.append(text)
            except Exception as exc:
                logger.debug("Failed to parse HAR entry: %s", exc)
    except Exception as exc:
        logger.debug("Error while accessing HAR traces: %s", exc)

    collector = PbDirectoryCollector(logger=logger)
    for payload in payloads:
        collector._payloads.append(payload)
    return collector.extract_cards()

    def _extract_cards_from_payload(self, payload) -> List[Dict[str, Optional[str]]]:
        results: List[Dict[str, Optional[str]]] = []

        def walk(node):
            if isinstance(node, list):
                if self._looks_like_place_entry(node):
                    parsed = self._parse_place_entry(node)
                    if parsed:
                        results.append(parsed)
                for item in node:
                    walk(item)

        walk(payload)
        return results

    def _looks_like_place_entry(self, node) -> bool:
        if not isinstance(node, list) or len(node) < 2:
            return False
        name = node[1]
        if not isinstance(name, str) or not name.strip():
            return False

        identifiers = []

        def collect_ids(value):
            if isinstance(value, str):
                identifiers.append(value)
            elif isinstance(value, list):
                for item in value:
                    collect_ids(item)

        collect_ids(node[0])
        return any(s.startswith("0x") or s.startswith("/g/") for s in identifiers)

    def _parse_place_entry(self, node) -> Optional[Dict[str, Optional[str]]]:
        try:
            name = node[1].strip()
        except Exception:
            return None

        if not name:
            return None

        category = self._find_category(node)
        floor = self._find_floor(node)

        return {
            "name": name,
            "category": category,
            "floor": floor,
        }

    def _find_category(self, node) -> Optional[str]:
        queue = [node]
        while queue:
            current = queue.pop()
            if isinstance(current, list):
                if (
                    len(current) >= 2
                    and isinstance(current[0], str)
                    and isinstance(current[1], str)
                    and current[1].startswith("gcid:")
                ):
                    return current[0]
                queue.extend(current)
        return None

    def _find_floor(self, node) -> Optional[str]:
        queue = [node]
        while queue:
            current = queue.pop()
            if isinstance(current, str):
                cleaned = current.strip()
                if cleaned.lower().startswith("level") or cleaned.lower().startswith("floor"):
                    return cleaned
            elif isinstance(current, list):
                queue.extend(current)
        return None


def _click_view_all_button(
    page,
    *,
    logger=None,
    selectors: Sequence[str] = VIEW_ALL_LOCATOR_PRIORITIES,
    retry_interval_ms: int = 500,
    max_attempts: int = 6,
) -> bool:
    """Attempt to click the View all button using prioritized selectors."""

    logger = logger or logging.getLogger(__name__)
    logger.info("Looking for View all button...")

    for attempt in range(1, max_attempts + 1):
        for selector in selectors:
            try:
                if selector.startswith("ROLE::"):
                    _, role, name = selector.split("::", 2)
                    locator = page.get_by_role(role, name=name)
                else:
                    locator = page.locator(selector)

                candidate = getattr(locator, "first", locator)
                if callable(candidate):
                    candidate = candidate()

                is_enabled = getattr(candidate, "is_enabled", None)
                if callable(is_enabled):
                    try:
                        if not is_enabled():
                            continue
                    except Exception:
                        continue

                scroll_into_view = getattr(candidate, "scroll_into_view_if_needed", None)
                if callable(scroll_into_view):
                    try:
                        scroll_into_view(timeout=retry_interval_ms)
                    except Exception as exc:
                        logger.debug("Scroll into view failed for selector %s: %s", selector, exc)

                is_visible = getattr(candidate, "is_visible", None)
                if callable(is_visible):
                    try:
                        if not is_visible(timeout=retry_interval_ms):
                            continue
                    except PlaywrightTimeoutError:
                        continue
                    except Exception:
                        continue

                try:
                    candidate.click()
                except Exception as exc:
                    logger.debug("Click via selector %s failed: %s", selector, exc)
                    continue

                page.wait_for_timeout(1500)
                logger.info("Clicked View all using selector %s", selector)
                return True
            except Exception as exc:
                logger.debug("View all selector %s failed on attempt %s: %s", selector, attempt, exc)
                continue

        page.wait_for_timeout(retry_interval_ms)

    logger.info("Primary View all selectors exhausted; attempting section-based fallback")

    fallback_candidate = _find_view_all_in_sections(
        page,
        logger=logger,
        visibility_timeout=retry_interval_ms,
    )

    if fallback_candidate is not None:
        scroll_into_view = getattr(fallback_candidate, "scroll_into_view_if_needed", None)
        if callable(scroll_into_view):
            try:
                scroll_into_view(timeout=retry_interval_ms)
            except Exception as exc:
                logger.debug("Fallback scroll into view failed: %s", exc)

        is_enabled = getattr(fallback_candidate, "is_enabled", None)
        if callable(is_enabled):
            try:
                if not is_enabled():
                    logger.debug("Fallback View all candidate disabled")
                    fallback_candidate = None
            except Exception as exc:
                logger.debug("Fallback is_enabled check failed: %s", exc)
                fallback_candidate = None

        if fallback_candidate is not None:
            is_visible = getattr(fallback_candidate, "is_visible", None)
            if callable(is_visible):
                try:
                    if not is_visible(timeout=retry_interval_ms):
                        fallback_candidate = None
                except PlaywrightTimeoutError:
                    fallback_candidate = None
                except Exception as exc:
                    logger.debug("Fallback visibility check failed: %s", exc)
                    fallback_candidate = None

        if fallback_candidate is not None:
            try:
                fallback_candidate.click()
                page.wait_for_timeout(1500)
                logger.info("Clicked View all using section fallback")
                return True
            except Exception as exc:
                logger.debug("Fallback click failed: %s", exc)

    logger.warning("Unable to locate View all button after %s attempts", max_attempts)
    return False


def _find_view_all_in_sections(page, *, logger=None, visibility_timeout: int = 500):
    """Attempt to locate View all button inside About/Directory sections."""

    logger = logger or logging.getLogger(__name__)

    heading_selectors: Sequence[str] = (
        "h2:has-text(\"Directory\")",
        "div[role='heading']:has-text(\"Directory\")",
        "[aria-label~='Directory'] h2",
        "h2:has-text(\"About\")",
    )

    container_selectors: Sequence[str] = (
        "xpath=ancestor::div[contains(@class,'m6QErb')][1]",
        "xpath=ancestor::div[contains(@class,'Hk4XGb')][1]",
        "xpath=ancestor::*[contains(@aria-label,'Directory') or contains(@aria-label,'About')][1]",
    )

    button_selectors: Sequence[str] = (
        "button:has-text(\"View all\")",
        "[aria-label='View all']",
        "xpath=.//button[normalize-space(.)='View all']",
    )

    for heading_selector in heading_selectors:
        try:
            heading_locator = page.locator(heading_selector)
        except Exception as exc:
            logger.debug("Heading selector %s failed: %s", heading_selector, exc)
            continue

        try:
            heading_count = heading_locator.count()
        except Exception as exc:
            logger.debug("Heading selector %s count failed: %s", heading_selector, exc)
            continue

        if heading_count == 0:
            continue

        candidate_heading = getattr(heading_locator, "first", heading_locator)
        if callable(candidate_heading):
            candidate_heading = candidate_heading()

        scroll_heading = getattr(candidate_heading, "scroll_into_view_if_needed", None)
        if callable(scroll_heading):
            try:
                scroll_heading(timeout=visibility_timeout)
            except Exception as exc:
                logger.debug("Heading scroll failed for %s: %s", heading_selector, exc)

        for container_selector in container_selectors:
            try:
                container_locator = candidate_heading.locator(container_selector)
            except Exception as exc:
                logger.debug("Container selector %s failed: %s", container_selector, exc)
                continue

            try:
                container_count = container_locator.count()
            except Exception as exc:
                logger.debug("Container selector %s count failed: %s", container_selector, exc)
                continue

            if container_count == 0:
                continue

            candidate_container = getattr(container_locator, "first", container_locator)
            if callable(candidate_container):
                candidate_container = candidate_container()

            for button_selector in button_selectors:
                try:
                    button_locator = candidate_container.locator(button_selector)
                except Exception as exc:
                    logger.debug("Button selector %s failed: %s", button_selector, exc)
                    continue

                try:
                    button_count = button_locator.count()
                except Exception as exc:
                    logger.debug("Button selector %s count failed: %s", button_selector, exc)
                    continue

                if button_count == 0:
                    continue

                candidate_button = getattr(button_locator, "first", button_locator)
                if callable(candidate_button):
                    candidate_button = candidate_button()

                is_enabled = getattr(candidate_button, "is_enabled", None)
                if callable(is_enabled):
                    try:
                        if not is_enabled():
                            continue
                    except Exception as exc:
                        logger.debug("Button selector %s enabled check failed: %s", button_selector, exc)
                        continue

                is_visible = getattr(candidate_button, "is_visible", None)
                if callable(is_visible):
                    try:
                        if not is_visible(timeout=visibility_timeout):
                            continue
                    except PlaywrightTimeoutError:
                        continue
                    except Exception as exc:
                        logger.debug("Button selector %s visibility check failed: %s", button_selector, exc)
                        continue

                logger.info(
                    "Located View all button via section fallback (%s -> %s)",
                    heading_selector,
                    button_selector,
                )
                return candidate_button

    return None


def get_directory_cards(page, *, logger=None) -> List[Dict[str, Optional[str]]]:
    """Return structured card data from the current directory pane."""

    logger = logger or logging.getLogger(__name__)

    container_handle = None
    for selector in DIRECTORY_CONTAINER_SELECTORS:
        logger.info("Trying directory container selector: %s", selector)
        try:
            candidate = page.locator(selector)
            count = candidate.count()
            logger.info("Selector %s found %d elements", selector, count)

            if count == 0:
                continue

            wait_for = getattr(candidate, "wait_for", None)
            if callable(wait_for):
                try:
                    wait_for(state="attached", timeout=1500)
                    logger.info("Selector %s is attached", selector)
                except PlaywrightTimeoutError:
                    logger.info("Selector %s attachment timeout", selector)
                    continue

            candidate.evaluate("el => el")
            container_handle = candidate
            logger.info("Using directory container selector: %s", selector)
            break
        except PlaywrightTimeoutError:
            logger.info("Selector %s timeout", selector)
            continue
        except Exception as e:
            logger.info("Selector %s failed: %s", selector, e)
            continue

    if container_handle is None:
        logger.warning("Directory container not found with known selectors; falling back to full page content")
        html = page.content()
    else:
        html = container_handle.evaluate("el => el.outerHTML")

    soup = BeautifulSoup(html, "html.parser")
    return parse_directory_cards(soup)


def extract_brands_from_page(page, *, logger=None) -> List[str]:
    """Extract brand names from a directory page using BeautifulSoup parsing."""

    logger = logger or logging.getLogger(__name__)
    logger.info("Extracting brands from directory snapshot...")

    cards = get_directory_cards(page, logger=logger)
    filtered = filter_cards(cards)
    unique_names = sorted({card["name"] for card in filtered if card["name"]})
    logger.info("Extracted %s unique brands", len(unique_names))
    return unique_names


def scroll_directory_until_complete(
    page,
    container_selector,
    *,
    logger=None,
    max_empty_scrolls: int = 4,
    max_total_scrolls: Optional[int] = None,
    wait_between_scrolls_ms: int = 300,
    pb_payload_threshold: int = 200,
    idle_scroll_threshold: int = 3,
    on_iteration=None,
    pb_collector: Optional[PbDirectoryCollector] = None,
    collect_pb_bodies: bool = True,
) -> ScrollTelemetry:
    """Scroll the directory container until no new cards appear or pb sentinel observed."""

    logger = logger or logging.getLogger(__name__)

    selectors: Sequence[str]
    if isinstance(container_selector, (list, tuple)):
        selectors = list(container_selector)
    else:
        selectors = [container_selector]

    container = None
    container_selector_str = None

    for selector in selectors:
        try:
            locator = page.locator(selector)
            wait_for = getattr(locator, "wait_for", None)
            if callable(wait_for):
                try:
                    wait_for(state="visible", timeout=3000)
                except PlaywrightTimeoutError:
                    continue
            locator.evaluate("el => el.scrollHeight")
            container = locator
            container_selector_str = selector
            break
        except PlaywrightTimeoutError:
            continue
        except Exception:
            continue

    if container is None:
        raise RuntimeError("Directory container not found with provided selectors")

    logger.debug("Scrolling directory container using selector: %s", container_selector_str)

    try:
        container.click(timeout=1000)
    except Exception:
        try:
            container.evaluate("el => el.focus && el.focus()")
        except Exception:
            pass

    pb_triggered = False
    responses = 0
    def _on_response(response):
        nonlocal pb_triggered, responses
        responses += 1
        url = getattr(response, "url", "")
        status = getattr(response, "status", None)
        if "pb=" in url and status in (204, 200):
            sentinel_hit = status == 204
            try:
                header_value = response.header_value("Content-Length")
                if header_value is not None:
                    try:
                        content_length = int(header_value)
                    except ValueError:
                        content_length = None
                else:
                    content_length = None
            except AttributeError:
                content_length = None

            if not sentinel_hit and content_length is not None and content_length < pb_payload_threshold:
                sentinel_hit = True

            # Don't override sentinel detection if pb_collector stored data
            stored_before = pb_collector.total_stored if pb_collector is not None else 0
            if pb_collector is not None:
                try:
                    pb_collector.on_response(response)
                except Exception as exc:
                    logger.debug("pb collector failed: %s", exc)
                else:
                    if pb_collector.total_stored > stored_before:
                        sentinel_hit = False

            if sentinel_hit:
                pb_triggered = True

    page.on("response", _on_response)

    telemetry = ScrollTelemetry(0, 0, False, 0)

    try:
        last_child_count = container.evaluate("el => el.children.length")
        last_scroll_height = container.evaluate("el => el.scrollHeight")
        empty_scrolls = 0
        idle_scrolls = 0
        total_scrolls = 0

        while True:
            if max_total_scrolls is not None and total_scrolls >= max_total_scrolls:
                break

            try:
                page.mouse.wheel(0, 800)
            except Exception:
                container.evaluate(
                    "el => el.scrollTo({ top: el.scrollTop + el.clientHeight, behavior: 'instant' })"
                )

            total_scrolls += 1
            page.wait_for_timeout(wait_between_scrolls_ms)

            current_child_count = container.evaluate("el => el.children.length")
            current_height = container.evaluate("el => el.scrollHeight")

            if current_child_count <= last_child_count:
                empty_scrolls += 1
            else:
                empty_scrolls = 0
                last_child_count = current_child_count

            if current_height <= last_scroll_height:
                idle_scrolls += 1
            else:
                idle_scrolls = 0
                last_scroll_height = current_height

            if callable(on_iteration):
                try:
                    on_iteration()
                except Exception as iteration_exc:
                    logger.debug("Iteration callback raised %s", iteration_exc)

            logger.debug(
                "Scroll iteration %s: child_count=%s height=%s empty=%s idle=%s",
                total_scrolls,
                current_child_count,
                current_height,
                empty_scrolls,
                idle_scrolls,
            )

            sentinel_ready = pb_triggered
            stagnated = empty_scrolls >= max_empty_scrolls or idle_scrolls >= idle_scroll_threshold

            # Stop when sentinel detected OR when clearly at end (stagnated)
            if sentinel_ready or stagnated:
                break

        telemetry.scrolls_performed = total_scrolls
        telemetry.final_card_count = last_child_count
        telemetry.pb_sentinel_triggered = pb_triggered
        telemetry.responses_observed = responses
        telemetry.cards_collected = (
            pb_collector.total_stored if pb_collector is not None else None
        )
        return telemetry
    finally:
        if callable(on_iteration):
            try:
                on_iteration()
            except Exception as final_exc:
                logger.debug("Final iteration callback raised %s", final_exc)

        off = getattr(page, "off", None)
        if callable(off):
            off("response", _on_response)
        else:
            remove_listener = getattr(page, "remove_listener", None)
            if callable(remove_listener):
                remove_listener("response", _on_response)


def parse_directory_cards(soup: BeautifulSoup) -> List[Dict[str, Optional[str]]]:
    """Parse directory cards extracting name, href, category, and floor data."""

    cards: List[Dict[str, Optional[str]]] = []
    seen = set()

    for selector in CARD_SELECTOR_PRIORITIES:
        for node in soup.select(selector):
            link = node.find("a", href=True)
            if link:
                name = (link.get_text(strip=True) or None)
                href = link.get("href")
                name_source = "link"
            else:
                heading = node.select_one(".qBF1Pd, .fontHeadlineSmall")
                name = (heading.get_text(strip=True) if heading else None)
                href = None
                name_source = "heading"

            if not name:
                continue

            key = (name, href, name_source)
            if key in seen:
                continue

            category_node = node.find(class_="category") or node.select_one(".ZkP5Je")
            floor_node = node.find(class_="floor") or node.select_one(".wzOB1")

            cards.append(
                {
                    "name": name,
                    "href": href,
                    "category": category_node.get_text(strip=True) if category_node else None,
                    "floor": floor_node.get_text(strip=True) if floor_node else None,
                }
            )
            seen.add(key)

    return cards


def filter_cards(cards: List[Dict[str, Optional[str]]]) -> List[Dict[str, Optional[str]]]:
    filtered = []
    for card in cards:
        name = (card.get("name") or "").strip()
        if not name:
            continue
        if name.lower() in CTA_EXCLUSION_NAMES:
            continue
        filtered.append(card)
    return filtered


class GoogleMapsBrandScraper:
    """
    Scrapes brand/store information from Google Maps business listings.

    This class focuses specifically on extracting brand names from directory listings
    in shopping centers and malls. It handles the "View all" button clicking and
    brand extraction logic.
    """

    def __init__(self, headless: bool = False, timeout: int = 30000, use_proxies: bool = False, proxy_manager: Optional[ProxyManager] = None):
        """
        Initialize the brand scraper.

        Args:
            headless: Whether to run browser in headless mode
            timeout: Default timeout for element operations in milliseconds
        """
        self.headless = headless
        self.timeout = timeout
        self.logger = logging.getLogger(__name__)
        self.use_proxies = use_proxies
        self.proxy_manager = proxy_manager
        self._debug_event_counter = 0
        debug_env = os.getenv("GMAPS_DEBUG_SNAPSHOTS") or os.getenv("GMAPS_DEBUG_DUMPS")
        self.debug_snapshots_enabled = self._parse_debug_flag(debug_env)

        if not hasattr(self, "_debug_dump"):
            self._debug_dump = lambda *args, **kwargs: None

        # Configure logging
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
            self.logger.setLevel(logging.INFO)

    def scrape_brands(self, url: str) -> List[str]:
        """
        Scrape all brands from a Google Maps business listing URL.

        Args:
            url: Google Maps URL (supports both goo.gl short links and direct maps URLs)

        Returns:
            List of brand/store names found at the location
        """
        self.logger.info(f"Starting brand scrape for URL: {url}")

        # Use session manager for authenticated browsing
        session_kwargs = {
            "headless": self.headless,
            "proxy_manager": (self.proxy_manager if self.use_proxies else None),
            "max_auth_attempts": (1 if self.use_proxies else 3),
        }

        if not self.headless:
            session_kwargs.update(
                {
                    "record_har": True,
                    "har_output_dir": "debug/har",
                }
            )

        session_manager = GoogleMapsSessionManager(**session_kwargs)

        page = None
        nav_handler = None

        try:
            self._debug_event_counter = 0
            # Get authenticated page
            page = session_manager.get_authenticated_page(target_url=url)

            def _on_navigation(frame):
                if frame is None or frame.page is None:
                    return
                if frame.page != page:
                    return
                if frame != page.main_frame:
                    return
                self.logger.info(f"[NAVIGATION] Frame navigated to: {frame.url}")
                self._debug_dump(page, label=f"navigation-{frame.url}")

            nav_handler = _on_navigation
            try:
                page.on("framenavigated", nav_handler)
            except Exception as exc:
                self.logger.debug("Failed to register navigation debug handler: %s", exc)

            self._debug_dump(page, label="post-auth")

            # Avoid redundant navigation when proxy flow already loads target page
            current_url = getattr(page, "url", "") or ""
            should_navigate = self._should_navigate(current_url, url)

            if should_navigate:
                page.goto(url, wait_until="domcontentloaded")
                self._debug_dump(page, label="post-goto")

            # Handle scenarios where navigation sends us back to consent page
            if "consent.google.com" in page.url:
                self.logger.info("Redirected to consent page after navigation; re-running consent handler")
                session_manager._handle_consent_flow(page)
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=5000)
                except PlaywrightTimeoutError:
                    self.logger.debug("Consent retry did not reach DOM loaded within 5s; proceeding")
                page.wait_for_timeout(1500)
                if "consent.google.com" in page.url:
                    raise RuntimeError("Unable to pass consent page after retry")
            else:
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=min(self.timeout, 8000))
                except PlaywrightTimeoutError:
                    self.logger.debug("DOM content load wait timed out; continuing with visible content")
                finally:
                    try:
                        page.wait_for_load_state("load", timeout=5000)
                    except PlaywrightTimeoutError:
                        self.logger.debug("Page load wait timed out; proceeding regardless")
                page.wait_for_timeout(1200)

            # NOW add directory parameters after consent is handled
            self._ensure_directory_view(page)

            # Extract brands from the directory
            brands = self._extract_brands_from_directory(page)

            self.logger.info("Successfully scraped %d brands", len(brands))
            return brands

        except Exception as e:
            self.logger.error(f"Error during scraping: {e}")
            return []

        finally:
            session_manager.cleanup()
            if page is not None and nav_handler is not None:
                try:
                    page.off("framenavigated", nav_handler)
                except Exception:
                    pass

    def _add_directory_parameters(self, url: str) -> str:
        """Add directory view parameters to URL before navigation."""
        # For short Google Maps URLs, we can't predict the final URL
        # So we'll add parameters after the first navigation/redirect
        # For now, return the original URL - parameters will be added after redirect
        return url

    def _ensure_directory_view(self, page):
        """Experiment with different URL parameter combinations for directory view."""
        try:
            current_url = getattr(page, "url", "")
        except Exception:
            current_url = ""

        # Define parameter combinations to test (in order of preference)
        param_combinations = [
            "!10e3!16s",  # Current working combination
            "!16s!10e3",  # Reversed order
            "!10e3",      # Just the first parameter
            "!16s",       # Just the second parameter
            "!10e3!16e",  # Variations
            "!10e3!16i",
            "!16s!10e3!16e",
        ]

        # Check if already has any of our target parameters
        has_directory_params = any(param in current_url for param in ["!10e3", "!16s"])
        if has_directory_params:
            self.logger.debug(f"Already has directory parameters in URL: {current_url}")
            return False

        # Try each parameter combination
        for params in param_combinations:
            try:
                # Construct new URL
                if "?" in current_url:
                    base_url, query = current_url.split("?", 1)
                    new_url = f"{base_url}{params}?{query}"
                else:
                    new_url = f"{current_url}{params}"

                if new_url == current_url:
                    continue

                self.logger.info(f"[EXPERIMENT] Testing directory params '{params}': {new_url}")

                # Navigate with the new parameters
                page.goto(new_url, wait_until="domcontentloaded", timeout=10000)

                # Check if navigation succeeded and directory content is present
                final_url = page.url
                content = page.content().lower()

                # Check for directory indicators
                has_directory = (
                    "directory" in content or
                    "aria-label" in content and "directory" in content or
                    any(selector in content for selector in ["directory", "k7jAl", "miFGmb"])
                )

                if has_directory:
                    self.logger.info(f"[EXPERIMENT] SUCCESS with '{params}' - directory content detected")
                    self.logger.info(f"[EXPERIMENT] Final URL: {final_url}")

                    # Wait for content to stabilize
                    page.wait_for_timeout(3000)
                    self._debug_dump(page, label=f"state-directory-{params.replace('!', '')}")
                    return True
                else:
                    self.logger.info(f"[EXPERIMENT] FAILED with '{params}' - no directory content detected")
                    # Continue to next parameter combination
                    continue

            except Exception as exc:
                self.logger.warning(f"[EXPERIMENT] Failed with '{params}': {exc}")
                continue

        # If no combination worked, fall back to original URL
        self.logger.warning("[EXPERIMENT] No parameter combination successfully activated directory view")
        return False

    def _extract_brands_from_directory(self, page) -> List[str]:
        """Extract brand names from the Google Maps directory."""

        # Debug: Check current URL and page state
        current_url = page.url
        self.logger.info(f"[EXTRACTION] Starting extraction on URL: {current_url}")

        # Pure URL manipulation approach - directory should already be expanded via URL parameters
        # No UI interaction (clicking "View all") in this experimental branch
        self.logger.info("[EXTRACTION] Pure URL manipulation approach - no UI interaction for directory expansion")
        self._debug_dump(page, label="state-directory-ready")

        # Brief wait for any dynamic content to settle after URL-based directory activation
        page.wait_for_timeout(1000)

        collected_cards: Dict[Tuple[str, Optional[str], Optional[str]], Dict[str, Optional[str]]] = {}

        def _capture_cards():
            cards = filter_cards(get_directory_cards(page, logger=self.logger))
            if self.logger.isEnabledFor(logging.DEBUG):
                self.logger.debug("Captured %s cards from DOM snapshot", len(cards))
            for card in cards:
                name = card.get("name")
                if not name:
                    continue
                key = (name, card.get("category"), card.get("floor"))
                collected_cards.setdefault(key, card)

        _capture_cards()

        # Extract brands from the expanded directory
        pb_collector = PbDirectoryCollector(logger=self.logger, min_payload_bytes=200)

        telemetry = scroll_directory_until_complete(
            page,
            DIRECTORY_CONTAINER_SELECTORS,
            logger=self.logger,
            max_empty_scrolls=4,
            wait_between_scrolls_ms=400,
            on_iteration=_capture_cards,
            pb_collector=pb_collector,
            collect_pb_bodies=True,
        )
        self.logger.info(
            "Scroll telemetry - scrolls: %s, responses: %s, pb_sentinel: %s",
            telemetry.scrolls_performed,
            telemetry.responses_observed,
            telemetry.pb_sentinel_triggered,
        )
        self._debug_dump(page, label="state-scroll-complete")

        brands = sorted({card[0] for card in collected_cards.keys() if card[0] and card[0].lower() not in CTA_EXCLUSION_NAMES})
        pb_cards = filter_cards(pb_collector.extract_cards())
        if self.logger.isEnabledFor(logging.DEBUG):
            self.logger.debug(
                "PB collector returned %s cards (seen=%s stored=%s)",
                len(pb_cards),
                pb_collector.total_seen,
                pb_collector.total_stored,
            )
        for card in pb_cards:
            name = card.get("name")
            if not name:
                continue
            key = (name, card.get("category"), card.get("floor"))
            collected_cards.setdefault(key, card)

        brands = sorted({key[0] for key in collected_cards.keys() if key[0] and key[0].lower() not in CTA_EXCLUSION_NAMES})
        self.logger.info(
            "Aggregated %s unique brands (%s from pb payloads)",
            len(brands),
            len(pb_cards),
        )

        return brands

    def save_results(self, brands: List[str], url: str, filename: Optional[str] = None) -> str:
        """
        Save scraping results to a JSON file.

        Args:
            brands: List of brand names
            url: Original URL that was scraped
            filename: Optional filename (default: auto-generated)

        Returns:
            Path to the saved file
        """

        if not filename:
            # Generate filename from URL or use default
            filename = 'google_maps_brands.json'

        result = {
            'url': url,
            'scraped_at': time.strftime('%Y-%m-%d %H:%M:%S'),
            'total_brands': len(brands),
            'brands': brands,
            'method': 'GoogleMapsBrandScraper v1.0 - Playwright-based extraction',
            'notes': 'Scraped using consent handler and directory expansion'
        }

        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=2, ensure_ascii=False)

        self.logger.info(f"Results saved to {filename}")
        return filename

    def _debug_dump(self, page, label: str):
        if not self.debug_snapshots_enabled:
            return

        safe_label = self._sanitize_label(label)
        self._debug_event_counter += 1
        prefix = f"{self._debug_event_counter:03d}_{safe_label}"

        try:
            current_url = page.url
        except Exception:
            current_url = "<unknown>"

        self.logger.info("[%s] current URL: %s", prefix, current_url)

        try:
            screenshot_path = f"debug_{prefix}.png"
            page.screenshot(path=screenshot_path, full_page=True)
            self.logger.info("[%s] screenshot saved to %s", prefix, screenshot_path)
        except Exception as exc:
            self.logger.debug("[%s] screenshot failed: %s", prefix, exc)

        try:
            html_path = f"debug_{prefix}.html"
            html = page.content()
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(html)
            self.logger.info("[%s] HTML dump saved to %s", prefix, html_path)
        except Exception as exc:
            self.logger.debug("[%s] HTML dump failed: %s", prefix, exc)

    @staticmethod
    def _sanitize_label(label: str) -> str:
        safe = re.sub(r"[^A-Za-z0-9_-]+", "-", label or "")
        safe = safe.strip("-_")
        if len(safe) > 80:
            safe = safe[:80]
        return safe or "event"

    @staticmethod
    def _parse_debug_flag(value: Optional[str]) -> bool:
        if value is None:
            return False
        normalized = value.strip().lower()
        if not normalized:
            return False
        return normalized in {"1", "true", "yes", "on", "debug"}

    @staticmethod
    def _urls_equivalent(current: str, target: str) -> bool:
        if not current or not target:
            return False

        def _normalize(url: str) -> str:
            base = url.split("#", 1)[0]
            if base.endswith("/"):
                base = base.rstrip("/")
            return base

        return _normalize(current) == _normalize(target)

    def _should_navigate(self, current_url: str, target_url: str) -> bool:
        if not target_url:
            return False
        if not current_url or current_url in {"about:blank", ""}:
            return True
        if "consent.google.com" in current_url:
            return True
        return not self._urls_equivalent(current_url, target_url)


def main():
    """Command-line interface for the scraper."""

    import argparse

    parser = argparse.ArgumentParser(description='Scrape brands from Google Maps business listings')
    parser.add_argument('url', help='Google Maps URL to scrape')
    parser.add_argument('--output', '-o', help='Output JSON file (optional)')
    parser.add_argument('--verbose', '-v', action='store_true', help='Enable verbose logging')
    parser.add_argument('--headed', action='store_true', help='Run browser in headed mode (visible)')
    parser.add_argument('--use-proxies', action='store_true', help='Enable proxy rotation via ProxyManager')

    args = parser.parse_args()

    # Configure logging
    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    # Create scraper
    proxy_mgr = create_default_proxy_manager() if args.use_proxies else None
    scraper = GoogleMapsBrandScraper(headless=not args.headed, use_proxies=args.use_proxies, proxy_manager=proxy_mgr)

    # Scrape brands
    brands = scraper.scrape_brands(args.url)

    # Save results
    filename = scraper.save_results(brands, args.url, args.output)

    # Print summary
    print(f"\nScraping completed!")
    print(f"Found {len(brands)} brands at {args.url}")
    print(f"Results saved to: {filename}")

    if brands:
        print("\nBrands found:")
        for brand in brands:
            print(f"  - {brand}")


if __name__ == '__main__':
    main()
