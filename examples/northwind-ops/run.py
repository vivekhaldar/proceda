# ABOUTME: Runs a Northwind ops skill using the Proceda Python API.
# ABOUTME: Accepts a skill name as CLI argument (e.g., "order-fulfillment").

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from seed_db import ensure_db

from proceda import Agent, RunEvent
from proceda.config import ProcedaConfig
from proceda.events import EventType
from proceda.human import TerminalHumanInterface

console = Console()

SKILLS = [
    "order-fulfillment",
    "inventory-restock",
    "customer-onboarding",
    "monthly-sales-report",
    "returns-processing",
]


async def handle_event(event: RunEvent) -> None:
    t = event.type
    p = event.payload

    if t == EventType.STEP_STARTED:
        console.print(f"\n[bold cyan]>>> Step {p['step_index']}: {p['step_title']}[/bold cyan]")
    elif t == EventType.STEP_COMPLETED:
        console.print(f"[green]    Step {p['step_index']} completed[/green]")
    elif t == EventType.MESSAGE_ASSISTANT:
        if p.get("content"):
            console.print(f"    {p['content']}")
    elif t == EventType.TOOL_CALLED:
        console.print(f"    [cyan]Calling tool:[/cyan] {p.get('tool_name')}")
    elif t == EventType.TOOL_COMPLETED:
        console.print(f"    [green]Result:[/green] {str(p.get('result', ''))[:200]}")
    elif t == EventType.SUMMARY_GENERATED:
        console.print(f"    [dim]{p.get('summary', '')}[/dim]")


class EventPrinter:
    async def handle(self, event: RunEvent) -> None:
        await handle_event(event)


async def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in SKILLS:
        console.print(f"[bold]Usage:[/bold] uv run python {sys.argv[0]} <skill-name>")
        console.print(f"\nAvailable skills: {', '.join(SKILLS)}")
        sys.exit(1)

    skill_name = sys.argv[1]
    skill_path = Path(__file__).parent / skill_name

    # Ensure the database exists
    ensure_db()

    config = ProcedaConfig.load()
    human = TerminalHumanInterface(console)
    agent = Agent.from_path(str(skill_path), config=config, human=human)

    console.print(
        Panel(f"[bold]{agent.skill.name}[/bold]\n{agent.skill.description}", border_style="blue")
    )

    result = await agent.run_async(event_sinks=[EventPrinter()])

    console.print()
    console.print(Panel(result.summary, title="Run Summary", border_style="blue"))
    console.print(f"Status: {result.status.value}")


if __name__ == "__main__":
    asyncio.run(main())
