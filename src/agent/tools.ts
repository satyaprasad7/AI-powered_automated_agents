import type Anthropic from "@anthropic-ai/sdk";
import { z } from "zod";
import type { BrowserDriver, PageSnapshot } from "../drivers/index.js";
import type { TaskSpec } from "../tasks/schema.js";
import { PROFILES } from "../detection/profiles.js";
import { redFlags } from "../detection/fingerprint.js";

type BetaTool = Anthropic.Beta.BetaTool;
type ToolResultContent = Anthropic.Beta.BetaToolResultBlockParam["content"];

export interface Check {
  name: string;
  passed: boolean;
  evidence: string;
}

export interface FinishResult {
  status: "success" | "failure" | "blocked";
  summary: string;
  data: unknown;
}

export interface ToolContext {
  driver: BrowserDriver;
  task: TaskSpec;
  /** placeholder name -> real value; never shown to the model. */
  secrets: Map<string, string>;
  /** Asks the operator to approve a sensitive action. */
  approve(action: string): Promise<boolean>;
  /** Returns a file path for a screenshot with the given label. */
  screenshotPath(label: string): string;
  checks: Check[];
  lastAllowedUrl: string;
}

export interface ToolOutcome {
  content: ToolResultContent;
  isError?: boolean;
  finish?: FinishResult;
  /** Screenshot saved for the report, if any. */
  screenshot?: string;
}

// ---------------------------------------------------------------------------
// Tool definitions. All use strict mode so inputs always match the schema.
// ---------------------------------------------------------------------------

const obj = (properties: Record<string, unknown>): BetaTool["input_schema"] => ({
  type: "object",
  properties,
  required: Object.keys(properties),
  additionalProperties: false,
});
const refProp = { type: "string", description: "Element ref number from the latest snapshot, e.g. \"12\"" };

export const TOOL_DEFINITIONS: BetaTool[] = [
  {
    name: "navigate",
    description: "Open a URL in the browser. Only URLs on the task's allowed domains are permitted. Returns a page snapshot.",
    input_schema: obj({ url: { type: "string", description: "Absolute URL" } }),
  },
  {
    name: "observe",
    description:
      "Return a snapshot of the current page: URL, title, visible interactive elements (each with a ref), and visible text. " +
      "Call this whenever you are unsure what the page currently shows.",
    input_schema: obj({}),
  },
  {
    name: "click",
    description: "Click an element (button, link, checkbox, radio, tab...). Returns a fresh page snapshot.",
    input_schema: obj({ ref: refProp }),
  },
  {
    name: "fill",
    description:
      "Clear a text input or textarea and type a value. To enter a secret, pass its placeholder exactly, e.g. {{secret:PASSWORD}}.",
    input_schema: obj({ ref: refProp, value: { type: "string" } }),
  },
  {
    name: "select_option",
    description: "Choose an option in a <select> by its value or visible label. Returns a fresh page snapshot.",
    input_schema: obj({ ref: refProp, option: { type: "string" } }),
  },
  {
    name: "press_key",
    description: "Press a keyboard key on the focused element, e.g. Enter, Tab, Escape, ArrowDown. Returns a fresh page snapshot.",
    input_schema: obj({ key: { type: "string" } }),
  },
  {
    name: "get_text",
    description: "Get the full visible text of one element, or of the whole page when ref is \"page\" (up to 20k chars).",
    input_schema: obj({ ref: { type: "string", description: "Element ref, or \"page\"" } }),
  },
  {
    name: "extract_table",
    description: "Extract an HTML table as rows of cell text (first row is usually the header). Pass the table's ref, or \"page\" for the first table on the page.",
    input_schema: obj({ ref: { type: "string", description: "Element ref, or \"page\"" } }),
  },
  {
    name: "wait_for_text",
    description: "Wait until the given text appears on the page. Use after actions that load content asynchronously.",
    input_schema: obj({
      text: { type: "string" },
      timeout_ms: { type: "integer", description: "Maximum wait, 500-30000" },
    }),
  },
  {
    name: "audit_page",
    description:
      "Return deterministic facts about the current page for accessibility, SEO and content checks: title, lang, meta description, " +
      "heading outline, images missing alt text, broken images, unlabeled form controls, links/buttons without an accessible name, " +
      "duplicate ids, and the page's internal links. Prefer this over guessing from snapshots.",
    input_schema: obj({}),
  },
  {
    name: "fingerprint",
    description:
      "Return the browser fingerprint the current page can read (webdriver flag, user agent, platform, client hints, languages, " +
      "time zone, CPU/memory, screen, WebGL GPU) plus the red flags a bot/fraud detector should raise for it, and the signals " +
      "this run's browser profile deliberately presents. Use it in detection tests to know what the site ought to catch.",
    input_schema: obj({}),
  },
  {
    name: "screenshot",
    description: "Capture the visible viewport as an image, for visual checks (layout, images, colors) that text snapshots cannot show.",
    input_schema: obj({ label: { type: "string", description: "Short label for the report" } }),
  },
  {
    name: "record_check",
    description:
      "Record the outcome of one verification (a success criterion, test case or validation rule) with concrete evidence from the page. " +
      "Call once per check; the checks appear in the final report.",
    input_schema: obj({
      name: { type: "string", description: "What was checked" },
      passed: { type: "boolean" },
      evidence: { type: "string", description: "What you observed that justifies the result" },
    }),
  },
  {
    name: "finish",
    description:
      "End the task. Call exactly once, after recording every check. status: success = goal achieved; failure = the goal could not be " +
      "achieved or a check failed; blocked = you were stopped (denied approval, disallowed domain, missing data, CAPTCHA, login wall).",
    input_schema: obj({
      status: { type: "string", enum: ["success", "failure", "blocked"] },
      summary: { type: "string", description: "Two to five sentences for a human reviewer" },
      data_json: { type: "string", description: "Extracted or produced data as a JSON string; \"null\" if none" },
    }),
  },
].map((tool) => ({ ...tool, strict: true }));

// ---------------------------------------------------------------------------
// Execution
// ---------------------------------------------------------------------------

const Inputs = {
  navigate: z.object({ url: z.string() }),
  click: z.object({ ref: z.string() }),
  fill: z.object({ ref: z.string(), value: z.string() }),
  select_option: z.object({ ref: z.string(), option: z.string() }),
  press_key: z.object({ key: z.string() }),
  get_text: z.object({ ref: z.string() }),
  extract_table: z.object({ ref: z.string() }),
  wait_for_text: z.object({ text: z.string(), timeout_ms: z.number() }),
  screenshot: z.object({ label: z.string() }),
  record_check: z.object({ name: z.string(), passed: z.boolean(), evidence: z.string() }),
  finish: z.object({ status: z.enum(["success", "failure", "blocked"]), summary: z.string(), data_json: z.string() }),
};

export function isAllowedUrl(url: string, allowedDomains: string[]): boolean {
  if (url === "about:blank") return true;
  let host: string;
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return false;
    host = parsed.hostname.toLowerCase();
  } catch {
    return false;
  }
  return allowedDomains.some((d) => {
    const domain = d.toLowerCase();
    return host === domain || host.endsWith(`.${domain}`);
  });
}

export function formatSnapshot(s: PageSnapshot): string {
  const lines = [`URL: ${s.url}`, `Title: ${s.title}`, "", "Interactive elements:"];
  for (const e of s.elements) {
    let line = `[${e.ref}] ${e.tag}`;
    if (e.type && e.type !== "text") line += `[type=${e.type}]`;
    if (e.role) line += `[role=${e.role}]`;
    line += ` "${e.name}"`;
    if (e.value !== undefined) line += ` value="${e.value}"`;
    if (e.checked !== undefined) line += e.checked ? " checked" : " unchecked";
    if (e.required) line += " required";
    if (e.disabled) line += " disabled";
    if (e.invalid) line += ` INVALID("${e.invalid}")`;
    if (e.href) line += ` href=${e.href}`;
    if (e.options) line += ` options=[${e.options.join(" | ")}]`;
    lines.push(line);
  }
  if (s.elements.length === 0) lines.push("(none visible)");
  lines.push("", "Visible text:", s.text || "(empty)");
  if (s.truncated) lines.push("", "(snapshot truncated - use get_text or extract_table for more)");
  return lines.join("\n");
}

function redact(text: string, secrets: Map<string, string>): string {
  let out = text;
  for (const [name, value] of secrets) {
    if (value) out = out.split(value).join(`{{secret:${name}}}`);
  }
  return out;
}

function resolveSecrets(value: string, secrets: Map<string, string>): string {
  return value.replace(/\{\{secret:([A-Za-z0-9_]+)\}\}/g, (_, name: string) => {
    const secret = secrets.get(name);
    if (secret === undefined) throw new Error(`Unknown secret placeholder {{secret:${name}}}`);
    return secret;
  });
}

async function snapshotText(ctx: ToolContext): Promise<string> {
  return redact(formatSnapshot(await ctx.driver.snapshot()), ctx.secrets);
}

/** After an action, make sure the browser is still on an allowed domain. */
async function guardDomain(ctx: ToolContext): Promise<string | null> {
  const current = ctx.driver.url();
  if (isAllowedUrl(current, ctx.task.allowedDomains)) {
    ctx.lastAllowedUrl = current;
    return null;
  }
  await ctx.driver.goto(ctx.lastAllowedUrl);
  return `The action led to ${current}, which is outside the allowed domains. The browser was returned to ${ctx.lastAllowedUrl}.`;
}

export async function executeTool(name: string, rawInput: unknown, ctx: ToolContext): Promise<ToolOutcome> {
  const { driver } = ctx;
  const parse = <K extends keyof typeof Inputs>(key: K) => Inputs[key].parse(rawInput) as z.infer<(typeof Inputs)[K]>;

  const afterAction = async (message: string): Promise<ToolOutcome> => {
    const violation = await guardDomain(ctx);
    if (violation) return { content: `${violation}\n\n${await snapshotText(ctx)}`, isError: true };
    return { content: `${message}\n\n${await snapshotText(ctx)}` };
  };

  switch (name) {
    case "navigate": {
      const { url } = parse("navigate");
      if (!isAllowedUrl(url, ctx.task.allowedDomains)) {
        return { content: `Navigation refused: ${url} is not on the allowed domains (${ctx.task.allowedDomains.join(", ")}).`, isError: true };
      }
      await driver.goto(url);
      return afterAction(`Navigated to ${url} (HTTP status ${driver.lastStatus() ?? "unknown"}).`);
    }
    case "observe":
      return { content: await snapshotText(ctx) };
    case "fingerprint": {
      const fp = await driver.fingerprint();
      const profile = PROFILES[ctx.task.profile];
      return {
        content: JSON.stringify(
          { profile: { id: profile.id, label: profile.label, simulatedSignals: profile.simulatedSignals }, redFlags: redFlags(fp), fingerprint: fp },
          null,
          1,
        ),
      };
    }
    case "audit_page":
      return { content: redact(JSON.stringify({ httpStatus: driver.lastStatus(), ...(await driver.audit()) }, null, 1), ctx.secrets) };
    case "click": {
      const { ref } = parse("click");
      const target = await driver.describe(ref);
      if (!target) return { content: `No element with ref ${ref}. Call observe to get current refs.`, isError: true };
      const label = `click ${target.tag} "${target.name}"`;
      const needsApproval = ctx.task.requireApprovalFor.some((p) => new RegExp(p, "i").test(target.name));
      if (needsApproval && !(await ctx.approve(label))) {
        return {
          content:
            `The operator DENIED approval for: ${label}. Do not retry this action. Continue with the parts of the task ` +
            `that don't need it; if nothing else can be done, finish with status "blocked".`,
          isError: true,
        };
      }
      await driver.click(ref);
      return afterAction(`Clicked ${target.tag} "${target.name}".`);
    }
    case "fill": {
      const { ref, value } = parse("fill");
      await driver.fill(ref, resolveSecrets(value, ctx.secrets));
      const info = await driver.describe(ref);
      return { content: redact(`Filled [${ref}] ${info ? `"${info.name}" -> value="${info.value ?? ""}"` : ""}`, ctx.secrets) };
    }
    case "select_option": {
      const { ref, option } = parse("select_option");
      const selected = await driver.select(ref, option);
      return afterAction(`Selected ${JSON.stringify(selected)} in [${ref}].`);
    }
    case "press_key": {
      const { key } = parse("press_key");
      await driver.press(key);
      return afterAction(`Pressed ${key}.`);
    }
    case "get_text": {
      const { ref } = parse("get_text");
      return { content: redact(await driver.getText(ref), ctx.secrets) || "(no text)" };
    }
    case "extract_table": {
      const { ref } = parse("extract_table");
      const rows = await driver.extractTable(ref);
      return { content: redact(JSON.stringify(rows), ctx.secrets) };
    }
    case "wait_for_text": {
      const { text, timeout_ms } = parse("wait_for_text");
      const found = await driver.waitForText(text, Math.min(Math.max(timeout_ms, 500), 30_000));
      return found ? { content: `Text "${text}" is present.` } : { content: `Timed out waiting for "${text}".`, isError: true };
    }
    case "screenshot": {
      const { label } = parse("screenshot");
      const path = ctx.screenshotPath(label);
      const image = await driver.screenshot(path);
      return {
        screenshot: path,
        content: [
          { type: "image", source: { type: "base64", media_type: "image/jpeg", data: image.toString("base64") } },
          { type: "text", text: `Screenshot "${label}" of ${driver.url()}` },
        ],
      };
    }
    case "record_check": {
      const check = parse("record_check");
      ctx.checks.push(check);
      return { content: `Recorded check "${check.name}": ${check.passed ? "PASSED" : "FAILED"}.` };
    }
    case "finish": {
      const { status, summary, data_json } = parse("finish");
      let data: unknown = data_json;
      try {
        data = JSON.parse(data_json);
      } catch {
        // keep the raw string if it isn't valid JSON
      }
      return { content: "Task finished.", finish: { status, summary, data } };
    }
    default:
      return { content: `Unknown tool "${name}".`, isError: true };
  }
}
