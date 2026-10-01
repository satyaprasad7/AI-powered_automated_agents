import type { BrowserDriver, DriverName } from "./types.js";

export { DRIVER_LABELS, DRIVER_NAMES } from "./types.js";
export type { BrowserDriver, DriverName, ElementInfo, LaunchOptions, PageSnapshot } from "./types.js";

/** Loads only the selected driver's library. */
export async function createDriver(name: DriverName): Promise<BrowserDriver> {
  switch (name) {
    case "playwright": {
      const { PlaywrightDriver } = await import("./playwright.js");
      return new PlaywrightDriver();
    }
    case "puppeteer": {
      const { PuppeteerDriver } = await import("./puppeteer.js");
      return new PuppeteerDriver();
    }
    case "standard": {
      const { StandardBrowserDriver } = await import("./playwright.js");
      return new StandardBrowserDriver();
    }
  }
}
