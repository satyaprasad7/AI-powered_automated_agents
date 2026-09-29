import Anthropic from "@anthropic-ai/sdk";
import { mkdir } from "node:fs/promises";
import { join } from "node:path";
import { createInterface } from "node:readline/promises";
import { createDriver, type DriverName } from "../drivers/index.js";
import type { TaskSpec } from "../tasks/schema.js";
import { SYSTEM_PROMPT, taskBrief } from "./prompts.js";
import { PROFILES } from "../detection/profiles.js";
import {
  TOOL_DEFINITIONS,
  executeTool,
  formatSnapshot,
  type Check,
  type FinishResult,
  type ToolContext,
} from "./tools.js";

export const MODEL = "claude-opus-5-5";
/** Server-side refusal fallback: a declined request is re-run on a recommended model. */
const FALLBACK_BETA = "server-side-fallback-2026-07-01";
/** USD per million tokens for claude-opus-5-5. */
const PRICE = { input: 4, output: 20, cacheWrite: 5, cacheRead: 0.2 };
/** Tools whose effect on the page is worth a screenshot in the report. */
const STATEFUL_TOOLS = new Set(["navigate", "click", "fill", "select_option", "press_key"]);
const MAX_NUDGES = 2;

export type RunStatus = FinishResult["status"] | "incomplete" | "error";

export interface StepRecord {
  index: number;
  tool: string;
  input: unknown;
  ok: boolean;
  output: string;
  url: string;
  durationMs: number;
  screenshot?: string;
  /** Claude's own narration right before this step, if any. */
  note?: string;
}

export interface RunResult {
  taskName: string;
  category: string;
  goal: string;
  driver: DriverName;
  model: string;
  startedAt: string;
  durationMs: number;
  status: RunStatus;
  summary: string;
  data: unknown;
  checks: Check[];
  steps: StepRecord[];
  usage: { requests: number; inputTokens: number; outputTokens: number; cacheReadTokens: number; cacheWriteTokens: number };
  costUsd: number;
  runDir: string;
  startUrl: string;
  profile: TaskSpec["profile"];
  /** Groups the runs of one UI job or CLI invocation in the history. */
  jobId?: string;
  /** How many times each agent tool was called. */
  toolUsage: Record<string, number>;
  startScreenshot?: string;
  error?: string;
}

export interface RunOptions {
  driver: DriverName;
  headless: boolean;
  /** Approve every sensitive action without asking. */
  autoApprove: boolean;
  runDir: string;
  jobId?: string;
  effort?: TaskSpec["effort"];
  client?: Anthropic;
  log?: (message: string) => void;
  /** Called after every tool step (used by the web UI for live progress). */
  onStep?: (step: StepRecord, checks: Check[]) => void;
  /** Called with the screenshot of the start page, before the first step. */
  onStart?: (screenshot: string) => void;
  /** Custom approval channel (e.g. the web UI). Overrides the terminal prompt. */
  approver?: (action: string) => Promise<boolean>;
  /** Aborts the run between steps and cancels an in-flight model request. */
  signal?: AbortSignal;
}

export async function runTask(task: TaskSpec, options: RunOptions): Promise<RunResult> {
  const log = options.log ?? (() => {});
  const client = options.client ?? new Anthropic();
  const started = Date.now();
  const screenshotsDir = join(options.runDir, "screenshots");
  await mkdir(screenshotsDir, { recursive: true });

  const result: RunResult = {
    taskName: task.name,
    category: task.category,
    goal: task.goal,
    driver: options.driver,
    model: MODEL,
    startedAt: new Date(started).toISOString(),
    durationMs: 0,
    status: "incomplete",
    summary: "",
    data: null,
    checks: [],
    steps: [],
    usage: { requests: 0, inputTokens: 0, outputTokens: 0, cacheReadTokens: 0, cacheWriteTokens: 0 },
    costUsd: 0,
    runDir: options.runDir,
    startUrl: task.startUrl,
    profile: task.profile,
    jobId: options.jobId,
    toolUsage: {},
  };

  const secrets = new Map<string, string>();
  for (const [name, envVar] of Object.entries(task.secrets)) {
    const value = process.env[envVar];
    if (value === undefined) throw new Error(`Secret ${name} needs environment variable ${envVar}, which is not set`);
    secrets.set(name, value);
  }

  const driver = await createDriver(options.driver);
  let shotCounter = 0;
  const ctx: ToolContext = {
    driver,
    task,
    secrets,
    checks: result.checks,
    lastAllowedUrl: task.startUrl,
    approve: (action) =>
      options.approver && !options.autoApprove ? options.approver(action) : approve(action, options.autoApprove, log),
    screenshotPath: (label) =>
      join(screenshotsDir, `${String(++shotCounter).padStart(3, "0")}-${label.replace(/[^a-z0-9]+/gi, "-").slice(0, 40)}.jpg`),
  };

  try {
    await driver.launch({ headless: options.headless, profile: PROFILES[task.profile] });
    await driver.goto(task.startUrl);
    log(`[${driver.name}] opened ${task.startUrl}`);
    const startShot = ctx.screenshotPath("start");
    await driver
      .screenshot(startShot)
      .then(() => {
        result.startScreenshot = startShot;
        options.onStart?.(startShot);
      })
      .catch(() => {});

    const initial = formatSnapshot(await driver.snapshot());
    const messages: Anthropic.Beta.BetaMessageParam[] = [
      { role: "user", content: `${taskBrief(task, [...secrets.keys()])}\n\nThe start URL is already open. Current page:\n\n${initial}` },
    ];

    let nudges = 0;
    let pendingNote: string | undefined;

    while (true) {
      if (options.signal?.aborted) {
        result.summary = "Cancelled by the user.";
        break;
      }
      const response = await client.beta.messages.create(
        {
        model: MODEL,
        max_tokens: 16000,
        betas: [FALLBACK_BETA],
        fallbacks: "default",
        output_config: { effort: options.effort ?? task.effort },
        cache_control: { type: "ephemeral" },
        system: SYSTEM_PROMPT,
        tools: TOOL_DEFINITIONS,
        // Browser actions are order-dependent, so take them one at a time.
        tool_choice: { type: "auto", disable_parallel_tool_use: true },
        messages,
        },
        { signal: options.signal },
      );
      addUsage(result, response.usage);

      // Append the full content unchanged (thinking and fallback blocks included).
      messages.push({ role: "assistant", content: response.content });

      const narration = response.content
        .filter((b): b is Anthropic.Beta.BetaTextBlock => b.type === "text")
        .map((b) => b.text.trim())
        .filter(Boolean)
        .join("\n");
      if (narration) {
        log(`  claude: ${narration.split("\n")[0].slice(0, 160)}`);
        pendingNote = narration;
      }

      if (response.stop_reason === "refusal") {
        result.status = "blocked";
        result.summary = `The model declined to continue (${response.stop_details?.category ?? "unspecified"}).`;
        break;
      }
      if (response.stop_reason === "max_tokens") {
        throw new Error("Model response hit max_tokens; the turn was truncated");
      }

      const toolUses = response.content.filter((b): b is Anthropic.Beta.BetaToolUseBlock => b.type === "tool_use");
      if (toolUses.length === 0) {
        if (nudges++ >= MAX_NUDGES) {
          result.summary = narration || "The agent stopped without calling finish.";
          break;
        }
        messages.push({ role: "user", content: "You ended your turn without calling finish. Continue the task, or call finish now." });
        continue;
      }

      const toolResults: Anthropic.Beta.BetaToolResultBlockParam[] = [];
      let finish: FinishResult | undefined;

      for (const use of toolUses) {
        const stepIndex = result.steps.length + 1;
        const overBudget = stepIndex > task.maxSteps && use.name !== "finish";
        const t0 = Date.now();
        let outcome;
        if (overBudget) {
          outcome = { content: "Step budget exhausted. Only finish is allowed now.", isError: true };
        } else {
          try {
            outcome = await executeTool(use.name, use.input, ctx);
          } catch (err) {
            outcome = { content: `Error: ${errorMessage(err)}`, isError: true };
          }
        }

        let screenshot = "screenshot" in outcome ? outcome.screenshot : undefined;
        if (!screenshot && STATEFUL_TOOLS.has(use.name) && !overBudget) {
          screenshot = ctx.screenshotPath(`step-${stepIndex}-${use.name}`);
          await driver.screenshot(screenshot).catch(() => (screenshot = undefined));
        }

        const remaining = task.maxSteps - stepIndex;
        let content = outcome.content;
        if (typeof content === "string" && remaining <= 3 && remaining >= 0 && use.name !== "finish") {
          content += `\n\n[Step budget: ${remaining} step(s) left. Wrap up and call finish.]`;
        }

        result.steps.push({
          index: stepIndex,
          tool: use.name,
          input: use.input,
          ok: !outcome.isError,
          output: typeof outcome.content === "string" ? outcome.content.slice(0, 3000) : "[image]",
          url: driver.url(),
          durationMs: Date.now() - t0,
          screenshot,
          note: pendingNote,
        });
        pendingNote = undefined;
        options.onStep?.(result.steps.at(-1)!, result.checks);
        log(`  ${outcome.isError ? "x" : "✓"} ${stepIndex}. ${use.name} ${summarizeInput(use.input)}`);

        toolResults.push({ type: "tool_result", tool_use_id: use.id, content, is_error: outcome.isError || undefined });
        if ("finish" in outcome && outcome.finish) finish = outcome.finish;
      }

      messages.push({ role: "user", content: toolResults });

      if (finish) {
        result.status = finish.status;
        result.summary = finish.summary;
        result.data = finish.data;
        break;
      }
      if (result.steps.length > task.maxSteps + 3) {
        result.summary = `Stopped after exceeding the ${task.maxSteps}-step budget.`;
        break;
      }
    }
  } catch (err) {
    if (options.signal?.aborted) {
      result.summary = "Cancelled by the user.";
      return result;
    }
    result.status = "error";
    result.error = errorMessage(err);
    result.summary ||= `Run aborted: ${result.error}`;
    log(`  error: ${result.error}`);
  } finally {
    await driver.close().catch(() => {});
    result.durationMs = Date.now() - started;
    result.costUsd = costOf(result.usage);
    for (const step of result.steps) result.toolUsage[step.tool] = (result.toolUsage[step.tool] ?? 0) + 1;
  }
  return result;
}

async function approve(action: string, autoApprove: boolean, log: (m: string) => void): Promise<boolean> {
  if (autoApprove) {
    log(`  approval auto-granted: ${action}`);
    return true;
  }
  if (!process.stdin.isTTY) {
    log(`  approval denied (non-interactive): ${action}`);
    return false;
  }
  const rl = createInterface({ input: process.stdin, output: process.stdout });
  try {
    const answer = await rl.question(`\n  APPROVAL NEEDED - agent wants to: ${action}\n  Allow? [y/N] `);
    return /^y(es)?$/i.test(answer.trim());
  } finally {
    rl.close();
  }
}

function addUsage(result: RunResult, usage: Anthropic.Beta.BetaUsage): void {
  result.usage.requests += 1;
  result.usage.inputTokens += usage.input_tokens;
  result.usage.outputTokens += usage.output_tokens;
  result.usage.cacheReadTokens += usage.cache_read_input_tokens ?? 0;
  result.usage.cacheWriteTokens += usage.cache_creation_input_tokens ?? 0;
}

function costOf(u: RunResult["usage"]): number {
  return (
    (u.inputTokens * PRICE.input + u.outputTokens * PRICE.output + u.cacheWriteTokens * PRICE.cacheWrite + u.cacheReadTokens * PRICE.cacheRead) /
    1_000_000
  );
}

function errorMessage(err: unknown): string {
  if (err instanceof Anthropic.APIError) return `Claude API error ${err.status ?? ""}: ${err.message}`;
  const message = err instanceof Error ? err.message : String(err);
  // Playwright/Puppeteer errors carry long call logs; the first lines are the useful part.
  return message.split("\n").slice(0, 4).join("\n").slice(0, 600);
}

function summarizeInput(input: unknown): string {
  const text = JSON.stringify(input);
  return text === "{}" ? "" : text.length > 100 ? `${text.slice(0, 97)}...` : text;
}
