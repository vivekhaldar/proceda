from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class SkillRunnerConfig:
    model: str = "gpt-4o-mini"
    run_root: Path = Path(".skillrunner") / "runs"


def load_config() -> SkillRunnerConfig:
    run_root = Path(os.environ.get("SKILLRUNNER_RUN_ROOT", ".skillrunner/runs"))
    model = os.environ.get("SKILLRUNNER_MODEL", "gpt-4o-mini")
    return SkillRunnerConfig(model=model, run_root=run_root)
