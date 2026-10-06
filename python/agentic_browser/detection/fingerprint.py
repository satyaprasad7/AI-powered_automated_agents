"""Collects the fingerprint signals a site can read from the browser, and derives the
red flags a bot / fraud detector would be expected to raise.

A fingerprint is a dict: {webdriver, userAgent, platform, uaDataPlatform, languages, timezone,
hardwareConcurrency, deviceMemory, screen: {width, height, colorDepth}, webgl: {vendor, renderer}, pluginCount}.
"""
from __future__ import annotations

import re
from typing import Any

Fingerprint = dict[str, Any]


def fingerprint_script() -> str:
    return """(() => {
    let vendor = null, renderer = null;
    try {
      const gl = document.createElement('canvas').getContext('webgl') || document.createElement('canvas').getContext('experimental-webgl');
      if (gl) {
        const ext = gl.getExtension('WEBGL_debug_renderer_info');
        vendor = gl.getParameter(ext ? ext.UNMASKED_VENDOR_WEBGL : gl.VENDOR);
        renderer = gl.getParameter(ext ? ext.UNMASKED_RENDERER_WEBGL : gl.RENDERER);
      }
    } catch (e) {}
    return {
      webdriver: !!navigator.webdriver,
      userAgent: navigator.userAgent,
      platform: navigator.platform,
      uaDataPlatform: navigator.userAgentData ? navigator.userAgentData.platform : null,
      languages: Array.from(navigator.languages || []),
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      hardwareConcurrency: navigator.hardwareConcurrency,
      deviceMemory: navigator.deviceMemory ?? null,
      screen: { width: screen.width, height: screen.height, colorDepth: screen.colorDepth },
      webgl: { vendor, renderer },
      pluginCount: navigator.plugins ? navigator.plugins.length : 0,
    };
  })()"""


VM_GPU = re.compile(r"vmware|virtualbox|vbox|parallels|hyper-v|qemu|virgl|llvmpipe|swiftshader|microsoft basic render", re.I)


def _ua_os(ua: str) -> str:
    if re.search(r"iPhone|iPad", ua):
        return "ios"
    if "Android" in ua:
        return "android"
    if re.search(r"Macintosh|Mac OS X", ua):
        return "mac"
    if "Windows" in ua:
        return "windows"
    if re.search(r"Linux|X11", ua):
        return "linux"
    return "unknown"


def _platform_os(fp: Fingerprint) -> str:
    p = f"{fp.get('uaDataPlatform') or ''} {fp.get('platform') or ''}".lower()
    if "mac" in p:
        return "mac"
    if "win" in p:
        return "windows"
    if "linux" in p:
        return "linux"
    return "unknown"


_EXPECTED_ZONES = {
    "US": re.compile(r"^America/|^US/|^Pacific/Honolulu"),
    "GB": re.compile(r"^Europe/London"),
    "IN": re.compile(r"^Asia/(Kolkata|Calcutta)"),
    "JP": re.compile(r"^Asia/Tokyo"),
    "DE": re.compile(r"^Europe/Berlin"),
    "AU": re.compile(r"^Australia/"),
}


def _locale_timezone_mismatch(fp: Fingerprint) -> bool:
    """Rough time-zone -> region check for the first navigator language."""
    languages = fp.get("languages") or []
    parts = (languages[0] if languages else "").split("-")
    region = parts[1].upper() if len(parts) > 1 else ""
    expected = _EXPECTED_ZONES.get(region)
    return bool(expected) and not expected.search(fp.get("timezone") or "")


def red_flags(fp: Fingerprint) -> list[str]:
    flags: list[str] = []
    webgl = fp.get("webgl") or {}
    gpu = f"{webgl.get('vendor')} {webgl.get('renderer')}"
    screen = fp.get("screen") or {}
    if fp.get("webdriver"):
        flags.append("Automation: navigator.webdriver is true")
    if "HeadlessChrome" in (fp.get("userAgent") or ""):
        flags.append("Automation: headless Chrome user agent")
    if webgl.get("renderer") and VM_GPU.search(gpu):
        flags.append(f"Virtual machine / software GPU: {webgl['renderer']}")
    cores = fp.get("hardwareConcurrency") or 0
    if cores <= 2:
        flags.append(f"Virtual machine hint: only {cores} CPU cores")
    size = f"{screen.get('width')}x{screen.get('height')}"
    if size in ("1024x768", "800x600"):
        flags.append(f"Virtual machine hint: default VM screen size {size}")
    claimed = _ua_os(fp.get("userAgent") or "")
    actual = _platform_os(fp)
    if claimed != "unknown" and actual != "unknown" and claimed != actual:
        flags.append(f"Fingerprint inconsistency: user agent claims {claimed} but the platform is {actual}")
    if re.search("apple", gpu, re.I) and actual != "mac":
        flags.append(f"Fingerprint inconsistency: Apple GPU reported on a {actual} platform")
    if _locale_timezone_mismatch(fp):
        flags.append(f"Fingerprint inconsistency: time zone {fp.get('timezone')} vs language {(fp.get('languages') or [''])[0]}")
    return flags
