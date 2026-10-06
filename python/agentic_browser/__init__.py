"""Agentic browser automation: Claude drives a real browser (Playwright, Selenium or an installed Chrome/Edge)."""

from pathlib import Path

# Shared with the TypeScript implementation: tasks/, demo-site/public/, runs/ and .env live at the repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]
