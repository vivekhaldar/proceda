# ABOUTME: Runs all Northwind skills with auto-approval and captures execution traces.
# ABOUTME: Outputs JSONL event logs and a summary for each skill run.

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from seed_db import ensure_db

from proceda import Agent
from proceda.config import AppConfig, LLMConfig, ProcedaConfig
from proceda.events import CollectorEventSink, EventType, RunEvent
from proceda.human import AutoApproveHumanInterface
from proceda.store.event_log import EventLogWriter

SKILLS = [
    "order-fulfillment",
    "inventory-restock",
    "customer-onboarding",
    "monthly-sales-report",
    "returns-processing",
]

TRACES_DIR = Path(__file__).parent / "traces"


class ConsolePrinter:
    """Prints key events to stdout during execution."""

    def __init__(self, skill_name: str) -> None:
        self._skill = skill_name

    async def handle(self, event: RunEvent) -> None:
        t = event.type
        p = event.payload
        prefix = f"  [{self._skill}]"

        if t == EventType.STEP_STARTED:
            print(f"{prefix} Step {p['step_index']}: {p['step_title']}")
        elif t == EventType.STEP_COMPLETED:
            print(f"{prefix}   ✓ Step {p['step_index']} completed")
        elif t == EventType.TOOL_CALLED:
            print(f"{prefix}   → tool: {p.get('tool_name')}")
        elif t == EventType.TOOL_FAILED:
            print(f"{prefix}   ✗ tool failed: {p.get('error', '')[:100]}")
        elif t == EventType.APPROVAL_REQUESTED:
            print(f"{prefix}   ⚡ approval requested (auto-approved)")
        elif t == EventType.RUN_COMPLETED:
            print(f"{prefix} ✓ RUN COMPLETED")
        elif t == EventType.RUN_FAILED:
            print(f"{prefix} ✗ RUN FAILED: {p.get('error', '')[:200]}")


def build_config() -> ProcedaConfig:
    server_path = str(Path(__file__).parent / "northwind_server.py")
    return ProcedaConfig(
        llm=LLMConfig(
            model="anthropic/claude-haiku-4-5-20251001",
            temperature=0.3,
            max_tokens=4096,
        ),
        apps=[
            AppConfig(
                name="northwind",
                description="Northwind company database",
                transport="stdio",
                command=["uv", "run", "python", server_path],
            ),
        ],
    )


async def run_skill(skill_name: str, config: ProcedaConfig) -> dict:
    """Run a single skill and return result summary."""
    skill_path = Path(__file__).parent / skill_name
    run_dir = TRACES_DIR / skill_name
    run_dir.mkdir(parents=True, exist_ok=True)

    human = AutoApproveHumanInterface()
    agent = Agent.from_path(str(skill_path), config=config, human=human)

    # Set up event sinks
    collector = CollectorEventSink()
    log_writer = EventLogWriter(run_dir)
    await log_writer.open()
    console = ConsolePrinter(skill_name)

    print(f"\n{'=' * 60}")
    print(f"Running: {skill_name} ({agent.skill.step_count} steps)")
    print(f"{'=' * 60}")

    try:
        result = await agent.run_async(event_sinks=[collector, log_writer, console])

        summary = {
            "skill": skill_name,
            "status": result.status.value,
            "summary": result.summary,
            "completed_steps": result.completed_steps,
            "total_steps": result.total_steps,
            "failed_step": result.failed_step,
            "event_count": len(collector.events),
            "tool_calls": len(collector.of_type(EventType.TOOL_CALLED)),
            "approvals": len(collector.of_type(EventType.APPROVAL_REQUESTED)),
        }
    except Exception as e:
        summary = {
            "skill": skill_name,
            "status": "error",
            "error": str(e),
        }
        print(f"  ERROR: {e}")

    # Write summary
    summary_path = run_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, default=str))

    await log_writer.close()
    return summary


async def main() -> None:
    ensure_db()
    config = build_config()

    TRACES_DIR.mkdir(parents=True, exist_ok=True)

    # Run skills to execute (all by default, or specific ones from CLI)
    skills_to_run = sys.argv[1:] if len(sys.argv) > 1 else SKILLS

    results = []
    for skill_name in skills_to_run:
        if skill_name not in SKILLS:
            print(f"Unknown skill: {skill_name}")
            continue
        result = await run_skill(skill_name, config)
        results.append(result)

    # Print final report
    print(f"\n{'=' * 60}")
    print("FINAL REPORT")
    print(f"{'=' * 60}")
    for r in results:
        status = r.get("status", "unknown")
        icon = "✓" if status == "completed" else "✗"
        steps = f"{r.get('completed_steps', '?')}/{r.get('total_steps', '?')}"
        tools = r.get("tool_calls", "?")
        approvals = r.get("approvals", "?")
        name = r["skill"]
        info = f"steps={steps} tools={tools} approvals={approvals}"
        print(f"  {icon} {name:25s} {status:10s} {info}")
        if r.get("summary"):
            # Print first 200 chars of summary
            summary_text = r["summary"][:200]
            print(f"    Summary: {summary_text}")
        if r.get("error"):
            print(f"    Error: {r['error'][:200]}")

    # Save overall report
    report_path = TRACES_DIR / "report.json"
    report_path.write_text(json.dumps(results, indent=2, default=str))
    print(f"\nTraces saved to: {TRACES_DIR}")
    print(f"Report saved to: {report_path}")


if __name__ == "__main__":
    asyncio.run(main())
