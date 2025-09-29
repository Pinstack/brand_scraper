#!/usr/bin/env bash
set -euo pipefail

if [ $# -lt 1 ]; then
  echo "Usage: $0 <maps-url> [--headed] [--use-proxies]"
  exit 1
fi

uv run python google_maps_brand_scraper.py "$@"
