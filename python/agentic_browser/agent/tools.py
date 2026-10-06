from __future__ import annotations

import base64
import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Awaitable, Callable, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict

from ..detection.fingerprint import red_flags
from ..detection.profiles import PROFILES
from ..drivers import BrowserDriver, PageSnapshot
from ..tasks.schema import TaskSpec

FinishStatus = Literal["success", "failure", "blocked"]


@dataclass
class Check:
    name: str
    passed: bool
    evidence: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FinishResult:
    status: FinishStatus
    summary: str
    data: Any


@dataclass
class ToolContext:
    driver: BrowserDriver
    task: TaskSpec
    # placeholder name -> real value; never shown to the model.
    secrets: dict[str, str]
    # Asks the operator to approve a sensitive action.
    approve: Callable[[str], Awaitable[bool]]
    # Returns a file path for a screenshot with the given label.
    screenshot_path: Callable[[str], str]
    last_allowed_url: str
    checks: list[Check] = field(default_factory=list)


@dataclass
class ToolOutcome:
    content: str | list[dict[str, Any]]
    is_error: bool = False
    finish: FinishResult | None = None
    # Screenshot saved for the report, if any.
    screenshot: str | None = None


# ---------------------------------------------------------------------------
# Tool definitions. All use strict mode so inputs always match the schema.
# ---------------------------------------------------------------------------


def _obj(properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


_REF_PROP = {"type": "string", "description": 'Element ref number from the latest snapshot, e.g. "12"'}

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {**tool, "strict": True}
    for tool in [
        {
            "name": "navigate",
            "description": "Open a URL in the browser. Only URLs on the task's allowed domains are permitted. Returns a page snapshot.",
            "input_schema": _obj({"url": {"type": "string", "description": "Absolute URL"}}),
        },
        {
            "name": "observe",
            "description": (
                "Return a snapshot of the current page: URL, title, visible interactive elements (each with a ref), and visible text. "
                "Call this whenever you are unsure what the page currently shows."
            ),
            "input_schema": _obj({}),
        },
        {
            "name": "click",
            "description": "Click an element (button, link, checkbox, radio, tab...). Returns a fresh page snapshot.",
            "input_schema": _obj({"ref": _REF_PROP}),
        },
        {
            "name": "fill",
            "description": "Clear a text input or textarea and type a value. To enter a secret, pass its placeholder exactly, e.g. {{secret:PASSWORD}}.",
            "input_schema": _obj({"ref": _REF_PROP, "value": {"type": "string"}}),
        },
        {
            "name": "select_option",
            "description": "Choose an option in a <select> by its value or visible label. Returns a fresh page snapshot.",
            "input_schema": _obj({"ref": _REF_PROP, "option": {"type": "string"}}),
        },
        {
            "name": "press_key",
            "description": "Press a keyboard key on the focused element, e.g. Enter, Tab, Escape, ArrowDown. Returns a fresh page snapshot.",
            "input_schema": _obj({"key": {"type": "string"}}),
        },
        {
            "name": "get_text",
            "description": 'Get the full visible text of one element, or of the whole page when ref is "page" (up to 20k chars).',
            "input_schema": _obj({"ref": {"type": "string", "description": 'Element ref, or "page"'}}),
        },
        {
            "name": "extract_table",
            "description": 'Extract an HTML table as rows of cell text (first row is usually the header). Pass the table\'s ref, or "page" for the first table on the page.',
            "input_schema": _obj({"ref": {"type": "string", "description": 'Element ref, or "page"'}}),
        },
        {
            "name": "wait_for_text",
            "description": "Wait until the given text appears on the page. Use after actions that load content asynchronously.",
            "input_schema": _obj({"text": {"type": "string"}, "timeout_ms": {"type": "integer", "description": "Maximum wait, 500-30000"}}),
        },
        {
            "name": "audit_page",
            "description": (
                "Return deterministic facts about the current page for accessibility, SEO and content checks: title, lang, meta description, "
                "heading outline, images missing alt text, broken images, unlabeled form controls, links/buttons without an accessible name, "
                "duplicate ids, and the page's internal links. Prefer this over guessing from snapshots."
            ),
            "input_schema": _obj({}),
        },
        {
            "name": "fingerprint",
            "description": (
                "Return the browser fingerprint the current page can read (webdriver flag, user agent, platform, client hints, languages, "
                "time zone, CPU/memory, screen, WebGL GPU) plus the red flags a bot/fraud detector should raise for it, and the signals "
                "this run's browser profile deliberately presents. Use it in detection tests to know what the site ought to catch."
            ),
            "input_schema": _obj({}),
        },
        {
            "name": "screenshot",
            "description": "Capture the visible viewport as an image, for visual checks (layout, images, colors) that text snapshots cannot show.",
            "input_schema": _obj({"label": {"type": "string", "description": "Short label for the report"}}),
        },
        {
            "name": "record_check",
            "description": (
                "Record the outcome of one verification (a success criterion, test case or validation rule) with concrete evidence from the page. "
                "Call once per check; the checks appear in the final report."
            ),
            "input_schema": _obj(
                {
                    "name": {"type": "string", "description": "What was checked"},
                    "passed": {"type": "boolean"},
                    "evidence": {"type": "string", "description": "What you observed that justifies the result"},
                }
            ),
        },
        {
            "name": "finish",
            "description": (
                "End the task. Call exactly once, after recording every check. status: success = goal achieved; failure = the goal could not be "
                "achieved or a check failed; blocked = you were stopped (denied approval, disallowed domain, missing data, CAPTCHA, login wall)."
            ),
            "input_schema": _obj(
                {
                    "status": {"type": "string", "enum": ["success", "failure", "blocked"]},
                    "summary": {"type": "string", "description": "Two to five sentences for a human reviewer"},
                    "data_json": {"type": "string", "description": 'Extracted or produced data as a JSON string; "null" if none'},
                }
            ),
        },
    ]
]

# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _Navigate(_Input):
    url: str


class _Ref(_Input):
    ref: str


class _Fill(_Input):
    ref: str
    value: str


class _Select(_Input):
    ref: str
    option: str


class _Key(_Input):
    key: str


class _Wait(_Input):
    text: str
    timeout_ms: int


class _Label(_Input):
    label: str


class _RecordCheck(_Input):
    name: str
    passed: bool
    evidence: str


class _Finish(_Input):
    status: FinishStatus
    summary: str
    data_json: str


def is_allowed_url(url: str, allowed_domains: list[str]) -> bool:
    if url == "about:blank":
        return True
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    return any(host == d.lower() or host.endswith(f".{d.lower()}") for d in allowed_domains)


def format_snapshot(s: PageSnapshot) -> str:
    lines = [f"URL: {s['url']}", f"Title: {s['title']}", "", "Interactive elements:"]
    for e in s["elements"]:
        line = f"[{e['ref']}] {e['tag']}"
        if e.get("type") and e["type"] != "text":
            line += f"[type={e['type']}]"
        if e.get("role"):
            line += f"[role={e['role']}]"
        line += f' "{e["name"]}"'
        if "value" in e:
            line += f' value="{e["value"]}"'
        if "checked" in e:
            line += " checked" if e["checked"] else " unchecked"
        if e.get("required"):
            line += " required"
        if e.get("disabled"):
            line += " disabled"
        if e.get("invalid"):
            line += f' INVALID("{e["invalid"]}")'
        if e.get("href"):
            line += f" href={e['href']}"
        if e.get("options"):
            line += f" options=[{' | '.join(e['options'])}]"
        lines.append(line)
    if not s["elements"]:
        lines.append("(none visible)")
    lines += ["", "Visible text:", s["text"] or "(empty)"]
    if s.get("truncated"):
        lines += ["", "(snapshot truncated - use get_text or extract_table for more)"]
    return "\n".join(lines)


def _redact(text: str, secrets: dict[str, str]) -> str:
    for name, value in secrets.items():
        if value:
            text = text.replace(value, f"{{{{secret:{name}}}}}")
    return text


def _resolve_secrets(value: str, secrets: dict[str, str]) -> str:
    def sub(m: re.Match[str]) -> str:
        name = m.group(1)
        if name not in secrets:
            raise ValueError(f"Unknown secret placeholder {{{{secret:{name}}}}}")
        return secrets[name]

    return re.sub(r"\{\{secret:([A-Za-z0-9_]+)\}\}", sub, value)


def _dumps(value: Any) -> str:
    return json.dumps(value, indent=1, ensure_ascii=False)


async def _snapshot_text(ctx: ToolContext) -> str:
    return _redact(format_snapshot(await ctx.driver.snapshot()), ctx.secrets)


async def _guard_domain(ctx: ToolContext) -> str | None:
    """After an action, make sure the browser is still on an allowed domain."""
    current = ctx.driver.url()
    if is_allowed_url(current, ctx.task.allowed_domains):
        ctx.last_allowed_url = current
        return None
    await ctx.driver.goto(ctx.last_allowed_url)
    return f"The action led to {current}, which is outside the allowed domains. The browser was returned to {ctx.last_allowed_url}."


async def execute_tool(name: str, raw_input: Any, ctx: ToolContext) -> ToolOutcome:
    driver = ctx.driver

    async def after_action(message: str) -> ToolOutcome:
        violation = await _guard_domain(ctx)
        if violation:
            return ToolOutcome(f"{violation}\n\n{await _snapshot_text(ctx)}", is_error=True)
        return ToolOutcome(f"{message}\n\n{await _snapshot_text(ctx)}")

    if name == "navigate":
        url = _Navigate.model_validate(raw_input).url
        if not is_allowed_url(url, ctx.task.allowed_domains):
            return ToolOutcome(f"Navigation refused: {url} is not on the allowed domains ({', '.join(ctx.task.allowed_domains)}).", is_error=True)
        await driver.goto(url)
        status = driver.last_status()
        return await after_action(f"Navigated to {url} (HTTP status {status if status is not None else 'unknown'}).")

    if name == "observe":
        return ToolOutcome(await _snapshot_text(ctx))

    if name == "fingerprint":
        fp = await driver.fingerprint()
        profile = PROFILES[ctx.task.profile]
        return ToolOutcome(
            _dumps(
                {
                    "profile": {"id": profile.id, "label": profile.label, "simulatedSignals": profile.simulated_signals},
                    "redFlags": red_flags(fp),
                    "fingerprint": fp,
                }
            )
        )

    if name == "audit_page":
        return ToolOutcome(_redact(_dumps({"httpStatus": driver.last_status(), **(await driver.audit())}), ctx.secrets))

    if name == "click":
        ref = _Ref.model_validate(raw_input).ref
        target = await driver.describe(ref)
        if not target:
            return ToolOutcome(f"No element with ref {ref}. Call observe to get current refs.", is_error=True)
        label = f'click {target["tag"]} "{target["name"]}"'
        needs_approval = any(re.search(p, target["name"], re.I) for p in ctx.task.require_approval_for)
        if needs_approval and not await ctx.approve(label):
            return ToolOutcome(
                f"The operator DENIED approval for: {label}. Do not retry this action. Continue with the parts of the task "
                'that don\'t need it; if nothing else can be done, finish with status "blocked".',
                is_error=True,
            )
        await driver.click(ref)
        return await after_action(f'Clicked {target["tag"]} "{target["name"]}".')

    if name == "fill":
        args = _Fill.model_validate(raw_input)
        await driver.fill(args.ref, _resolve_secrets(args.value, ctx.secrets))
        info = await driver.describe(args.ref)
        detail = f'"{info["name"]}" -> value="{info.get("value", "")}"' if info else ""
        return ToolOutcome(_redact(f"Filled [{args.ref}] {detail}", ctx.secrets))

    if name == "select_option":
        args = _Select.model_validate(raw_input)
        selected = await driver.select(args.ref, args.option)
        return await after_action(f"Selected {json.dumps(selected)} in [{args.ref}].")

    if name == "press_key":
        key = _Key.model_validate(raw_input).key
        await driver.press(key)
        return await after_action(f"Pressed {key}.")

    if name == "get_text":
        ref = _Ref.model_validate(raw_input).ref
        return ToolOutcome(_redact(await driver.get_text(ref), ctx.secrets) or "(no text)")

    if name == "extract_table":
        ref = _Ref.model_validate(raw_input).ref
        rows = await driver.extract_table(ref)
        return ToolOutcome(_redact(json.dumps(rows, ensure_ascii=False), ctx.secrets))

    if name == "wait_for_text":
        args = _Wait.model_validate(raw_input)
        found = await driver.wait_for_text(args.text, min(max(args.timeout_ms, 500), 30_000))
        return ToolOutcome(f'Text "{args.text}" is present.') if found else ToolOutcome(f'Timed out waiting for "{args.text}".', is_error=True)

    if name == "screenshot":
        label = _Label.model_validate(raw_input).label
        path = ctx.screenshot_path(label)
        image = await driver.screenshot(path)
        return ToolOutcome(
            [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(image).decode()}},
                {"type": "text", "text": f'Screenshot "{label}" of {driver.url()}'},
            ],
            screenshot=path,
        )

    if name == "record_check":
        args = _RecordCheck.model_validate(raw_input)
        ctx.checks.append(Check(args.name, args.passed, args.evidence))
        return ToolOutcome(f'Recorded check "{args.name}": {"PASSED" if args.passed else "FAILED"}.')

    if name == "finish":
        args = _Finish.model_validate(raw_input)
        try:
            data: Any = json.loads(args.data_json)
        except json.JSONDecodeError:
            data = args.data_json  # keep the raw string if it isn't valid JSON
        return ToolOutcome("Task finished.", finish=FinishResult(args.status, args.summary, data))

    return ToolOutcome(f'Unknown tool "{name}".', is_error=True)
