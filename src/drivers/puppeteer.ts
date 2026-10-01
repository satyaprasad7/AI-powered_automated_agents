import puppeteer, { type Browser, type KeyInput, type Page } from "puppeteer";
import type { BrowserDriver, ElementInfo, LaunchOptions, PageSnapshot } from "./types.js";
import {
  describeScript,
  extractTableScript,
  getTextScript,
  refSelector,
  resolveOptionScript,
  auditScript,
  snapshotScript,
  textPresentScript,
} from "./page-scripts.js";
import { fingerprintScript, type Fingerprint } from "../detection/fingerprint.js";

export class PuppeteerDriver implements BrowserDriver {
  readonly name = "puppeteer" as const;
  private browser?: Browser;
  private _page?: Page;
  private timeout = 10_000;
  private status: number | null = null;
  private browserVersion = "";

  private get page(): Page {
    if (!this._page) throw new Error("Browser not launched");
    return this._page;
  }

  async launch(options: LaunchOptions): Promise<void> {
    this.timeout = options.actionTimeoutMs ?? this.timeout;
    const profile = options.profile;
    this.browser = await puppeteer.launch({
      headless: options.headless,
      defaultViewport: profile?.viewport ?? options.viewport ?? { width: 1280, height: 900 },
      args: profile?.locale ? [`--lang=${profile.locale}`] : [],
    });
    this.browserVersion = (await this.browser.version()).replace(/^(Headless)?Chrome\//, "");
    this._page = await this.browser.newPage();
    this._page.setDefaultTimeout(this.timeout);
    const page = this._page;
    if (profile?.userAgent) await page.setUserAgent({ userAgent: profile.userAgent });
    if (profile?.timezoneId) await page.emulateTimezone(profile.timezoneId);
    if (profile?.locale) await page.setExtraHTTPHeaders({ "Accept-Language": profile.locale });
    if (profile?.initScript) await page.evaluateOnNewDocument(profile.initScript);
    page.on("response", (r) => {
      if (r.request().isNavigationRequest() && r.frame() === page.mainFrame()) this.status = r.status();
    });
  }

  browserInfo(): string | undefined {
    return this.browser && `Chrome for Testing ${this.browserVersion}`;
  }

  async goto(url: string): Promise<void> {
    await this.page.goto(url, { waitUntil: "domcontentloaded" });
  }

  url(): string {
    return this.page.url();
  }

  async snapshot(): Promise<PageSnapshot> {
    return (await this.page.evaluate(snapshotScript())) as PageSnapshot;
  }

  async describe(ref: string): Promise<ElementInfo | null> {
    refSelector(ref);
    return (await this.page.evaluate(describeScript(ref))) as ElementInfo | null;
  }

  async click(ref: string): Promise<void> {
    await this.page.locator(refSelector(ref)).click();
    await this.settle();
  }

  async fill(ref: string, value: string): Promise<void> {
    await this.page.locator(refSelector(ref)).fill(value);
  }

  async select(ref: string, valueOrLabel: string): Promise<string[]> {
    const value = (await this.page.evaluate(resolveOptionScript(ref, valueOrLabel))) as string | null;
    if (value === null) throw new Error(`No option matching "${valueOrLabel}"`);
    const selected = await this.page.select(refSelector(ref), value);
    await this.settle();
    return selected;
  }

  async press(key: string): Promise<void> {
    await this.page.keyboard.press(key as KeyInput);
    await this.settle();
  }

  async getText(ref: string): Promise<string> {
    if (ref !== "page") refSelector(ref);
    return (await this.page.evaluate(getTextScript(ref))) as string;
  }

  async extractTable(ref: string): Promise<string[][]> {
    if (ref !== "page") refSelector(ref);
    return (await this.page.evaluate(extractTableScript(ref))) as string[][];
  }

  async waitForText(text: string, timeoutMs: number): Promise<boolean> {
    try {
      await this.page.waitForFunction(textPresentScript(text), { timeout: timeoutMs });
      return true;
    } catch {
      return false;
    }
  }

  async audit(): Promise<Record<string, unknown>> {
    return (await this.page.evaluate(auditScript())) as Record<string, unknown>;
  }

  async fingerprint(): Promise<Fingerprint> {
    return (await this.page.evaluate(fingerprintScript())) as Fingerprint;
  }

  lastStatus(): number | null {
    return this.status;
  }

  async screenshot(path?: string): Promise<Buffer> {
    const data = await this.page.screenshot({ path: path as `${string}.jpeg` | undefined, type: "jpeg", quality: 60 });
    return Buffer.from(data);
  }

  async close(): Promise<void> {
    await this.browser?.close();
  }

  /** Give navigations and XHR triggered by an action a moment to land. */
  private async settle(): Promise<void> {
    await this.page.waitForNetworkIdle({ idleTime: 300, timeout: 3_000 }).catch(() => {});
  }
}
