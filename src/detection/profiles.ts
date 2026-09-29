/**
 * Browser profiles for testing a site's bot / fraud detection.
 *
 * Each profile reproduces the tell-tale signals of one kind of suspicious
 * client, so you can check that YOUR site's defenses flag it. They are meant
 * to be caught: automation flags such as navigator.webdriver are never hidden,
 * and the spoofed values are deliberately inconsistent, the way real
 * anti-detect browsers and virtual machines leak.
 */

export type ProfileId = "default" | "vm" | "anti-detect";

export interface BrowserProfile {
  id: ProfileId;
  label: string;
  description: string;
  /** Signals this profile presents, i.e. what a good detector should flag. */
  simulatedSignals: string[];
  userAgent?: string;
  locale?: string;
  timezoneId?: string;
  viewport?: { width: number; height: number };
  /** Runs in every document before the site's own scripts. */
  initScript?: string;
}

const webglOverride = (vendor: string, renderer: string) => `
  (() => {
    const VENDOR = 0x9245, RENDERER = 0x9246;
    const patch = (proto) => {
      if (!proto) return;
      const getParameter = proto.getParameter;
      proto.getParameter = function (p) {
        if (p === VENDOR) return ${JSON.stringify(vendor)};
        if (p === RENDERER) return ${JSON.stringify(renderer)};
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
  })();`;

const getter = (proto: string, prop: string, value: unknown) =>
  `Object.defineProperty(${proto}.prototype, ${JSON.stringify(prop)}, { get: () => ${JSON.stringify(value)}, configurable: true });`;

export const PROFILES: Record<ProfileId, BrowserProfile> = {
  default: {
    id: "default",
    label: "Standard automation",
    description: "An unmodified automated Chromium, like a basic bot or scripted survey filler.",
    simulatedSignals: ["navigator.webdriver = true", "HeadlessChrome in the user agent (headless mode)"],
  },
  vm: {
    id: "vm",
    label: "Virtual machine",
    description: "Automated Chromium presenting the hardware fingerprint of a typical VMware guest (common in survey and ad fraud farms).",
    simulatedSignals: [
      "navigator.webdriver = true",
      "WebGL renderer 'VMware SVGA 3D' (virtual GPU)",
      "2 CPU cores and 2 GB device memory",
      "1024x768 screen",
    ],
    viewport: { width: 1024, height: 700 },
    initScript: [
      webglOverride("VMware, Inc.", "VMware SVGA 3D"),
      getter("Navigator", "hardwareConcurrency", 2),
      getter("Navigator", "deviceMemory", 2),
      getter("Screen", "width", 1024),
      getter("Screen", "height", 768),
      getter("Screen", "availWidth", 1024),
      getter("Screen", "availHeight", 728),
    ].join("\n"),
  },
  "anti-detect": {
    id: "anti-detect",
    label: "Anti-detect browser",
    description:
      "Automated Chromium with a spoofed fingerprint like an anti-detect browser profile (Multilogin, GoLogin, etc.): it claims to be Chrome on macOS with an Apple GPU and a Tokyo time zone, while navigator.platform and client hints leak Windows.",
    simulatedSignals: [
      "navigator.webdriver = true",
      "User agent says macOS but navigator.platform / client hints say Windows",
      "Apple GPU reported on a non-Mac platform",
      "Time zone (Asia/Tokyo) inconsistent with the en-US locale",
    ],
    userAgent:
      "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36",
    locale: "en-US",
    timezoneId: "Asia/Tokyo",
    // Pin the leaked platform explicitly: Playwright would otherwise align navigator.platform with the spoofed UA.
    initScript: [
      webglOverride("Apple Inc.", "Apple M1"),
      getter("Navigator", "hardwareConcurrency", 8),
      getter("Navigator", "platform", "Win32"),
      `(() => {
        const d = Object.getOwnPropertyDescriptor(Navigator.prototype, 'userAgentData');
        if (!d || !d.get) return;
        Object.defineProperty(Navigator.prototype, 'userAgentData', {
          configurable: true,
          get() {
            const real = d.get.call(this);
            return real && new Proxy(real, { get: (t, k) => (k === 'platform' ? 'Windows' : typeof t[k] === 'function' ? t[k].bind(t) : t[k]) });
          },
        });
      })();`,
    ].join("\n"),
  },
};
