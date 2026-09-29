/**
 * Local web UI: paste a URL, pick suites, watch the agents work.
 *
 *   npm run ui   -> http://127.0.0.1:4180
 */
import { existsSync } from "node:fs";
import { readFile } from "node:fs/promises";
import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { dirname, extname, join, normalize, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { randomUUID } from "node:crypto";
import { z } from "zod";
import { runTask, type RunResult, type StepRecord } from "../agent/agent.js";
import type { Check } from "../agent/tools.js";
import type { DriverName } from "../drivers/index.js";
import { writeReport } from "../report/report.js";
import { loadHistory, newJobId, writeJobSummary, type JobMeta } from "../report/history.js";
import { buildSuiteTasks, SUITES, type SuiteId } from "../tasks/suites.js";
import { DEMO_ORIGIN, isDemoServerUp, startDemoServer } from "../../demo-site/server.js";

const PORT = Number(process.env.UI_PORT ?? 4180);
const HOST = "127.0.0.1";
const PUBLIC_DIR = join(dirname(fileURLToPath(import.meta.url)), "public");
const RUNS_DIR = resolve("runs");
const MIME: Record<string, string> = {
  ".html": "text/html; charset=utf-8",
  ".json": "application/json",
  ".jpg": "image/jpeg",
  ".css": "text/css",
  ".js": "text/javascript",
};

if (existsSync(".env")) process.loadEnvFile(".env");

// ---------------------------------------------------------------------------
// Jobs
// ---------------------------------------------------------------------------

const JobRequest = z.object({
  url: z.url({ protocol: /^https?$/ }),
  suites: z.array(z.enum(Object.keys(SUITES) as [SuiteId, ...SuiteId[]])).min(1),
  customGoal: z.string().max(4000).optional(),
  drivers: z.array(z.enum(["playwright", "puppeteer"])).min(1),
  allowSubmissions: z.boolean().default(false),
  effort: z.enum(["low", "medium", "high", "xhigh", "max"]).default("medium"),
  headed: z.boolean().default(false),
});
type JobRequest = z.infer<typeof JobRequest>;

type JobEvent = Record<string, unknown> & { type: string };

interface Job {
  id: string;
  request: JobRequest;
  running: boolean;
  events: JobEvent[];
  listeners: Set<ServerResponse>;
  abort: AbortController;
  approvals: Map<string, (approved: boolean) => void>;
}

const jobs = new Map<string, Job>();
let activeJob: Job | undefined;

function emit(job: Job, event: JobEvent): void {
  job.events.push(event);
  const line = `data: ${JSON.stringify(event)}\n\n`;
  for (const res of job.listeners) res.write(line);
}

const webPath = (file: string) => "/runs/" + relative(RUNS_DIR, file).split(sep).map(encodeURIComponent).join("/");

function publicStep(step: StepRecord) {
  return { ...step, screenshot: step.screenshot ? webPath(step.screenshot) : undefined };
}

function publicResult(r: RunResult, reportPath: string) {
  return {
    status: r.status,
    summary: r.summary,
    error: r.error,
    checks: r.checks,
    data: r.data,
    steps: r.steps.length,
    durationMs: r.durationMs,
    costUsd: r.costUsd,
    report: webPath(reportPath),
  };
}

async function runJob(job: Job): Promise<void> {
  const { request } = job;
  const tasks = buildSuiteTasks({
    url: request.url,
    suites: request.suites,
    customGoal: request.customGoal,
    allowSubmissions: request.allowSubmissions,
    effort: request.effort,
    headless: !request.headed,
  });
  const plan = tasks.flatMap((task) =>
    request.drivers.map((driver) => ({ key: `${slug(task.name)}-${driver}`, title: task.name, category: task.category, driver })),
  );
  emit(job, { type: "plan", url: request.url, items: plan });

  if (request.url.startsWith(DEMO_ORIGIN) && !(await isDemoServerUp())) {
    await startDemoServer();
    emit(job, { type: "info", message: `Started the demo site at ${DEMO_ORIGIN}` });
  }

  const meta: JobMeta = {
    jobId: job.id,
    source: "ui",
    startedAt: new Date().toISOString(),
    urls: [request.url],
    suites: request.suites,
    drivers: request.drivers,
  };
  const results: RunResult[] = [];
  let totalCost = 0;

  for (const task of tasks) {
    for (const driver of request.drivers as DriverName[]) {
      const key = `${slug(task.name)}-${driver}`;
      if (job.abort.signal.aborted) {
        emit(job, { type: "task-end", key, result: { status: "cancelled", summary: "Not started (cancelled).", checks: [] } });
        continue;
      }
      emit(job, { type: "task-start", key });
      let lastShot: string | undefined;
      const result = await runTask(task, {
        driver,
        headless: task.headless,
        autoApprove: false,
        runDir: join(RUNS_DIR, `${job.id}-${slug(task.name)}-${driver}`),
        jobId: job.id,
        signal: job.abort.signal,
        log: (message) => emit(job, { type: "log", key, message }),
        onStart: (shot) => {
          lastShot = webPath(shot);
          emit(job, { type: "screenshot", key, screenshot: lastShot });
        },
        onStep: (step: StepRecord, checks: Check[]) => {
          if (step.screenshot) lastShot = webPath(step.screenshot);
          emit(job, { type: "step", key, step: publicStep(step), checks });
        },
        approver: (action) =>
          new Promise<boolean>((resolveApproval) => {
            const id = randomUUID();
            job.approvals.set(id, (approved) => {
              job.approvals.delete(id);
              emit(job, { type: "approval-resolved", id, approved });
              resolveApproval(approved);
            });
            emit(job, { type: "approval", id, key, action, screenshot: lastShot, title: task.name, driver });
          }),
      });
      const { html } = await writeReport(result);
      results.push(result);
      totalCost += result.costUsd;
      emit(job, { type: "task-end", key, result: publicResult(result, html) });
    }
  }
  meta.finishedAt = new Date().toISOString();
  meta.cancelled = job.abort.signal.aborted;
  const summary = results.length ? webPath(await writeJobSummary(RUNS_DIR, meta, results)) : undefined;
  emit(job, { type: "done", cancelled: job.abort.signal.aborted, totalCostUsd: totalCost, summary });
}

function startJob(request: JobRequest): Job {
  const job: Job = {
    id: newJobId(),
    request,
    running: true,
    events: [],
    listeners: new Set(),
    abort: new AbortController(),
    approvals: new Map(),
  };
  jobs.set(job.id, job);
  activeJob = job;
  job.abort.signal.addEventListener("abort", () => {
    for (const resolveApproval of [...job.approvals.values()]) resolveApproval(false);
  });
  runJob(job)
    .catch((err) => emit(job, { type: "fatal", message: err instanceof Error ? err.message : String(err) }))
    .finally(() => {
      job.running = false;
      if (activeJob === job) activeJob = undefined;
      for (const res of job.listeners) res.end();
      job.listeners.clear();
    });
  return job;
}

// ---------------------------------------------------------------------------
// HTTP
// ---------------------------------------------------------------------------

async function readJson(req: IncomingMessage): Promise<unknown> {
  const chunks: Buffer[] = [];
  for await (const chunk of req) chunks.push(chunk as Buffer);
  return JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}");
}

function sendJson(res: ServerResponse, status: number, body: unknown): void {
  res.writeHead(status, { "content-type": "application/json" });
  res.end(JSON.stringify(body));
}

async function serveFile(res: ServerResponse, root: string, relPath: string): Promise<void> {
  const file = normalize(join(root, relPath));
  if (!file.startsWith(root + sep) && file !== root) return sendJson(res, 403, { error: "forbidden" });
  try {
    const body = await readFile(file);
    res.writeHead(200, { "content-type": MIME[extname(file)] ?? "application/octet-stream", "cache-control": "no-store" });
    res.end(body);
  } catch {
    sendJson(res, 404, { error: "not found" });
  }
}

const server = createServer(async (req, res) => {
  const url = new URL(req.url ?? "/", `http://${HOST}:${PORT}`);
  const parts = url.pathname.split("/").filter(Boolean);
  try {
    if (req.method === "GET" && url.pathname === "/") return serveFile(res, PUBLIC_DIR, "index.html");
    if (req.method === "GET" && parts[0] === "runs") return serveFile(res, RUNS_DIR, parts.slice(1).map(decodeURIComponent).join(sep));

    if (req.method === "GET" && url.pathname === "/api/status") {
      return sendJson(res, 200, {
        hasCredentials: Boolean(process.env.ANTHROPIC_API_KEY || process.env.ANTHROPIC_AUTH_TOKEN),
        demoUrl: `${DEMO_ORIGIN}/`,
        activeJob: activeJob?.id ?? null,
        suites: SUITES,
      });
    }

    if (req.method === "GET" && url.pathname === "/api/history") {
      const history = await loadHistory(RUNS_DIR);
      const running = activeJob?.id;
      return sendJson(res, 200, {
        jobs: history.map((j) => ({
          ...j,
          running: j.jobId === running,
          summaryReport: j.summaryReport && `/runs/${j.summaryReport}`,
          runs: j.runs.map((r) => ({ ...r, report: `/runs/${r.report}` })),
        })),
      });
    }

    if (req.method === "POST" && url.pathname === "/api/jobs") {
      if (activeJob) return sendJson(res, 409, { error: "A run is already in progress. Wait for it to finish or cancel it." });
      const parsed = JobRequest.safeParse(await readJson(req));
      if (!parsed.success) return sendJson(res, 400, { error: z.prettifyError(parsed.error) });
      if (parsed.data.suites.length === 1 && parsed.data.suites[0] === "custom" && !parsed.data.customGoal?.trim()) {
        return sendJson(res, 400, { error: "Enter a custom goal, or pick at least one other suite." });
      }
      return sendJson(res, 201, { id: startJob(parsed.data).id });
    }

    const job = parts[0] === "api" && parts[1] === "jobs" ? jobs.get(parts[2] ?? "") : undefined;
    if (parts[0] === "api" && parts[1] === "jobs" && parts[2] && !job) return sendJson(res, 404, { error: "unknown job" });

    if (job && req.method === "GET" && parts[3] === "events") {
      res.writeHead(200, { "content-type": "text/event-stream", "cache-control": "no-store", connection: "keep-alive" });
      for (const event of job.events) res.write(`data: ${JSON.stringify(event)}\n\n`);
      if (!job.running) return res.end();
      job.listeners.add(res);
      req.on("close", () => job.listeners.delete(res));
      return;
    }
    if (job && req.method === "POST" && parts[3] === "approvals" && parts[4]) {
      const body = z.object({ approved: z.boolean() }).parse(await readJson(req));
      const resolveApproval = job.approvals.get(parts[4]);
      if (!resolveApproval) return sendJson(res, 404, { error: "approval not pending" });
      resolveApproval(body.approved);
      return sendJson(res, 200, { ok: true });
    }
    if (job && req.method === "POST" && parts[3] === "cancel") {
      job.abort.abort();
      emit(job, { type: "info", message: "Cancelling… the current step will finish first." });
      return sendJson(res, 200, { ok: true });
    }
    sendJson(res, 404, { error: "not found" });
  } catch (err) {
    sendJson(res, 500, { error: err instanceof Error ? err.message : String(err) });
  }
});

const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 40);

server.listen(PORT, HOST, () => {
  console.log(`Agentic Web Tester UI: http://${HOST}:${PORT}`);
  if (!process.env.ANTHROPIC_API_KEY && !process.env.ANTHROPIC_AUTH_TOKEN) {
    console.log("Note: ANTHROPIC_API_KEY is not set. Add it to .env, or sign in with `ant auth login`.");
  }
});
