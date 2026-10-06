"""The browser surface the agent drives.

Playwright, Selenium and the standard (installed) browser each implement BrowserDriver,
so tasks and the agent loop are driver-agnostic. Elements are addressed by the `ref`
values returned from snapshot().
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Literal

from ..detection.profiles import BrowserProfile

DriverName = Literal["playwright", "selenium", "standard"]
DRIVER_NAMES: tuple[DriverName, ...] = ("playwright", "selenium", "standard")
"""playwright / selenium use test browsers; standard drives the installed Chrome or Edge."""

DRIVER_LABELS: dict[str, str] = {
    "playwright": "Playwright",
    "selenium": "Selenium",
    "standard": "Standard browser",
    # Runs recorded by the TypeScript implementation share the same history.
    "puppeteer": "Puppeteer",
}


@dataclass
class LaunchOptions:
    headless: bool = True
    # Detection-test profile (user agent, locale, time zone, injected fingerprint).
    profile: BrowserProfile | None = None
    viewport: dict[str, int] | None = None
    # Standard browser engine only: "auto" (default), an installed browser id
    # ("chrome", "msedge", ...) or a full path to a Chromium-based executable.
    browser: str | None = None
    # Per-action timeout in ms (clicks, fills, waits).
    action_timeout_ms: int = 10_000


# One interactive element on the page: {ref, tag, name, role?, type?, value?, checked?, ...}.
ElementInfo = dict[str, Any]
# {url, title, elements: [ElementInfo], text, truncated}
PageSnapshot = dict[str, Any]


class BrowserDriver(ABC):
    name: DriverName

    @abstractmethod
    async def launch(self, options: LaunchOptions) -> None: ...

    @abstractmethod
    def browser_info(self) -> str | None:
        """The browser that actually ran, e.g. "Microsoft Edge 141.0.3537.57", once launched."""

    @abstractmethod
    async def goto(self, url: str) -> None: ...

    @abstractmethod
    def url(self) -> str: ...

    @abstractmethod
    async def snapshot(self) -> PageSnapshot: ...

    @abstractmethod
    async def describe(self, ref: str) -> ElementInfo | None: ...

    @abstractmethod
    async def click(self, ref: str) -> None: ...

    @abstractmethod
    async def fill(self, ref: str, value: str) -> None: ...

    @abstractmethod
    async def select(self, ref: str, value_or_label: str) -> list[str]:
        """Selects by option value or visible label; returns the selected values."""

    @abstractmethod
    async def press(self, key: str) -> None: ...

    @abstractmethod
    async def get_text(self, ref: str) -> str: ...

    @abstractmethod
    async def extract_table(self, ref: str) -> list[list[str]]: ...

    @abstractmethod
    async def wait_for_text(self, text: str, timeout_ms: int) -> bool: ...

    @abstractmethod
    async def audit(self) -> dict[str, Any]:
        """Accessibility/content facts about the current page (see audit_script)."""

    @abstractmethod
    async def fingerprint(self) -> dict[str, Any]:
        """Fingerprint signals the current page can read from this browser."""

    @abstractmethod
    def last_status(self) -> int | None:
        """HTTP status of the last main-frame navigation, if known."""

    @abstractmethod
    async def screenshot(self, path: str | None = None) -> bytes: ...

    @abstractmethod
    async def close(self) -> None: ...
