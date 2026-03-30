"""ABOUTME: Data models for step compilation.
ABOUTME: Defines StepOutput returned by compiled functions and CompiledSkill manifest."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class StepOutput:
    """Result of executing a compiled step function."""

    summary: str
    captured_values: dict[str, Any] = field(default_factory=dict)
    output_fields: dict[str, str] | None = None
    terminate: bool = False
    terminate_values: dict[str, str] | None = None


@dataclass
class CompiledStepInfo:
    """Metadata about a single compiled step."""

    step_index: int
    status: str  # "compiled" or "failed"
    verified_against: int = 0
    held_out_passed: int = 0
    failures: int = 0
    failure_reason: str = ""


@dataclass
class CompiledSkillManifest:
    """Manifest for a compiled skill — tracks which steps are compiled."""

    skill_id: str
    skill_content_hash: str
    compiled_at: str = ""
    model_used: str = ""
    num_traces_total: int = 0
    num_prompt_examples: int = 0
    num_held_out: int = 0
    steps: dict[int, CompiledStepInfo] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "skill_content_hash": self.skill_content_hash,
            "compiled_at": self.compiled_at,
            "model_used": self.model_used,
            "num_traces_total": self.num_traces_total,
            "num_prompt_examples": self.num_prompt_examples,
            "num_held_out": self.num_held_out,
            "steps": {
                str(idx): {
                    "step_index": info.step_index,
                    "status": info.status,
                    "verified_against": info.verified_against,
                    "held_out_passed": info.held_out_passed,
                    "failures": info.failures,
                    "failure_reason": info.failure_reason,
                }
                for idx, info in self.steps.items()
            },
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> CompiledSkillManifest:
        steps = {}
        for idx_str, info in data.get("steps", {}).items():
            idx = int(idx_str)
            steps[idx] = CompiledStepInfo(
                step_index=info["step_index"],
                status=info["status"],
                verified_against=info.get("verified_against", 0),
                held_out_passed=info.get("held_out_passed", 0),
                failures=info.get("failures", 0),
                failure_reason=info.get("failure_reason", ""),
            )
        return CompiledSkillManifest(
            skill_id=data["skill_id"],
            skill_content_hash=data["skill_content_hash"],
            compiled_at=data.get("compiled_at", ""),
            model_used=data.get("model_used", ""),
            num_traces_total=data.get("num_traces_total", 0),
            num_prompt_examples=data.get("num_prompt_examples", 0),
            num_held_out=data.get("num_held_out", 0),
            steps=steps,
        )
