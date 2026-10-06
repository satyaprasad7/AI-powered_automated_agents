"""Browser profiles for testing a site's bot / fraud detection.

Each profile reproduces the tell-tale signals of one kind of suspicious client, so you
can check that YOUR site's defenses flag it. They are meant to be caught: automation
flags such as navigator.webdriver are never hidden, and the spoofed values are
deliberately inconsistent, the way real anti-detect browsers and virtual machines leak.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Literal

ProfileId = Literal["default", "vm", "anti-detect"]


@dataclass(frozen=True)
class BrowserProfile:
    id: ProfileId
    label: str
    description: str
    # Signals this profile presents, i.e. what a good detector should flag.
    simulated_signals: list[str] = field(default_factory=list)
    user_agent: str | None = None
    locale: str | None = None
    timezone_id: str | None = None
    viewport: dict[str, int] | None = None
    # Runs in every document before the site's own scripts.
    init_script: str | None = None


def _webgl_override(vendor: str, renderer: str) -> str:
    return (
        """
  (() => {
    const VENDOR = 0x9245, RENDERER = 0x9246;
    const patch = (proto) => {
      if (!proto) return;
      const getParameter = proto.getParameter;
      proto.getParameter = function (p) {
        if (p === VENDOR) return __VENDOR__;
        if (p === RENDERER) return __RENDERER__;
        return getParameter.call(this, p);
      };
      const getExtension = proto.getExtension;
      proto.getExtension = function (name) {
        const ext = getExtension.call(this, name);
        if (!ext && name === 'WEBGL_debug_renderer_info') return { UNMASKED_VENDOR_WEBGL: VENDOR, UNMASKED_RENDERER_WEBGL: RENDERER };
        return ext;
      };
    };
    patch(window.WebGLRenderingContext && WebGLRenderingContext.prototype);
    patch(window.WebGL2RenderingContext && WebGL2RenderingContext.prototype);
  })();"""
        .replace("__VENDOR__", json.dumps(vendor))
        .replace("__RENDERER__", json.dumps(renderer))
    )


def _getter(proto: str, prop: str, value: object) -> str:
    return f"Object.defineProperty({proto}.prototype, {json.dumps(prop)}, {{ get: () => {json.dumps(value)}, configurable: true }});"


PROFILES: dict[str, BrowserProfile] = {
    "default": BrowserProfile(
        id="default",
        label="Standard automation",
        description="An unmodified automated Chromium, like a basic bot or scripted survey filler.",
        simulated_signals=["navigator.webdriver = true", "HeadlessChrome in the user agent (headless mode)"],
    ),
    "vm": BrowserProfile(
        id="vm",
        label="Virtual machine",
        description="Automated Chromium presenting the hardware fingerprint of a typical VMware guest (common in survey and ad fraud farms).",
        simulated_signals=[
            "navigator.webdriver = true",
            "WebGL renderer 'VMware SVGA 3D' (virtual GPU)",
            "2 CPU cores and 2 GB device memory",
            "1024x768 screen",
        ],
        viewport={"width": 1024, "height": 700},
        init_script="\n".join(
            [
                _webgl_override("VMware, Inc.", "VMware SVGA 3D"),
                _getter("Navigator", "hardwareConcurrency", 2),
                _getter("Navigator", "deviceMemory", 2),
                _getter("Screen", "width", 1024),
                _getter("Screen", "height", 768),
                _getter("Screen", "availWidth", 1024),
                _getter("Screen", "availHeight", 728),
            ]
        ),
    ),
    "anti-detect": BrowserProfile(
        id="anti-detect",
        label="Anti-detect browser",
        description=(
            "Automated Chromium with a spoofed fingerprint like an anti-detect browser profile (Multilogin, GoLogin, etc.): it claims "
            "to be Chrome on macOS with an Apple GPU and a Tokyo time zone, while navigator.platform and client hints leak Windows."
        ),
        simulated_signals=[
            "navigator.webdriver = true",
            "User agent says macOS but navigator.platform / client hints say Windows",
            "Apple GPU reported on a non-Mac platform",
            "Time zone (Asia/Tokyo) inconsistent with the en-US locale",
        ],
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36",
        locale="en-US",
        timezone_id="Asia/Tokyo",
        # Pin the leaked platform explicitly: Playwright would otherwise align navigator.platform with the spoofed UA.
        init_script="\n".join(
            [
                _webgl_override("Apple Inc.", "Apple M1"),
                _getter("Navigator", "hardwareConcurrency", 8),
                _getter("Navigator", "platform", "Win32"),
                """(() => {
        const d = Object.getOwnPropertyDescriptor(Navigator.prototype, 'userAgentData');
        if (!d || !d.get) return;
        Object.defineProperty(Navigator.prototype, 'userAgentData', {
          configurable: true,
          get() {
            const real = d.get.call(this);
            return real && new Proxy(real, { get: (t, k) => (k === 'platform' ? 'Windows' : typeof t[k] === 'function' ? t[k].bind(t) : t[k]) });
          },
        });
      })();""",
            ]
        ),
    ),
}
