/**
 * Collects the fingerprint signals a site can read from the browser, and
 * derives the red flags a bot / fraud detector would be expected to raise.
 */

export interface Fingerprint {
  webdriver: boolean;
  userAgent: string;
  platform: string;
  uaDataPlatform: string | null;
  languages: string[];
  timezone: string;
  hardwareConcurrency: number;
  deviceMemory: number | null;
  screen: { width: number; height: number; colorDepth: number };
  webgl: { vendor: string | null; renderer: string | null };
  pluginCount: number;
}

export function fingerprintScript(): string {
  return `(() => {
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
  })()`;
}

const VM_GPU = /vmware|virtualbox|vbox|parallels|hyper-v|qemu|virgl|llvmpipe|swiftshader|microsoft basic render/i;

function uaOs(ua: string): "mac" | "windows" | "linux" | "android" | "ios" | "unknown" {
  if (/iPhone|iPad/.test(ua)) return "ios";
  if (/Android/.test(ua)) return "android";
  if (/Macintosh|Mac OS X/.test(ua)) return "mac";
  if (/Windows/.test(ua)) return "windows";
  if (/Linux|X11/.test(ua)) return "linux";
  return "unknown";
}

function platformOs(fp: Fingerprint): string {
  const p = `${fp.uaDataPlatform ?? ""} ${fp.platform}`.toLowerCase();
  if (p.includes("mac")) return "mac";
  if (p.includes("win")) return "windows";
  if (p.includes("linux")) return "linux";
  return "unknown";
}

/** Rough time-zone -> region check for the first navigator language. */
function localeTimezoneMismatch(fp: Fingerprint): boolean {
  const region = (fp.languages[0] ?? "").split("-")[1]?.toUpperCase();
  if (!region) return false;
  const zone = fp.timezone;
  const expected: Record<string, RegExp> = {
    US: /^America\/|^US\/|^Pacific\/Honolulu/,
    GB: /^Europe\/London/,
    IN: /^Asia\/(Kolkata|Calcutta)/,
    JP: /^Asia\/Tokyo/,
    DE: /^Europe\/Berlin/,
    AU: /^Australia\//,
  };
  return expected[region] ? !expected[region].test(zone) : false;
}

export function redFlags(fp: Fingerprint): string[] {
  const flags: string[] = [];
  if (fp.webdriver) flags.push("Automation: navigator.webdriver is true");
  if (/HeadlessChrome/.test(fp.userAgent)) flags.push("Automation: headless Chrome user agent");
  if (fp.webgl.renderer && VM_GPU.test(`${fp.webgl.vendor} ${fp.webgl.renderer}`)) {
    flags.push(`Virtual machine / software GPU: ${fp.webgl.renderer}`);
  }
  if (fp.hardwareConcurrency <= 2) flags.push(`Virtual machine hint: only ${fp.hardwareConcurrency} CPU cores`);
  if ([`${fp.screen.width}x${fp.screen.height}`].some((s) => ["1024x768", "800x600"].includes(s))) {
    flags.push(`Virtual machine hint: default VM screen size ${fp.screen.width}x${fp.screen.height}`);
  }
  const claimed = uaOs(fp.userAgent);
  const actual = platformOs(fp);
  if (claimed !== "unknown" && actual !== "unknown" && claimed !== actual) {
    flags.push(`Fingerprint inconsistency: user agent claims ${claimed} but the platform is ${actual}`);
  }
  if (/apple/i.test(`${fp.webgl.vendor} ${fp.webgl.renderer}`) && actual !== "mac") {
    flags.push(`Fingerprint inconsistency: Apple GPU reported on a ${actual} platform`);
  }
  if (localeTimezoneMismatch(fp)) flags.push(`Fingerprint inconsistency: time zone ${fp.timezone} vs language ${fp.languages[0]}`);
  return flags;
}
