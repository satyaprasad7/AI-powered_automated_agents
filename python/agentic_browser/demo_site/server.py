"""Tiny local site the demo tasks run against. It has a registration form (with a
deliberately seeded validation bug), a paginated product catalogue, a multi-step
expense-claim workflow and a survey with respondent fraud screening.

The pages are shared with the TypeScript implementation (repo-root demo-site/public).

    python -m agentic_browser.demo_site.server   -> http://127.0.0.1:4173
"""
from __future__ import annotations

import asyncio
import re
import secrets
import urllib.request
from typing import Any

from aiohttp import web

from .. import REPO_ROOT

DEMO_PORT = 4173
# Loopback only, and the IP rather than "localhost" so IPv4/IPv6 resolution can't miss.
DEMO_ORIGIN = f"http://127.0.0.1:{DEMO_PORT}"
PUBLIC_DIR = (REPO_ROOT / "demo-site" / "public").resolve()

# SEEDED BUG (intentional, for the testing demo): no TLD required, so "jane@example" is accepted.
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+$")

# Survey fraud screening. SEEDED GAP (intentional, for the detection demo): the
# virtual-GPU blocklist has VirtualBox but not VMware, and CPU/screen hints are ignored.
VM_GPU_BLOCKLIST = re.compile(r"virtualbox|vbox|llvmpipe", re.I)


def screen_respondent(s: dict[str, Any]) -> dict[str, Any]:
    reasons: list[str] = []
    ua = str(s.get("userAgent") or "")
    platform = f"{s.get('uaDataPlatform') or ''} {s.get('platform') or ''}".lower()
    gpu = f"{s.get('webglVendor') or ''} {s.get('webglRenderer') or ''}"
    if s.get("webdriver"):
        reasons.append("Automation detected (navigator.webdriver)")
    if "HeadlessChrome" in ua:
        reasons.append("Headless browser detected")
    if VM_GPU_BLOCKLIST.search(gpu):
        reasons.append(f"Virtual machine GPU detected ({s.get('webglRenderer')})")
    ua_mac = bool(re.search(r"Macintosh|Mac OS X", ua))
    ua_win = "Windows" in ua
    if (ua_mac and "mac" not in platform) or (ua_win and "win" not in platform):
        reasons.append("Fingerprint mismatch: user agent OS differs from the platform")
    if re.search("apple", gpu, re.I) and "mac" not in platform:
        reasons.append("Fingerprint mismatch: Apple GPU on a non-Mac platform")
    if str(s.get("language") or "").startswith("en-US") and not re.search(r"^America/|^US/|^Pacific/Honolulu", str(s.get("timezone") or "")):
        reasons.append(f"Location mismatch: time zone {s.get('timezone')} for language {s.get('language')}")
    return {"verdict": "flagged" if reasons else "passed", "score": min(100, len(reasons) * 35), "reasons": reasons}


registrations: list[dict[str, Any]] = []
claims: list[dict[str, Any]] = []


def _id(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(3).upper()}"


async def _body(request: web.Request) -> dict[str, Any]:
    try:
        data = await request.json()
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 - malformed JSON is treated as empty
        return {}


async def register(request: web.Request) -> web.Response:
    b = await _body(request)
    errors: dict[str, str] = {}
    if not str(b.get("fullName") or "").strip():
        errors["fullName"] = "Full name is required"
    if not EMAIL_RE.search(str(b.get("email") or "")):
        errors["email"] = "Enter a valid work email"
    if not b.get("terms"):
        errors["terms"] = "You must accept the terms"
    if errors:
        return web.json_response({"errors": errors}, status=422)
    confirmation = _id("REG")
    registrations.append({**b, "confirmation": confirmation})
    return web.json_response({"confirmation": confirmation}, status=201)


async def create_claim(request: web.Request) -> web.Response:
    b = await _body(request)
    claim_id = _id("EXP")
    claims.append({**b, "claimId": claim_id, "status": "Pending approval"})
    return web.json_response({"claimId": claim_id, "status": "Pending approval"}, status=201)


async def screen(request: web.Request) -> web.Response:
    return web.json_response(screen_respondent(await _body(request)))


async def static(request: web.Request) -> web.StreamResponse:
    rel = request.match_info.get("path") or "index.html"
    file = (PUBLIC_DIR / rel).resolve()
    if not file.is_relative_to(PUBLIC_DIR) or not file.is_file():
        return web.Response(status=404, text="Not found")
    return web.FileResponse(file)


async def list_claims(_: web.Request) -> web.Response:
    return web.json_response(claims)


async def list_registrations(_: web.Request) -> web.Response:
    return web.json_response(registrations)


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_post("/api/register", register)
    app.router.add_post("/api/claims", create_claim)
    app.router.add_post("/api/screen", screen)
    app.router.add_get("/api/claims", list_claims)
    app.router.add_get("/api/registrations", list_registrations)
    app.router.add_get("/{path:.*}", static)
    return app


async def start_demo_server(port: int = DEMO_PORT) -> web.AppRunner:
    """Starts the demo site on the running event loop. Stop it with `await runner.cleanup()`."""
    runner = web.AppRunner(create_app(), access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    return runner


async def is_demo_server_up() -> bool:
    def probe() -> bool:
        try:
            with urllib.request.urlopen(DEMO_ORIGIN, timeout=1) as res:
                return 200 <= res.status < 300
        except Exception:  # noqa: BLE001
            return False

    return await asyncio.to_thread(probe)


async def _serve_forever() -> None:
    await start_demo_server()
    print(f"Demo site running at {DEMO_ORIGIN}")
    await asyncio.Event().wait()


if __name__ == "__main__":
    try:
        asyncio.run(_serve_forever())
    except KeyboardInterrupt:
        pass
