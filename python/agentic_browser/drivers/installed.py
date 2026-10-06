"""Finds the Chromium-based browsers installed on this machine, for the "standard browser" engine.

Playwright can drive any of them because they share Chrome's DevTools protocol;
Firefox and Safari are not supported.
"""
from __future__ import annotations

import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class InstalledBrowser:
    # Stable id used in task files, the CLI and the UI ("chrome", "msedge", ...).
    id: str
    label: str
    executable_path: str

    def to_dict(self) -> dict[str, str]:
        d = asdict(self)
        return {"id": d["id"], "label": d["label"], "executablePath": d["executable_path"]}


def _win(*rel: str) -> list[str]:
    """Joins under each Windows install root that is set."""
    roots = [os.environ.get(v, "") for v in ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)")]
    return [str(Path(root, *rel)) for root in roots if root]


def _mac(app: str) -> list[str]:
    return [f"/Applications/{app}.app/Contents/MacOS/{app}"]


def _known() -> list[tuple[str, str, dict[str, list[str]]]]:
    """Detection order is also the "auto" preference order."""
    return [
        ("chrome", "Google Chrome", {"win32": _win("Google", "Chrome", "Application", "chrome.exe"), "darwin": _mac("Google Chrome"), "linux": ["/opt/google/chrome/chrome"]}),
        ("msedge", "Microsoft Edge", {"win32": _win("Microsoft", "Edge", "Application", "msedge.exe"), "darwin": _mac("Microsoft Edge"), "linux": ["/opt/microsoft/msedge/msedge"]}),
        ("chrome-beta", "Google Chrome Beta", {"win32": _win("Google", "Chrome Beta", "Application", "chrome.exe"), "darwin": _mac("Google Chrome Beta"), "linux": ["/opt/google/chrome-beta/chrome"]}),
        ("msedge-beta", "Microsoft Edge Beta", {"win32": _win("Microsoft", "Edge Beta", "Application", "msedge.exe"), "darwin": _mac("Microsoft Edge Beta"), "linux": ["/opt/microsoft/msedge-beta/msedge"]}),
        (
            "brave",
            "Brave",
            {
                "win32": _win("BraveSoftware", "Brave-Browser", "Application", "brave.exe"),
                "darwin": _mac("Brave Browser"),
                "linux": ["/opt/brave.com/brave/brave", "/usr/bin/brave-browser"],
            },
        ),
        ("vivaldi", "Vivaldi", {"win32": _win("Vivaldi", "Application", "vivaldi.exe"), "darwin": _mac("Vivaldi"), "linux": ["/opt/vivaldi/vivaldi"]}),
    ]


KNOWN_BROWSER_IDS = [b[0] for b in _known()]


def _platform() -> str:
    return "linux" if sys.platform.startswith("linux") else sys.platform


def find_installed_browsers() -> list[InstalledBrowser]:
    found: list[InstalledBrowser] = []
    for browser_id, label, paths in _known():
        path = next((p for p in paths.get(_platform(), []) if Path(p).is_file()), None)
        if path:
            found.append(InstalledBrowser(browser_id, label, path))
    return found


def resolve_browser(choice: str | None) -> InstalledBrowser:
    """Resolves a browser choice: "auto" (or empty) picks the first installed one,
    a known id picks that browser, and an absolute path is used as-is."""
    want = (choice or "").strip() or "auto"
    if Path(want).is_absolute():
        if not Path(want).is_file():
            raise ValueError(f"Browser executable not found: {want}")
        for b in find_installed_browsers():
            if str(Path(b.executable_path).resolve()).lower() == str(Path(want).resolve()).lower():
                return b
        label = Path(want).name
        if label.lower().endswith(".exe"):
            label = label[:-4]
        return InstalledBrowser("custom", label, want)
    installed = find_installed_browsers()
    if want == "auto":
        if not installed:
            raise ValueError(
                "No installed Google Chrome or Microsoft Edge was found for the Standard browser engine. "
                "Install one, or pick Playwright / Selenium."
            )
        return installed[0]
    known = next((b for b in _known() if b[0] == want), None)
    if not known:
        raise ValueError(f'Unknown browser "{want}". Use auto, {", ".join(KNOWN_BROWSER_IDS)}, or a full path to the executable.')
    match = next((b for b in installed if b.id == want), None)
    if not match:
        raise ValueError(f"{known[1]} is not installed on this machine. Installed: {', '.join(b.id for b in installed) or 'none'}.")
    return match
