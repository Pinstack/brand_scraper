# Google Maps Brand Scraper

A modular Python system for scraping brand/store information from Google Maps business listings. This tool is specifically designed to extract all brands and stores from shopping centers, malls, and other business locations on Google Maps.

## Architecture

The system is split into three focused modules:

1. **`google_consent_handler.py`** – Handles Google consent/privacy pages
2. **`google_maps_session_manager.py`** – Manages Playwright sessions, including proxy-aware flows
3. **`google_maps_brand_scraper.py`** – Performs the actual brand scraping and orchestration

### Session Manager Highlights

The session manager supports two execution paths:

- **Proxy mode** – Lightweight flow that launches a fresh browser per proxy attempt and handles consent inline
- **Non-proxy mode** – Legacy persistent-context flow with storage-state reuse

This keeps the project DRY by centralising session logic in a single module.

## Features

- **Modular Design**: Separate consent handling and scraping logic
- **Automated Browser Control**: Uses Playwright for headless browser automation
- **Robust Consent Handling**: Multiple strategies for Google consent/privacy pages
- **Smart Element Detection**: Multiple strategies to find and click "View all" buttons
- **Comprehensive Extraction**: Extracts brand names from various UI elements
- **Intelligent Filtering**: Filters out UI elements, navigation items, and irrelevant text
- **Command Line Interface**: Easy-to-use CLI for quick scraping
- **JSON Output**: Saves results in structured JSON format

## Installation

1. **Clone or download the repository**

2. **Create a virtual environment** (recommended):
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```

3. **Install dependencies** (using uv for speed and reproducibility):
   ```bash
   uv pip install -r requirements.txt
   ```

4. **Install Playwright browsers**:
   ```bash
   uv run playwright install chromium
   ```

## Usage

### As Python Modules

```python
from google_maps_brand_scraper import GoogleMapsBrandScraper

# Create scraper (use_proxies=True enables proxy-integrated session manager)
scraper = GoogleMapsBrandScraper(use_proxies=True)

# Scrape brands from a Google Maps URL
brands = scraper.scrape_brands("https://maps.app.goo.gl/FsGevWWrjvab4tZ9A")

print(f"Found {len(brands)} brands:")
for brand in brands:
    print(f"  - {brand}")

# Save results to JSON file
scraper.save_results(brands, "https://maps.app.goo.gl/FsGevWWrjvab4tZ9A", "st_james_brands.json")
```

### Command Line Interface

```bash
# Basic usage
python google_maps_brand_scraper.py "https://maps.app.goo.gl/FsGevWWrjvab4tZ9A"

# With custom output file
python google_maps_brand_scraper.py "https://maps.app.goo.gl/FsGevWWrjvab4tZ9A" --output my_brands.json

# Run with visible browser (for debugging)
python google_maps_brand_scraper.py "https://maps.app.goo.gl/FsGevWWrjvab4tZ9A" --headed

# Verbose logging
python google_maps_brand_scraper.py "https://maps.app.goo.gl/FsGevWWrjvab4tZ9A" --verbose
```

## How It Works

1. **Navigation**: Opens the provided Google Maps URL in a headless Chromium browser
2. **Consent Handling**: Automatically detects and accepts Google consent/privacy pages
3. **View All Click**: Uses prioritized locators with retries to expand the directory reliably
4. **Content Loading**: Waits for dynamic content to load after clicking "View all"
5. **Brand Extraction**: Parses the list container with BeautifulSoup to extract brand metadata (name, category, floor)
6. **Intelligent Filtering**: Applies comprehensive filtering to exclude UI elements, navigation items, and irrelevant text
7. **Deduplication**: Removes duplicate brand names
8. **Results Output**: Returns sorted list of unique brand names

When proxies are enabled, `google_maps_session_manager.py` handles proxy acquisition, consent, and retries automatically.

## Supported URL Formats

- **Google Maps Short Links**: `https://maps.app.goo.gl/FsGevWWrjvab4tZ9A`
- **Direct Google Maps URLs**: `https://www.google.com/maps/place/...`

## Output Format

Results are saved as JSON with the following structure:

```json
{
  "url": "https://maps.app.goo.gl/FsGevWWrjvab4tZ9A",
  "scraped_at": "2025-09-28 16:45:53",
  "total_brands": 9,
  "brands": [
    "Bonnie & Wild",
    "Gordon Ramsay Street Burger - Edinburgh",
    "John Lewis & Partners",
    "Lane7 Edinburgh",
    "Maki & Ramen",
    "Pho Edinburgh",
    "Tortilla Edinburgh",
    "Thai Express Kitchen Edinburgh",
    "The Real Greek - Edinburgh"
  ],
  "method": "GoogleMapsScraper v1.0 - Playwright-based extraction",
  "notes": "Scraped using automated browser with consent handling and View all button clicking"
}
```

## Configuration

### Constructor Options

Use CLI flags or constructor parameters on `GoogleMapsBrandScraper`:

```python
scraper = GoogleMapsBrandScraper(
    headless=True,     # Run browser in headless mode (default: True)
    timeout=30000,     # Element operation timeout in milliseconds
    use_proxies=True,  # Enable proxy rotation via ProxyManager
)
```

### Logging

The module uses Python's logging system. Configure logging before using:

```python
import logging
import uvicorn
logging.basicConfig(level=logging.INFO)  # or logging.DEBUG for verbose output
```

## Troubleshooting

### Common Issues

1. **"Could not find View all button"**
   - Google Maps UI may have changed
   - Try running with `--headed` flag to see the browser
   - Check if the URL is correct and accessible

2. **Empty results**
   - The location may not have a directory section
   - Check if the URL points to a business with multiple stores
   - Try running with verbose logging: `--verbose`

3. **Consent page issues**
   - The session manager handles most consent scenarios automatically
   - If manual intervention is required, run with `--headed` flag
   - For more detail, see `docs/PROXY_NAVIGATION_FIX.md`

### Browser Dependencies

If you encounter browser-related errors, ensure Playwright browsers are installed:

```bash
playwright install chromium
```

## Examples

### Scrape St James Quarter (Edinburgh)

```bash
python google_maps_scraper.py "https://maps.app.goo.gl/FsGevWWrjvab4tZ9A" --output st_james_quarter_brands.json
```

### Scrape Multiple Locations

```python
from google_maps_brand_scraper import GoogleMapsBrandScraper

scraper = GoogleMapsBrandScraper(use_proxies=True)
locations = [
    "https://maps.app.goo.gl/FsGevWWrjvab4tZ9A",  # St James Quarter
    "https://maps.app.goo.gl/ABC123",            # Another mall
]

for url in locations:
    brands = scraper.scrape_brands_from_url(url)
    scraper.save_results(brands, url)
```

## Technical Details

- **Browser Engine**: Chromium via Playwright
- **Language**: Python 3.7+
- **Dependencies**: Playwright
- **License**: MIT (assumed)

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests if applicable
5. Submit a pull request

## Disclaimer

This tool is for educational and research purposes. Always respect website terms of service and robots.txt files. Use responsibly and avoid overloading servers with excessive requests.
