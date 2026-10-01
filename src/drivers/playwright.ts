import { chromium, type Browser, type Page } from "playwright";
import type { BrowserDriver, DriverName, ElementInfo, LaunchOptions, PageSnapshot } from "./types.js";
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
import { resolveBrowser } from "./installed.js";

/**
 * Launches Chromium, falling back when the default build is missing or
 * unreadable (e.g. a Playwright upgrade without `playwright install`, or
 * antivirus holding the file): headless shell -> full Chromium -> installed Chrome.
 */
async function launchChromium(headless: boolean): Promise<Browser> {
  const attempts: Array<{ channel?: string }> = [{}, { channel: "chromium" }, { channel: "chrome" }];
  let firstError: unknown;
  for (const attempt of attempts) {
    try {
      return await chromium.launch({ headless, ...attempt });
    } catch (err) {
      firstError ??= err;
      if (!/Executable doesn't exist|is not found|ENOENT/i.test(String(err))) throw err;
    }
  }
  throw new Error(
    "Playwright could not find a Chromium browser. Run `npx playwright install chromium` in the project folder, " +
      `or pick the Puppeteer engine. (${String(firstError).split("\n")[0]})`,
  );
}

export class PlaywrightDriver implements BrowserDriver {
  readonly name: DriverName = "playwright";
  private browser?: Browser;
  protected browserLabel = "Playwright Chromium";
  private _page?: Page;
  private timeout = 10_000;
  private status: number | null = null;
  private inflight = 0;
  private lastNetworkActivity = 0;

  private get page(): Page {
    if (!this._page) throw new Error("Browser not launched");
    return this._page;
  }

  async launch(options: LaunchOptions): Promise<void> {
    this.timeout = options.actionTimeoutMs ?? this.timeout;
    this.browser = await this.launchBrowser(options);
    const profile = options.profile;
    const context = await this.browser.newContext({
      viewport: profile?.viewport ?? options.viewport ?? { width: 1280, height: 900 },
      userAgent: profile?.userAgent,
      locale: profile?.locale,
      timezoneId: profile?.timezoneId,
    });
    if (profile?.initScript) await context.addInitScript(profile.initScript);
    this._page = await context.newPage();
    this._page.setDefaultTimeout(this.timeout);
    const page = this._page;
    page.on("response", (r) => {
      if (r.request().isNavigationRequest() && r.frame() === page.mainFrame()) this.status = r.status();
    });
    const done = () => {
      this.inflight = Math.max(0, this.inflight - 1);
      this.lastNetworkActivity = Date.now();
    };
    page.on("request", () => {
      this.inflight++;
      this.lastNetworkActivity = Date.now();
    });
    page.on("requestfinished", done);
    page.on("requestfailed", done);
  }

  protected launchBrowser(options: LaunchOptions): Promise<Browser> {
    return launchChromium(options.headless);
  }

  browserInfo(): string | undefined {
    return this.browser && `${this.browserLabel} ${this.browser.version()}`;
  }

  async goto(url: string): Promise<void> {
    await this.page.goto(url, { waitUntil: "domcontentloaded" });
  }

  url(): string {
    return this.page.url();
  }

  async snapshot(): Promise<PageSnapshot> {
    return this.page.evaluate(snapshotScript()) as Promise<PageSnapshot>;
  }

  async describe(ref: string): Promise<ElementInfo | null> {
    refSelector(ref);
    return this.page.evaluate(describeScript(ref)) as Promise<ElementInfo | null>;
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
    const selected = await this.page.locator(refSelector(ref)).selectOption(value);
    await this.settle();
    return selected;
  }

  async press(key: string): Promise<void> {
    await this.page.keyboard.press(key);
    await this.settle();
  }

  async getText(ref: string): Promise<string> {
    if (ref !== "page") refSelector(ref);
    return this.page.evaluate(getTextScript(ref)) as Promise<string>;
  }

  async extractTable(ref: string): Promise<string[][]> {
    if (ref !== "page") refSelector(ref);
    return this.page.evaluate(extractTableScript(ref)) as Promise<string[][]>;
  }

  async waitForText(text: string, timeoutMs: number): Promise<boolean> {
    try {
      await this.page.waitForFunction(textPresentScript(text), undefined, { timeout: timeoutMs });
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
    return this.page.screenshot({ path, type: "jpeg", quality: 60 });
  }

  async close(): Promise<void> {
    await this.browser?.close();
  }

  /**
   * Give navigations and XHR triggered by an action a moment to land: wait until
   * no request has been in flight for 300 ms (max 3 s). Playwright's own
   * "networkidle" load state resolves immediately once a page has loaded, so it
   * misses requests started by a click (e.g. a fetch-based form submit).
   */
  private async settle(): Promise<void> {
    await this.page.waitForLoadState("domcontentloaded").catch(() => {});
    this.lastNetworkActivity = Math.max(this.lastNetworkActivity, Date.now());
    const deadline = Date.now() + 3_000;
    while (Date.now() < deadline) {
      if (this.inflight === 0 && Date.now() - this.lastNetworkActivity >= 300) return;
      await new Promise((r) => setTimeout(r, 50));
    }
  }
}

/**
 * The "standard browser" engine: a browser installed on this machine (Chrome,
 * Edge, Brave... or any Chromium-based executable), driven through Playwright
 * with a fresh, temporary profile. It is still an automated
 * browser (navigator.webdriver stays true); what changes is the binary, so
 * sites see a regular branded build instead of a test Chromium.
 */
export class StandardBrowserDriver extends PlaywrightDriver {
  override readonly name: DriverName = "standard";

  protected override launchBrowser(options: LaunchOptions): Promise<Browser> {
    const target = resolveBrowser(options.browser);
    this.browserLabel = target.label;
    return chromium.launch({ headless: options.headless, executablePath: target.executablePath });
  }
}
