from __future__ import annotations

from skillrunner.skills.loader import resolve_skill_path
from skillrunner.skills.parser import lint_skill_markdown


def lint(path: str) -> int:
    skill_path = resolve_skill_path(path)
    result = lint_skill_markdown(skill_path.read_text(encoding="utf-8"))
    for warning in result.warnings:
        print(f"WARN: {warning}")
    for error in result.errors:
        print(f"ERROR: {error}")
    if not result.ok:
        return 2
    print("Skill lint passed")
    return 0
