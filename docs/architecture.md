# Architecture Overview

## Modules
- `google_consent_handler.py`: Encapsulates all consent-page logic and helper methods.
- `google_maps_session_manager.py`: Owns Playwright session lifecycle, including proxy-aware flows.
- `google_maps_brand_scraper.py`: High-level scraper orchestrating sessions and brand extraction.
- `proxy_manager.py`: Handles proxy rotation, telemetry, and storage.

## Docs
- `docs/PROXY_NAVIGATION_FIX.md`: Detailed write-up of proxy session refactor.
- `docs/architecture.md`: Directory layout and module responsibilities.

## CLI & Entry Points
- `google_maps_brand_scraper.py` exposes CLI arguments and can be imported as a module.

## Data & Misc
- Temporary JSONs (e.g., `*_retry.json`) capture run artefacts; consider moving into `data/` if persistence is needed.

## Todo
- Migrate remaining legacy standalone scraper `google_maps_scraper.py` into the modular flow or archive under `legacy/` if required.
