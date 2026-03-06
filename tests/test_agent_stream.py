import asyncio
from pathlib import Path

from skillrunner.agent import Agent


def test_agent_run_stream(tmp_path: Path) -> None:
    skill_path = tmp_path / "SKILL.md"
    skill_path.write_text(
        """---
id: s
name: Stream
description: Desc
---
## Step 1: A
Body
""",
        encoding="utf-8",
    )
    agent = Agent.from_path(str(skill_path))

    async def collect() -> list[str]:
        items = []
        async for event in agent.run_stream():
            items.append(event.type)
        return items

    event_types = asyncio.run(collect())
    assert "run.started" in event_types
    assert "run.completed" in event_types
