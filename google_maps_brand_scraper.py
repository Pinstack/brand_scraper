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
import re
import time
import json
import base64
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple
from urllib.parse import parse_qsl, urlsplit

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

    for attempt in range(1, max_attempts + 1):
        for selector in selectors:
            try:
                locator = page.locator(selector)
                candidate = getattr(locator, "first", locator)
                if callable(candidate):
                    candidate = candidate()

                if hasattr(candidate, "is_enabled"):
                    try:
                        if not candidate.is_enabled():
                            continue
                    except Exception:
                        continue

                scroll_heading = getattr(candidate, "scroll_into_view_if_needed", None)
                if callable(scroll_heading):
                    try:
                        scroll_heading(timeout=wait_between_attempts_ms)
                    except Exception:
                        pass

                if not hasattr(candidate, "is_visible"):
                    continue

                try:
                    if not candidate.is_visible(timeout=wait_between_attempts_ms):
                        continue
                except PlaywrightTimeoutError:
                    continue

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


def _load_pb_payloads_from_har(
    context,
    *,
    har_path: Optional[Path] = None,
    logger=None,
) -> List[Dict[str, Optional[str]]]:
    logger = logger or logging.getLogger(__name__)
    payloads: List[str] = []

    if har_path is None:
        har_path = getattr(context, "_recording_har_path", None)
        if har_path is None:
            har_path = getattr(context, "record_har_path", None)

    har_file: Optional[Path]
    if isinstance(har_path, Path):
        har_file = har_path
    elif isinstance(har_path, str):
        har_file = Path(har_path)
    else:
        har_file = None

    if har_file and har_file.exists():
        try:
            with har_file.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except Exception as exc:
            logger.debug("Failed to load HAR file %s: %s", har_file, exc)
        else:
            for record in data.get("log", {}).get("entries", []):
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

    if not payloads:
        logger.debug("No pb payloads found in HAR file")
        return []

    collector = PbDirectoryCollector(logger=logger)
    for payload in payloads:
        collector._payloads.append(payload)
    return collector.extract_cards()


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
        try:
            candidate = page.locator(selector)
            wait_for = getattr(candidate, "wait_for", None)
            if callable(wait_for):
                try:
                    wait_for(state="attached", timeout=1500)
                except PlaywrightTimeoutError:
                    continue
            candidate.evaluate("el => el")
            container_handle = candidate
            logger.debug("Using directory container selector: %s", selector)
            break
        except PlaywrightTimeoutError:
            continue
        except Exception:
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
    pb_sentinel_required: int = 3,
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

    if not collect_pb_bodies:
        pb_collector = None

    pb_sentinel_count = 0

    def _on_response(response):
        nonlocal pb_triggered, responses, pb_sentinel_count
        responses += 1
        url = getattr(response, "url", "")
        status = getattr(response, "status", None)
        if "pb=" in url and status in (204, 200):
            content_length = None
            try:
                header_value = response.header_value("Content-Length")
            except AttributeError:
                header_value = None
            if header_value is not None:
                try:
                    content_length = int(header_value)
                except ValueError:
                    content_length = None
            if status == 204 or (content_length is not None and content_length < pb_payload_threshold):
                pb_sentinel_count += 1
            else:
                pb_sentinel_count = 0
            pb_triggered = pb_sentinel_count >= max(1, pb_sentinel_required)
        if pb_collector is not None:
            try:
                pb_collector.on_response(response)
            except Exception as exc:
                logger.debug("pb collector failed: %s", exc)

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

            if pb_triggered and idle_scrolls >= idle_scroll_threshold:
                break

            if max_empty_scrolls and empty_scrolls >= max_empty_scrolls and idle_scrolls >= idle_scroll_threshold:
                break

        telemetry.scrolls_performed = total_scrolls
        telemetry.final_card_count = last_child_count
        telemetry.pb_sentinel_triggered = pb_triggered
        telemetry.responses_observed = responses
        telemetry.cards_collected = None
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

    def __init__(
        self,
        headless: bool = False,
        timeout: int = 30000,
        use_proxies: bool = False,
        proxy_manager: Optional[ProxyManager] = None,
        enable_debug_dumps: bool = False,
    ):
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
        self.enable_debug_dumps = enable_debug_dumps

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
            active_har_path = session_manager.active_har_path

            def _on_navigation(frame):
                if frame is None or frame.page is None:
                    return
                if frame.page != page:
                    return
                if frame != page.main_frame:
                    return
                self._debug_dump(page, label=f"navigation-{frame.url}")

            nav_handler = _on_navigation
            try:
                page.on("framenavigated", nav_handler)
            except Exception as exc:
                self.logger.debug("Failed to register navigation debug handler: %s", exc)

            self._debug_dump(page, label="post-auth")

            # Avoid redundant navigation when proxy flow already loads target page
            current_url = getattr(page, "url", "") or ""
            should_navigate = not self._urls_equivalent(current_url, url)
            if "consent.google.com" in current_url:
                should_navigate = True

            if should_navigate:
                page.goto(url, wait_until="domcontentloaded")
                self._debug_dump(page, label="post-goto")
            else:
                self.logger.info("Reusing existing page already at target location")

            self._ensure_directory_view(page)

            # Handle scenarios where navigation sends us back to consent page
            if "consent.google.com" in page.url:
                self.logger.info("Redirected to consent page after navigation; re-running consent handler")
                session_manager._handle_consent_flow(page)
                page.wait_for_load_state("networkidle")
                page.wait_for_timeout(2000)
                if "consent.google.com" in page.url:
                    raise RuntimeError("Unable to pass consent page after retry")
            else:
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=min(self.timeout, 10000))
                except PlaywrightTimeoutError:
                    self.logger.debug("DOM content load wait timed out; continuing")
                page.wait_for_timeout(750)

            # Extract brands from the directory
            brands = self._extract_brands_from_directory(page, har_path=active_har_path)

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

    def _ensure_directory_view(self, page):
        try:
            current_url = getattr(page, "url", "")
        except Exception:
            current_url = ""

        if "!10e3" in current_url:
            return

        if "!10e" in current_url:
            new_url = current_url.split("!10e", 1)[0] + "!10e3"
        else:
            new_url = f"{current_url}!10e3" if current_url else current_url

        if not new_url:
            return

        self.logger.info("Switching to directory view via !10e3 URL variant")
        try:
            page.goto(new_url, wait_until="domcontentloaded")
            self._debug_dump(page, label="state-directory-view-navigate")
        except Exception as exc:
            self.logger.warning("Failed to navigate to directory view URL: %s", exc)

    def _extract_brands_from_directory(self, page, *, har_path: Optional[Path] = None) -> List[str]:
        """Extract brand names from the Google Maps directory."""

        # Ensure the directory tab is active before attempting to expand
        if not activate_directory_tab(page, logger=self.logger):
            self.logger.warning("Directory tab not found; falling back to default content")
        else:
            self._debug_dump(page, label="state-directory-tab-active")

        # Click "View all" to expand the directory when available
        view_all_clicked = _click_view_all_button(page, logger=self.logger)
        if not view_all_clicked:
            self.logger.info("View all button not available; proceeding with current directory content")
            self._debug_dump(page, label="state-view-all-missing")
        else:
            self._debug_dump(page, label="state-view-all-clicked")

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
        if not pb_cards:
            try:
                har_cards = _load_pb_payloads_from_har(page.context, har_path=har_path, logger=self.logger)
                pb_cards = filter_cards(har_cards)
            except Exception as exc:
                self.logger.debug("HAR parsing failed: %s", exc)
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
        if not self.enable_debug_dumps:
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
    def _urls_equivalent(left: str, right: str) -> bool:
        if not left or not right:
            return False

        try:
            left_parts = urlsplit(left)
            right_parts = urlsplit(right)
        except Exception:
            return left == right

        if (left_parts.scheme, left_parts.netloc, left_parts.path) != (
            right_parts.scheme,
            right_parts.netloc,
            right_parts.path,
        ):
            return False

        excluded = {"authuser", "hl", "entry", "g_st", "g_ep"}

        def _normalise_query(parts):
            params = [
                (key, value)
                for key, value in parse_qsl(parts.query, keep_blank_values=True)
                if key not in excluded
            ]
            return tuple(sorted(params))

        return _normalise_query(left_parts) == _normalise_query(right_parts)


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
