import { existsSync } from "node:fs";
import { readdir } from "node:fs/promises";
import { basename, join, resolve } from "node:path";
import { newJobId, writeJobSummary, type JobMeta } from "./report/history.js";
import { parseArgs } from "node:util";
import type { Server } from "node:http";
import { runTask, type RunResult } from "./agent/agent.js";
import type { DriverName } from "./drivers/index.js";
import { writeReport } from "./report/report.js";
import { loadTask, type TaskSpec } from "./tasks/schema.js";
import { DEMO_ORIGIN, isDemoServerUp, startDemoServer } from "../demo-site/server.js";

const USAGE = `Usage:
  npm run agent -- run <task.json ...> [options]
  npm run agent -- list

Options:
  --driver <playwright|puppeteer|both>  Override the task's driver ("both" runs each task on both)
  --headed                              Show the browser window
  --auto-approve                        Approve sensitive actions without prompting
  --effort <low|medium|high|xhigh|max>  Override the task's effort level
  --runs-dir <dir>                      Where reports are written (default: runs)`;

async function main(): Promise<number> {
  if (existsSync(".env")) process.loadEnvFile(".env");

  const { positionals, values } = parseArgs({
    allowPositionals: true,
    options: {
      driver: { type: "string" },
      headed: { type: "boolean", default: false },
      "auto-approve": { type: "boolean", default: false },
      effort: { type: "string" },
      "runs-dir": { type: "string", default: "runs" },
      help: { type: "boolean", short: "h", default: false },
    },
  });
  const [command, ...files] = positionals;

  if (values.help || !command) {
    console.log(USAGE);
    return 0;
  }
  if (command === "list") {
    for (const f of (await readdir("tasks")).filter((f) => f.endsWith(".json"))) {
      const t = await loadTask(join("tasks", f));
      console.log(`${join("tasks", f).padEnd(36)} [${t.category}] ${t.name}`);
    }
    return 0;
  }
  if (command !== "run" || files.length === 0) {
    console.error(USAGE);
    return 2;
  }

  const driverArg = values.driver;
  if (driverArg && !["playwright", "puppeteer", "both"].includes(driverArg)) throw new Error(`Unknown driver "${driverArg}"`);
  const effort = values.effort as TaskSpec["effort"] | undefined;
  if (effort && !["low", "medium", "high", "xhigh", "max"].includes(effort)) throw new Error(`Unknown effort "${effort}"`);

  const tasks = await Promise.all(files.map((f) => loadTask(f)));
  const demo = await ensureDemoServer(tasks);
  const results: RunResult[] = [];
  const jobId = newJobId();
  const meta: JobMeta = {
    jobId,
    source: "cli",
    startedAt: new Date().toISOString(),
    urls: [...new Set(tasks.map((t) => t.startUrl))],
    suites: files.map((f) => basename(f, ".json")),
    drivers: driverArg === "both" ? ["playwright", "puppeteer"] : [...new Set(tasks.map((t) => driverArg ?? t.driver))],
  };

  try {
    for (const task of tasks) {
      const drivers: DriverName[] =
        driverArg === "both" ? ["playwright", "puppeteer"] : [(driverArg as DriverName | undefined) ?? task.driver];
      for (const driver of drivers) {
        const runDir = resolve(values["runs-dir"]!, `${jobId}-${slug(task.name)}-${driver}`);
        console.log(`\n▶ ${task.name} (${driver})`);
        const result = await runTask(task, {
          driver,
          headless: values.headed ? false : task.headless,
          autoApprove: values["auto-approve"]!,
          effort,
          runDir,
          jobId,
          log: (m) => console.log(m),
        });
        const { html } = await writeReport(result);
        results.push(result);
        console.log(`  → ${result.status.toUpperCase()}: ${result.summary}`);
        console.log(`  → report: ${html}`);
      }
    }
  } finally {
    demo?.close();
  }

  printSummary(results);
  if (results.length) {
    meta.finishedAt = new Date().toISOString();
    console.log(`\nSession summary: ${await writeJobSummary(resolve(values["runs-dir"]!), meta, results)}`);
  }
  return results.every((r) => r.status === "success") ? 0 : 1;
}

/** Starts the bundled demo site when a task targets it and it isn't already running. */
async function ensureDemoServer(tasks: TaskSpec[]): Promise<Server | undefined> {
  if (!tasks.some((t) => t.startUrl.startsWith(DEMO_ORIGIN))) return undefined;
  if (await isDemoServerUp()) return undefined;
  const server = await startDemoServer();
  console.log(`Started demo site at ${DEMO_ORIGIN}`);
  return server;
}

function printSummary(results: RunResult[]): void {
  if (results.length === 0) return;
  console.log("\nSummary");
  console.table(
    results.map((r) => ({
      task: r.taskName,
      driver: r.driver,
      status: r.status,
      checks: `${r.checks.filter((c) => c.passed).length}/${r.checks.length}`,
      steps: r.steps.length,
      seconds: +(r.durationMs / 1000).toFixed(1),
      costUsd: +r.costUsd.toFixed(3),
    })),
  );
}

const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 40);

main().then(
  (code) => process.exit(code),
  (err) => {
    console.error(err instanceof Error ? err.message : err);
    process.exit(2);
  },
);
