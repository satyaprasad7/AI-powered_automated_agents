import { existsSync, statSync } from "node:fs";
import { basename, isAbsolute, join, resolve } from "node:path";

/**
 * Finds the Chromium-based browsers installed on this machine, for the
 * "standard browser" engine. Playwright can drive any of them because they
 * share Chrome's DevTools protocol; Firefox and Safari are not supported.
 */

export interface InstalledBrowser {
  /** Stable id used in task files, the CLI and the UI ("chrome", "msedge", ...). */
  id: string;
  label: string;
  executablePath: string;
}

interface KnownBrowser {
  id: string;
  label: string;
  /** Candidate executable paths per platform, most common first. */
  paths: Partial<Record<NodeJS.Platform, string[]>>;
}

const env = (name: string) => process.env[name] ?? "";
/** Joins under each Windows install root that is set. */
const win = (...rel: string[]) =>
  [env("LOCALAPPDATA"), env("PROGRAMFILES"), env("PROGRAMFILES(X86)")].filter(Boolean).map((root) => join(root, ...rel));
const mac = (app: string) => [`/Applications/${app}.app/Contents/MacOS/${app}`];

/** Detection order is also the "auto" preference order. */
const KNOWN: KnownBrowser[] = [
  {
    id: "chrome",
    label: "Google Chrome",
    paths: { win32: win("Google", "Chrome", "Application", "chrome.exe"), darwin: mac("Google Chrome"), linux: ["/opt/google/chrome/chrome"] },
  },
  {
    id: "msedge",
    label: "Microsoft Edge",
    paths: { win32: win("Microsoft", "Edge", "Application", "msedge.exe"), darwin: mac("Microsoft Edge"), linux: ["/opt/microsoft/msedge/msedge"] },
  },
  {
    id: "chrome-beta",
    label: "Google Chrome Beta",
    paths: { win32: win("Google", "Chrome Beta", "Application", "chrome.exe"), darwin: mac("Google Chrome Beta"), linux: ["/opt/google/chrome-beta/chrome"] },
  },
  {
    id: "msedge-beta",
    label: "Microsoft Edge Beta",
    paths: { win32: win("Microsoft", "Edge Beta", "Application", "msedge.exe"), darwin: mac("Microsoft Edge Beta"), linux: ["/opt/microsoft/msedge-beta/msedge"] },
  },
  {
    id: "brave",
    label: "Brave",
    paths: {
      win32: win("BraveSoftware", "Brave-Browser", "Application", "brave.exe"),
      darwin: mac("Brave Browser"),
      linux: ["/opt/brave.com/brave/brave", "/usr/bin/brave-browser"],
    },
  },
  {
    id: "vivaldi",
    label: "Vivaldi",
    paths: { win32: win("Vivaldi", "Application", "vivaldi.exe"), darwin: mac("Vivaldi"), linux: ["/opt/vivaldi/vivaldi"] },
  },
];

export const KNOWN_BROWSER_IDS = KNOWN.map((b) => b.id);

const isFile = (p: string) => existsSync(p) && statSync(p).isFile();

export function findInstalledBrowsers(): InstalledBrowser[] {
  const found: InstalledBrowser[] = [];
  for (const b of KNOWN) {
    const path = (b.paths[process.platform] ?? []).find(isFile);
    if (path) found.push({ id: b.id, label: b.label, executablePath: path });
  }
  return found;
}

/**
 * Resolves a browser choice: "auto" (or empty) picks the first installed one,
 * a known id picks that browser, and an absolute path is used as-is.
 */
export function resolveBrowser(choice: string | undefined): InstalledBrowser {
  const want = choice?.trim() || "auto";
  if (isAbsolute(want)) {
    if (!isFile(want)) throw new Error(`Browser executable not found: ${want}`);
    const same = findInstalledBrowsers().find((b) => resolve(b.executablePath).toLowerCase() === resolve(want).toLowerCase());
    if (same) return same;
    return { id: "custom", label: basename(want).replace(/\.exe$/i, ""), executablePath: want };
  }
  const installed = findInstalledBrowsers();
  if (want === "auto") {
    if (!installed[0]) {
      throw new Error("No installed Google Chrome or Microsoft Edge was found for the Standard browser engine. Install one, or pick Playwright / Puppeteer.");
    }
    return installed[0];
  }
  const known = KNOWN.find((b) => b.id === want);
  if (!known) throw new Error(`Unknown browser "${want}". Use auto, ${KNOWN_BROWSER_IDS.join(", ")}, or a full path to the executable.`);
  const match = installed.find((b) => b.id === want);
  if (!match) throw new Error(`${known.label} is not installed on this machine. Installed: ${installed.map((b) => b.id).join(", ") || "none"}.`);
  return match;
}
