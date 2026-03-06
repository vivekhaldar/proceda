from __future__ import annotations

from dataclasses import dataclass

from skillrunner.skill import SkillStep


@dataclass(slots=True)
class LLMDecision:
    complete_step: bool = True
    assistant_message: str = ""
    request_clarification: str | None = None


class LLMRuntime:
    async def decide_step(self, step: SkillStep, context: list[str]) -> LLMDecision:
        # Default deterministic behavior for OSS local runtime.
        return LLMDecision(complete_step=True, assistant_message=f"Completed step {step.index}: {step.title}")
