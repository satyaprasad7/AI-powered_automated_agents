"""Driver smoke test - no LLM, no API key. Exercises every BrowserDriver method
against the demo site on Playwright, Selenium and the standard (installed) browser.

    python -m agentic_browser.smoke
"""
from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

from .agent.tools import format_snapshot
from .demo_site.server import DEMO_ORIGIN, is_demo_server_up, start_demo_server
from .detection.fingerprint import red_flags
from .detection.profiles import PROFILES
from .drivers import BrowserDriver, DriverName, LaunchOptions, create_driver


class SmokeError(AssertionError):
    pass


def check(cond: object, message: str) -> None:
    if not cond:
        raise SmokeError(f"Assertion failed: {message}")


async def ref_for(driver: BrowserDriver, name: str) -> str:
    snap = await driver.snapshot()
    el = next((e for e in snap["elements"] if name.lower() in e["name"].lower()), None)
    check(el, f'element "{name}" on {snap["url"]}\n{format_snapshot(snap)}')
    return el["ref"]  # type: ignore[index]


async def smoke(name: DriverName) -> None:
    driver = create_driver(name)
    await driver.launch(LaunchOptions(headless=True))
    try:
        # Form submission
        await driver.goto(f"{DEMO_ORIGIN}/register.html")
        await driver.fill(await ref_for(driver, "Full name"), "Jane Doe")
        await driver.fill(await ref_for(driver, "Work email"), "jane.doe@acme.test")
        selected = await driver.select(await ref_for(driver, "Plan"), "Enterprise")
        check(selected and selected[0] == "enterprise", f"select by label returned {selected}")
        await driver.click(await ref_for(driver, "terms"))
        await driver.click(await ref_for(driver, "Create account"))
        # No explicit wait: click() must settle until the fetch-driven result has rendered.
        check("Confirmation number" in await driver.get_text("page"), "registration confirmation shown right after click")

        # Table extraction + pagination
        await driver.goto(f"{DEMO_ORIGIN}/products.html")
        page1 = await driver.extract_table("page")
        check(len(page1) == 6 and page1[0][0] == "SKU", f"table rows: {page1}")
        await driver.click(await ref_for(driver, "Next page"))
        page2 = await driver.extract_table("page")
        check(page2[1][0] == "AC-105", f"page 2 first SKU: {page2[1][0] if len(page2) > 1 else None}")

        # Multi-step workflow with a date input and a validation message
        await driver.goto(f"{DEMO_ORIGIN}/expenses.html")
        await driver.fill(await ref_for(driver, "Employee ID"), "bad")
        await driver.click(await ref_for(driver, "Continue"))
        invalid = next((e for e in (await driver.snapshot())["elements"] if e.get("invalid")), None)
        check(invalid and "E-12345" in invalid["invalid"], "validation message surfaced in snapshot")
        await driver.fill(await ref_for(driver, "Employee ID"), "E-10234")
        await driver.select(await ref_for(driver, "Cost center"), "CC-100")
        await driver.click(await ref_for(driver, "Continue"))
        await driver.fill(await ref_for(driver, "Expense date"), "2026-09-15")
        await driver.fill(await ref_for(driver, "Amount"), "42.50")
        await driver.click(await ref_for(driver, "Review claim"))
        check("$42.50" in await driver.get_text("page"), "review shows amount")
        await driver.click(await ref_for(driver, "Submit claim"))
        check("Claim ID" in await driver.get_text("page"), "claim submitted")
        check(await driver.wait_for_text("Claim ID", 1000), "wait_for_text finds present text")

        Path("runs").mkdir(exist_ok=True)
        shot = await driver.screenshot(f"runs/smoke-py-{name}.jpg")
        check(len(shot) > 1000, "screenshot captured")
        check(driver.last_status() == 200, f"last navigation status is 200 (got {driver.last_status()})")
        print(f"✓ {name}: form, table, workflow, screenshot OK ({driver.browser_info()})")
    finally:
        await driver.close()


async def smoke_profiles(name: DriverName) -> None:
    """Each detection profile presents its signals, and the demo survey screens them (with its seeded VMware gap)."""
    expect = {
        "default": ([r"navigator\.webdriver"], [r"Automation detected"], []),
        "vm": ([r"VMware SVGA 3D", r"2 CPU cores", r"1024x768"], [r"Automation detected"], [r"Virtual machine GPU"]),
        "anti-detect": (
            [r"claims mac but the platform is windows", r"Apple GPU", r"Asia/Tokyo"],
            [r"user agent OS differs", r"Apple GPU", r"Location mismatch"],
            [],
        ),
    }
    for profile_id, (flags_re, site_flags, site_misses) in expect.items():
        driver = create_driver(name)
        await driver.launch(LaunchOptions(headless=True, profile=PROFILES[profile_id]))
        try:
            await driver.goto(f"{DEMO_ORIGIN}/survey.html")
            flags = "\n".join(red_flags(await driver.fingerprint()))
            for pattern in flags_re:
                check(re.search(pattern, flags), f"{profile_id}: fingerprint red flag {pattern}\n{flags}")
            check(await driver.wait_for_text("quality check:", 5000), f"{profile_id}: screening result shown")
            page = await driver.get_text("page")
            check("FLAGGED" in page, f"{profile_id}: demo survey flags the visitor")
            for pattern in site_flags:
                check(re.search(pattern, page), f"{profile_id}: survey reports {pattern}\n{page}")
            for pattern in site_misses:
                check(not re.search(pattern, page), f"{profile_id}: seeded gap {pattern} should be missed")
        finally:
            await driver.close()
    print(f"✓ {name}: default / vm / anti-detect profiles present their signals; survey screening behaves as seeded")


async def main(engines: list[DriverName]) -> int:
    server = None if await is_demo_server_up() else await start_demo_server()
    failed = False
    try:
        for name in engines:
            try:
                await smoke(name)
                await smoke_profiles(name)
            except Exception as err:  # noqa: BLE001 - report every engine
                # The standard engine is optional: it needs Chrome or Edge installed on this machine.
                if name == "standard" and "No installed Google Chrome or Microsoft Edge" in str(err):
                    print("- standard: skipped (no installed Chrome or Edge)")
                    continue
                failed = True
                print(f"✗ {name}: {err}", file=sys.stderr)
    finally:
        if server:
            await server.cleanup()
    return 1 if failed else 0


if __name__ == "__main__":
    selected = sys.argv[1:] or ["playwright", "selenium", "standard"]
    sys.exit(asyncio.run(main(selected)))  # type: ignore[arg-type]
