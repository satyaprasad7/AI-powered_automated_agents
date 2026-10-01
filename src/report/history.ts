/**
 * Report history. Every run folder under runs/ keeps its report.json; runs that
 * belong to one UI job or CLI invocation share a jobId. Each finished job also
 * gets a final summary report at runs/_jobs/<jobId>.html (+ .json) that links
 * every task report and compares engines (Playwright vs Puppeteer).
 */
import { randomBytes } from "node:crypto";
import { mkdir, readdir, readFile, writeFile } from "node:fs/promises";
import { basename, join } from "node:path";
import type { RunResult } from "../agent/agent.js";
import { SUITES } from "../tasks/suites.js";
import { DRIVER_LABELS } from "../drivers/types.js";

export const JOBS_DIR = "_jobs";
// Includes the combined detection suite's title from before it was split into bot / anti-detect / VM.
const engineLabel = (d: string) => DRIVER_LABELS[d as keyof typeof DRIVER_LABELS] ?? d;

const SUITE_TITLES = [...Object.values(SUITES).map((s) => s.title), "Bot, anti-detect & VM detection"];

export interface JobMeta {
  jobId: string;
  source: "ui" | "cli";
  startedAt: string;
  finishedAt?: string;
  urls: string[];
  suites: string[];
  drivers: string[];
  /** The standard-browser choice (auto, an id or a path), when that engine ran. */
  browser?: string;
  cancelled?: boolean;
}

/** Compact per-run entry used by the history list. */
export interface RunEntry {
  dir: string;
  taskName: string;
  category: string;
  driver: string;
  profile: string;
  browser?: string;
  startUrl: string;
  status: string;
  summary: string;
  checksPassed: number;
  checksTotal: number;
  steps: number;
  durationMs: number;
  costUsd: number;
  startedAt: string;
  toolUsage: Record<string, number>;
  report: string;
}

export interface HistoryJob extends JobMeta {
  runs: RunEntry[];
  summaryReport?: string;
  totals: { runs: number; passed: number; failed: number; costUsd: number; durationMs: number };
}

export function newJobId(): string {
  const stamp = new Date().toISOString().replace(/[-:]/g, "").replace("T", "-").slice(0, 15);
  return `${stamp}-${randomBytes(2).toString("hex")}`;
}

const esc = (s: unknown) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);

function toEntry(r: RunResult & { startScreenshot?: string }, dir: string): RunEntry {
  return {
    dir,
    taskName: r.taskName,
    category: r.category,
    driver: r.driver,
    profile: r.profile ?? "default",
    browser: r.browser,
    startUrl: r.startUrl ?? "",
    status: r.status,
    summary: r.summary,
    checksPassed: r.checks.filter((c) => c.passed).length,
    checksTotal: r.checks.length,
    steps: r.steps.length,
    durationMs: r.durationMs,
    costUsd: r.costUsd,
    startedAt: r.startedAt,
    toolUsage: r.toolUsage ?? {},
    report: `${dir}/report.html`,
  };
}

function totalsOf(runs: RunEntry[]): HistoryJob["totals"] {
  return {
    runs: runs.length,
    passed: runs.filter((r) => r.status === "success").length,
    failed: runs.filter((r) => r.status !== "success").length,
    costUsd: runs.reduce((s, r) => s + r.costUsd, 0),
    durationMs: runs.reduce((s, r) => s + r.durationMs, 0),
  };
}

// ---------------------------------------------------------------------------
// Job summary ("final report")
// ---------------------------------------------------------------------------

export async function writeJobSummary(runsDir: string, meta: JobMeta, results: RunResult[]): Promise<string> {
  const dir = join(runsDir, JOBS_DIR);
  await mkdir(dir, { recursive: true });
  const runs = results.map((r) => toEntry(r, basename(r.runDir)));
  await writeFile(join(dir, `${meta.jobId}.json`), JSON.stringify({ ...meta, runs }, null, 2));
  const html = join(dir, `${meta.jobId}.html`);
  await writeFile(html, renderJobSummary(meta, runs));
  return html;
}

const pillClass = (s: string) => (s === "success" ? "ok" : s === "failure" || s === "error" ? "bad" : "warn");

function renderJobSummary(meta: JobMeta, runs: RunEntry[]): string {
  const t = totalsOf(runs);
  const drivers = [...new Set(runs.map((r) => r.driver))];
  const taskNames = [...new Set(runs.map((r) => r.taskName))];

  // Engine comparison: one row per task, one column per engine.
  const matrix =
    drivers.length > 1
      ? `<section><h2>Engine comparison</h2><div class="scroll"><table>
        <tr><th>Task</th>${drivers.map((d) => `<th>${esc(engineLabel(d))}</th>`).join("")}</tr>
        ${taskNames
          .map(
            (name) =>
              `<tr><td>${esc(name)}</td>${drivers
                .map((d) => {
                  const r = runs.find((x) => x.taskName === name && x.driver === d);
                  return r
                    ? `<td><span class="pill ${pillClass(r.status)}">${esc(r.status)}</span> <span class="muted">${r.checksPassed}/${r.checksTotal} · ${(r.durationMs / 1000).toFixed(0)}s</span></td>`
                    : "<td class='muted'>not run</td>";
                })
                .join("")}</tr>`,
          )
          .join("")}
        <tr class="foot"><td>Per engine</td>${drivers
          .map((d) => {
            const rs = runs.filter((r) => r.driver === d);
            const tt = totalsOf(rs);
            return `<td>${tt.passed}/${tt.runs} passed · ${(tt.durationMs / 1000).toFixed(0)}s · $${tt.costUsd.toFixed(3)}</td>`;
          })
          .join("")}</tr>
      </table></div></section>`
      : "";

  const rows = runs
    .map(
      (r) => `<tr>
        <td><a href="../${encodeURIComponent(r.dir)}/report.html">${esc(r.taskName)}</a><div class="muted small">${esc(r.summary)}</div></td>
        <td>${esc(engineLabel(r.driver))}${r.browser ? `<div class="muted small">${esc(r.browser)}</div>` : ""}${r.profile !== "default" ? `<div class="muted small">${esc(r.profile)}</div>` : ""}</td>
        <td><span class="pill ${pillClass(r.status)}">${esc(r.status)}</span></td>
        <td>${r.checksPassed}/${r.checksTotal}</td>
        <td>${r.steps}</td>
        <td>${(r.durationMs / 1000).toFixed(1)}s</td>
        <td>$${r.costUsd.toFixed(3)}</td>
      </tr>`,
    )
    .join("");

  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Test session ${esc(meta.jobId)}</title>
<style>
:root{--bg:#f7f7f5;--card:#fff;--fg:#1d1d1b;--muted:#6b6b66;--line:#e4e4df;--ok:#1f7a4d;--okbg:#e3f3ea;--bad:#b3261e;--badbg:#fbe7e5;--warn:#8a5a00;--warnbg:#fdf1d8;--accent:#3b5bdb}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--card:#1f1f1d;--fg:#ecece8;--muted:#9a9a93;--line:#33332f;--ok:#6fd3a0;--okbg:#173828;--bad:#ff8a80;--badbg:#3d1a17;--warn:#f5c66b;--warnbg:#3a2c0e;--accent:#8ea5ff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1080px;margin:0 auto;padding:24px 16px 64px}h1{font-size:24px;margin:0 0 4px}h2{font-size:17px;margin:0 0 12px}
section{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:18px;margin-top:16px}
a{color:var(--accent)}.muted{color:var(--muted)}.small{font-size:12.5px}
.meta{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px}.meta div{font-size:13px}.meta b{display:block;font-size:18px}
.scroll{overflow-x:auto}table{width:100%;border-collapse:collapse}th,td{padding:8px 6px;border-top:1px solid var(--line);text-align:left;vertical-align:top;font-size:14px}
th{font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}tr.foot td{font-weight:600}
.pill{display:inline-block;padding:1px 9px;border-radius:99px;font-size:12px;font-weight:600}
.ok{background:var(--okbg);color:var(--ok)}.bad{background:var(--badbg);color:var(--bad)}.warn{background:var(--warnbg);color:var(--warn)}
</style></head><body><main>
<h1>Test session summary</h1>
<div class="muted">${esc(meta.jobId)} · ${esc(meta.source.toUpperCase())} · ${esc(meta.startedAt)}${meta.cancelled ? " · cancelled" : ""}</div>
<div class="muted">${meta.urls.map(esc).join(", ")}</div>
<section><div class="meta">
  <div>Runs<b>${t.runs}</b></div><div>Passed<b>${t.passed}</b></div><div>Not passed<b>${t.failed}</b></div>
  <div>Engines<b>${esc(drivers.map(engineLabel).join(", "))}</b></div><div>Total time<b>${(t.durationMs / 1000).toFixed(0)} s</b></div><div>Total cost<b>$${t.costUsd.toFixed(3)}</b></div>
</div></section>
${matrix}
<section><h2>Task reports</h2><div class="scroll"><table>
<tr><th>Task</th><th>Engine</th><th>Status</th><th>Checks</th><th>Steps</th><th>Time</th><th>Cost</th></tr>${rows}
</table></div></section>
</main></body></html>`;
}

// ---------------------------------------------------------------------------
// History index
// ---------------------------------------------------------------------------

export async function loadHistory(runsDir: string): Promise<HistoryJob[]> {
  let dirs: string[];
  try {
    dirs = (await readdir(runsDir, { withFileTypes: true })).filter((d) => d.isDirectory() && d.name !== JOBS_DIR).map((d) => d.name);
  } catch {
    return [];
  }

  const metas = new Map<string, JobMeta>();
  try {
    for (const f of await readdir(join(runsDir, JOBS_DIR))) {
      if (!f.endsWith(".json")) continue;
      const { runs: _runs, ...meta } = JSON.parse(await readFile(join(runsDir, JOBS_DIR, f), "utf8"));
      metas.set(meta.jobId, meta);
    }
  } catch {
    // no finished jobs yet
  }

  const jobs = new Map<string, HistoryJob>();
  for (const dir of dirs) {
    let r: RunResult;
    try {
      r = JSON.parse(await readFile(join(runsDir, dir, "report.json"), "utf8"));
    } catch {
      continue; // run still in progress, or not a run folder
    }
    const entry = toEntry(r, dir);
    // Runs from before history existed have no jobId; their folders start with a
    // per-session timestamp (YYYYMMDD-HHMMSS-...), so group by that instead.
    const legacyStamp = /^\d{8}-\d{6}(?=-)/.exec(dir)?.[0];
    const jobId = r.jobId ?? (legacyStamp ? `legacy-${legacyStamp}` : `run-${dir}`);
    let job = jobs.get(jobId);
    if (!job) {
      const meta = metas.get(jobId) ?? {
        jobId,
        // Legacy UI runs used suite names ("Exploration & smoke test", ...); CLI runs used task-file names.
        source: SUITE_TITLES.some((t) => r.taskName.startsWith(t)) ? ("ui" as const) : ("cli" as const),
        startedAt: r.startedAt,
        urls: [],
        suites: [],
        drivers: [],
      };
      job = { ...meta, runs: [], totals: totalsOf([]), summaryReport: metas.has(jobId) ? `${JOBS_DIR}/${jobId}.html` : undefined };
      jobs.set(jobId, job);
    }
    job.runs.push(entry);
  }

  for (const job of jobs.values()) {
    job.runs.sort((a, b) => a.startedAt.localeCompare(b.startedAt));
    job.totals = totalsOf(job.runs);
    if (!job.urls.length) job.urls = [...new Set(job.runs.map((r) => r.startUrl).filter(Boolean))];
    if (!job.drivers.length) job.drivers = [...new Set(job.runs.map((r) => r.driver))];
    if (!metas.has(job.jobId)) job.startedAt = job.runs[0]?.startedAt ?? job.startedAt;
  }
  return [...jobs.values()].sort((a, b) => b.startedAt.localeCompare(a.startedAt));
}
