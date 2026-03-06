from __future__ import annotations

import asyncio
from dataclasses import dataclass

from skillrunner.human import HumanInterface
from skillrunner.runtime import Runtime, run_sync
from skillrunner.session import RunResult, RunSession
from skillrunner.skill import Skill
from skillrunner.skills.loader import load_skill


@dataclass(slots=True)
class Agent:
    skill: Skill
    human: HumanInterface | None = None

    @classmethod
    def from_path(cls, path: str, human: HumanInterface | None = None) -> "Agent":
        return cls(skill=load_skill(path), human=human)

    def create_session(self) -> RunSession:
        return RunSession.create(skill_id=self.skill.id, skill_name=self.skill.name)

    def run(self) -> RunResult:
        runtime = Runtime(human=self.human)
        return run_sync(runtime, self.skill)

    async def run_stream(self):
        from skillrunner.events import CompositeEventSink, RunEvent

        queue: asyncio.Queue[RunEvent | None] = asyncio.Queue()

        class QueueSink:
            async def handle(self, event: RunEvent) -> None:
                await queue.put(event)

        session = self.create_session()
        runtime = Runtime(human=self.human, sink=CompositeEventSink([QueueSink()]))

        async def _runner() -> None:
            await runtime.run(self.skill, session=session)
            await queue.put(None)

        task = asyncio.create_task(_runner())
        while True:
            item = await queue.get()
            if item is None:
                break
            yield item
        await task
