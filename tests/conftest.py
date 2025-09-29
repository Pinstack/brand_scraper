"""Test configuration and shared fixtures."""

from pathlib import Path
import sys
import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def load_fixture():
    """Load file contents from the tests/fixtures directory."""

    fixtures_dir = Path(__file__).parent / "fixtures"

    def _loader(name: str) -> str:
        path = fixtures_dir / name
        return path.read_text(encoding="utf-8")

    return _loader


def pytest_addoption(parser):
    parser.addoption(
        "--run-live",
        action="store_true",
        default=False,
        help="Run live Google Maps extraction test",
    )
    parser.addoption(
        "--live-url",
        action="store",
        default=None,
        help="Target Google Maps URL for live extraction",
    )
    parser.addoption(
        "--live-use-proxies",
        action="store_true",
        default=False,
        help="Enable proxy usage during live test",
    )


def pytest_configure(config):
    config.addinivalue_line("markers", "live: marks tests that hit live Google Maps (deselect with --run-live)")

