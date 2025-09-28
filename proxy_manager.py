#!/usr/bin/env python3
"""Proxy Manager Module for IP Rotation and Anti-Detection.

Manages residential proxy rotation for enhanced anti-detection capabilities.
Integrates with HAR analysis for intelligent IP rotation strategies.
Includes automatic integration with the Webshare proxy API when credentials
are available via environment variables.
"""

import hashlib
import json
import logging
import os
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from dotenv import load_dotenv

try:
    import requests
except ImportError:  # pragma: no cover - optional dependency
    requests = None


_MODULE_DIR = Path(__file__).resolve().parent
_PARENT_DIR = _MODULE_DIR.parent


def _load_env():
    """Load environment variables from common .env locations."""
    for candidate in (
        _MODULE_DIR / ".env",
        _PARENT_DIR / ".env",
        Path.cwd() / ".env",
    ):
        if candidate.exists():
            load_dotenv(candidate, override=False)


_load_env()

from dotenv import load_dotenv

load_dotenv()


@dataclass
class ProxyStats:
    uses: int = 0
    success: int = 0
    failures: int = 0
    blocks: int = 0
    last_use: float = 0.0
    last_success: float = 0.0
    last_failure: float = 0.0
    last_block: float = 0.0
    last_health_check: float = 0.0
    healthy: bool = True


class ProxyManager:
    """Manages residential proxy rotation for anti-detection."""

    def __init__(
        self,
        proxy_list: Optional[List[str]] = None,
        *,
        cooldown_period: int = 30,
        health_check_url: Optional[str] = None,
        storage_dir: Optional[str] = None,
        recheck_interval: Optional[int] = None,
    ):
        """Initialize proxy manager.

        Args:
            proxy_list: List of proxy strings in format 'IP:PORT:USERNAME:PASSWORD'
        """
        self.proxies: List[Dict[str, any]] = []
        self.current_index = 0
        self.last_rotation = time.time()
        self.cooldown_period = cooldown_period  # seconds between rotations
        self.health_check_url = health_check_url or os.getenv(
            "WEBSHARE_HEALTH_CHECK_URL",
            "https://ipv4.webshare.io/",
        )
        self.recheck_interval = recheck_interval or int(os.getenv("WEBSHARE_RECHECK_INTERVAL", "300"))
        self.storage_dir = Path(storage_dir or ".proxy_state")
        self.storage_dir.mkdir(parents=True, exist_ok=True)

        if proxy_list:
            self.load_proxies(proxy_list)

    def load_proxies(self, proxy_list: List[str]) -> None:
        """Load proxies from list of proxy strings."""
        self.proxies = []
        for proxy_str in proxy_list:
            if proxy_str.strip():
                proxy_info = self._parse_proxy_string(proxy_str.strip())
                if proxy_info:
                    proxy_info.setdefault("stats", ProxyStats())
                    proxy_info.setdefault("slug", self._slug_for_proxy(proxy_info))
                    proxy_info["storage_path"] = self.storage_dir / proxy_info["slug"]
                    proxy_info["storage_path"].mkdir(parents=True, exist_ok=True)
                    self._load_proxy_state(proxy_info)
                    self.proxies.append(proxy_info)

        # Shuffle for random initial order
        random.shuffle(self.proxies)
        self.last_rotation = time.time()

    def _parse_proxy_string(self, proxy_str: str) -> Optional[Dict[str, str]]:
        """Parse proxy string into components.

        Format: IP:PORT:USERNAME:PASSWORD
        """
        try:
            parts = proxy_str.split(":")
            if len(parts) != 4:
                return None

            ip, port, username, password = parts

            # Validate IP format
            try:
                urlparse(f"http://{ip}")
            except Exception:
                return None

            return {
                "ip": ip,
                "port": port,
                "username": username,
                "password": password,
                "proxy_url": f"http://{username}:{password}@{ip}:{port}",
                "https_url": f"http://{username}:{password}@{ip}:{port}",
            }
        except Exception:
            return None

    def _slug_for_proxy(self, proxy_info: Dict[str, str]) -> str:
        base = f"{proxy_info['ip']}:{proxy_info['port']}:{proxy_info['username']}"
        digest = hashlib.sha1(base.encode("utf-8")).hexdigest()[:12]
        safe_ip = proxy_info["ip"].replace(".", "-").replace(":", "-")
        return f"{safe_ip[:20]}-{digest}"

    def _load_proxy_state(self, proxy_info: Dict[str, any]) -> None:
        stats_path = proxy_info["storage_path"] / "stats.json"
        if stats_path.exists():
            try:
                data = json.loads(stats_path.read_text())
                proxy_info["stats"] = ProxyStats(**data)
            except Exception:
                pass

    def _save_proxy_state(self, proxy_info: Dict[str, any]) -> None:
        stats_path = proxy_info["storage_path"] / "stats.json"
        try:
            stats_path.write_text(json.dumps(proxy_info["stats"].__dict__, indent=2))
        except Exception:
            pass

    def get_current_proxy(self) -> Optional[Dict[str, str]]:
        """Get current proxy configuration."""
        if not self.proxies:
            return None
        proxy = self.proxies[self.current_index]
        stats: ProxyStats = proxy["stats"]
        stats.last_use = time.time()
        stats.uses += 1
        self._save_proxy_state(proxy)
        return proxy

    def get_next_proxy(self) -> Optional[Dict[str, str]]:
        """Get next proxy in rotation."""
        if not self.proxies:
            return None

        self.current_index = (self.current_index + 1) % len(self.proxies)
        self.last_rotation = time.time()
        next_proxy = self.proxies[self.current_index]
        stats: ProxyStats = next_proxy["stats"]
        stats.last_use = self.last_rotation
        self._save_proxy_state(next_proxy)
        return next_proxy

    def rotate_on_rate_limit(
        self, backoff_seconds: int = 10
    ) -> Optional[Dict[str, str]]:
        """Rotate to new proxy when rate limited, with backoff.

        Args:
            backoff_seconds: Base backoff time before rotation

        Returns:
            New proxy configuration
        """
        # Add cooldown to prevent rapid rotations
        elapsed = time.time() - self.last_rotation
        if elapsed < self.cooldown_period:
            sleep_time = self.cooldown_period - elapsed
            time.sleep(sleep_time)

        # Add random backoff
        backoff = backoff_seconds + random.uniform(0, 5)
        time.sleep(backoff)

        return self.get_next_proxy()

    def get_proxy_for_requests(self) -> Optional[Dict[str, str]]:
        """Get proxy configuration formatted for requests library."""
        proxy = self.get_current_proxy()
        if not proxy:
            return None

        return {"http": proxy["proxy_url"], "https": proxy["https_url"]}

    def record_success(self, proxy: Dict[str, any]) -> None:
        stats: ProxyStats = proxy["stats"]
        stats.success += 1
        stats.last_success = time.time()
        stats.healthy = True
        self._save_proxy_state(proxy)

    def record_failure(self, proxy: Dict[str, any], *, block: bool = False) -> None:
        stats: ProxyStats = proxy["stats"]
        stats.failures += 1
        stats.last_failure = time.time()
        if block:
            stats.blocks += 1
            stats.last_block = stats.last_failure
        stats.healthy = False
        self._save_proxy_state(proxy)

    def mark_rate_limit(self, backoff_seconds: int = 10) -> Optional[Dict[str, str]]:
        proxy = self.get_current_proxy()
        if proxy:
            self.record_failure(proxy, block=True)
        return self.rotate_on_rate_limit(backoff_seconds)

    def test_proxy(self, proxy_info: Dict[str, str], timeout: int = 10) -> bool:
        """Test if a proxy is working.

        Args:
            proxy_info: Proxy configuration
            timeout: Request timeout in seconds

        Returns:
            True if proxy works, False otherwise
        """
        if not requests:
            proxy_info["stats"].last_health_check = time.time()
            proxy_info["stats"].healthy = True
            self._save_proxy_state(proxy_info)
            return True  # Can't test without requests

        try:
            proxies = {
                "http": proxy_info["proxy_url"],
                "https": proxy_info["https_url"],
            }

            response = requests.get(
                self.health_check_url,
                proxies=proxies,
                timeout=timeout,
                headers={"User-Agent": "Mozilla/5.0 (compatible; ProxyTest/1.0)"},
            )

            healthy = response.status_code == 200
            stats: ProxyStats = proxy_info["stats"]
            stats.last_health_check = time.time()
            stats.healthy = healthy
            if healthy:
                stats.last_success = stats.last_health_check
            else:
                stats.failures += 1
                stats.last_failure = stats.last_health_check
            self._save_proxy_state(proxy_info)
            return healthy
        except Exception:
            stats: ProxyStats = proxy_info["stats"]
            stats.failures += 1
            stats.last_failure = time.time()
            stats.healthy = False
            stats.last_health_check = stats.last_failure
            self._save_proxy_state(proxy_info)
            return False

    def get_working_proxy(self, max_attempts: int = 3) -> Optional[Dict[str, str]]:
        """Get a working proxy, testing up to max_attempts proxies.

        Args:
            max_attempts: Maximum number of proxies to test

        Returns:
            Working proxy configuration, or None if none work
        """
        for _ in range(min(max_attempts, len(self.proxies))):
            proxy = self.get_current_proxy()
            if not proxy:
                break

            if (time.time() - proxy["stats"].last_health_check) > self.recheck_interval:
                self.test_proxy(proxy)

            if proxy["stats"].healthy and self.test_proxy(proxy):
                return proxy

            # Try next proxy
            self.get_next_proxy()

        return None

    def get_proxy_stats(self) -> Dict[str, any]:
        """Get statistics about proxy usage."""
        now = time.time()
        proxy_stats = []
        for proxy in self.proxies:
            stats: ProxyStats = proxy["stats"]
            proxy_stats.append({
                "ip": proxy["ip"],
                "port": proxy["port"],
                "uses": stats.uses,
                "success": stats.success,
                "failures": stats.failures,
                "blocks": stats.blocks,
                "healthy": stats.healthy,
                "seconds_since_use": now - stats.last_use if stats.last_use else None,
                "seconds_since_health_check": now - stats.last_health_check if stats.last_health_check else None,
            })

        return {
            "total_proxies": len(self.proxies),
            "current_index": self.current_index,
            "last_rotation": self.last_rotation,
            "time_since_rotation": now - self.last_rotation,
            "proxies": proxy_stats,
        }


# Default proxy list from Webshare credentials (deprecated fallback)
DEFAULT_WEBHARE_PROXIES: List[str] = []


def _fetch_webshare_proxies(api_key: str, *, limit: int = 50) -> List[str]:
    if not requests:
        raise RuntimeError("requests is required to fetch Webshare proxies")

    url = os.getenv(
        "WEBSHARE_PROXY_LIST_ENDPOINT",
        "https://proxy.webshare.io/api/v2/proxy/list/?mode=direct&proxy_protocol=http",
    )

    params: Dict[str, Any] = {
        "limit": os.getenv("WEBSHARE_PROXY_LIMIT", str(limit)),
        "mode": os.getenv("WEBSHARE_PROXY_MODE", "direct"),
        "proxy_protocol": os.getenv("WEBSHARE_PROXY_PROTOCOL", "http"),
    }
    country_codes = os.getenv("WEBSHARE_COUNTRY_CODES")
    if country_codes:
        params["country_codes"] = country_codes

    response = requests.get(
        url,
        headers={"Authorization": f"Token {api_key}"},
        params=params,
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()

    proxies = []
    for item in data.get("results", []):
        proxy_address = item.get("proxy_address")
        port = item.get("port")
        username = item.get("username")
        password = item.get("password")
        if all((proxy_address, port, username, password)):
            proxies.append(f"{proxy_address}:{port}:{username}:{password}")

    return proxies


def create_default_proxy_manager() -> ProxyManager:
    """Create proxy manager populated with Webshare proxies when possible."""
    api_key = os.getenv("WEBSHARE_API_KEY")
    proxies: List[str] = []

    if api_key:
        try:
            proxies = _fetch_webshare_proxies(api_key)
        except Exception as exc:
            logging.warning(f"Failed to fetch Webshare proxies via API: {exc}")

    if not proxies and DEFAULT_WEBHARE_PROXIES:
        proxies = DEFAULT_WEBHARE_PROXIES
        logging.info("Falling back to bundled default Webshare proxies")

    manager = ProxyManager()
    if proxies:
        manager.load_proxies(proxies)
    else:
        logging.warning("ProxyManager created without proxies; supply API key or proxy list")
    return manager


