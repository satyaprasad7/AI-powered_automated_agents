from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from playwright.async_api import Browser, Page, Playwright, async_playwright

from ..detection.fingerprint import fingerprint_script
from .installed import resolve_browser
from .page_scripts import (
    audit_script,
    describe_script,
    extract_table_script,
    get_text_script,
    ref_selector,
    resolve_option_script,
    snapshot_script,
    text_present_script,
)
from .types import BrowserDriver, DriverName, ElementInfo, LaunchOptions, PageSnapshot


async def _launch_chromium(pw: Playwright, headless: bool) -> Browser:
    """Launches Chromium, falling back when the default build is missing or unreadable
    (e.g. a Playwright upgrade without `playwright install`, or antivirus holding the
    file): headless shell -> full Chromium -> installed Chrome."""
    first_error: Exception | None = None
    for attempt in ({}, {"channel": "chromium"}, {"channel": "chrome"}):
        try:
            return await pw.chromium.launch(headless=headless, **attempt)
        except Exception as err:  # noqa: BLE001 - Playwright raises a generic Error
            first_error = first_error or err
            if not re.search(r"Executable doesn't exist|is not found|ENOENT", str(err), re.I):
                raise
    raise RuntimeError(
        "Playwright could not find a Chromium browser. Run `python -m playwright install chromium`, "
        f"or pick the Selenium engine. ({str(first_error).splitlines()[0]})"
    )


class PlaywrightDriver(BrowserDriver):
    name: DriverName = "playwright"

    def __init__(self) -> None:
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._page: Page | None = None
        self._browser_label = "Playwright Chromium"
        self._timeout = 10_000
        self._status: int | None = None
        self._inflight = 0
        self._last_network_activity = 0.0

    @property
    def page(self) -> Page:
        if not self._page:
            raise RuntimeError("Browser not launched")
        return self._page

    async def launch(self, options: LaunchOptions) -> None:
        self._timeout = options.action_timeout_ms
        self._pw = await async_playwright().start()
        self._browser = await self._launch_browser(self._pw, options)
        profile = options.profile
        context = await self._browser.new_context(
            viewport=(profile and profile.viewport) or options.viewport or {"width": 1280, "height": 900},
            user_agent=profile.user_agent if profile else None,
            locale=profile.locale if profile else None,
            timezone_id=profile.timezone_id if profile else None,
        )
        if profile and profile.init_script:
            await context.add_init_script(profile.init_script)
        page = await context.new_page()
        page.set_default_timeout(self._timeout)
        self._page = page

        def on_response(r: Any) -> None:
            if r.request.is_navigation_request() and r.frame == page.main_frame:
                self._status = r.status

        def on_request(_: Any) -> None:
            self._inflight += 1
            self._last_network_activity = time.monotonic()

        def on_done(_: Any) -> None:
            self._inflight = max(0, self._inflight - 1)
            self._last_network_activity = time.monotonic()

        page.on("response", on_response)
        page.on("request", on_request)
        page.on("requestfinished", on_done)
        page.on("requestfailed", on_done)

    async def _launch_browser(self, pw: Playwright, options: LaunchOptions) -> Browser:
        return await _launch_chromium(pw, options.headless)

    def browser_info(self) -> str | None:
        return f"{self._browser_label} {self._browser.version}" if self._browser else None

    async def goto(self, url: str) -> None:
        await self.page.goto(url, wait_until="domcontentloaded")

    def url(self) -> str:
        return self.page.url

    async def snapshot(self) -> PageSnapshot:
        return await self.page.evaluate(snapshot_script())

    async def describe(self, ref: str) -> ElementInfo | None:
        ref_selector(ref)
        return await self.page.evaluate(describe_script(ref))

    async def click(self, ref: str) -> None:
        await self.page.locator(ref_selector(ref)).click()
        await self._settle()

    async def fill(self, ref: str, value: str) -> None:
        await self.page.locator(ref_selector(ref)).fill(value)

    async def select(self, ref: str, value_or_label: str) -> list[str]:
        value = await self.page.evaluate(resolve_option_script(ref, value_or_label))
        if value is None:
            raise ValueError(f'No option matching "{value_or_label}"')
        selected = await self.page.locator(ref_selector(ref)).select_option(value)
        await self._settle()
        return selected

    async def press(self, key: str) -> None:
        await self.page.keyboard.press(key)
        await self._settle()

    async def get_text(self, ref: str) -> str:
        if ref != "page":
            ref_selector(ref)
        return await self.page.evaluate(get_text_script(ref))

    async def extract_table(self, ref: str) -> list[list[str]]:
        if ref != "page":
            ref_selector(ref)
        return await self.page.evaluate(extract_table_script(ref))

    async def wait_for_text(self, text: str, timeout_ms: int) -> bool:
        try:
            await self.page.wait_for_function(text_present_script(text), timeout=timeout_ms)
            return True
        except Exception:  # noqa: BLE001 - timeout or navigation
            return False

    async def audit(self) -> dict[str, Any]:
        return await self.page.evaluate(audit_script())

    async def fingerprint(self) -> dict[str, Any]:
        return await self.page.evaluate(fingerprint_script())

    def last_status(self) -> int | None:
        return self._status

    async def screenshot(self, path: str | None = None) -> bytes:
        return await self.page.screenshot(path=path, type="jpeg", quality=60)

    async def close(self) -> None:
        try:
            if self._browser:
                await self._browser.close()
        finally:
            if self._pw:
                await self._pw.stop()

    async def _settle(self) -> None:
        """Give navigations and XHR triggered by an action a moment to land: wait until no
        request has been in flight for 300 ms (max 3 s). Playwright's own "networkidle" load
        state resolves immediately once a page has loaded, so it misses requests started by
        a click (e.g. a fetch-based form submit)."""
        try:
            await self.page.wait_for_load_state("domcontentloaded")
        except Exception:  # noqa: BLE001
            pass
        self._last_network_activity = max(self._last_network_activity, time.monotonic())
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if self._inflight == 0 and time.monotonic() - self._last_network_activity >= 0.3:
                return
            await asyncio.sleep(0.05)


class StandardBrowserDriver(PlaywrightDriver):
    """The "standard browser" engine: a browser installed on this machine (Chrome, Edge,
    Brave... or any Chromium-based executable), driven through Playwright with a fresh,
    temporary profile. It is still an automated browser (navigator.webdriver stays true);
    what changes is the binary, so sites see a regular branded build instead of a test Chromium."""

    name: DriverName = "standard"

    async def _launch_browser(self, pw: Playwright, options: LaunchOptions) -> Browser:
        target = resolve_browser(options.browser)
        self._browser_label = target.label
        return await pw.chromium.launch(headless=options.headless, executable_path=target.executable_path)
