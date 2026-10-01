import { writeFile } from "node:fs/promises";
import { basename, join, relative } from "node:path";
import type { RunResult } from "../agent/agent.js";

const esc = (s: unknown) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);

export async function writeReport(result: RunResult): Promise<{ json: string; html: string }> {
  const json = join(result.runDir, "report.json");
  const html = join(result.runDir, "report.html");
  const portable = {
    ...result,
    startScreenshot: result.startScreenshot && relative(result.runDir, result.startScreenshot),
    steps: result.steps.map((s) => ({ ...s, screenshot: s.screenshot && relative(result.runDir, s.screenshot) })),
  };
  await writeFile(json, JSON.stringify(portable, null, 2));
  await writeFile(html, renderHtml(result));
  return { json, html };
}

function renderHtml(r: RunResult): string {
  const passed = r.checks.filter((c) => c.passed).length;
  const checks = r.checks.length
    ? r.checks
        .map(
          (c) => `<tr><td><span class="pill ${c.passed ? "ok" : "bad"}">${c.passed ? "PASS" : "FAIL"}</span></td>
          <td>${esc(c.name)}</td><td class="muted">${esc(c.evidence)}</td></tr>`,
        )
        .join("")
    : `<tr><td colspan="3" class="muted">No checks recorded.</td></tr>`;

  const steps = r.steps
    .map((s) => {
      const shot = s.screenshot ? `screenshots/${basename(s.screenshot)}` : "";
      return `<li class="step ${s.ok ? "" : "err"}">
        <div class="step-head"><span class="idx">${s.index}</span><code>${esc(s.tool)}</code>
          <span class="muted">${esc(JSON.stringify(s.input))}</span><span class="dur">${s.durationMs} ms</span></div>
        ${s.note ? `<p class="note">${esc(s.note)}</p>` : ""}
        <details><summary>Result · ${esc(s.url)}</summary><pre>${esc(s.output)}</pre></details>
        ${shot ? `<a href="${shot}" target="_blank"><img loading="lazy" src="${shot}" alt="Screenshot after step ${s.index}"></a>` : ""}
      </li>`;
    })
    .join("");

  const data = r.data === null || r.data === undefined ? "" : `<section><h2>Output data</h2><pre>${esc(JSON.stringify(r.data, null, 2))}</pre></section>`;

  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>${esc(r.taskName)} · ${esc(r.driver)}</title>
<style>
:root{--bg:#f7f7f5;--card:#fff;--fg:#1d1d1b;--muted:#6b6b66;--line:#e4e4df;--ok:#1f7a4d;--okbg:#e3f3ea;--bad:#b3261e;--badbg:#fbe7e5;--warn:#8a5a00;--warnbg:#fdf1d8;--accent:#3b5bdb}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--card:#1f1f1d;--fg:#ecece8;--muted:#9a9a93;--line:#33332f;--ok:#6fd3a0;--okbg:#173828;--bad:#ff8a80;--badbg:#3d1a17;--warn:#f5c66b;--warnbg:#3a2c0e;--accent:#8ea5ff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1040px;margin:0 auto;padding:24px 16px 64px}h1{font-size:24px;margin:0 0 4px}h2{font-size:17px;margin:0 0 12px}
section{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:18px;margin-top:16px}
.muted{color:var(--muted)}.meta{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-top:12px}
.meta div{font-size:13px}.meta b{display:block;font-size:17px}
.pill{display:inline-block;padding:2px 10px;border-radius:99px;font-size:12px;font-weight:600;letter-spacing:.03em}
.ok{background:var(--okbg);color:var(--ok)}.bad{background:var(--badbg);color:var(--bad)}.warn{background:var(--warnbg);color:var(--warn)}
table{width:100%;border-collapse:collapse}td{padding:8px 6px;border-top:1px solid var(--line);vertical-align:top;font-size:14px}
pre{white-space:pre-wrap;word-break:break-word;font-size:12.5px;background:var(--bg);padding:10px;border-radius:6px;max-height:420px;overflow:auto;margin:6px 0 0}
ol{list-style:none;padding:0;margin:0}.step{border-top:1px solid var(--line);padding:12px 0}.step.err .idx{background:var(--badbg);color:var(--bad)}
.step-head{display:flex;gap:10px;align-items:baseline;flex-wrap:wrap}.step-head .muted{font-size:13px;overflow-wrap:anywhere;flex:1}
.idx{display:inline-grid;place-items:center;min-width:26px;height:26px;border-radius:6px;background:var(--bg);font-size:12px;font-weight:600}
.dur{font-size:12px;color:var(--muted)}.note{margin:6px 0 0 36px;font-style:italic;color:var(--muted);font-size:14px}
details{margin:6px 0 0 36px;font-size:13px}summary{cursor:pointer;color:var(--accent);overflow-wrap:anywhere}
.step img{display:block;margin:8px 0 0 36px;max-width:min(420px,calc(100% - 36px));border:1px solid var(--line);border-radius:6px}
code{font-size:13px;font-weight:600}a{color:var(--accent)}
</style></head><body><main>
<h1>${esc(r.taskName)}</h1>
<div class="muted">${esc(r.category)} · engine: <b>${esc(r.driver)}</b>${r.browser ? ` (${esc(r.browser)})` : ""}${r.profile && r.profile !== "default" ? ` · profile: <b>${esc(r.profile)}</b>` : ""} · ${esc(r.model)} · ${esc(r.startedAt)}</div>
<div class="muted">${esc(r.startUrl)}${r.jobId ? ` · <a href="../_jobs/${esc(r.jobId)}.html">session summary</a>` : ""}</div>
<section>
  <span class="pill ${r.status === "success" ? "ok" : r.status === "failure" || r.status === "error" ? "bad" : "warn"}">${esc(r.status.toUpperCase())}</span>
  <p>${esc(r.summary)}</p>
  ${r.error ? `<pre>${esc(r.error)}</pre>` : ""}
  <div class="meta">
    <div>Checks<b>${passed} / ${r.checks.length} passed</b></div>
    <div>Steps<b>${r.steps.length}</b></div>
    <div>Duration<b>${(r.durationMs / 1000).toFixed(1)} s</b></div>
    <div>Model requests<b>${r.usage.requests}</b></div>
    <div>Tokens in / out<b>${(r.usage.inputTokens + r.usage.cacheReadTokens + r.usage.cacheWriteTokens).toLocaleString()} / ${r.usage.outputTokens.toLocaleString()}</b></div>
    <div>Est. cost<b>$${r.costUsd.toFixed(3)}</b></div>
  </div>
</section>
<section><h2>Goal</h2><p>${esc(r.goal)}</p></section>
<section><h2>Checks</h2><table>${checks}</table></section>
<section><h2>Tools used</h2><p>${
    Object.entries(r.toolUsage ?? {})
      .sort((a, b) => b[1] - a[1])
      .map(([tool, n]) => `<code>${esc(tool)}</code> ×${n}`)
      .join(" · ") || '<span class="muted">None</span>'
  }</p></section>
${data}
<section><h2>Step timeline</h2><ol>${steps || '<li class="muted">No steps.</li>'}</ol></section>
</main></body></html>`;
}
