"""End-to-end test of the agent loop without an API key: a scripted fake Claude client drives
the real browser engines against the demo site, exercising tools, guardrails, approvals,
reports and history.

    python -m pytest python/tests
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

import pytest
from anthropic.types.beta import BetaMessage

from agentic_browser.agent.agent import RunOptions, run_task
from agentic_browser.demo_site.server import DEMO_ORIGIN, is_demo_server_up, start_demo_server
from agentic_browser.report.history import load_history, write_job_summary
from agentic_browser.report.report import write_report
from agentic_browser.tasks.schema import TaskSpec, load_task
from agentic_browser.tasks.suites import SuiteOptions, build_suite_tasks

REPO = Path(__file__).resolve().parents[2]


def _message(blocks: list[dict[str, Any]], stop: str = "tool_use") -> BetaMessage:
    return BetaMessage.model_validate(
        {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "model": "claude-opus-5-5",
            "content": blocks,
            "stop_reason": stop,
            "usage": {"input_tokens": 1000, "output_tokens": 100, "cache_read_input_tokens": 500, "cache_creation_input_tokens": 0},
        }
    )


def _tool(name: str, args: dict[str, Any], n: int) -> dict[str, Any]:
    return {"type": "tool_use", "id": f"toolu_{n}", "name": name, "input": args}


def _last_result(messages: list[dict[str, Any]]) -> str:
    content = messages[-1]["content"]
    if isinstance(content, str):
        return content
    return content[-1]["content"] if isinstance(content[-1]["content"], str) else ""


class ScriptedClient:
    """Plays a fixed tool-call script, reading refs from the latest snapshot like Claude would."""

    def __init__(self) -> None:
        self.turn = 0
        self.beta = self
        self.messages = self

    async def create(self, **kwargs: Any) -> BetaMessage:
        messages = kwargs["messages"]
        assert kwargs["tool_choice"]["disable_parallel_tool_use"] is True
        assert all(t["strict"] for t in kwargs["tools"])
        self.turn += 1
        last = _last_result(messages)
        n = self.turn
        if n == 1:
            return _message([{"type": "text", "text": "Opening the registration form."}, _tool("navigate", {"url": f"{DEMO_ORIGIN}/register.html"}, n)])
        if n == 2:
            assert "HTTP status 200" in last, last
            return _message([_tool("navigate", {"url": "https://evil.example.org/"}, n)])
        if n == 3:
            assert "Navigation refused" in last
            ref = re.search(r'\[(\d+)\] input "Full name', messages[2]["content"][0]["content"]).group(1)  # type: ignore[union-attr]
            return _message([_tool("fill", {"ref": ref, "value": "{{secret:NAME}}"}, n)])
        if n == 4:
            assert "{{secret:NAME}}" in last and "Jane Secret" not in last, last
            return _message([_tool("observe", {}, n)])
        if n == 5:
            ref = re.search(r'\[(\d+)\] button "Create account"', last).group(1)  # type: ignore[union-attr]
            return _message([_tool("click", {"ref": ref}, n)])
        if n == 6:
            assert "DENIED" in last, last
            return _message([_tool("navigate", {"url": f"{DEMO_ORIGIN}/products.html"}, n)])
        if n == 7:
            return _message([_tool("extract_table", {"ref": "page"}, n)])
        if n == 8:
            rows = json.loads(last)
            assert rows[0][0] == "SKU"
            return _message([_tool("record_check", {"name": "Table extracted", "passed": True, "evidence": f"{len(rows) - 1} rows"}, n)])
        if n == 9:
            return _message([_tool("fingerprint", {}, n)])
        if n == 10:
            assert "navigator.webdriver is true" in last
            return _message([_tool("screenshot", {"label": "catalogue"}, n)])
        if n == 11:
            return _message([{"type": "text", "text": "Thinking out loud without a tool call."}], stop="end_turn")
        if n == 12:
            assert "without calling finish" in last
            return _message([_tool("finish", {"status": "success", "summary": "Scripted run complete.", "data_json": '{"ok": true}'}, n)])
        raise AssertionError(f"unexpected turn {n}")


async def _deny(_: str) -> bool:
    return False


@pytest.mark.parametrize("engine", ["playwright", "selenium"])
def test_agent_loop(engine: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_NAME_SECRET", "Jane Secret")

    async def scenario() -> None:
        server = None if await is_demo_server_up() else await start_demo_server()
        try:
            task = TaskSpec.model_validate(
                {
                    "name": "Offline scripted run",
                    "startUrl": f"{DEMO_ORIGIN}/index.html",
                    "goal": "Exercise every tool.",
                    "allowedDomains": ["127.0.0.1"],
                    "secrets": {"NAME": "TEST_NAME_SECRET"},
                    "requireApprovalFor": ["create"],
                    "maxSteps": 20,
                }
            )
            run_dir = tmp_path / f"job-offline-{engine}"
            result = await run_task(
                task,
                RunOptions(driver=engine, headless=True, auto_approve=False, run_dir=str(run_dir), job_id="job", client=ScriptedClient(), approver=_deny),  # type: ignore[arg-type]
            )
        finally:
            if server:
                await server.cleanup()

        assert result.status == "success", result.error or result.summary
        assert result.data == {"ok": True}
        assert result.usage.requests == 12
        assert [c.name for c in result.checks] == ["Table extracted"]
        assert result.tool_usage["navigate"] == 3
        assert result.steps[0].note == "Opening the registration form."
        assert result.steps[-1].tool == "finish"

        paths = write_report(result)
        report = json.loads(Path(paths["json"]).read_text(encoding="utf-8"))
        assert report["taskName"] == "Offline scripted run" and report["driver"] == engine
        assert not Path(report["steps"][0]["screenshot"]).is_absolute()
        assert all((run_dir / s["screenshot"]).is_file() for s in report["steps"] if "screenshot" in s)
        assert "Jane Secret" not in Path(paths["html"]).read_text(encoding="utf-8")

        write_job_summary(tmp_path, {"jobId": "job", "source": "cli", "startedAt": result.started_at, "urls": [], "suites": [], "drivers": [engine]}, [result])
        (job,) = load_history(tmp_path)
        assert job["jobId"] == "job" and job["totals"]["passed"] == 1 and job["summaryReport"] == "_jobs/job.html"

    asyncio.run(scenario())


def test_task_files_load() -> None:
    tasks = {p.name: load_task(p) for p in (REPO / "tasks").glob("*.json")}
    assert len(tasks) >= 5
    # TypeScript task files may name Puppeteer; Selenium is its Python counterpart.
    assert tasks["data-extraction.json"].driver == "selenium"


def test_suites_build() -> None:
    tasks = build_suite_tasks(
        SuiteOptions(url="https://www.example.com/", suites=["explore", "forms", "vm", "custom"], allow_submissions=False, effort="low", headless=True)
    )
    assert [t.name for t in tasks] == ["Exploration & smoke test", "Form & input validation", "Virtual machine detection"]
    assert tasks[0].allowed_domains == ["example.com"]
    assert tasks[2].profile == "vm"
    assert len(tasks[1].require_approval_for) == 2
