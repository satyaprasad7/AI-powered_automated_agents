"""Task file validation. Task files are the same JSON (camelCase keys) the TypeScript implementation reads."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from pydantic.alias_generators import to_camel

Effort = Literal["low", "medium", "high", "xhigh", "max"]


class TaskSpec(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore")

    name: str = Field(min_length=1)
    # testing | form-submission | data-extraction | workflow | validation - used for reporting only.
    category: str = "general"
    description: str | None = None
    driver: Literal["playwright", "selenium", "standard"] = "playwright"
    start_url: str
    # Natural-language objective handed to the agent.
    goal: str = Field(min_length=1)
    # Checks the agent must verify and record before finishing.
    success_criteria: list[str] = Field(default_factory=list)
    # Non-sensitive input data the agent may type into the page.
    inputs: dict[str, str] = Field(default_factory=dict)
    # Secret placeholders: name -> environment variable. The agent only ever sees
    # {{secret:NAME}}; the executor substitutes the real value on fill.
    secrets: dict[str, str] = Field(default_factory=dict)
    # Optional JSON Schema (as an object) describing the data `finish` should return.
    output_schema: dict[str, Any] | None = None
    # Hostnames the agent may visit (subdomains included).
    allowed_domains: list[str] = Field(min_length=1)
    max_steps: int = Field(default=30, gt=0, le=200)
    # Regexes matched against an element's accessible name; matching clicks need operator approval.
    require_approval_for: list[str] = Field(default_factory=list)
    effort: Effort = "medium"
    # Browser profile for bot / fraud detection testing (see detection/profiles.py).
    profile: Literal["default", "vm", "anti-detect"] = "default"
    headless: bool = True
    # Which installed browser the "standard" driver uses: "auto" (first found of Chrome, Edge,
    # Chrome Beta, Edge Beta, Brave, Vivaldi), one of those ids, or a full path to a
    # Chromium-based executable. Ignored by other drivers.
    browser: str = "auto"

    @field_validator("driver", mode="before")
    @classmethod
    def _puppeteer_alias(cls, value: Any) -> Any:
        # Task files written for the TypeScript version may say "puppeteer"; Selenium is its Python counterpart.
        return "selenium" if value == "puppeteer" else value

    @field_validator("start_url")
    @classmethod
    def _url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https", "file") or (parsed.scheme != "file" and not parsed.netloc):
            raise ValueError("must be an absolute URL")
        return value


def load_task(path: str | Path) -> TaskSpec:
    raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    try:
        return TaskSpec.model_validate(raw)
    except ValidationError as err:
        raise ValueError(f"Invalid task file {path}:\n{err}") from None
