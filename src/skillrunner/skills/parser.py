from __future__ import annotations

import re
from dataclasses import dataclass, field

from skillrunner.exceptions import SkillParseError
from skillrunner.skill import Skill, SkillStep, StepMarker

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
_STEP_RE = re.compile(r"^##\s+Step\s+(\d+):\s*(.+)$", re.MULTILINE)
_MARKER_MAP = {
    "[requires_approval]": StepMarker.PRE_APPROVAL,
    "[requires_post_approval]": StepMarker.POST_APPROVAL,
    "[optional]": StepMarker.OPTIONAL,
}


@dataclass(slots=True)
class LintResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _parse_frontmatter(raw: str) -> tuple[dict[str, str], str]:
    match = _FRONTMATTER_RE.match(raw)
    if not match:
        raise SkillParseError("Missing YAML frontmatter delimited by ---")
    frontmatter_text = match.group(1)
    meta: dict[str, str] = {}
    for line in frontmatter_text.splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        if ":" not in line:
            raise SkillParseError(f"Invalid frontmatter line: {line}")
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip().strip('"')
    return meta, raw[match.end() :]


def parse_skill_markdown(raw: str) -> Skill:
    meta, body = _parse_frontmatter(raw)
    required = ["id", "name", "description"]
    missing = [k for k in required if not meta.get(k)]
    if missing:
        raise SkillParseError(f"Missing required frontmatter fields: {', '.join(missing)}")

    matches = list(_STEP_RE.finditer(body))
    if not matches:
        raise SkillParseError("No steps found. Use headings like '## Step 1: Title'")

    steps: list[SkillStep] = []
    for i, match in enumerate(matches):
        step_number = int(match.group(1))
        title = match.group(2).strip()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        content = body[start:end].strip()
        markers = [mapped for token, mapped in _MARKER_MAP.items() if token in content]
        steps.append(SkillStep(index=step_number, title=title, content=content, markers=markers))

    return Skill(
        id=meta["id"],
        name=meta["name"],
        description=meta["description"],
        raw_content=raw,
        required_tools=[t.strip() for t in meta.get("required_tools", "").split(",") if t.strip()] or None,
        steps=steps,
    )


def lint_skill_markdown(raw: str) -> LintResult:
    result = LintResult()
    try:
        skill = parse_skill_markdown(raw)
    except SkillParseError as exc:
        result.errors.append(str(exc))
        return result

    if not skill.required_tools:
        result.warnings.append("No required_tools declared")

    indexes = [s.index for s in skill.steps]
    if len(indexes) != len(set(indexes)):
        result.errors.append("Duplicate step numbering found")
    if indexes != list(range(1, len(indexes) + 1)):
        result.errors.append("Step numbering must be contiguous starting at 1")
    if len(skill.steps) > 30:
        result.warnings.append("Skill has many steps (>30)")

    return result
