# Agentic Browser Automation (Claude + Playwright / Puppeteer)

A prototype of LLM-driven browser agents. You describe a job as a goal. Claude reads the page, decides the next action, drives a real browser through **Playwright or Puppeteer**, verifies the outcome and writes an HTML/JSON report. It covers the same ground as the Agentic Survey Tester, generalized to:

| Capability | Demo task |
|---|---|
| Form submission | [tasks/form-submission.json](tasks/form-submission.json) |
| Data extraction (paginated) | [tasks/data-extraction.json](tasks/data-extraction.json) |
| Testing / validation (finds a seeded bug) | [tasks/registration-validation-test.json](tasks/registration-validation-test.json) |
| Multi-step workflow with human approval | [tasks/expense-workflow.json](tasks/expense-workflow.json) |

## Quick start

```bash
npm install
npx playwright install chromium      # Puppeteer downloads its own Chrome during npm install
npm run smoke                        # checks both drivers against the demo site; no API key needed
cp .env.example .env                 # add ANTHROPIC_API_KEY (or use `ant auth login`)
npm run ui                           # web UI at http://127.0.0.1:4180
```

## Web UI: paste a link and run everything

`npm run ui` opens a local page where you paste a URL, choose test suites, and watch the agents work live:

| Suite | What the agent does |
|---|---|
| Exploration & smoke test | Visits up to 8 key internal pages. Checks HTTP status, title, content and error messages |
| Form & input validation | Finds up to 3 forms and runs negative tests (empty required fields, bad email, out-of-range values) with obviously fake test data |
| Structured data extraction | Returns the page type, summary, key facts, tables, repeated items (products, listings…) and contacts as JSON |
| Accessibility & content | Uses a deterministic `audit_page` tool (alt text, labels, headings, lang, meta tags, duplicate ids, broken images) and gives a 0–100 score |
| Bot, anti-detect & VM detection | Visits the site three times: as a standard automated browser, as a virtual machine, and as an anti-detect browser. Checks whether the site's bot/fraud defenses flag each one (see below) |
| Custom goal (optional) | Any free-text instruction, e.g. "search for 'laptop' and verify the first result has a price" |

- Choose Playwright, Puppeteer or **Both** to compare engines on the same site.
- Each task card shows the current step, the latest browser screenshot, pass/fail checks, extracted JSON, cost, and a link to the full report.
- **Approvals happen in the browser.** Unless you tick "Allow form submissions", every submit-like click pauses the agent and shows the action and a screenshot, with Allow / Deny buttons. Payment and delete clicks always ask. If you deny, the agent continues with what it can do without that action.
- The agent stays on the domain you entered (subdomains included). Runs can be cancelled.
- Only test sites you own or are authorized to test. Sites with CAPTCHAs or login walls end as `blocked`.

## Report history

Every run is kept under `runs/` and grouped into a **session**: one click of "Run tests" in the UI, or one `npm run agent -- run …` command.

- **History tab** in the UI lists all sessions, newest first. Each shows the URL, the source (web UI or command line), the engines used, passed/total and cost, with a row per task: engine, browser profile, status, checks, steps, the agent tools it used (`click ×4`, `fingerprint ×1`…), time and cost. Every row links to that run's full report.
- **Filters**: engine (Playwright / Puppeteer), suite, status, source, and a text search on URL or task name. The **engine stats** cards at the top (runs, pass rate, average time, cost per engine) update with the filters, so you can compare the two tools on any slice of history.
- **Session summary report** (`runs/_jobs/<session>.html`): the final report for a session. It has totals, an **engine comparison** table (each task under Playwright vs Puppeteer) and links to every task report. The command line prints its path, the UI links it when a run finishes, and every task report links back to it.
- **Run again** prefills the New test form with a past session's URL, suites and engines.

Runs saved before sessions existed are grouped by their folder timestamp and labelled as older runs.

## Bot, anti-detect browser & virtual machine detection

This suite checks whether **your** site's defenses catch suspicious clients, which matters for survey fraud, fake sign-ups and ad fraud. Each run uses a browser profile ([src/detection/profiles.ts](src/detection/profiles.ts)) that reproduces the signals of one kind of client:

| Profile | Signals it presents |
|---|---|
| Standard automation | `navigator.webdriver`, headless user agent |
| Virtual machine | VMware virtual GPU (`VMware SVGA 3D`), 2 CPU cores / 2 GB memory, 1024×768 screen |
| Anti-detect browser | Spoofed macOS user agent while the platform and client hints leak Windows, an Apple GPU on Windows, a Tokyo time zone with an en-US locale |

The agent calls the `fingerprint` tool to see what the browser exposes and which red flags a good detector should raise. It then observes the site's reaction: a block page, HTTP 403/429, a CAPTCHA, a warning or a quality-check result. A check passes only when the site visibly detected the signal. If the site shows nothing, the check fails with a note that detection might be happening invisibly on the server.

**This tests detection. It does not evade it.** No profile hides automation (`navigator.webdriver` stays true), and the agent is told never to solve CAPTCHAs or get around challenges. The spoofed values are deliberately inconsistent, the way real anti-detect browsers and VMs leak.

The demo site's survey ([/survey.html](demo-site/public/survey.html)) runs a respondent quality check with a **seeded gap**: its virtual-GPU blocklist contains VirtualBox but not VMware. The virtual-machine run should therefore fail its "VM signals identified" check, while the other two profiles are flagged. Try it from the command line:

```bash
npm run agent -- run tasks/survey-vm-detection.json
```

To use a profile in your own task file, add `"profile": "vm"` or `"profile": "anti-detect"`.

## Command line

Other commands:

```bash
npm run agent -- list
npm run agent -- run tasks/*.json --driver both        # same tasks on Playwright and Puppeteer
npm run agent -- run tasks/expense-workflow.json --headed
npm run agent -- run tasks/data-extraction.json --effort high
npm run demo                                           # serve the demo site on http://127.0.0.1:4173
```

Tasks that target the demo site start it automatically. Each run writes `runs/<timestamp>-<task>-<driver>/report.html` and `report.json`, plus a screenshot after every page-changing step. The CLI exits non-zero if any run doesn't end in `success`, so it can gate a CI job.

## How it works

```
task.json ──► Agent loop (Claude, claude-opus-5-5) ──tool calls──► Tool executor ──► BrowserDriver
                    ▲                                                   │         ├─ PlaywrightDriver
                    └──────── page snapshot / results ◄─────────────────┘         └─ PuppeteerDriver
                                                                  guardrails: domain allowlist, step budget,
                                                                  approval gate, secret placeholders
```

- **Page understanding.** An in-page script ([src/drivers/page-scripts.ts](src/drivers/page-scripts.ts)) tags each visible interactive element with a stable `data-agent-ref`. It returns a compact list of those elements (role, accessible name, value, validation error, select options) plus the visible text. Claude acts on refs, not brittle CSS selectors. Screenshots are available as a tool for visual checks.
- **Driver-agnostic.** Both drivers implement one `BrowserDriver` interface ([src/drivers/types.ts](src/drivers/types.ts)). Switch per task (`"driver"`) or per run (`--driver`).
- **Tools** ([src/agent/tools.ts](src/agent/tools.ts)): `navigate`, `observe`, `click`, `fill`, `select_option`, `press_key`, `get_text`, `extract_table`, `wait_for_text`, `audit_page`, `fingerprint`, `screenshot`, `record_check`, `finish`. Every tool schema is strict. Actions run one at a time because browser steps depend on order.
- **Verification.** Claude records one `record_check` per success criterion, with evidence. `finish` returns structured data that matches the task's `outputSchema`.

## Guardrails

| Control | Where |
|---|---|
| Domain allowlist: refused before navigation, and the browser is sent back if a click leaves the list | `allowedDomains` |
| Step budget, with a wrap-up warning near the end | `maxSteps` |
| Human-in-the-loop approval for sensitive clicks (prompted in a terminal, **denied** in CI unless `--auto-approve`) | `requireApprovalFor` (regexes on the element's name) |
| Secrets never reach the model: Claude types `{{secret:NAME}}` and the executor substitutes the value from an env var. Values echoed on the page are redacted, and password fields are masked in snapshots | `secrets` |
| Page text is treated as untrusted data. The demo catalogue includes a prompt-injection line to show this | system prompt |
| Refusal handling with server-side fallback (`fallbacks: "default"`) | [src/agent/agent.ts](src/agent/agent.ts) |

## Task file reference

```jsonc
{
  "name": "…", "category": "testing | form-submission | data-extraction | workflow | validation",
  "driver": "playwright",                // or "puppeteer"
  "startUrl": "https://…",
  "goal": "What the agent should achieve",
  "description": "Optional extra context",
  "successCriteria": ["one check per line"],
  "inputs": { "field": "value" },        // non-sensitive data the agent may type
  "secrets": { "PASSWORD": "ENV_VAR" },  // placeholder -> environment variable
  "outputSchema": { /* JSON Schema for finish.data_json */ },
  "allowedDomains": ["example.com"],
  "maxSteps": 30,
  "requireApprovalFor": ["submit", "pay", "delete"],
  "effort": "medium",                    // low | medium | high | xhigh | max
  "profile": "default",                  // default | vm | anti-detect (detection testing)
  "headless": true
}
```

## The demo site's seeded defect

`demo-site/server.ts` and `register.html` deliberately accept emails with no top-level domain (`user@company`). The validation-test task should report this as a defect and end with status `failure`. That is the expected result, and it shows the agent acting as a tester rather than a happy-path script.

## Cost

Each report shows the token usage and an estimated cost at claude-opus-5-5 list prices ($4 in / $20 out per 1M tokens, cache reads $0.20). The system prompt and tool definitions are prompt-cached across turns. For high-volume, simple extraction jobs, set `"effort": "low"`.

## Project layout

```
src/
  ui/server.ts           web UI server: jobs, live events (SSE), approvals, report hosting
  ui/public/index.html   the UI page
  tasks/suites.ts        generic suites built from a pasted URL
  cli.ts                 run/list commands, summary table, exit codes
  smoke.ts               driver test without an LLM
  agent/agent.ts         agent loop, usage/cost, approvals
  agent/tools.ts         tool schemas, executor, guardrails
  agent/prompts.ts       system prompt + task brief
  drivers/               BrowserDriver interface, Playwright + Puppeteer, in-page scripts
  report/report.ts       HTML + JSON reports per run
  report/history.ts      sessions, session summary reports, history index
  tasks/schema.ts        task file validation (zod)
demo-site/               local target site (form, catalogue, workflow)
tasks/                   example task specs
```
