from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal

import anthropic

from ..detection.profiles import PROFILES
from ..drivers import DriverName, LaunchOptions, create_driver
from ..tasks.schema import Effort, TaskSpec
from .prompts import SYSTEM_PROMPT, task_brief
from .tools import TOOL_DEFINITIONS, Check, FinishResult, ToolContext, ToolOutcome, execute_tool, format_snapshot

MODEL = "claude-opus-5-5"
# Server-side refusal fallback: a declined request is re-run on a recommended model.
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# USD per million tokens for claude-opus-5-5.
PRICE = {"input": 4, "output": 20, "cache_write": 5, "cache_read": 0.2}
# Tools whose effect on the page is worth a screenshot in the report.
STATEFUL_TOOLS = {"navigate", "click", "fill", "select_option", "press_key"}
MAX_NUDGES = 2


def _env_number(name: str, fallback: float) -> float:
    try:
        value = float(os.environ[name])
    except (KeyError, ValueError):
        return fallback
    return value if value >= 0 else fallback


# Per-request model timeout and retry budget. Without these the SDK waits 10 minutes per
# attempt and retries twice, so a provider that accepts the connection but never answers
# stalls a run for about 30 minutes. A turn can legitimately take a while (up to 16k output
# tokens), so the default stays generous; worst case is about (retries + 1) x timeout.
LLM_TIMEOUT_S = _env_number("AGENT_LLM_TIMEOUT", 120)
LLM_MAX_RETRIES = int(_env_number("AGENT_LLM_MAX_RETRIES", 1))

RunStatus = Literal["success", "failure", "blocked", "incomplete", "error"]


@dataclass
class StepRecord:
    index: int
    tool: str
    input: Any
    ok: bool
    output: str
    url: str
    duration_ms: int
    screenshot: str | None = None
    # Claude's own narration right before this step, if any.
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = {
            "index": self.index,
            "tool": self.tool,
            "input": self.input,
            "ok": self.ok,
            "output": self.output,
            "url": self.url,
            "durationMs": self.duration_ms,
            "screenshot": self.screenshot,
            "note": self.note,
        }
        return {k: v for k, v in d.items() if v is not None}


@dataclass
class Usage:
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "requests": self.requests,
            "inputTokens": self.input_tokens,
            "outputTokens": self.output_tokens,
            "cacheReadTokens": self.cache_read_tokens,
            "cacheWriteTokens": self.cache_write_tokens,
        }


@dataclass
class RunResult:
    task_name: str
    category: str
    goal: str
    driver: DriverName
    model: str
    started_at: str
    run_dir: str
    start_url: str
    profile: str
    duration_ms: int = 0
    status: RunStatus = "incomplete"
    summary: str = ""
    data: Any = None
    checks: list[Check] = field(default_factory=list)
    steps: list[StepRecord] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    cost_usd: float = 0.0
    # The browser build that ran, e.g. "Microsoft Edge 141.0.3537.57".
    browser: str | None = None
    # Groups the runs of one UI job or CLI invocation in the history.
    job_id: str | None = None
    # How many times each agent tool was called.
    tool_usage: dict[str, int] = field(default_factory=dict)
    start_screenshot: str | None = None
    error: str | None = None

    def to_dict(self, relative_paths: bool = False) -> dict[str, Any]:
        """The report.json shape, identical to the TypeScript implementation's so both share one history."""

        def rel(p: str | None) -> str | None:
            return os.path.relpath(p, self.run_dir) if p and relative_paths else p

        d = {
            "taskName": self.task_name,
            "category": self.category,
            "goal": self.goal,
            "driver": self.driver,
            "model": self.model,
            "startedAt": self.started_at,
            "durationMs": self.duration_ms,
            "status": self.status,
            "summary": self.summary,
            "data": self.data,
            "checks": [c.to_dict() for c in self.checks],
            "steps": [{**s.to_dict(), **({"screenshot": rel(s.screenshot)} if s.screenshot else {})} for s in self.steps],
            "usage": self.usage.to_dict(),
            "costUsd": self.cost_usd,
            "runDir": self.run_dir,
            "startUrl": self.start_url,
            "profile": self.profile,
            "browser": self.browser,
            "jobId": self.job_id,
            "toolUsage": self.tool_usage,
            "startScreenshot": rel(self.start_screenshot),
            "error": self.error,
            "implementation": "python",
        }
        return {k: v for k, v in d.items() if v is not None or k == "data"}


@dataclass
class RunOptions:
    driver: DriverName
    headless: bool
    # Approve every sensitive action without asking.
    auto_approve: bool
    run_dir: str
    job_id: str | None = None
    effort: Effort | None = None
    # Overrides the task's standard-browser choice (auto, an id or a path).
    browser: str | None = None
    client: anthropic.AsyncAnthropic | None = None
    log: Callable[[str], None] | None = None
    # Called after every tool step (used by the web UI for live progress).
    on_step: Callable[[StepRecord, list[Check]], None] | None = None
    # Called with the screenshot of the start page, before the first step.
    on_start: Callable[[str], None] | None = None
    # Custom approval channel (e.g. the web UI). Overrides the terminal prompt.
    approver: Callable[[str], Awaitable[bool]] | None = None
    # Set to cancel the run between steps and abort an in-flight model request.
    cancel: asyncio.Event | None = None


class _Cancelled(Exception):
    pass


async def _cancellable(coro: Awaitable[Any], cancel: asyncio.Event | None) -> Any:
    if cancel is None:
        return await coro
    task = asyncio.ensure_future(coro)
    waiter = asyncio.ensure_future(cancel.wait())
    done, _ = await asyncio.wait({task, waiter}, return_when=asyncio.FIRST_COMPLETED)
    if task in done:
        waiter.cancel()
        return task.result()
    task.cancel()
    raise _Cancelled()


def _slug_label(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", label, flags=re.I)[:40]


async def run_task(task: TaskSpec, options: RunOptions) -> RunResult:
    log = options.log or (lambda _m: None)
    client = options.client or anthropic.AsyncAnthropic(timeout=LLM_TIMEOUT_S, max_retries=LLM_MAX_RETRIES)
    started = time.monotonic()
    screenshots_dir = Path(options.run_dir, "screenshots")
    screenshots_dir.mkdir(parents=True, exist_ok=True)

    result = RunResult(
        task_name=task.name,
        category=task.category,
        goal=task.goal,
        driver=options.driver,
        model=MODEL,
        started_at=datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        run_dir=options.run_dir,
        start_url=task.start_url,
        profile=task.profile,
        job_id=options.job_id,
    )

    secrets: dict[str, str] = {}
    for name, env_var in task.secrets.items():
        if env_var not in os.environ:
            raise ValueError(f"Secret {name} needs environment variable {env_var}, which is not set")
        secrets[name] = os.environ[env_var]

    driver = create_driver(options.driver)
    shot_counter = 0

    def screenshot_path(label: str) -> str:
        nonlocal shot_counter
        shot_counter += 1
        return str(screenshots_dir / f"{shot_counter:03d}-{_slug_label(label)}.jpg")

    def approve_action(action: str) -> Awaitable[bool]:
        if options.approver and not options.auto_approve:
            return options.approver(action)
        return _terminal_approve(action, options.auto_approve, log)

    ctx = ToolContext(
        driver=driver,
        task=task,
        secrets=secrets,
        approve=approve_action,
        screenshot_path=screenshot_path,
        last_allowed_url=task.start_url,
        checks=result.checks,
    )
    cancelled = lambda: bool(options.cancel and options.cancel.is_set())  # noqa: E731

    try:
        await driver.launch(LaunchOptions(headless=options.headless, profile=PROFILES[task.profile], browser=options.browser or task.browser))
        result.browser = driver.browser_info()
        await driver.goto(task.start_url)
        log(f"[{driver.name}: {result.browser}] opened {task.start_url}")
        start_shot = screenshot_path("start")
        try:
            await driver.screenshot(start_shot)
            result.start_screenshot = start_shot
            if options.on_start:
                options.on_start(start_shot)
        except Exception:  # noqa: BLE001 - a missing screenshot shouldn't fail the run
            pass

        initial = format_snapshot(await driver.snapshot())
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": f"{task_brief(task, list(secrets))}\n\nThe start URL is already open. Current page:\n\n{initial}"}
        ]
        nudges = 0
        pending_note: str | None = None

        while True:
            if cancelled():
                result.summary = "Cancelled by the user."
                break
            response = await _cancellable(
                client.beta.messages.create(
                    model=MODEL,
                    max_tokens=16000,
                    betas=[FALLBACK_BETA],
                    fallbacks="default",
                    output_config={"effort": options.effort or task.effort},
                    cache_control={"type": "ephemeral"},
                    system=SYSTEM_PROMPT,
                    tools=TOOL_DEFINITIONS,
                    # Browser actions are order-dependent, so take them one at a time.
                    tool_choice={"type": "auto", "disable_parallel_tool_use": True},
                    messages=messages,
                ),
                options.cancel,
            )
            _add_usage(result.usage, response.usage)

            # Append the full content unchanged (thinking and fallback blocks included).
            messages.append({"role": "assistant", "content": [b.to_dict() for b in response.content]})

            narration = "\n".join(b.text.strip() for b in response.content if b.type == "text" and b.text.strip())
            if narration:
                log(f"  claude: {narration.splitlines()[0][:160]}")
                pending_note = narration

            if response.stop_reason == "refusal":
                details = getattr(response, "stop_details", None)
                result.status = "blocked"
                result.summary = f"The model declined to continue ({getattr(details, 'category', None) or 'unspecified'})."
                break
            if response.stop_reason == "max_tokens":
                raise RuntimeError("Model response hit max_tokens; the turn was truncated")

            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if not tool_uses:
                if nudges >= MAX_NUDGES:
                    result.summary = narration or "The agent stopped without calling finish."
                    break
                nudges += 1
                messages.append({"role": "user", "content": "You ended your turn without calling finish. Continue the task, or call finish now."})
                continue

            tool_results: list[dict[str, Any]] = []
            finish: FinishResult | None = None

            for use in tool_uses:
                step_index = len(result.steps) + 1
                over_budget = step_index > task.max_steps and use.name != "finish"
                t0 = time.monotonic()
                if over_budget:
                    outcome = ToolOutcome("Step budget exhausted. Only finish is allowed now.", is_error=True)
                else:
                    try:
                        outcome = await execute_tool(use.name, use.input, ctx)
                    except Exception as err:  # noqa: BLE001 - tool errors go back to the model
                        outcome = ToolOutcome(f"Error: {_error_message(err)}", is_error=True)

                screenshot = outcome.screenshot
                if not screenshot and use.name in STATEFUL_TOOLS and not over_budget:
                    screenshot = screenshot_path(f"step-{step_index}-{use.name}")
                    try:
                        await driver.screenshot(screenshot)
                    except Exception:  # noqa: BLE001
                        screenshot = None

                remaining = task.max_steps - step_index
                content = outcome.content
                if isinstance(content, str) and 0 <= remaining <= 3 and use.name != "finish":
                    content += f"\n\n[Step budget: {remaining} step(s) left. Wrap up and call finish.]"

                step = StepRecord(
                    index=step_index,
                    tool=use.name,
                    input=use.input,
                    ok=not outcome.is_error,
                    output=outcome.content[:3000] if isinstance(outcome.content, str) else "[image]",
                    url=driver.url(),
                    duration_ms=int((time.monotonic() - t0) * 1000),
                    screenshot=screenshot,
                    note=pending_note,
                )
                result.steps.append(step)
                pending_note = None
                if options.on_step:
                    options.on_step(step, result.checks)
                log(f"  {'x' if outcome.is_error else '✓'} {step_index}. {use.name} {_summarize_input(use.input)}")

                block: dict[str, Any] = {"type": "tool_result", "tool_use_id": use.id, "content": content}
                if outcome.is_error:
                    block["is_error"] = True
                tool_results.append(block)
                if outcome.finish:
                    finish = outcome.finish

            messages.append({"role": "user", "content": tool_results})

            if finish:
                result.status = finish.status
                result.summary = finish.summary
                result.data = finish.data
                break
            if len(result.steps) > task.max_steps + 3:
                result.summary = f"Stopped after exceeding the {task.max_steps}-step budget."
                break
    except (_Cancelled, asyncio.CancelledError):
        result.summary = "Cancelled by the user."
    except Exception as err:  # noqa: BLE001 - any failure ends the run with an error report
        if cancelled():
            result.summary = "Cancelled by the user."
        else:
            result.status = "error"
            result.error = _error_message(err)
            result.summary = result.summary or f"Run aborted: {result.error}"
            log(f"  error: {result.error}")
    finally:
        try:
            await driver.close()
        except Exception:  # noqa: BLE001
            pass
        result.duration_ms = int((time.monotonic() - started) * 1000)
        result.cost_usd = _cost_of(result.usage)
        for s in result.steps:
            result.tool_usage[s.tool] = result.tool_usage.get(s.tool, 0) + 1
    return result


async def _terminal_approve(action: str, auto_approve: bool, log: Callable[[str], None]) -> bool:
    if auto_approve:
        log(f"  approval auto-granted: {action}")
        return True
    if not sys.stdin.isatty():
        log(f"  approval denied (non-interactive): {action}")
        return False
    answer = await asyncio.to_thread(input, f"\n  APPROVAL NEEDED - agent wants to: {action}\n  Allow? [y/N] ")
    return bool(re.fullmatch(r"y(es)?", answer.strip(), re.I))


def _add_usage(usage: Usage, u: Any) -> None:
    usage.requests += 1
    usage.input_tokens += u.input_tokens
    usage.output_tokens += u.output_tokens
    usage.cache_read_tokens += getattr(u, "cache_read_input_tokens", None) or 0
    usage.cache_write_tokens += getattr(u, "cache_creation_input_tokens", None) or 0


def _cost_of(u: Usage) -> float:
    return (
        u.input_tokens * PRICE["input"]
        + u.output_tokens * PRICE["output"]
        + u.cache_write_tokens * PRICE["cache_write"]
        + u.cache_read_tokens * PRICE["cache_read"]
    ) / 1_000_000


def _error_message(err: BaseException) -> str:
    if isinstance(err, anthropic.APITimeoutError):
        return f"AI provider timed out (no response within {LLM_TIMEOUT_S:g}s, {LLM_MAX_RETRIES} retries)"
    if isinstance(err, anthropic.APIConnectionError):
        return f"Could not reach the AI provider: {err.__cause__ or err}"
    if isinstance(err, anthropic.APIStatusError):
        return f"Claude API error {err.status_code}: {err.message}"
    # Playwright/Selenium errors carry long call logs; the first lines are the useful part.
    return "\n".join(str(err).splitlines()[:4])[:600] or type(err).__name__


def _summarize_input(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False)
    return "" if text == "{}" else f"{text[:97]}..." if len(text) > 100 else text
