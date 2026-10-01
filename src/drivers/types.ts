import type { BrowserProfile } from "../detection/profiles.js";
import type { Fingerprint } from "../detection/fingerprint.js";

export const DRIVER_NAMES = ["playwright", "puppeteer", "standard"] as const;
/** playwright / puppeteer use their bundled test browsers; standard drives the installed Chrome or Edge. */
export type DriverName = (typeof DRIVER_NAMES)[number];

export const DRIVER_LABELS: Record<DriverName, string> = {
  playwright: "Playwright",
  puppeteer: "Puppeteer",
  standard: "Standard browser",
};

export interface LaunchOptions {
  headless: boolean;
  /** Detection-test profile (user agent, locale, time zone, injected fingerprint). */
  profile?: BrowserProfile;
  viewport?: { width: number; height: number };
  /**
   * Standard browser engine only: "auto" (default), an installed browser id
   * ("chrome", "msedge", ...) or a full path to a Chromium-based executable.
   */
  browser?: string;
  /** Per-action timeout in ms (clicks, fills, waits). */
  actionTimeoutMs?: number;
}

/** One interactive element on the page, addressable by `ref`. */
export interface ElementInfo {
  ref: string;
  tag: string;
  role?: string;
  type?: string;
  name: string;
  value?: string;
  checked?: boolean;
  disabled?: boolean;
  required?: boolean;
  invalid?: string;
  href?: string;
  options?: string[];
}

export interface PageSnapshot {
  url: string;
  title: string;
  elements: ElementInfo[];
  /** Visible page text, truncated. */
  text: string;
  truncated: boolean;
}

/**
 * The browser surface the agent drives. Playwright, Puppeteer and the
 * standard (installed) browser each implement it, so tasks and the agent loop are driver-agnostic.
 * Elements are addressed by the `ref` values returned from `snapshot()`.
 */
export interface BrowserDriver {
  readonly name: DriverName;
  launch(options: LaunchOptions): Promise<void>;
  /** The browser that actually ran, e.g. "Microsoft Edge 141.0.3537.57", once launched. */
  browserInfo(): string | undefined;
  goto(url: string): Promise<void>;
  url(): string;
  snapshot(): Promise<PageSnapshot>;
  describe(ref: string): Promise<ElementInfo | null>;
  click(ref: string): Promise<void>;
  fill(ref: string, value: string): Promise<void>;
  /** Selects by option value or visible label; returns the selected values. */
  select(ref: string, valueOrLabel: string): Promise<string[]>;
  press(key: string): Promise<void>;
  getText(ref: string): Promise<string>;
  extractTable(ref: string): Promise<string[][]>;
  waitForText(text: string, timeoutMs: number): Promise<boolean>;
  /** Accessibility/content facts about the current page (see auditScript). */
  audit(): Promise<Record<string, unknown>>;
  /** Fingerprint signals the current page can read from this browser. */
  fingerprint(): Promise<Fingerprint>;
  /** HTTP status of the last main-frame navigation, if known. */
  lastStatus(): number | null;
  screenshot(path?: string): Promise<Buffer>;
  close(): Promise<void>;
}
