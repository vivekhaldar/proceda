from skillrunner.skills.parser import lint_skill_markdown, parse_skill_markdown


def test_parse_skill_success() -> None:
    raw = """---
id: expense
name: Expense Approval
description: Process expenses
required_tools: ledger.submit, slack.notify
---
## Step 1: Validate
Check policy [requires_approval]

## Step 2: Submit
Submit in system [requires_post_approval]
"""
    skill = parse_skill_markdown(raw)
    assert skill.id == "expense"
    assert len(skill.steps) == 2
    assert skill.steps[0].requires_pre_approval is True
    assert skill.steps[1].requires_post_approval is True


def test_lint_warnings_and_errors() -> None:
    raw = """---
id: test
name: Test
description: Desc
---
## Step 2: Wrong
Body
"""
    result = lint_skill_markdown(raw)
    assert result.ok is False
    assert any("contiguous" in e for e in result.errors)
    assert any("required_tools" in w for w in result.warnings)
