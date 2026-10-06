"""Local web UI: paste a URL, pick suites, watch the agents work.

    python -m agentic_browser.ui.server   -> http://127.0.0.1:4180

Same HTTP + server-sent-events API as the TypeScript UI server.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote, urlparse

from aiohttp import web
from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationError, field_validator

from ..agent.agent import RunOptions, RunResult, StepRecord, run_task
from ..agent.tools import Check
from ..demo_site.server import DEMO_ORIGIN, is_demo_server_up, start_demo_server
from ..drivers import DRIVER_LABELS, DRIVER_NAMES, DriverName
from ..drivers.installed import find_installed_browsers, resolve_browser
from ..report.history import load_history, new_job_id, now_iso, write_job_summary
from ..report.report import write_report
from ..tasks.schema import Effort
from ..tasks.suites import SUITES, SuiteOptions, build_suite_tasks

PORT = int(os.environ.get("UI_PORT", 4180))
HOST = "127.0.0.1"
PUBLIC_DIR = Path(__file__).resolve().parent / "public"
RUNS_DIR = Path("runs").resolve()

# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

SuiteId = Literal["explore", "forms", "extract", "a11y", "bot", "anti-detect", "vm", "custom"]


class JobRequest(BaseModel):
    url: str
    suites: list[SuiteId] = Field(min_length=1)
    customGoal: str | None = Field(default=None, max_length=4000)
    drivers: list[Literal["playwright", "selenium", "standard"]] = Field(min_length=1)
    allowSubmissions: bool = False
    effort: Effort = "medium"
    headed: bool = False
    # Standard browser engine: "auto", an installed browser id, or a full executable path.
    browser: str = Field(default="auto", max_length=1000)

    @field_validator("url")
    @classmethod
    def _http_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("must be an http(s) URL")
        return value

    @field_validator("browser")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip() or "auto"


@dataclass
class Job:
    id: str
    request: JobRequest
    running: bool = True
    events: list[dict[str, Any]] = field(default_factory=list)
    listeners: set[asyncio.Queue[dict[str, Any] | None]] = field(default_factory=set)
    cancel: asyncio.Event = field(default_factory=asyncio.Event)
    approvals: dict[str, asyncio.Future[bool]] = field(default_factory=dict)


jobs: dict[str, Job] = {}
active_job: Job | None = None
demo_runner: web.AppRunner | None = None


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40]


def emit(job: Job, event: dict[str, Any]) -> None:
    job.events.append(event)
    for queue in job.listeners:
        queue.put_nowait(event)


def web_path(file: str) -> str:
    rel = Path(file).resolve().relative_to(RUNS_DIR)
    return "/runs/" + "/".join(quote(part, safe="") for part in rel.parts)


def public_step(step: StepRecord) -> dict[str, Any]:
    d = step.to_dict()
    if step.screenshot:
        d["screenshot"] = web_path(step.screenshot)
    return d


def public_result(r: RunResult, report_path: str) -> dict[str, Any]:
    return {
        "status": r.status,
        "summary": r.summary,
        "error": r.error,
        "checks": [c.to_dict() for c in r.checks],
        "data": r.data,
        "steps": len(r.steps),
        "durationMs": r.duration_ms,
        "costUsd": r.cost_usd,
        "report": web_path(report_path),
    }


async def run_job(job: Job) -> None:
    global demo_runner
    request = job.request
    tasks = build_suite_tasks(
        SuiteOptions(
            url=request.url,
            suites=list(request.suites),
            custom_goal=request.customGoal,
            allow_submissions=request.allowSubmissions,
            effort=request.effort,
            headless=not request.headed,
        )
    )
    plan = [
        {"key": f"{slug(t.name)}-{d}", "title": t.name, "category": t.category, "driver": d}
        for t in tasks
        for d in request.drivers
    ]
    emit(job, {"type": "plan", "url": request.url, "items": plan})

    if request.url.startswith(DEMO_ORIGIN) and not await is_demo_server_up():
        demo_runner = await start_demo_server()
        emit(job, {"type": "info", "message": f"Started the demo site at {DEMO_ORIGIN}"})

    meta: dict[str, Any] = {
        "jobId": job.id,
        "source": "ui",
        "startedAt": now_iso(),
        "urls": [request.url],
        "suites": list(request.suites),
        "drivers": list(request.drivers),
        "browser": request.browser if "standard" in request.drivers else None,
    }
    results: list[RunResult] = []
    total_cost = 0.0

    for task in tasks:
        for driver in request.drivers:
            key = f"{slug(task.name)}-{driver}"
            if job.cancel.is_set():
                emit(job, {"type": "task-end", "key": key, "result": {"status": "cancelled", "summary": "Not started (cancelled).", "checks": []}})
                continue
            emit(job, {"type": "task-start", "key": key})
            last_shot: dict[str, str | None] = {"path": None}

            def on_start(shot: str, key: str = key) -> None:
                last_shot["path"] = web_path(shot)
                emit(job, {"type": "screenshot", "key": key, "screenshot": last_shot["path"]})

            def on_step(step: StepRecord, checks: list[Check], key: str = key) -> None:
                if step.screenshot:
                    last_shot["path"] = web_path(step.screenshot)
                emit(job, {"type": "step", "key": key, "step": public_step(step), "checks": [c.to_dict() for c in checks]})

            async def approver(action: str, key: str = key, title: str = task.name, driver: DriverName = driver) -> bool:
                approval_id = str(uuid.uuid4())
                future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
                job.approvals[approval_id] = future
                emit(job, {"type": "approval", "id": approval_id, "key": key, "action": action, "screenshot": last_shot["path"], "title": title, "driver": driver})
                try:
                    approved = await future
                finally:
                    job.approvals.pop(approval_id, None)
                emit(job, {"type": "approval-resolved", "id": approval_id, "approved": approved})
                return approved

            result = await run_task(
                task,
                RunOptions(
                    driver=driver,
                    headless=task.headless,
                    auto_approve=False,
                    browser=request.browser,
                    run_dir=str(RUNS_DIR / f"{job.id}-{slug(task.name)}-{driver}"),
                    job_id=job.id,
                    cancel=job.cancel,
                    log=lambda message, key=key: emit(job, {"type": "log", "key": key, "message": message}),
                    on_start=on_start,
                    on_step=on_step,
                    approver=approver,
                ),
            )
            paths = write_report(result)
            results.append(result)
            total_cost += result.cost_usd
            emit(job, {"type": "task-end", "key": key, "result": public_result(result, paths["html"])})

    meta["finishedAt"] = now_iso()
    meta["cancelled"] = job.cancel.is_set()
    summary = web_path(write_job_summary(RUNS_DIR, meta, results)) if results else None
    emit(job, {"type": "done", "cancelled": job.cancel.is_set(), "totalCostUsd": total_cost, "summary": summary})


def resolve_approval(job: Job, approval_id: str, approved: bool) -> bool:
    future = job.approvals.get(approval_id)
    if not future or future.done():
        return False
    future.set_result(approved)
    return True


async def _job_wrapper(job: Job) -> None:
    global active_job
    try:
        await run_job(job)
    except Exception as err:  # noqa: BLE001 - surface any failure in the UI
        emit(job, {"type": "fatal", "message": str(err)})
    finally:
        job.running = False
        if active_job is job:
            active_job = None
        for queue in job.listeners:
            queue.put_nowait(None)
        job.listeners.clear()


def start_job(request: JobRequest) -> Job:
    global active_job
    job = Job(id=new_job_id(), request=request)
    jobs[job.id] = job
    active_job = job
    asyncio.get_running_loop().create_task(_job_wrapper(job))
    return job


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


def _error(status: int, message: str) -> web.Response:
    return web.json_response({"error": message}, status=status)


async def index(_: web.Request) -> web.StreamResponse:
    return web.FileResponse(PUBLIC_DIR / "index.html", headers={"cache-control": "no-store"})


async def runs_file(request: web.Request) -> web.StreamResponse:
    file = (RUNS_DIR / request.match_info["path"]).resolve()
    if not file.is_relative_to(RUNS_DIR):
        return _error(403, "forbidden")
    if not file.is_file():
        return _error(404, "not found")
    return web.FileResponse(file, headers={"cache-control": "no-store"})


async def status(_: web.Request) -> web.Response:
    return web.json_response(
        {
            "hasCredentials": bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")),
            "demoUrl": f"{DEMO_ORIGIN}/",
            "activeJob": active_job.id if active_job else None,
            "suites": SUITES,
            "drivers": {d: DRIVER_LABELS[d] for d in DRIVER_NAMES},
            "browsers": [b.to_dict() for b in find_installed_browsers()],
        }
    )


async def history(_: web.Request) -> web.Response:
    running = active_job.id if active_job else None
    out = []
    for j in await asyncio.to_thread(load_history, RUNS_DIR):
        out.append(
            {
                **j,
                "running": j["jobId"] == running,
                "summaryReport": f"/runs/{j['summaryReport']}" if j.get("summaryReport") else None,
                "runs": [{**r, "report": f"/runs/{r['report']}"} for r in j["runs"]],
            }
        )
    return web.json_response({"jobs": out})


async def create_job(request: web.Request) -> web.Response:
    if active_job:
        return _error(409, "A run is already in progress. Wait for it to finish or cancel it.")
    try:
        body = JobRequest.model_validate(await request.json())
    except (ValidationError, json.JSONDecodeError) as err:
        return _error(400, str(err))
    if body.suites == ["custom"] and not (body.customGoal or "").strip():
        return _error(400, "Enter a custom goal, or pick at least one other suite.")
    if "standard" in body.drivers:
        try:
            resolve_browser(body.browser)
        except ValueError as err:
            return _error(400, str(err))
    return web.json_response({"id": start_job(body).id}, status=201)


def _job(request: web.Request) -> Job:
    job = jobs.get(request.match_info["job_id"])
    if not job:
        raise web.HTTPNotFound(text=json.dumps({"error": "unknown job"}), content_type="application/json")
    return job


async def job_events(request: web.Request) -> web.StreamResponse:
    job = _job(request)
    res = web.StreamResponse(headers={"content-type": "text/event-stream", "cache-control": "no-store", "connection": "keep-alive"})
    await res.prepare(request)
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    backlog = list(job.events)
    if job.running:
        job.listeners.add(queue)
    try:
        for event in backlog:
            await res.write(f"data: {json.dumps(event)}\n\n".encode())
        while job.running or not queue.empty():
            event = await queue.get()
            if event is None:
                break
            await res.write(f"data: {json.dumps(event)}\n\n".encode())
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    finally:
        job.listeners.discard(queue)
    return res


async def job_approval(request: web.Request) -> web.Response:
    job = _job(request)
    try:
        approved = (await request.json())["approved"]
        if not isinstance(approved, bool):
            raise TypeError
    except (KeyError, TypeError, json.JSONDecodeError):
        return _error(400, '"approved" must be a boolean')
    if not resolve_approval(job, request.match_info["approval_id"], approved):
        return _error(404, "approval not pending")
    return web.json_response({"ok": True})


async def job_cancel(request: web.Request) -> web.Response:
    job = _job(request)
    job.cancel.set()
    for approval_id in list(job.approvals):
        resolve_approval(job, approval_id, False)
    emit(job, {"type": "info", "message": "Cancelling… the current step will finish first."})
    return web.json_response({"ok": True})


@web.middleware
async def json_errors(request: web.Request, handler: Any) -> web.StreamResponse:
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except Exception as err:  # noqa: BLE001
        return _error(500, str(err))


def create_app() -> web.Application:
    app = web.Application(middlewares=[json_errors])
    app.router.add_get("/", index)
    app.router.add_get("/runs/{path:.+}", runs_file)
    app.router.add_get("/api/status", status)
    app.router.add_get("/api/history", history)
    app.router.add_post("/api/jobs", create_job)
    app.router.add_get("/api/jobs/{job_id}/events", job_events)
    app.router.add_post("/api/jobs/{job_id}/approvals/{approval_id}", job_approval)
    app.router.add_post("/api/jobs/{job_id}/cancel", job_cancel)

    async def cleanup(_: web.Application) -> None:
        if demo_runner:
            await demo_runner.cleanup()

    app.on_cleanup.append(cleanup)
    return app


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m agentic_browser.ui.server")
    parser.add_argument("--port", type=int, default=PORT, help=f"Port to listen on (default: UI_PORT or {PORT})")
    port = parser.parse_args().port
    load_dotenv(".env")
    print(f"Agentic Web Tester UI (Python): http://{HOST}:{port}", flush=True)
    if not os.environ.get("ANTHROPIC_API_KEY") and not os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        print("Note: ANTHROPIC_API_KEY is not set. Add it to .env, or sign in with `ant auth login`.", flush=True)
    web.run_app(create_app(), host=HOST, port=port, print=None)


if __name__ == "__main__":
    main()
