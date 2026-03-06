from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class StepMarker(str, Enum):
    PRE_APPROVAL = "pre_approval"
    POST_APPROVAL = "post_approval"
    OPTIONAL = "optional"


@dataclass(slots=True)
class SkillStep:
    index: int
    title: str
    content: str
    markers: list[StepMarker] = field(default_factory=list)

    @property
    def requires_pre_approval(self) -> bool:
        return StepMarker.PRE_APPROVAL in self.markers

    @property
    def requires_post_approval(self) -> bool:
        return StepMarker.POST_APPROVAL in self.markers

    @property
    def is_optional(self) -> bool:
        return StepMarker.OPTIONAL in self.markers


@dataclass(slots=True)
class Skill:
    id: str
    name: str
    description: str
    raw_content: str
    steps: list[SkillStep]
    path: Path | None = None
    source_url: str | None = None
    required_tools: list[str] | None = None
