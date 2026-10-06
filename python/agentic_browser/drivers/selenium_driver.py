"""Selenium WebDriver engine (ChromeDriver).

Selenium plays the role Puppeteer has in the TypeScript implementation: a second,
independent automation stack to compare against Playwright on the same site. Selenium
Manager finds (or downloads) a matching Chrome and ChromeDriver automatically.

Selenium's API is blocking, so every call runs on one dedicated worker thread.
HTTP status and in-flight network requests come from ChromeDriver's performance log
(DevTools Network events), so nothing is injected into the page to track them.
"""
from __future__ import annotations

import asyncio
import base64
import json
import time
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from typing import Any, Callable, TypeVar

from selenium import webdriver
from selenium.common.exceptions import ElementClickInterceptedException, JavascriptException, TimeoutException
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait

from ..detection.fingerprint import fingerprint_script
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

T = TypeVar("T")

# Playwright-style key names (what the agent is told to use) -> Selenium keys.
_KEYS = {
    "enter": Keys.ENTER,
    "return": Keys.RETURN,
    "tab": Keys.TAB,
    "escape": Keys.ESCAPE,
    "esc": Keys.ESCAPE,
    "backspace": Keys.BACKSPACE,
    "delete": Keys.DELETE,
    "space": Keys.SPACE,
    "arrowdown": Keys.ARROW_DOWN,
    "arrowup": Keys.ARROW_UP,
    "arrowleft": Keys.ARROW_LEFT,
    "arrowright": Keys.ARROW_RIGHT,
    "home": Keys.HOME,
    "end": Keys.END,
    "pageup": Keys.PAGE_UP,
    "pagedown": Keys.PAGE_DOWN,
    "shift": Keys.SHIFT,
    "control": Keys.CONTROL,
    "ctrl": Keys.CONTROL,
    "alt": Keys.ALT,
    "meta": Keys.META,
    **{f"f{i}": getattr(Keys, f"F{i}") for i in range(1, 13)},
}

# Input types whose value can't be typed reliably (Chrome's date pickers are locale-dependent).
_SET_VALUE_TYPES = {"date", "time", "datetime-local", "month", "week", "color", "range"}
_SET_VALUE_JS = """
const el = arguments[0], v = arguments[1];
const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, v);
el.dispatchEvent(new Event('input', { bubbles: true }));
el.dispatchEvent(new Event('change', { bubbles: true }));
"""


class SeleniumDriver(BrowserDriver):
    name: DriverName = "selenium"

    def __init__(self) -> None:
        self._driver: webdriver.Chrome | None = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="selenium")
        self._timeout = 10_000
        self._status: int | None = None
        self._inflight: set[str] = set()
        self._last_network_activity = 0.0
        self._main_frame: str | None = None
        self._version = ""

    @property
    def driver(self) -> webdriver.Chrome:
        if not self._driver:
            raise RuntimeError("Browser not launched")
        return self._driver

    async def _run(self, fn: Callable[..., T], *args: Any) -> T:
        return await asyncio.get_running_loop().run_in_executor(self._executor, partial(fn, *args))

    async def _eval(self, script: str) -> Any:
        def run() -> Any:
            try:
                return self.driver.execute_script("return " + script)
            except JavascriptException as err:
                raise RuntimeError(err.msg or str(err)) from None

        return await self._run(run)

    # -- lifecycle ---------------------------------------------------------

    async def launch(self, options: LaunchOptions) -> None:
        self._timeout = options.action_timeout_ms
        await self._run(self._launch_sync, options)

    def _launch_sync(self, options: LaunchOptions) -> None:
        profile = options.profile
        viewport = (profile and profile.viewport) or options.viewport or {"width": 1280, "height": 900}
        opts = webdriver.ChromeOptions()
        if options.headless:
            opts.add_argument("--headless=new")
        opts.add_argument(f"--window-size={viewport['width']},{viewport['height']}")
        opts.page_load_strategy = "eager"  # return at DOMContentLoaded, like the other engines
        opts.set_capability("goog:loggingPrefs", {"performance": "ALL"})
        if profile and profile.user_agent:
            opts.add_argument(f"--user-agent={profile.user_agent}")
        if profile and profile.locale:
            opts.add_argument(f"--lang={profile.locale}")
            opts.add_experimental_option("prefs", {"intl.accept_languages": profile.locale})
        driver = webdriver.Chrome(options=opts)
        self._driver = driver
        driver.set_page_load_timeout(30)
        caps = driver.capabilities
        self._version = caps.get("browserVersion", "")
        driver.execute_cdp_cmd(
            "Emulation.setDeviceMetricsOverride",
            {"width": viewport["width"], "height": viewport["height"], "deviceScaleFactor": 1, "mobile": False},
        )
        if profile and profile.timezone_id:
            driver.execute_cdp_cmd("Emulation.setTimezoneOverride", {"timezoneId": profile.timezone_id})
        if profile and profile.init_script:
            driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": profile.init_script})
        self._main_frame = driver.execute_cdp_cmd("Page.getFrameTree", {})["frameTree"]["frame"]["id"]

    def browser_info(self) -> str | None:
        return f"Chrome {self._version} (ChromeDriver)" if self._driver else None

    async def close(self) -> None:
        try:
            if self._driver:
                await self._run(self._driver.quit)
        finally:
            self._executor.shutdown(wait=False)

    # -- navigation & network ----------------------------------------------

    def _pump(self) -> None:
        """Drains ChromeDriver's performance log: tracks the main-frame HTTP status and in-flight requests."""
        for entry in self.driver.get_log("performance"):
            msg = json.loads(entry["message"]).get("message", {})
            method, params = msg.get("method", ""), msg.get("params", {})
            request_id = params.get("requestId")
            if method == "Network.requestWillBeSent":
                self._inflight.add(request_id)
                self._last_network_activity = time.monotonic()
            elif method in ("Network.loadingFinished", "Network.loadingFailed"):
                self._inflight.discard(request_id)
                self._last_network_activity = time.monotonic()
            elif method == "Network.responseReceived" and params.get("type") == "Document" and params.get("frameId") == self._main_frame:
                self._status = params.get("response", {}).get("status")

    async def goto(self, url: str) -> None:
        def run() -> None:
            self.driver.get(url)
            self._pump()

        await self._run(run)

    def url(self) -> str:
        return self.driver.current_url

    def last_status(self) -> int | None:
        return self._status

    async def _settle(self) -> None:
        """Give navigations and XHR triggered by an action a moment to land: wait until no
        request has been in flight for 300 ms (max 3 s)."""

        def run() -> None:
            self._last_network_activity = max(self._last_network_activity, time.monotonic())
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                self._pump()
                ready = self.driver.execute_script("return document.readyState") != "loading"
                if ready and not self._inflight and time.monotonic() - self._last_network_activity >= 0.3:
                    return
                time.sleep(0.05)

        await self._run(run)

    # -- page reading --------------------------------------------------------

    async def snapshot(self) -> PageSnapshot:
        return await self._eval(snapshot_script())

    async def describe(self, ref: str) -> ElementInfo | None:
        ref_selector(ref)
        return await self._eval(describe_script(ref))

    async def get_text(self, ref: str) -> str:
        if ref != "page":
            ref_selector(ref)
        return await self._eval(get_text_script(ref))

    async def extract_table(self, ref: str) -> list[list[str]]:
        if ref != "page":
            ref_selector(ref)
        return await self._eval(extract_table_script(ref))

    async def wait_for_text(self, text: str, timeout_ms: int) -> bool:
        def run() -> bool:
            try:
                WebDriverWait(self.driver, timeout_ms / 1000, poll_frequency=0.1).until(
                    lambda d: d.execute_script("return " + text_present_script(text))
                )
                return True
            except TimeoutException:
                return False

        return await self._run(run)

    async def audit(self) -> dict[str, Any]:
        return await self._eval(audit_script())

    async def fingerprint(self) -> dict[str, Any]:
        return await self._eval(fingerprint_script())

    async def screenshot(self, path: str | None = None) -> bytes:
        def run() -> bytes:
            data = base64.b64decode(self.driver.execute_cdp_cmd("Page.captureScreenshot", {"format": "jpeg", "quality": 60})["data"])
            if path:
                Path(path).write_bytes(data)
            return data

        return await self._run(run)

    # -- actions -------------------------------------------------------------

    def _element(self, ref: str, clickable: bool = False) -> WebElement:
        condition = EC.element_to_be_clickable if clickable else EC.visibility_of_element_located
        try:
            return WebDriverWait(self.driver, self._timeout / 1000).until(condition((By.CSS_SELECTOR, ref_selector(ref))))
        except TimeoutException:
            state = "clickable" if clickable else "visible"
            raise TimeoutError(f"Element [{ref}] was not {state} within {self._timeout} ms") from None

    async def click(self, ref: str) -> None:
        def run() -> None:
            el = self._element(ref, clickable=True)
            try:
                el.click()
            except ElementClickInterceptedException:
                self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'})", el)
                el.click()

        await self._run(run)
        await self._settle()

    async def fill(self, ref: str, value: str) -> None:
        def run() -> None:
            el = self._element(ref)
            if (el.get_attribute("type") or "").lower() in _SET_VALUE_TYPES or not value:
                self.driver.execute_script(_SET_VALUE_JS, el, value)
                return
            el.clear()
            el.send_keys(value)

        await self._run(run)

    async def select(self, ref: str, value_or_label: str) -> list[str]:
        value = await self._eval(resolve_option_script(ref, value_or_label))
        if value is None:
            raise ValueError(f'No option matching "{value_or_label}"')

        def run() -> list[str]:
            select = Select(self._element(ref))
            select.select_by_value(value)
            return [o.get_attribute("value") or "" for o in select.all_selected_options]

        selected = await self._run(run)
        await self._settle()
        return selected

    async def press(self, key: str) -> None:
        parts = [key] if key == "+" else key.split("+")
        names = [_KEYS.get(p.lower(), p) for p in parts]
        modifiers, main = names[:-1], names[-1]

        def run() -> None:
            chain = ActionChains(self.driver)
            for m in modifiers:
                chain.key_down(m)
            chain.send_keys(main)
            for m in reversed(modifiers):
                chain.key_up(m)
            chain.perform()

        await self._run(run)
        await self._settle()
