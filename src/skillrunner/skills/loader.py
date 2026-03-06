from __future__ import annotations

from pathlib import Path

from skillrunner.exceptions import SkillLoadError
from skillrunner.skill import Skill
from skillrunner.skills.parser import parse_skill_markdown


def resolve_skill_path(path: str | Path) -> Path:
    candidate = Path(path).expanduser().resolve()
    if candidate.is_dir():
        candidate = candidate / "SKILL.md"
    if not candidate.exists():
        raise SkillLoadError(f"Skill path not found: {candidate}")
    if candidate.name != "SKILL.md":
        raise SkillLoadError("Path must target SKILL.md or a directory containing it")
    return candidate


def load_skill(path: str | Path) -> Skill:
    skill_path = resolve_skill_path(path)
    raw = skill_path.read_text(encoding="utf-8")
    skill = parse_skill_markdown(raw)
    skill.path = skill_path
    return skill
