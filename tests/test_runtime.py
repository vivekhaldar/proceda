import asyncio
from pathlib import Path

from skillrunner.human import HumanInterface
from skillrunner.runtime import Runtime
from skillrunner.skills.parser import parse_skill_markdown
from skillrunner.store import RunDirectoryManager


class RejectSecondApproval(HumanInterface):
    def __init__(self) -> None:
        self.calls = 0

    async def request_approval(self, request):
        self.calls += 1
        return self.calls == 1

    async def request_clarification(self, request):
        return "answer"


def test_runtime_completed(tmp_path: Path) -> None:
    skill = parse_skill_markdown(
        """---
id: s
name: Skill
description: Desc
---
## Step 1: One
Do one
## Step 2: Two
Do two
"""
    )
    runtime = Runtime(run_dir_manager=RunDirectoryManager(tmp_path))
    result = asyncio.run(runtime.run(skill))
    assert result.status.value == "completed"
    assert result.event_log_path.exists()


def test_runtime_cancelled_on_rejected_approval(tmp_path: Path) -> None:
    skill = parse_skill_markdown(
        """---
id: s
name: Skill
description: Desc
---
## Step 1: One
Do one [requires_approval]
## Step 2: Two
Do two [requires_approval]
"""
    )
    runtime = Runtime(human=RejectSecondApproval(), run_dir_manager=RunDirectoryManager(tmp_path))
    result = asyncio.run(runtime.run(skill))
    assert result.status.value == "cancelled"
    assert result.failed_step == 2
