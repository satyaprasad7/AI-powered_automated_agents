from __future__ import annotations

import html
import json
from pathlib import Path

from ..agent.agent import RunResult


def esc(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def pill_class(status: str) -> str:
    return "ok" if status == "success" else "bad" if status in ("failure", "error") else "warn"


THEME = """:root{--bg:#f7f7f5;--card:#fff;--fg:#1d1d1b;--muted:#6b6b66;--line:#e4e4df;--ok:#1f7a4d;--okbg:#e3f3ea;--bad:#b3261e;--badbg:#fbe7e5;--warn:#8a5a00;--warnbg:#fdf1d8;--accent:#3b5bdb}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--card:#1f1f1d;--fg:#ecece8;--muted:#9a9a93;--line:#33332f;--ok:#6fd3a0;--okbg:#173828;--bad:#ff8a80;--badbg:#3d1a17;--warn:#f5c66b;--warnbg:#3a2c0e;--accent:#8ea5ff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
.ok{background:var(--okbg);color:var(--ok)}.bad{background:var(--badbg);color:var(--bad)}.warn{background:var(--warnbg);color:var(--warn)}
"""

RUN_CSS = (
    THEME
    + """main{max-width:1040px;margin:0 auto;padding:24px 16px 64px}h1{font-size:24px;margin:0 0 4px}h2{font-size:17px;margin:0 0 12px}
section{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:18px;margin-top:16px}
.muted{color:var(--muted)}.meta{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-top:12px}
.meta div{font-size:13px}.meta b{display:block;font-size:17px}
.pill{display:inline-block;padding:2px 10px;border-radius:99px;font-size:12px;font-weight:600;letter-spacing:.03em}
table{width:100%;border-collapse:collapse}td{padding:8px 6px;border-top:1px solid var(--line);vertical-align:top;font-size:14px}
pre{white-space:pre-wrap;word-break:break-word;font-size:12.5px;background:var(--bg);padding:10px;border-radius:6px;max-height:420px;overflow:auto;margin:6px 0 0}
ol{list-style:none;padding:0;margin:0}.step{border-top:1px solid var(--line);padding:12px 0}.step.err .idx{background:var(--badbg);color:var(--bad)}
.step-head{display:flex;gap:10px;align-items:baseline;flex-wrap:wrap}.step-head .muted{font-size:13px;overflow-wrap:anywhere;flex:1}
.idx{display:inline-grid;place-items:center;min-width:26px;height:26px;border-radius:6px;background:var(--bg);font-size:12px;font-weight:600}
.dur{font-size:12px;color:var(--muted)}.note{margin:6px 0 0 36px;font-style:italic;color:var(--muted);font-size:14px}
details{margin:6px 0 0 36px;font-size:13px}summary{cursor:pointer;color:var(--accent);overflow-wrap:anywhere}
.step img{display:block;margin:8px 0 0 36px;max-width:min(420px,calc(100% - 36px));border:1px solid var(--line);border-radius:6px}
code{font-size:13px;font-weight:600}a{color:var(--accent)}
"""
)


def write_report(result: RunResult) -> dict[str, str]:
    run_dir = Path(result.run_dir)
    json_path, html_path = run_dir / "report.json", run_dir / "report.html"
    json_path.write_text(json.dumps(result.to_dict(relative_paths=True), indent=2, ensure_ascii=False), encoding="utf-8")
    html_path.write_text(render_html(result), encoding="utf-8")
    return {"json": str(json_path), "html": str(html_path)}


def render_html(r: RunResult) -> str:
    passed = sum(1 for c in r.checks if c.passed)
    checks = (
        "".join(
            f'<tr><td><span class="pill {"ok" if c.passed else "bad"}">{"PASS" if c.passed else "FAIL"}</span></td>'
            f'<td>{esc(c.name)}</td><td class="muted">{esc(c.evidence)}</td></tr>'
            for c in r.checks
        )
        or '<tr><td colspan="3" class="muted">No checks recorded.</td></tr>'
    )

    steps = []
    for s in r.steps:
        shot = f"screenshots/{Path(s.screenshot).name}" if s.screenshot else ""
        steps.append(
            f'<li class="step {"" if s.ok else "err"}">'
            f'<div class="step-head"><span class="idx">{s.index}</span><code>{esc(s.tool)}</code>'
            f'<span class="muted">{esc(json.dumps(s.input, ensure_ascii=False))}</span><span class="dur">{s.duration_ms} ms</span></div>'
            + (f'<p class="note">{esc(s.note)}</p>' if s.note else "")
            + f"<details><summary>Result · {esc(s.url)}</summary><pre>{esc(s.output)}</pre></details>"
            + (f'<a href="{shot}" target="_blank"><img loading="lazy" src="{shot}" alt="Screenshot after step {s.index}"></a>' if shot else "")
            + "</li>"
        )

    data = "" if r.data is None else f"<section><h2>Output data</h2><pre>{esc(json.dumps(r.data, indent=2, ensure_ascii=False))}</pre></section>"
    tools = " · ".join(f"<code>{esc(t)}</code> ×{n}" for t, n in sorted(r.tool_usage.items(), key=lambda kv: -kv[1])) or '<span class="muted">None</span>'
    browser = f" ({esc(r.browser)})" if r.browser else ""
    profile = f" · profile: <b>{esc(r.profile)}</b>" if r.profile and r.profile != "default" else ""
    session = f' · <a href="../_jobs/{esc(r.job_id)}.html">session summary</a>' if r.job_id else ""
    u = r.usage
    tokens_in = u.input_tokens + u.cache_read_tokens + u.cache_write_tokens

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(r.task_name)} · {esc(r.driver)}</title>
<style>
{RUN_CSS}</style></head><body><main>
<h1>{esc(r.task_name)}</h1>
<div class="muted">{esc(r.category)} · engine: <b>{esc(r.driver)}</b>{browser}{profile} · {esc(r.model)} · {esc(r.started_at)}</div>
<div class="muted">{esc(r.start_url)}{session}</div>
<section>
  <span class="pill {pill_class(r.status)}">{esc(r.status.upper())}</span>
  <p>{esc(r.summary)}</p>
  {f"<pre>{esc(r.error)}</pre>" if r.error else ""}
  <div class="meta">
    <div>Checks<b>{passed} / {len(r.checks)} passed</b></div>
    <div>Steps<b>{len(r.steps)}</b></div>
    <div>Duration<b>{r.duration_ms / 1000:.1f} s</b></div>
    <div>Model requests<b>{u.requests}</b></div>
    <div>Tokens in / out<b>{tokens_in:,} / {u.output_tokens:,}</b></div>
    <div>Est. cost<b>${r.cost_usd:.3f}</b></div>
  </div>
</section>
<section><h2>Goal</h2><p>{esc(r.goal)}</p></section>
<section><h2>Checks</h2><table>{checks}</table></section>
<section><h2>Tools used</h2><p>{tools}</p></section>
{data}
<section><h2>Step timeline</h2><ol>{"".join(steps) or '<li class="muted">No steps.</li>'}</ol></section>
</main></body></html>"""
