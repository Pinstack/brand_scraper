setup:
	./scripts/setup.sh

run:
	./scripts/run_scraper.sh $(url)

fmt:
	uv run black google_maps_brand_scraper.py google_maps_session_manager.py google_consent_handler.py proxy_manager.py

lint:
	uv run flake8
