from __future__ import annotations

from pathlib import Path


def discover_skills(root: str | Path) -> list[Path]:
    root_path = Path(root)
    return sorted(p for p in root_path.rglob("SKILL.md") if p.is_file())
