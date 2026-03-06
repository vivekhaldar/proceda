from pathlib import Path

from skillrunner.skills.loader import load_skill, resolve_skill_path


def test_loader_directory(tmp_path: Path) -> None:
    skill_path = tmp_path / "SKILL.md"
    skill_path.write_text(
        """---
id: t
name: T
description: D
---
## Step 1: A
Do it
""",
        encoding="utf-8",
    )
    resolved = resolve_skill_path(tmp_path)
    assert resolved == skill_path
    skill = load_skill(tmp_path)
    assert skill.name == "T"
