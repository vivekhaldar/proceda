"""Tests that verify all example skills parse and lint correctly."""

from __future__ import annotations

from pathlib import Path

import pytest

from proceda.skills.loader import load_skill
from proceda.skills.parser import lint_skill

EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples"


def _get_example_dirs() -> list[Path]:
    if not EXAMPLES_DIR.exists():
        return []
    return sorted(d.parent for d in EXAMPLES_DIR.rglob("SKILL.md"))


class TestExampleSkills:
    @pytest.mark.parametrize("example_dir", _get_example_dirs(), ids=lambda p: p.name)
    def test_example_loads(self, example_dir: Path) -> None:
        skill = load_skill(example_dir)
        assert skill.name
        assert skill.description
        assert skill.step_count > 0

    @pytest.mark.parametrize("example_dir", _get_example_dirs(), ids=lambda p: p.name)
    def test_example_lints_clean(self, example_dir: Path) -> None:
        content = (example_dir / "SKILL.md").read_text()
        result = lint_skill(content, path=example_dir / "SKILL.md")
        assert result.ok, f"Lint errors: {[e.message for e in result.errors]}"

    def test_expense_processing(self) -> None:
        skill = load_skill(EXAMPLES_DIR / "expense-processing")
        assert skill.name == "expense-processing"
        assert skill.step_count == 3
        assert skill.required_tools is not None
        assert "receipts__extract" in skill.required_tools

    def test_support_escalation(self) -> None:
        skill = load_skill(EXAMPLES_DIR / "support-escalation")
        assert skill.name == "support-escalation"
        assert skill.step_count == 4

    def test_change_management(self) -> None:
        skill = load_skill(EXAMPLES_DIR / "change-management")
        assert skill.name == "change-management"
        assert skill.step_count == 5
        # Step 2 should require pre-approval
        step2 = skill.get_step(2)
        assert step2.requires_pre_approval
        # Step 5 should be optional
        step5 = skill.get_step(5)
        assert step5.is_optional

    def test_expense_approval_markers(self) -> None:
        skill = load_skill(EXAMPLES_DIR / "expense-processing")
        step2 = skill.get_step(2)
        assert step2.requires_post_approval
        step3 = skill.get_step(3)
        assert step3.requires_pre_approval

    def test_northwind_order_fulfillment(self) -> None:
        skill = load_skill(EXAMPLES_DIR / "northwind-ops" / "order-fulfillment")
        assert skill.name == "order-fulfillment"
        assert skill.step_count == 4
        assert skill.required_tools is not None
        assert "northwind__query" in skill.required_tools
        assert "northwind__execute" in skill.required_tools
        step3 = skill.get_step(3)
        assert step3.requires_pre_approval

    def test_northwind_monthly_sales_no_execute(self) -> None:
        """Read-only skill should not require the execute tool."""
        skill = load_skill(EXAMPLES_DIR / "northwind-ops" / "monthly-sales-report")
        assert skill.name == "monthly-sales-report"
        assert skill.required_tools is not None
        assert "northwind__execute" not in skill.required_tools

    def test_northwind_returns_chained_approvals(self) -> None:
        """Returns processing has two consecutive pre-approval gates."""
        skill = load_skill(EXAMPLES_DIR / "northwind-ops" / "returns-processing")
        assert skill.name == "returns-processing"
        assert skill.step_count == 5
        step3 = skill.get_step(3)
        assert step3.requires_pre_approval
        step4 = skill.get_step(4)
        assert step4.requires_pre_approval
