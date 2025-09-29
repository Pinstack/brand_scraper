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
   - Keep a ranked selector list (e.g. `get_by_role("button", name="View all")` > `[aria-label="View all"]` > `[jslog^="103597"]`) with retries before failing.
   - Ensure the entry URL lands on the indoor directory pane by forcing the `!10e3` mode flag when needed.
3. **Infinite scroll harvesting**
   - Implement loop that scrolls the list container and waits for network-idle, breaking when no new cards appear.
   - Capture telemetry for request throttling or captcha detection.
   - Monitor `page.on("response")` for the `pb=` data feed; treat `204` or very small payloads (`<200 bytes`) as an end-of-scroll sentinel.
4. **Brand parsing**
   - Target directory card selectors directly instead of generic role selectors.
   - Extract structured data (name, category, floor if present) for enrichment.
5. **Filtering & output**
   - Keep a curated stop-list and normalise brand strings.
   - Produce both JSON output and optional CSV for analyst workflows.

## Task Breakdown
- [x] **Session flow validation** – add integration test ensuring `get_authenticated_page` returns the mall URL when proxies are enabled.
- [x] **View all stabilisation** – refactor `_click_view_all_button` with explicit locator priorities and retries.
- [ ] **Infinite scroll loop** – initial scroll helper implemented; still needs virtualised list support and `pb=` sentinel validation to capture the full directory.
- [x] **Card parser** – introduce DOM parser targeting list item structure; unit test with fixture HTML.
- [ ] **Filtering rules** – formalise exclusion logic and document in `docs/brand_extraction_plan.md`.
- [ ] **Output formats** – extend `save_results` to emit optional CSV.
- [ ] **Telemetry logging** – extend current telemetry (scroll counts, `pb=` stats) with richer diagnostics for throttling/captcha and directory coverage.
  - Enforce directory-view navigation with `!10e3` when landing on overview panes.
  - Parser now uses BeautifulSoup selectors with structured card output.
  - TODO: integrate structured metadata into downstream filtering rules.
  - Scroll helper records pb sentinel, scroll counts, and network responses for analysis.

## Outstanding Issues
- Directory scroll currently surfaces only the first few cards (CTA links included); must continue scrolling until all brands load or harvest from the `pb=` payload.
- CTA entries such as "Order online" / "Reserve a table" need filtering before output.
- Cold-start authentication still shows a transient setup window; investigate returning the auth page instead of closing it.

## Next Steps
- Enhance `scroll_directory_until_complete` to account for virtualised nodes (compare `scrollHeight`, increase empty thresholds, or follow `pb=` responses).
- Investigate parsing the `pb=` response payload directly to guarantee complete coverage.
- Add post-processing filters to drop non-brand CTA rows.
- Explore reusing the authentication page on first-run to eliminate extra browser windows.

## Documentation
- This plan (living document) tracks the status of each subtask.
- Update the README once the new extraction flow is production-ready.
