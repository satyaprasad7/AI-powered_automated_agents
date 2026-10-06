"""Report history.

Every run folder under runs/ keeps its report.json; runs that belong to one UI job or
CLI invocation share a jobId. Each finished job also gets a final summary report at
runs/_jobs/<jobId>.html (+ .json) that links every task report and compares engines.
The JSON matches the TypeScript implementation, so runs from both appear in one history.
"""
from __future__ import annotations

import json
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ..agent.agent import RunResult
from ..drivers.types import DRIVER_LABELS
from ..tasks.suites import SUITES
from .report import THEME, esc, pill_class

JOBS_DIR = "_jobs"
# Includes the combined detection suite's title from before it was split into bot / anti-detect / VM.
SUITE_TITLES = [s["title"] for s in SUITES.values()] + ["Bot, anti-detect & VM detection"]


def engine_label(driver: str) -> str:
    return DRIVER_LABELS.get(driver, driver)


def new_job_id() -> str:
    return f"{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _to_entry(r: dict[str, Any], run_dir: str) -> dict[str, Any]:
    """Compact per-run entry used by the history list."""
    checks = r.get("checks") or []
    entry = {
        "dir": run_dir,
        "taskName": r.get("taskName"),
        "category": r.get("category"),
        "driver": r.get("driver"),
        "profile": r.get("profile") or "default",
        "browser": r.get("browser"),
        "startUrl": r.get("startUrl") or "",
        "status": r.get("status"),
        "summary": r.get("summary"),
        "checksPassed": sum(1 for c in checks if c.get("passed")),
        "checksTotal": len(checks),
        "steps": len(r.get("steps") or []),
        "durationMs": r.get("durationMs") or 0,
        "costUsd": r.get("costUsd") or 0,
        "startedAt": r.get("startedAt") or "",
        "toolUsage": r.get("toolUsage") or {},
        "report": f"{run_dir}/report.html",
    }
    return {k: v for k, v in entry.items() if v is not None}


def _totals(runs: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "runs": len(runs),
        "passed": sum(1 for r in runs if r["status"] == "success"),
        "failed": sum(1 for r in runs if r["status"] != "success"),
        "costUsd": sum(r["costUsd"] for r in runs),
        "durationMs": sum(r["durationMs"] for r in runs),
    }


# ---------------------------------------------------------------------------
# Job summary ("final report")
# ---------------------------------------------------------------------------


def write_job_summary(runs_dir: str | Path, meta: dict[str, Any], results: list[RunResult]) -> str:
    out = Path(runs_dir, JOBS_DIR)
    out.mkdir(parents=True, exist_ok=True)
    runs = [_to_entry(r.to_dict(), Path(r.run_dir).name) for r in results]
    meta = {k: v for k, v in meta.items() if v is not None}
    (out / f"{meta['jobId']}.json").write_text(json.dumps({**meta, "runs": runs}, indent=2, ensure_ascii=False), encoding="utf-8")
    html_path = out / f"{meta['jobId']}.html"
    html_path.write_text(_render_job_summary(meta, runs), encoding="utf-8")
    return str(html_path)


SUMMARY_CSS = (
    THEME
    + """main{max-width:1080px;margin:0 auto;padding:24px 16px 64px}h1{font-size:24px;margin:0 0 4px}h2{font-size:17px;margin:0 0 12px}
section{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:18px;margin-top:16px}
a{color:var(--accent)}.muted{color:var(--muted)}.small{font-size:12.5px}
.meta{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px}.meta div{font-size:13px}.meta b{display:block;font-size:18px}
.scroll{overflow-x:auto}table{width:100%;border-collapse:collapse}th,td{padding:8px 6px;border-top:1px solid var(--line);text-align:left;vertical-align:top;font-size:14px}
th{font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}tr.foot td{font-weight:600}
.pill{display:inline-block;padding:1px 9px;border-radius:99px;font-size:12px;font-weight:600}
"""
)


def _render_job_summary(meta: dict[str, Any], runs: list[dict[str, Any]]) -> str:
    t = _totals(runs)
    drivers = list(dict.fromkeys(r["driver"] for r in runs))
    task_names = list(dict.fromkeys(r["taskName"] for r in runs))

    # Engine comparison: one row per task, one column per engine.
    matrix = ""
    if len(drivers) > 1:
        rows = []
        for name in task_names:
            cells = []
            for d in drivers:
                r = next((x for x in runs if x["taskName"] == name and x["driver"] == d), None)
                cells.append(
                    f'<td><span class="pill {pill_class(r["status"])}">{esc(r["status"])}</span> '
                    f'<span class="muted">{r["checksPassed"]}/{r["checksTotal"]} · {r["durationMs"] / 1000:.0f}s</span></td>'
                    if r
                    else "<td class='muted'>not run</td>"
                )
            rows.append(f"<tr><td>{esc(name)}</td>{''.join(cells)}</tr>")
        foot = []
        for d in drivers:
            tt = _totals([r for r in runs if r["driver"] == d])
            foot.append(f"<td>{tt['passed']}/{tt['runs']} passed · {tt['durationMs'] / 1000:.0f}s · ${tt['costUsd']:.3f}</td>")
        matrix = (
            '<section><h2>Engine comparison</h2><div class="scroll"><table>'
            f"<tr><th>Task</th>{''.join(f'<th>{esc(engine_label(d))}</th>' for d in drivers)}</tr>"
            f"{''.join(rows)}"
            f'<tr class="foot"><td>Per engine</td>{"".join(foot)}</tr>'
            "</table></div></section>"
        )

    rows = "".join(
        f"""<tr>
        <td><a href="../{quote(r['dir'])}/report.html">{esc(r['taskName'])}</a><div class="muted small">{esc(r['summary'])}</div></td>
        <td>{esc(engine_label(r['driver']))}{f'<div class="muted small">{esc(r["browser"])}</div>' if r.get('browser') else ''}{f'<div class="muted small">{esc(r["profile"])}</div>' if r['profile'] != 'default' else ''}</td>
        <td><span class="pill {pill_class(r['status'])}">{esc(r['status'])}</span></td>
        <td>{r['checksPassed']}/{r['checksTotal']}</td>
        <td>{r['steps']}</td>
        <td>{r['durationMs'] / 1000:.1f}s</td>
        <td>${r['costUsd']:.3f}</td>
      </tr>"""
        for r in runs
    )

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Test session {esc(meta['jobId'])}</title>
<style>
{SUMMARY_CSS}</style></head><body><main>
<h1>Test session summary</h1>
<div class="muted">{esc(meta['jobId'])} · {esc(meta['source'].upper())} · {esc(meta['startedAt'])}{' · cancelled' if meta.get('cancelled') else ''}</div>
<div class="muted">{', '.join(esc(u) for u in meta.get('urls', []))}</div>
<section><div class="meta">
  <div>Runs<b>{t['runs']}</b></div><div>Passed<b>{t['passed']}</b></div><div>Not passed<b>{t['failed']}</b></div>
  <div>Engines<b>{esc(', '.join(engine_label(d) for d in drivers))}</b></div><div>Total time<b>{t['durationMs'] / 1000:.0f} s</b></div><div>Total cost<b>${t['costUsd']:.3f}</b></div>
</div></section>
{matrix}
<section><h2>Task reports</h2><div class="scroll"><table>
<tr><th>Task</th><th>Engine</th><th>Status</th><th>Checks</th><th>Steps</th><th>Time</th><th>Cost</th></tr>{rows}
</table></div></section>
</main></body></html>"""


# ---------------------------------------------------------------------------
# History index
# ---------------------------------------------------------------------------


def load_history(runs_dir: str | Path) -> list[dict[str, Any]]:
    root = Path(runs_dir)
    if not root.is_dir():
        return []
    metas: dict[str, dict[str, Any]] = {}
    jobs_dir = root / JOBS_DIR
    if jobs_dir.is_dir():
        for f in jobs_dir.glob("*.json"):
            try:
                meta = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            meta.pop("runs", None)
            metas[meta["jobId"]] = meta

    jobs: dict[str, dict[str, Any]] = {}
    for d in sorted(p for p in root.iterdir() if p.is_dir() and p.name != JOBS_DIR):
        try:
            r = json.loads((d / "report.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue  # run still in progress, or not a run folder
        entry = _to_entry(r, d.name)
        # Runs from before history existed have no jobId; their folders start with a
        # per-session timestamp (YYYYMMDD-HHMMSS-...), so group by that instead.
        legacy = re.match(r"^\d{8}-\d{6}(?=-)", d.name)
        job_id = r.get("jobId") or (f"legacy-{legacy.group(0)}" if legacy else f"run-{d.name}")
        job = jobs.get(job_id)
        if not job:
            meta = metas.get(job_id) or {
                "jobId": job_id,
                # Legacy UI runs used suite names ("Exploration & smoke test", ...); CLI runs used task-file names.
                "source": "ui" if any(str(r.get("taskName", "")).startswith(t) for t in SUITE_TITLES) else "cli",
                "startedAt": r.get("startedAt", ""),
                "urls": [],
                "suites": [],
                "drivers": [],
            }
            job = {**meta, "runs": [], "totals": _totals([])}
            if job_id in metas:
                job["summaryReport"] = f"{JOBS_DIR}/{job_id}.html"
            jobs[job_id] = job
        job["runs"].append(entry)

    for job in jobs.values():
        job["runs"].sort(key=lambda r: r["startedAt"])
        job["totals"] = _totals(job["runs"])
        if not job.get("urls"):
            job["urls"] = list(dict.fromkeys(r["startUrl"] for r in job["runs"] if r["startUrl"]))
        if not job.get("drivers"):
            job["drivers"] = list(dict.fromkeys(r["driver"] for r in job["runs"]))
        if job["jobId"] not in metas and job["runs"]:
            job["startedAt"] = job["runs"][0]["startedAt"]
    return sorted(jobs.values(), key=lambda j: j["startedAt"], reverse=True)
