"""Generic test suites that can be pointed at any URL. The web UI turns
"paste a link, run everything" into one TaskSpec per selected suite."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from ..detection.profiles import PROFILES
from .schema import Effort, TaskSpec

SUITES: dict[str, dict[str, str]] = {
    "explore": {"title": "Exploration & smoke test", "category": "testing"},
    "forms": {"title": "Form & input validation", "category": "testing"},
    "extract": {"title": "Structured data extraction", "category": "data-extraction"},
    "a11y": {"title": "Accessibility & content validation", "category": "validation"},
    "bot": {"title": "Bot detection", "category": "security"},
    "anti-detect": {"title": "Anti-detect browser detection", "category": "security"},
    "vm": {"title": "Virtual machine detection", "category": "security"},
    "custom": {"title": "Custom goal", "category": "workflow"},
}


@dataclass
class SuiteOptions:
    url: str
    suites: list[str]
    # When False, every submit-like click must be approved by the operator.
    allow_submissions: bool
    effort: Effort
    headless: bool
    custom_goal: str | None = None


# Obviously fake data; example.com is reserved for documentation, so nothing reaches a real inbox.
TEST_DATA = {
    "fullName": "Alex Tester",
    "firstName": "Alex",
    "lastName": "Tester",
    "email": "qa.agent.test@example.com",
    "phone": "+1 555 0100",
    "company": "Example QA Co",
    "jobTitle": "QA Engineer",
    "address": "123 Test Street, Springfield, IL 62701",
    "message": "Automated QA test message - please ignore.",
    "searchQuery": "test",
}

# Each detection suite visits the site with one browser profile and lists what the site should catch.
DETECTION_SUITES: dict[str, tuple[str, list[str]]] = {
    "bot": ("default", ["The site detects the automated browser (flag, challenge, block or quality failure)"]),
    "anti-detect": (
        "anti-detect",
        [
            "The site detects the automated browser",
            "The site identifies the spoofed / inconsistent fingerprint (OS vs platform, GPU vs platform, time zone vs locale)",
        ],
    ),
    "vm": (
        "vm",
        [
            "The site detects the automated browser",
            "The site identifies the virtual-machine signals (virtual GPU, low CPU/memory, VM screen size)",
        ],
    ),
}

# Clicks matching these always need approval: money, deletion, irreversible account changes.
ALWAYS_GATED = [r"\b(pay|purchase|buy|checkout|place order|delete|remove|cancel (my )?(account|subscription)|unsubscribe|transfer|donate)\b"]
# Clicks matching these need approval unless submissions are allowed.
SUBMIT_GATED = [
    r"\b(submit|send|sign ?up|register|create|save|apply|book|subscribe|post|publish|confirm|log ?in|sign ?in|request|get started|contact us|join)\b",
]

_SEVERITY = {"enum": ["low", "medium", "high"]}


def allowed_domains_for(url: str) -> list[str]:
    host = (urlparse(url).hostname or "").lower()
    return [host[4:] if host.startswith("www.") else host]


def build_suite_tasks(opts: SuiteOptions) -> list[TaskSpec]:
    base: dict[str, Any] = {
        "startUrl": opts.url,
        "allowedDomains": allowed_domains_for(opts.url),
        "effort": opts.effort,
        "headless": opts.headless,
        "driver": "playwright",
        "secrets": {},
        "requireApprovalFor": ALWAYS_GATED if opts.allow_submissions else [*ALWAYS_GATED, *SUBMIT_GATED],
    }
    submit_rule = (
        "Form submissions are allowed, but use only the provided test data."
        if opts.allow_submissions
        else "Form submissions need operator approval, which may be denied. If a submit is denied, trigger validation without submitting "
        "(e.g. Tab out of each field) and inspect the messages instead."
    )

    tasks: list[TaskSpec] = []
    for suite in opts.suites:
        meta = SUITES[suite]
        common = {**base, "name": meta["title"], "category": meta["category"]}
        spec: dict[str, Any] | None = None
        if suite == "explore":
            spec = {
                **common,
                "maxSteps": 30,
                "goal": (
                    "Smoke-test the site like a QA engineer. Call audit_page on the start page to get its internal links, then visit up to 8 "
                    "distinct, important internal pages (main navigation first). For each page, verify it loads (HTTP status below 400), "
                    "has a meaningful title and main content, and shows no error messages or obviously broken content. Record one check per "
                    "page visited. Do not submit any forms in this suite."
                ),
                "successCriteria": [
                    "The start page loads with a meaningful title and content",
                    "Main navigation links lead to working pages",
                    "No error pages, error messages or broken content were found",
                ],
                "outputSchema": {
                    "type": "object",
                    "properties": {
                        "pagesVisited": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "url": {"type": "string"},
                                    "title": {"type": "string"},
                                    "httpStatus": {"type": "integer"},
                                    "ok": {"type": "boolean"},
                                    "notes": {"type": "string"},
                                },
                            },
                        },
                        "issues": {
                            "type": "array",
                            "items": {"type": "object", "properties": {"url": {"type": "string"}, "description": {"type": "string"}, "severity": _SEVERITY}},
                        },
                    },
                },
            }
        elif suite == "forms":
            spec = {
                **common,
                "maxSteps": 45,
                "inputs": TEST_DATA,
                "goal": (
                    "Find the forms on the start page. If it has none, look for up to 3 linked pages likely to have one (contact, sign-up, "
                    "search, newsletter). For each form (at most 3), identify its purpose, required fields and input types. Then test "
                    "validation with negative cases: required fields left empty, a malformed email, and wrong-type or out-of-range values "
                    "where applicable. Use the input data as the valid baseline. "
                    + submit_rule
                    + " Record one check per test case, passing only when the form clearly rejects bad input with a helpful message. "
                    "If no forms exist, record that as a check and finish with success."
                ),
                "successCriteria": [
                    "Required fields are enforced",
                    "Malformed input (for example an invalid email) is rejected with a clear message",
                    "Valid input is accepted",
                ],
                "outputSchema": {
                    "type": "object",
                    "properties": {
                        "forms": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {"page": {"type": "string"}, "purpose": {"type": "string"}, "fields": {"type": "array", "items": {"type": "string"}}},
                            },
                        },
                        "defects": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "testCase": {"type": "string"},
                                    "expected": {"type": "string"},
                                    "actual": {"type": "string"},
                                    "severity": _SEVERITY,
                                },
                            },
                        },
                    },
                },
            }
        elif suite == "extract":
            spec = {
                **common,
                "maxSteps": 20,
                "goal": (
                    "Work out what kind of page the start URL is, then extract its main structured content as JSON: the page type, a "
                    "two-sentence summary, key facts, every table (use extract_table), repeated items such as products, listings, "
                    "articles, people or pricing plans (with their visible fields), and any contact details shown. If the content is "
                    "paginated, follow up to 3 pages. Do not click anything that submits data."
                ),
                "successCriteria": [
                    "The main content type of the page was identified",
                    "All visible repeated items / tables on the covered pages were extracted without duplicates",
                    "Numbers, prices and dates were captured exactly as shown",
                ],
                "outputSchema": {
                    "type": "object",
                    "properties": {
                        "pageType": {"type": "string"},
                        "summary": {"type": "string"},
                        "keyFacts": {"type": "array", "items": {"type": "string"}},
                        "items": {"type": "array", "items": {"type": "object"}},
                        "tables": {"type": "array", "items": {"type": "object", "properties": {"caption": {"type": "string"}, "rows": {"type": "array"}}}},
                        "contacts": {"type": "object", "properties": {"emails": {"type": "array"}, "phones": {"type": "array"}, "addresses": {"type": "array"}}},
                    },
                },
            }
        elif suite == "a11y":
            spec = {
                **common,
                "maxSteps": 20,
                "goal": (
                    "Audit the start page and up to 2 other key pages from the main navigation for basic accessibility and content "
                    "quality. Run audit_page on each page and take one screenshot of the start page to look for visible layout problems. "
                    "Evaluate these rules: page title present; html lang set; exactly one h1 and a logical heading order; images have "
                    "alt text; no broken images; form controls have labels; links and buttons have accessible names; no duplicate ids; "
                    "meta description present; viewport meta present. Record one check per rule, combining the pages and naming the "
                    "offending page in the evidence. Give an overall score from 0 to 100."
                ),
                "successCriteria": [],
                "outputSchema": {
                    "type": "object",
                    "properties": {
                        "pagesAudited": {"type": "array", "items": {"type": "string"}},
                        "score": {"type": "integer"},
                        "findings": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {"rule": {"type": "string"}, "page": {"type": "string"}, "severity": _SEVERITY, "detail": {"type": "string"}},
                            },
                        },
                    },
                },
            }
        elif suite in DETECTION_SUITES:
            profile_id, expectations = DETECTION_SUITES[suite]
            profile = PROFILES[profile_id]
            spec = {
                **common,
                "profile": profile_id,
                "maxSteps": 20,
                "inputs": TEST_DATA,
                "goal": (
                    f'Test whether this site\'s bot / fraud defenses detect a suspicious visitor. The browser runs the "{profile.label}" '
                    "profile. First call fingerprint to see exactly which signals this browser presents. Then look at how the site "
                    'responds: a block or "access denied" page, HTTP 403/429, a CAPTCHA or bot challenge, a warning, or a '
                    "quality/fraud-check message (common on survey platforms). If nothing appears on the start page and it is a survey "
                    "or form, move through its first screen with the input data (submits still need approval), since many platforms "
                    "only screen on submit. Where the site names its detection reasons, check each simulated signal against them. "
                    "Record one check per success criterion: it passes only if you saw the site detect that signal. If the site shows "
                    "no reaction, the check fails with a note that detection may be happening invisibly on the server. Do not try to "
                    "solve challenges or avoid detection. Finish with success when every check passed, otherwise failure."
                ),
                "successCriteria": expectations,
                "outputSchema": {
                    "type": "object",
                    "properties": {
                        "profile": {"type": "string"},
                        "simulatedSignals": {"type": "array", "items": {"type": "string"}},
                        "siteReaction": {"type": "string", "description": "What the site did: blocked, challenged, flagged, or nothing visible"},
                        "detectedSignals": {"type": "array", "items": {"type": "string"}},
                        "missedSignals": {"type": "array", "items": {"type": "string"}},
                    },
                },
            }
        elif suite == "custom" and (opts.custom_goal or "").strip():
            spec = {
                **common,
                "maxSteps": 35,
                "inputs": TEST_DATA,
                "goal": f"{opts.custom_goal.strip()}\n\n{submit_rule} Use the input data wherever the task needs form values.",  # type: ignore[union-attr]
                "successCriteria": ["The requested goal was achieved, verified against what the page shows"],
            }
        if spec:
            tasks.append(TaskSpec.model_validate(spec))
    return tasks
