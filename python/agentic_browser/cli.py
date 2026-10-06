"""Command line: run task files, list tasks, list installed browsers.

    python -m agentic_browser run tasks/form-submission.json --driver both
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

from .agent.agent import RunOptions, RunResult, run_task
from .demo_site.server import DEMO_ORIGIN, is_demo_server_up, start_demo_server
from .drivers import DRIVER_NAMES, DriverName
from .drivers.installed import find_installed_browsers
from .report.history import new_job_id, now_iso, write_job_summary
from .report.report import write_report
from .tasks.schema import TaskSpec, load_task

EPILOG = """examples:
  python -m agentic_browser list
  python -m agentic_browser run tasks/*.json --driver both        # same tasks on Playwright and Selenium
  python -m agentic_browser browsers                              # browsers the standard engine can use
  python -m agentic_browser run tasks/*.json --driver standard --browser msedge
  python -m agentic_browser run tasks/*.json --driver all         # all three (or a list: --driver playwright,standard)
  python -m agentic_browser run tasks/expense-workflow.json --headed
"""


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40]


def parse_drivers(arg: str | None) -> list[DriverName] | None:
    """Parses --driver: one engine, a comma list, "both" (Playwright + Selenium) or "all"."""
    if not arg:
        return None
    if arg == "both":
        return ["playwright", "selenium"]
    if arg == "all":
        return list(DRIVER_NAMES)
    names = list(dict.fromkeys("selenium" if n.strip() == "puppeteer" else n.strip() for n in arg.split(",") if n.strip()))
    for n in names:
        if n not in DRIVER_NAMES:
            raise ValueError(f'Unknown driver "{n}"')
    return names  # type: ignore[return-value]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m agentic_browser", formatter_class=argparse.RawDescriptionHelpFormatter, epilog=EPILOG)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="List the task files in tasks/")
    sub.add_parser("browsers", help="List the browsers the standard engine can use")
    run = sub.add_parser("run", help="Run one or more task files")
    run.add_argument("files", nargs="+", help="Task JSON files")
    run.add_argument(
        "--driver",
        help='Override the task\'s driver: playwright, selenium, standard (installed Chrome/Edge), a comma list, "both" (playwright,selenium) or "all"',
    )
    run.add_argument(
        "--browser",
        help="Which installed browser the standard engine uses: auto, chrome, msedge, chrome-beta, msedge-beta, brave, vivaldi, or a full path",
    )
    run.add_argument("--headed", action="store_true", help="Show the browser window")
    run.add_argument("--auto-approve", action="store_true", help="Approve sensitive actions without prompting")
    run.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"], help="Override the task's effort level")
    run.add_argument("--runs-dir", default="runs", help="Where reports are written (default: runs)")
    return parser


def print_summary(results: list[RunResult]) -> None:
    if not results:
        return
    header = ["task", "driver", "status", "checks", "steps", "seconds", "costUsd"]
    rows = [
        [
            r.task_name,
            r.driver,
            r.status,
            f"{sum(1 for c in r.checks if c.passed)}/{len(r.checks)}",
            str(len(r.steps)),
            f"{r.duration_ms / 1000:.1f}",
            f"{r.cost_usd:.3f}",
        ]
        for r in results
    ]
    widths = [max(len(header[i]), *(len(row[i]) for row in rows)) for i in range(len(header))]
    print("\nSummary")
    for row in [header, ["-" * w for w in widths], *rows]:
        print("  ".join(cell.ljust(w) for cell, w in zip(row, widths)))


async def run_command(args: argparse.Namespace) -> int:
    driver_override = parse_drivers(args.driver)
    tasks: list[TaskSpec] = [load_task(f) for f in args.files]
    runs_dir = Path(args.runs_dir).resolve()

    # Starts the bundled demo site when a task targets it and it isn't already running.
    demo = None
    if any(t.start_url.startswith(DEMO_ORIGIN) for t in tasks) and not await is_demo_server_up():
        demo = await start_demo_server()
        print(f"Started demo site at {DEMO_ORIGIN}")

    results: list[RunResult] = []
    job_id = new_job_id()
    meta = {
        "jobId": job_id,
        "source": "cli",
        "startedAt": now_iso(),
        "urls": list(dict.fromkeys(t.start_url for t in tasks)),
        "suites": [Path(f).stem for f in args.files],
        "drivers": driver_override or list(dict.fromkeys(t.driver for t in tasks)),
        "browser": args.browser,
    }
    try:
        for task in tasks:
            for driver in driver_override or [task.driver]:
                run_dir = str(runs_dir / f"{job_id}-{slug(task.name)}-{driver}")
                print(f"\n▶ {task.name} ({driver})")
                result = await run_task(
                    task,
                    RunOptions(
                        driver=driver,
                        headless=False if args.headed else task.headless,
                        auto_approve=args.auto_approve,
                        effort=args.effort,
                        browser=args.browser,
                        run_dir=run_dir,
                        job_id=job_id,
                        log=print,
                    ),
                )
                paths = write_report(result)
                results.append(result)
                print(f"  → {result.status.upper()}: {result.summary}")
                print(f"  → report: {paths['html']}")
    finally:
        if demo:
            await demo.cleanup()

    print_summary(results)
    if results:
        meta["finishedAt"] = now_iso()
        print(f"\nSession summary: {write_job_summary(runs_dir, meta, results)}")
    return 0 if all(r.status == "success" for r in results) else 1


def main(argv: list[str] | None = None) -> int:
    load_dotenv(".env")
    args = build_parser().parse_args(argv)
    if args.command == "list":
        for f in sorted(Path("tasks").glob("*.json")):
            t = load_task(f)
            print(f"{str(f).ljust(36)} [{t.category}] {t.name}")
        return 0
    if args.command == "browsers":
        found = find_installed_browsers()
        if not found:
            print("No supported browsers found. Pass --browser <path> to use another Chromium-based browser.")
        for i, b in enumerate(found):
            print(f"{b.id.ljust(12)} {b.label.ljust(20)} {b.executable_path}{'  (auto)' if i == 0 else ''}")
        return 0
    return asyncio.run(run_command(args))


def entry() -> None:
    # Report text contains ✓ / → / ▶; don't crash on legacy Windows code pages.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")  # type: ignore[union-attr]
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as err:  # noqa: BLE001 - print a clean message, like the TS CLI
        print(err, file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    entry()
