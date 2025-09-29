# Brand Extraction Plan

## Goal
Deliver a reliable pipeline that opens a Google Maps mall directory, clicks "View all", scrolls until all brands load, and returns a clean list of unique store names.

## Strategy
1. **Improve entry flow**
   - Guarantee the unified session manager returns a page on the target directory.
   - Harden consent/redirect detection post navigation.
2. **Directory expansion**
   - Stabilise "View all" detection using resilient locators.
   - Add verification that the directory list is present (fallback messaging if missing).
3. **Infinite scroll harvesting**
   - Implement loop that scrolls the list container and waits for network-idle, breaking when no new cards appear.
   - Capture telemetry for request throttling or captcha detection.
4. **Brand parsing**
   - Target directory card selectors directly instead of generic role selectors.
   - Extract structured data (name, category, floor if present) for enrichment.
5. **Filtering & output**
   - Keep a curated stop-list and normalise brand strings.
   - Produce both JSON output and optional CSV for analyst workflows.

## Task Breakdown
- [ ] **Session flow validation** – add integration test ensuring `get_authenticated_page` returns the mall URL when proxies are enabled.
- [ ] **View all stabilisation** – refactor `_click_view_all_button` with explicit locator priorities and retries.
- [ ] **Infinite scroll loop** – implement scroll helper that detects when no new results load.
- [ ] **Card parser** – introduce DOM parser targeting list item structure; unit test with fixture HTML.
- [ ] **Filtering rules** – formalise exclusion logic and document in `docs/brand_extraction_plan.md`.
- [ ] **Output formats** – extend `save_results` to emit optional CSV.
- [ ] **Telemetry logging** – add debug hooks capturing scroll iterations, request counts, and recaptcha flags.

## Documentation
- This plan (living document) tracks the status of each subtask.
- Update the README once the new extraction flow is production-ready.
