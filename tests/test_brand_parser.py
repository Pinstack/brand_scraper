"""Unit tests for brand extraction parsing logic."""

from bs4 import BeautifulSoup
import pytest

from google_maps_brand_scraper import parse_directory_cards


@pytest.fixture
def directory_soup(load_fixture):
    html = load_fixture("mall_directory.html")
    return BeautifulSoup(html, "html.parser")


def test_parse_directory_cards(directory_soup):
    cards = parse_directory_cards(directory_soup)

    assert cards == [
        {
            "name": "Brand A",
            "href": "/maps/place/Brand+A",
            "category": "Clothing store",
            "floor": None,
        },
        {
            "name": "Brand B",
            "href": "/maps/place/Brand+B",
            "category": "Restaurant",
            "floor": None,
        },
        {
            "name": "Store With Floor",
            "href": "/maps/place/Store+With+Floor",
            "category": None,
            "floor": "Level 2",
        },
    ]


def test_parse_directory_cards_no_duplicates(directory_soup):
    cards = parse_directory_cards(directory_soup)

    names = [card["name"] for card in cards]
    assert len(names) == len(set(names))


@pytest.fixture
def modern_directory(load_fixture):
    html = load_fixture("mall_directory_modern.html")
    return BeautifulSoup(html, "html.parser")


def test_parse_directory_modern_cards(modern_directory):
    cards = parse_directory_cards(modern_directory)

    assert cards == [
        {
            "name": "Maki & Ramen",
            "href": None,
            "category": "Japanese restaurant",
            "floor": "Level 4",
        },
        {
            "name": "Black Sheep Coffee",
            "href": None,
            "category": "Coffee shop",
            "floor": "Ground floor",
        },
    ]

