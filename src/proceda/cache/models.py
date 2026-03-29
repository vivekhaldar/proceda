"""ABOUTME: Data models for step-level execution cache.
ABOUTME: Defines recipes that encode tool call patterns extracted from traces."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any


class ArgumentSourceType(enum.Enum):
    """Where a tool call argument value comes from."""

    VARIABLE = "variable"
    STEP_RESULT = "step_result"
    LITERAL = "literal"


@dataclass
class ArgumentMapping:
    """Describes how to resolve a single tool call argument at runtime."""

    source: ArgumentSourceType
    key: str = ""
    literal_value: Any = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"source": self.source.value, "key": self.key}
        if self.source == ArgumentSourceType.LITERAL:
            d["literal_value"] = self.literal_value
        return d

    @staticmethod
    def from_dict(data: dict[str, Any]) -> ArgumentMapping:
        return ArgumentMapping(
            source=ArgumentSourceType(data["source"]),
            key=data.get("key", ""),
            literal_value=data.get("literal_value"),
        )


@dataclass
class ToolCallRecipe:
    """Encodes a single tool call to make during a step."""

    tool_name: str
    argument_mappings: dict[str, ArgumentMapping] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "argument_mappings": {
                name: mapping.to_dict() for name, mapping in self.argument_mappings.items()
            },
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> ToolCallRecipe:
        return ToolCallRecipe(
            tool_name=data["tool_name"],
            argument_mappings={
                name: ArgumentMapping.from_dict(m)
                for name, m in data.get("argument_mappings", {}).items()
            },
        )


@dataclass
class ResultCapture:
    """Describes a value to extract from a tool call result for use by later steps."""

    field_name: str
    from_tool_call_index: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "field_name": self.field_name,
            "from_tool_call_index": self.from_tool_call_index,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> ResultCapture:
        return ResultCapture(
            field_name=data["field_name"],
            from_tool_call_index=data.get("from_tool_call_index", 0),
        )


@dataclass
class StepRecipe:
    """A cached execution recipe for a single step."""

    step_index: int
    tool_call_recipes: list[ToolCallRecipe] = field(default_factory=list)
    result_captures: list[ResultCapture] = field(default_factory=list)
    summary_template: str = ""
    confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_index": self.step_index,
            "tool_call_recipes": [tc.to_dict() for tc in self.tool_call_recipes],
            "result_captures": [rc.to_dict() for rc in self.result_captures],
            "summary_template": self.summary_template,
            "confidence": self.confidence,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> StepRecipe:
        return StepRecipe(
            step_index=data["step_index"],
            tool_call_recipes=[
                ToolCallRecipe.from_dict(tc) for tc in data.get("tool_call_recipes", [])
            ],
            result_captures=[ResultCapture.from_dict(rc) for rc in data.get("result_captures", [])],
            summary_template=data.get("summary_template", ""),
            confidence=data.get("confidence", 0.0),
        )


@dataclass
class SkillCache:
    """The complete cache for a skill — one StepRecipe per step."""

    skill_id: str
    skill_content_hash: str
    step_recipes: dict[int, StepRecipe] = field(default_factory=dict)
    source_run_ids: list[str] = field(default_factory=list)
    created_at: str = ""

    def get_recipe(self, step_index: int) -> StepRecipe | None:
        """Return recipe for a step, or None if not cached."""
        return self.step_recipes.get(step_index)

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "skill_content_hash": self.skill_content_hash,
            "step_recipes": {
                str(idx): recipe.to_dict() for idx, recipe in self.step_recipes.items()
            },
            "source_run_ids": self.source_run_ids,
            "created_at": self.created_at,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> SkillCache:
        return SkillCache(
            skill_id=data["skill_id"],
            skill_content_hash=data["skill_content_hash"],
            step_recipes={
                int(idx): StepRecipe.from_dict(recipe)
                for idx, recipe in data.get("step_recipes", {}).items()
            },
            source_run_ids=data.get("source_run_ids", []),
            created_at=data.get("created_at", ""),
        )
