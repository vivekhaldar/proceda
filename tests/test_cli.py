from pathlib import Path

from skillrunner.cli.main import main


def _write_skill(tmp_path: Path) -> Path:
    path = tmp_path / "SKILL.md"
    path.write_text(
        """---
id: c
name: Cli
description: Desc
---
## Step 1: A
Body
""",
        encoding="utf-8",
    )
    return path


def test_cli_lint(tmp_path: Path, capsys) -> None:
    path = _write_skill(tmp_path)
    code = main(["lint", str(path)])
    out = capsys.readouterr().out
    assert code == 0
    assert "Skill lint passed" in out


def test_cli_run_and_replay(tmp_path: Path, capsys) -> None:
    path = _write_skill(tmp_path)
    code = main(["run", str(path)])
    out = capsys.readouterr().out
    assert code == 0
    event_log_line = [line for line in out.splitlines() if line.startswith("event_log=")][0]
    event_log = event_log_line.split("=", 1)[1]

    replay_code = main(["replay", event_log])
    replay_out = capsys.readouterr().out
    assert replay_code == 0
    assert "run.started" in replay_out
