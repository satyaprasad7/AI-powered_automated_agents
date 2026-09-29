/**
 * Driver smoke test - no LLM, no API key. Exercises every BrowserDriver method
 * against the demo site on both Playwright and Puppeteer.
 *
 *   npm run smoke
 */
import { mkdir } from "node:fs/promises";
import { createDriver, type BrowserDriver, type DriverName } from "./drivers/index.js";
import { formatSnapshot } from "./agent/tools.js";
import { DEMO_ORIGIN, isDemoServerUp, startDemoServer } from "../demo-site/server.js";
import { PROFILES, type ProfileId } from "./detection/profiles.js";
import { redFlags } from "./detection/fingerprint.js";

function assert(cond: unknown, message: string): asserts cond {
  if (!cond) throw new Error(`Assertion failed: ${message}`);
}

async function refFor(driver: BrowserDriver, name: string): Promise<string> {
  const snap = await driver.snapshot();
  const el = snap.elements.find((e) => e.name.toLowerCase().includes(name.toLowerCase()));
  assert(el, `element "${name}" on ${snap.url}\n${formatSnapshot(snap)}`);
  return el.ref;
}

async function smoke(name: DriverName): Promise<void> {
  const driver = await createDriver(name);
  await driver.launch({ headless: true });
  try {
    // Form submission
    await driver.goto(`${DEMO_ORIGIN}/register.html`);
    await driver.fill(await refFor(driver, "Full name"), "Jane Doe");
    await driver.fill(await refFor(driver, "Work email"), "jane.doe@acme.test");
    const selected = await driver.select(await refFor(driver, "Plan"), "Enterprise");
    assert(selected[0] === "enterprise", `select by label returned ${selected}`);
    await driver.click(await refFor(driver, "terms"));
    await driver.click(await refFor(driver, "Create account"));
    // No explicit wait: click() must settle until the fetch-driven result has rendered.
    assert((await driver.getText("page")).includes("Confirmation number"), "registration confirmation shown right after click");

    // Table extraction + pagination
    await driver.goto(`${DEMO_ORIGIN}/products.html`);
    const page1 = await driver.extractTable("page");
    assert(page1.length === 6 && page1[0][0] === "SKU", `table rows: ${JSON.stringify(page1)}`);
    await driver.click(await refFor(driver, "Next page"));
    const page2 = await driver.extractTable("page");
    assert(page2[1][0] === "AC-105", `page 2 first SKU: ${page2[1]?.[0]}`);

    // Multi-step workflow with a date input and a validation message
    await driver.goto(`${DEMO_ORIGIN}/expenses.html`);
    await driver.fill(await refFor(driver, "Employee ID"), "bad");
    await driver.click(await refFor(driver, "Continue"));
    const invalid = (await driver.snapshot()).elements.find((e) => e.invalid);
    assert(invalid?.invalid?.includes("E-12345"), "validation message surfaced in snapshot");
    await driver.fill(await refFor(driver, "Employee ID"), "E-10234");
    await driver.select(await refFor(driver, "Cost center"), "CC-100");
    await driver.click(await refFor(driver, "Continue"));
    await driver.fill(await refFor(driver, "Expense date"), "2026-09-15");
    await driver.fill(await refFor(driver, "Amount"), "42.50");
    await driver.click(await refFor(driver, "Review claim"));
    assert((await driver.getText("page")).includes("$42.50"), "review shows amount");
    await driver.click(await refFor(driver, "Submit claim"));
    assert((await driver.getText("page")).includes("Claim ID"), "claim submitted");
    assert(await driver.waitForText("Claim ID", 1000), "waitForText finds present text");

    await mkdir("runs", { recursive: true });
    const shot = await driver.screenshot(`runs/smoke-${name}.jpg`);
    assert(shot.length > 1000, "screenshot captured");
    console.log(`✓ ${name}: form, table, workflow, screenshot OK`);
  } finally {
    await driver.close();
  }
}

/** Each detection profile presents its signals, and the demo survey screens them (with its seeded VMware gap). */
async function smokeProfiles(name: DriverName): Promise<void> {
  const expect: Record<ProfileId, { flags: RegExp[]; siteFlags: RegExp[]; siteMisses: RegExp[] }> = {
    default: { flags: [/navigator\.webdriver/], siteFlags: [/Automation detected/], siteMisses: [] },
    vm: { flags: [/VMware SVGA 3D/, /2 CPU cores/, /1024x768/], siteFlags: [/Automation detected/], siteMisses: [/Virtual machine GPU/] },
    "anti-detect": {
      flags: [/claims mac but the platform is windows/, /Apple GPU/, /Asia\/Tokyo/],
      siteFlags: [/user agent OS differs/, /Apple GPU/, /Location mismatch/],
      siteMisses: [],
    },
  };
  for (const id of Object.keys(PROFILES) as ProfileId[]) {
    const driver = await createDriver(name);
    await driver.launch({ headless: true, profile: PROFILES[id] });
    try {
      await driver.goto(`${DEMO_ORIGIN}/survey.html`);
      const flags = redFlags(await driver.fingerprint()).join("\n");
      for (const re of expect[id].flags) assert(re.test(flags), `${id}: fingerprint red flag ${re}\n${flags}`);
      assert(await driver.waitForText("quality check:", 5000), `${id}: screening result shown`);
      const page = await driver.getText("page");
      assert(page.includes("FLAGGED"), `${id}: demo survey flags the visitor`);
      for (const re of expect[id].siteFlags) assert(re.test(page), `${id}: survey reports ${re}\n${page}`);
      for (const re of expect[id].siteMisses) assert(!re.test(page), `${id}: seeded gap ${re} should be missed`);
    } finally {
      await driver.close();
    }
  }
  console.log(`✓ ${name}: default / vm / anti-detect profiles present their signals; survey screening behaves as seeded`);
}

const server = (await isDemoServerUp()) ? undefined : await startDemoServer();
let failed = false;
for (const name of ["playwright", "puppeteer"] as const) {
  try {
    await smoke(name);
    await smokeProfiles(name);
  } catch (err) {
    failed = true;
    console.error(`✗ ${name}: ${err instanceof Error ? err.message : err}`);
  }
}
server?.close();
process.exit(failed ? 1 : 0);
