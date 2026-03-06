from pathlib import Path

from skillrunner.skills.loader import load_skill
from skillrunner.skills.parser import lint_skill_markdown


def test_example_skills_lint_clean() -> None:
    example_paths = [
        Path("examples/expense-approval/SKILL.md"),
        Path("examples/customer-escalation/SKILL.md"),
    ]

    for path in example_paths:
        raw = path.read_text(encoding="utf-8")
        lint = lint_skill_markdown(raw)
        assert lint.ok, f"lint errors for {path}: {lint.errors}"
        load_skill(path)
