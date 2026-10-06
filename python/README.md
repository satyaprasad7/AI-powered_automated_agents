# Agentic Browser Automation: Python implementation

A Python port of the TypeScript project in the repo root. It has the same agent, the same tools, guardrails, detection profiles, suites, reports, history and web UI, built on the official `anthropic` Python SDK.

| TypeScript | Python |
|---|---|
| `@anthropic-ai/sdk` | `anthropic` (`AsyncAnthropic`) |
| Playwright (Node) | Playwright for Python (async API) |
| **Puppeteer** | **Selenium WebDriver** (ChromeDriver, found automatically by Selenium Manager) |
| Standard browser (installed Chrome/Edge via Playwright) | Same |
| zod | pydantic |
| `node:http` UI + demo servers | aiohttp |

Puppeteer has no maintained Python port, so **Selenium** is the second, independent automation stack you compare against Playwright. Task files that say `"driver": "puppeteer"` run on Selenium, and `--driver puppeteer` is accepted as an alias.

Both implementations share `tasks/`, `demo-site/public/`, `runs/` and `.env` at the repo root. Their `report.json` files have the same shape, so runs from either one show up together in the History tab and in session summaries.

## Quick start

Run everything from the **repo root**:

```bash
python -m venv python/.venv
python/.venv/Scripts/pip install -e python          # macOS/Linux: python/.venv/bin/pip
python/.venv/Scripts/python -m playwright install chromium
python/.venv/Scripts/python -m agentic_browser.smoke    # all engines vs the demo site, no API key needed
cp .env.example .env                                # add ANTHROPIC_API_KEY
python/.venv/Scripts/python -m agentic_browser.ui.server   # web UI at http://127.0.0.1:4180
```

Activate the venv to drop the `python/.venv/Scripts/` prefix. The commands below assume it is active.

## Command line

```bash
python -m agentic_browser list
python -m agentic_browser run tasks/form-submission.json
python -m agentic_browser run tasks/*.json --driver both          # Playwright + Selenium
python -m agentic_browser run tasks/*.json --driver all           # + standard (installed) browser
python -m agentic_browser browsers
python -m agentic_browser run tasks/*.json --driver standard --browser msedge
python -m agentic_browser run tasks/expense-workflow.json --headed
python -m agentic_browser run tasks/survey-vm-detection.json --effort low
python -m agentic_browser.demo_site.server                        # demo site on http://127.0.0.1:4173
python -m agentic_browser.ui.server --port 4181                   # UI on another port (or set UI_PORT)
```

The flags, exit codes (0 when every run is `success`, 1 otherwise, 2 for usage errors), report layout and session summaries are the same as the TypeScript CLI. `AGENT_LLM_TIMEOUT` and `AGENT_LLM_MAX_RETRIES` work the same way too.

## Tests

```bash
python -m agentic_browser.smoke                 # drivers only: forms, tables, workflow, detection profiles
python -m pytest python/tests                   # agent loop with a scripted fake Claude client (no API key)
```

The offline test drives the real browsers through a scripted conversation. It covers navigation, the domain allowlist, secret substitution and redaction, approval denial, table extraction, fingerprinting, screenshots, the "no tool call" nudge and `finish`, then checks `report.json`, `report.html` and the history index.

## How the Selenium engine works

- Selenium's API is blocking, so every call runs on one dedicated worker thread and the agent loop stays async.
- HTTP status and network idle come from ChromeDriver's performance log (DevTools `Network.*` events). No script is injected into the page to track requests.
- Detection profiles use DevTools commands: `Page.addScriptToEvaluateOnNewDocument` for the fingerprint overrides, `Emulation.setTimezoneOverride`, `Emulation.setDeviceMetricsOverride`, plus `--user-agent` and `--lang`.
- Date, time, color and range inputs are set via the native value setter plus `input`/`change` events, because typing into Chrome's date pickers depends on the locale.
- Screenshots are JPEG (quality 60) through `Page.captureScreenshot`, matching the other engines.

## Layout

```
python/agentic_browser/
  cli.py                  run / list / browsers
  smoke.py                driver smoke test (no LLM)
  agent/agent.py          agent loop, usage/cost, approvals, cancellation
  agent/tools.py          tool schemas, executor, guardrails
  agent/prompts.py        system prompt + task brief
  drivers/                BrowserDriver ABC, Playwright, Selenium, standard browser, in-page scripts
  detection/              fingerprint script + red flags, detection profiles
  report/report.py        per-run HTML + JSON
  report/history.py       sessions, summaries, history index
  tasks/schema.py         task file validation (pydantic)
  tasks/suites.py         paste-a-URL suites
  ui/server.py            web UI server (aiohttp, SSE, approvals)
  ui/public/index.html    the UI page
  demo_site/server.py     demo site API, serving ../demo-site/public
python/tests/             offline end-to-end tests
```
